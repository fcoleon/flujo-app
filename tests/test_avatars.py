import io
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from PIL import Image
from botocore.exceptions import EndpointConnectionError
import app
import avatars


class AvatarTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=app.DB
        self.old_settings=app.SETTINGS['backblaze']
        app.DB=os.path.join(self.tmp.name,'test.db')
        app.SETTINGS['backblaze']={'endpoint':'https://s3.example.com','region':'us-west-1','bucket':'test','key_id':'id','application_key':'secret'}
        app.init()
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt) VALUES(1,'A','a','','')")
            c.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('token',1,?)",(int(time.time())+1000,))
        self.client=app.app.test_client()
        self.client.set_cookie('session','token')
        self.s3=MagicMock()
        self.s3.generate_presigned_url.return_value='https://s3.example.com/signed'
        self.s3.get_paginator.return_value.paginate.return_value=[]
        self.patcher=patch('avatars.client',return_value=self.s3)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        app.DB=self.old_db
        app.SETTINGS['backblaze']=self.old_settings
        self.tmp.cleanup()

    def picture(self):
        result=io.BytesIO()
        Image.new('RGB',(600,300),'red').save(result,format='PNG')
        result.seek(0)
        return result

    def upload(self, image):
        return self.client.post('/api/avatar',data={'avatar':(image,'a.png')},headers={'X-Flujo-Request':'avatar'})

    def test_upload_get_and_remove(self):
        self.assertEqual(self.upload(self.picture()).status_code,200)
        stored=self.s3.put_object.call_args.kwargs
        with Image.open(io.BytesIO(stored['Body'])) as image:
            self.assertEqual(image.size,(512,256))
            self.assertEqual(image.format,'JPEG')
        self.assertTrue(self.client.get('/api/data').json['users'][0]['avatar_version'])
        response=self.client.get('/api/avatar/1')
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')
        self.assertEqual(app.app.test_client().get('/api/avatar/1').status_code,401)
        self.assertEqual(self.client.post('/api/avatar/delete',json={}).status_code,200)
        self.assertEqual(self.client.get('/api/avatar/1').status_code,404)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],1)

    def test_invalid_blocked_and_csrf(self):
        self.assertEqual(self.upload(io.BytesIO(b'not an image')).status_code,400)
        self.assertEqual(self.client.post('/api/avatar',data={'avatar':(self.picture(),'a.png')}).status_code,403)
        with app.db() as c: c.execute('UPDATE users SET blocked=1 WHERE id=1')
        self.assertEqual(self.upload(self.picture()).status_code,403)
        self.assertEqual(self.client.get('/api/avatar/1').status_code,403)
        self.assertFalse(self.s3.put_object.called)

    def test_cleanup_deletes_versions(self):
        with app.db() as c:
            c.execute("INSERT INTO avatar_cleanup VALUES('avatars/old',0)")
        self.s3.get_paginator.return_value.paginate.return_value=[{'Versions':[{'Key':'avatars/old','VersionId':'v1'}]}]
        with app.db() as c:
            avatars.cleanup(c,app.SETTINGS['backblaze'])
            self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],0)
        self.s3.delete_object.assert_called_once_with(Bucket='test',Key='avatars/old',VersionId='v1')

    def test_upload_failure_keeps_previous_photo_and_queues_cleanup(self):
        self.assertEqual(self.upload(self.picture()).status_code,200)
        with app.db() as c:
            before=c.execute('SELECT avatar_key FROM users WHERE id=1').fetchone()[0]
        self.s3.put_object.side_effect=EndpointConnectionError(endpoint_url='https://s3.example.com')
        self.assertEqual(self.upload(self.picture()).status_code,503)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT avatar_key FROM users WHERE id=1').fetchone()[0],before)
            self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],1)

    def test_request_size_is_limited(self):
        self.assertEqual(self.upload(io.BytesIO(b'x'*(2*1024*1024+65537))).status_code,413)
        self.assertFalse(self.s3.put_object.called)

    def test_cached_url_still_checks_session_and_changes_with_photo(self):
        from cache import Cache
        cache=Cache({'type':'memcached','host':'localhost','port':11211})
        values={}
        def call(operation,key,value=None,ttl=None):
            if operation=='get': return values.get(key)
            values[key]=value
        with patch.object(app,'CACHE',cache),patch.object(cache,'_call',side_effect=call):
            self.assertEqual(self.upload(self.picture()).status_code,200)
            self.assertEqual(self.client.get('/api/avatar/1').status_code,302)
            self.assertEqual(self.client.get('/api/avatar/1').status_code,302)
            self.assertEqual(self.s3.generate_presigned_url.call_count,1)
            self.assertEqual(app.app.test_client().get('/api/avatar/1').status_code,401)
            self.assertEqual(self.upload(self.picture()).status_code,200)
            self.assertEqual(self.client.get('/api/avatar/1').status_code,302)
            self.assertEqual(self.s3.generate_presigned_url.call_count,2)
            with app.db() as c: c.execute('UPDATE users SET blocked=1 WHERE id=1')
            self.assertEqual(self.client.get('/api/avatar/1').status_code,403)
