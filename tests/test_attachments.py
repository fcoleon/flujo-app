import io
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import app


class AttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=app.DB
        self.old_settings=app.SETTINGS['backblaze']
        app.DB=os.path.join(self.tmp.name,'test.db')
        app.SETTINGS['backblaze']={k:'test' for k in ('endpoint','region','bucket','key_id','application_key')}
        app.init()
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(1,'Admin','admin','','',1)")
            c.execute("INSERT INTO users(id,name,username,password,salt) VALUES(2,'Otro','otro','','')")
            c.executemany('INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)',[('one',1,time.time()+1000),('two',2,time.time()+1000)])
            type_id=app.ensure_base_type(c,None)
            self.task=c.execute("INSERT INTO tasks(title,created_at,type_id,status_id) VALUES('Prueba',?,?,?)",(app.now(),type_id,app.default_status(c,type_id))).lastrowid
        self.client=app.app.test_client();self.client.set_cookie('session','one')
        self.s3=MagicMock()
        self.patcher=patch('avatars.client',return_value=self.s3);self.patcher.start()
        self.cleanup=patch('avatars.cleanup');self.cleanup.start()

    def tearDown(self):
        self.cleanup.stop();self.patcher.stop()
        app.DB=self.old_db;app.SETTINGS['backblaze']=self.old_settings
        self.tmp.cleanup()

    def upload(self,content=b'abc',name='doc.pdf',headers=None):
        return self.client.post(f'/api/tasks/{self.task}/attachments',data={'file':(io.BytesIO(content),name)},headers=headers or {'X-Flujo-Request':'attachment'})

    def test_upload_download_and_delete(self):
        self.assertEqual(self.client.get('/api/data').json['tasks'][0]['attachment_count'],0)
        result=self.upload();self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['attachment_count'],1)
        self.assertEqual(self.client.get('/api/data').json['tasks'][0]['attachment_count'],1)
        ident=result.json['id']
        items=self.client.get(f'/api/tasks/{self.task}/attachments').json['attachments']
        self.assertEqual(items[0]['name'],'doc.pdf')
        self.s3.get_object.return_value={'Body':io.BytesIO(b'abc')}
        response=self.client.get('/api/attachments/'+ident)
        self.assertEqual(response.data,b'abc')
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        deleted=self.client.post('/api/attachments/'+ident,json={})
        self.assertEqual(deleted.status_code,200)
        self.assertEqual(deleted.json['attachment_count'],0)
        self.assertEqual(self.client.get('/api/data').json['tasks'][0]['attachment_count'],0)
        with app.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],1)

    def test_permissions_and_validation(self):
        self.assertEqual(self.upload(headers={'Other':'x'}).status_code,403)
        self.assertEqual(self.upload(content=b'').status_code,400)
        ident=self.upload().json['id']
        other=app.app.test_client();other.set_cookie('session','two')
        self.assertEqual(other.get(f'/api/tasks/{self.task}/attachments').status_code,403)
        self.assertEqual(other.get('/api/attachments/'+ident).status_code,403)
        self.assertEqual(other.post('/api/attachments/'+ident,json={}).status_code,403)
        self.assertEqual(app.app.test_client().get('/api/attachments/'+ident).status_code,401)

    def test_deleting_task_queues_file_and_disabled_storage(self):
        self.upload()
        with app.db() as c:
            c.execute('DELETE FROM tasks WHERE id=?',(self.task,))
            self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],1)
        app.SETTINGS['backblaze']={k:'' for k in app.SETTINGS['backblaze']}
        self.assertEqual(self.client.get(f'/api/tasks/{self.task}/attachments').status_code,503)
        self.assertFalse(self.client.get('/api/data').json['attachments_enabled'])

    def test_blocked_during_upload_cannot_attach_file(self):
        def block(**kwargs):
            with app.db() as c: c.execute('UPDATE users SET blocked=1 WHERE id=1')
        self.s3.put_object.side_effect=block
        self.assertEqual(self.upload().status_code,403)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM task_attachments').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM avatar_cleanup').fetchone()[0],1)

    def test_viewer_can_download_but_not_modify(self):
        ident=self.upload().json['id']
        with app.db() as c:
            c.execute("INSERT INTO projects(id,name) VALUES(1,'Proyecto')")
            c.execute("INSERT INTO members VALUES(1,2,'viewer')")
            c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(1,1,'Columna',0)")
            c.execute("INSERT INTO placements VALUES(?,1,1,0)",(self.task,))
        other=app.app.test_client();other.set_cookie('session','two')
        self.s3.get_object.return_value={'Body':io.BytesIO(b'abc')}
        self.assertEqual(other.get(f'/api/tasks/{self.task}/attachments').status_code,200)
        self.assertEqual(other.get('/api/attachments/'+ident).status_code,200)
        self.assertEqual(other.post('/api/attachments/'+ident,json={}).status_code,403)
        self.assertEqual(other.post(f'/api/tasks/{self.task}/attachments',data={'file':(io.BytesIO(b'x'),'x')},headers={'X-Flujo-Request':'attachment'}).status_code,403)
