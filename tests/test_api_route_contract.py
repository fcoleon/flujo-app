import os
import tempfile
import unittest

import app
import api_router
from flask import Flask, jsonify


class ApiRouteContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=app.DB
        app.DB=os.path.join(self.tmp.name,'test.db')
        app.init()
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(1,'Usuario','usuario','','',0)")
            c.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('test',1,4102444800)")
        self.client=app.app.test_client()
        self.client.set_cookie('session','test')

    def tearDown(self):
        app.DB=self.old_db
        self.tmp.cleanup()

    def test_unknown_routes_and_json_validation(self):
        for method in ('get','post'):
            response=getattr(self.client,method)('/api/no-existe',**({'json':{}} if method=='post' else {}))
            self.assertEqual(response.status_code,404)
            self.assertEqual(response.json['error'],'No encontrado')
        response=self.client.post('/api/projects',data='{}',content_type='text/plain')
        self.assertEqual(response.status_code,415)
        self.assertIn('error',response.json)

    def test_protected_routes_require_session(self):
        client=app.app.test_client()
        for method,path in [('get','/api/data'),('get','/api/automations?project_id=1'),
                            ('post','/api/projects'),('post','/api/task/update')]:
            response=getattr(client,method)(path,**({'json':{}} if method=='post' else {}))
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.json['error'],'Inicia sesión')

    def test_head_never_calls_a_post_handler(self):
        response=self.client.head('/api/tasks',json={'title':'No crear'})
        self.assertEqual(response.status_code,404)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_router_uses_only_explicit_services(self):
        isolated=Flask(__name__)
        services=api_router.ApiServices(
            db=app.db,settings={'allow_self_registration':False},version='test',
            data_response=lambda c,u: jsonify({'owner':u['id']}),
            analytics=lambda c,p,month,typ: {})
        api_router.register_api(isolated,services)
        client=isolated.test_client();client.set_cookie('session','test')
        self.assertEqual(client.get('/api/data').json,{'owner':1})
        self.assertEqual(client.get('/api/revision').json['version'],'test')
