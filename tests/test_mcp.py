import hashlib
import json
import logging
import os
import tempfile
import unittest
from uuid import uuid4

from starlette.testclient import TestClient

import app


class McpTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=app.DB;app.DB=os.path.join(self.tmp.name,'test.db')
        self.addCleanup(self.tmp.cleanup);self.addCleanup(setattr,app,'DB',self.old_db)
        app.init()
        with app.db() as c:
            for uid,admin in ((1,1),(2,0)):
                c.execute('INSERT INTO users(id,name,username,password,salt,admin) VALUES(?,?,?,?,?,?)',(uid,str(uid),str(uid),'','',admin))
                c.execute('INSERT INTO sessions VALUES(?,?)',(str(uid),uid))

    def client(self,user=None):
        client=app.app.test_client()
        if user: client.set_cookie('session',str(user))
        return client

    def create(self,name='OpenCode',user=1,status=200):
        response=self.client(user).post('/api/tokens',json={'name':name})
        self.assertEqual(response.status_code,status,response.get_json())
        return response.get_json()

    def test_token_is_shown_once_and_only_its_hash_is_stored(self):
        created=self.create('Portátil')
        token=created['token']
        self.assertTrue(token.startswith('flj_'))
        with app.db() as c:
            row=c.execute('SELECT user_id,name,token_hash,last_used_at,revoked_at FROM mcp_tokens').fetchone()
            self.assertEqual((row['user_id'],row['name']),(1,'Portátil'))
            self.assertEqual(row['token_hash'],hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(token,[value for value in tuple(row) if isinstance(value,str)])
            self.assertIsNone(row['revoked_at'])

    def test_list_returns_own_tokens_without_hashes(self):
        self.create('Uno'); self.create('Dos',user=2)
        payload=self.client(1).get('/api/tokens').get_json()['tokens']
        self.assertEqual([t['name'] for t in payload],['Uno'])
        self.assertEqual(sorted(payload[0]),['created_at','id','last_used_at','name','revoked_at'])

    def test_requires_session_and_unblocked_password(self):
        self.assertEqual(self.client().get('/api/tokens').status_code,401)
        self.assertEqual(self.client().post('/api/tokens',json={'name':'x'}).status_code,401)
        with app.db() as c:
            c.execute('UPDATE users SET blocked=1 WHERE id=2')
        self.assertEqual(self.client(2).post('/api/tokens',json={'name':'x'}).status_code,403)
        with app.db() as c:
            c.execute('UPDATE users SET blocked=0,must_change_password=1 WHERE id=2')
        self.assertEqual(self.client(2).post('/api/tokens',json={'name':'x'}).status_code,403)
        self.assertEqual(self.client(2).get('/api/tokens').status_code,403)

    def test_name_validation(self):
        for name in ('', '   ', 'x'*61, 5, None):
            with self.subTest(name=name):
                self.assertEqual(self.client(1).post('/api/tokens',json={'name':name}).status_code,400)

    def test_revoke_only_affects_own_active_tokens(self):
        first=self.create('Uno'); second=self.create('Dos')
        other=self.create('Ajeno',user=2)
        client=self.client(1)
        self.assertEqual(client.post('/api/tokens/revoke',json={'id':9999}).status_code,400)
        self.assertEqual(self.client(2).post('/api/tokens/revoke',json={'id':first['id']}).status_code,400)
        self.assertEqual(client.post('/api/tokens/revoke',json={'id':first['id']}).status_code,200)
        self.assertEqual(client.post('/api/tokens/revoke',json={'id':first['id']}).status_code,400)
        with app.db() as c:
            self.assertIsNotNone(c.execute('SELECT revoked_at FROM mcp_tokens WHERE id=?',(first['id'],)).fetchone()['revoked_at'])
            self.assertIsNone(c.execute('SELECT revoked_at FROM mcp_tokens WHERE id=?',(second['id'],)).fetchone()['revoked_at'])
            self.assertIsNone(c.execute('SELECT revoked_at FROM mcp_tokens WHERE id=?',(other['id'],)).fetchone()['revoked_at'])

    def test_token_resolution_for_agents(self):
        token=self.create('Agente')['token']
        blocked_token=self.create('Bloqueado')['token']
        password_token=self.create('Temporal')['token']
        with app.db() as c:
            user,reason=app.mcp_token_user(c,token)
            self.assertEqual((user['id'],reason),(1,None))
            last=c.execute('SELECT last_used_at FROM mcp_tokens WHERE token_hash=?',(app.mcp_token_hash(token),)).fetchone()['last_used_at']
            self.assertIsNotNone(last)
            c.execute('UPDATE mcp_tokens SET revoked_at=? WHERE token_hash=?',(app.now(),app.mcp_token_hash(token)))
            self.assertEqual(app.mcp_token_user(c,token),(None,'invalid'))
        with app.db() as c:
            c.execute('UPDATE users SET blocked=1 WHERE id=1')
        with app.db() as c:
            self.assertEqual(app.mcp_token_user(c,blocked_token)[1],'blocked')
        with app.db() as c:
            c.execute('UPDATE users SET blocked=0,must_change_password=1 WHERE id=1')
        with app.db() as c:
            self.assertEqual(app.mcp_token_user(c,password_token)[1],'password')
        self.assertEqual(app.mcp_token_user(c,'flj_desconocido'),(None,'invalid'))


class McpToolsTest(unittest.TestCase):
    """Visibilidad de las tools MCP: cada token ve exactamente lo que su usuario."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = os.path.join(self.tmp.name, 'tools.db')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, app, 'DB', self.old_db)
        app.init()
        self.public_id = str(uuid4())
        with app.db() as c:
            for uid, admin in ((1, 1), (2, 0), (3, 0)):
                c.execute('INSERT INTO users(id,name,username,password,salt,admin) VALUES(?,?,?,?,?,?)',
                          (uid, f'user{uid}', f'uid{uid}', '', '', admin))
            c.execute('INSERT INTO projects(id,name,public_id) VALUES(1,?,?)', ('Visible', self.public_id))
            c.execute('INSERT INTO projects(id,name) VALUES(2,?)', ('Privado',))
            c.execute('INSERT INTO members(project_id,user_id,role) VALUES(1,2,?)', ('viewer',))
            for pid in (1, 2):
                type_id = app.ensure_base_type(c, pid)
                c.execute('INSERT INTO columns(id,project_id,name,position) VALUES(?,?,?,?)', (pid, pid, 'To do', 1))
                status = app.default_status(c, type_id)
                done = c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1', (type_id,)).fetchone()['id']
                if pid == 1:
                    c.execute('''INSERT INTO tasks(id,title,description,due_date,priority,created_at,type_id,status_id,public_id)
                                 VALUES(1,'Comprar pan','Leche y pan','2026-01-01','Alta','2026-01-01T00:00:00+00:00',?,?,?)''',
                              (type_id, status, str(uuid4())))
                    c.execute('''INSERT INTO tasks(id,title,assignee_id,created_at,type_id,status_id,public_id)
                                 VALUES(2,'Terminar informe',2,'2026-01-02T00:00:00+00:00',?,?,?)''',
                              (type_id, done, str(uuid4())))
                    c.execute('INSERT INTO placements(task_id,project_id,column_id,position) VALUES(1,1,1,1)')
                    c.execute('INSERT INTO placements(task_id,project_id,column_id,position) VALUES(2,1,1,2)')
                    c.execute('INSERT INTO comments(task_id,user_id,body,created_at) VALUES(1,1,?,?)', ('Hola', '2026-01-03T00:00:00+00:00'))
                    c.execute('INSERT INTO history(task_id,user_id,message,created_at) VALUES(1,1,?,?)', ('Creada', '2026-01-03T00:00:00+00:00'))
                else:
                    c.execute('''INSERT INTO tasks(id,title,created_at,type_id,status_id,public_id)
                                 VALUES(3,'Secreto','2026-01-03T00:00:00+00:00',?,?,?)''', (type_id, status, str(uuid4())))
                    c.execute('INSERT INTO placements(task_id,project_id,column_id,position) VALUES(3,2,1,1)')
            self.tokens = {}
            for name, uid in (('admin', 1), ('viewer', 2)):
                token = 'flj_' + name
                self.tokens[name] = token
                c.execute('INSERT INTO mcp_tokens(user_id,name,token_hash,created_at) VALUES(?,?,?,?)',
                          (uid, name, app.mcp_token_hash(token), app.now()))
        # El SDK registra las peticiones fallidas a nivel INFO: evita ruido en la salida.
        logging.getLogger('mcp').setLevel(logging.ERROR)
        import mcp_server
        self.mcp = mcp_server
        # Cada instancia crea su propio session manager, que solo admite un lifespan.
        self.client = TestClient(mcp_server.asgi_app(), base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, body, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        return self.client.post('/mcp', json=body, headers=headers)

    def call(self, name, args=None, token='viewer', mid=1):
        response = self.post({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call',
                              'params': {'name': name, 'arguments': args or {}}}, self.tokens[token])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['result']

    @staticmethod
    def text(result):
        return result['content'][0]['text']

    @classmethod
    def data(cls, result):
        """Payload JSON que la tool devolvió (los errores vienen como texto libre)."""
        return json.loads(cls.text(result))

    def test_requests_without_a_valid_token_are_rejected(self):
        for token in (None, 'flj_desconocido'):
            with self.subTest(token=token):
                response = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}, token)
                self.assertEqual(response.status_code, 401)
        with app.db() as c:
            c.execute('UPDATE mcp_tokens SET revoked_at=? WHERE user_id=1', (app.now(),))
            c.execute('UPDATE users SET blocked=1 WHERE id=2')
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}, 'flj_admin').status_code, 401)
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}, 'flj_viewer').status_code, 403)

    def test_viewer_lists_only_its_projects_and_foreign_projects_look_missing(self):
        projects = self.data(self.call('list_projects', token='viewer'))['projects']
        self.assertEqual([(p['name'], p['role']) for p in projects], [('Visible', 'viewer')])
        self.assertEqual(projects[0]['task_count'], 2)
        for ref in (2,):
            with self.subTest(project=ref):
                result = self.call('get_project', {'project': ref}, token='viewer')
                self.assertTrue(result['isError'])
                self.assertIn('Proyecto no encontrado', self.text(result))
        detail = self.data(self.call('get_project', {'project': self.public_id}, token='viewer'))
        self.assertEqual((detail['name'], detail['role']), ('Visible', 'viewer'))
        self.assertEqual(len(detail['columns']), 1)
        self.assertEqual([m['username'] for m in detail['members']], ['uid2'])
        admin_projects = self.data(self.call('list_projects', token='admin'))['projects']
        self.assertEqual({p['name'] for p in admin_projects}, {'Visible', 'Privado'})

    def test_task_listing_applies_membership_and_filters(self):
        titles = lambda result: {t['title'] for t in self.data(result)['tasks']}
        self.assertEqual(titles(self.call('list_tasks')), {'Comprar pan', 'Terminar informe'})
        self.assertEqual(titles(self.call('list_tasks', {'project': 1})), {'Comprar pan', 'Terminar informe'})
        self.assertTrue(self.call('list_tasks', {'project': 2})['isError'])
        self.assertEqual(titles(self.call('list_tasks', {'assignee': 'me'})), {'Terminar informe'})
        self.assertEqual(titles(self.call('list_tasks', {'assignee': 'uid2'})), {'Terminar informe'})
        self.assertEqual(titles(self.call('list_tasks', {'done': True})), {'Terminar informe'})
        self.assertEqual(titles(self.call('list_tasks', {'done': False})), {'Comprar pan'})
        self.assertEqual(titles(self.call('list_tasks', {'search': 'pan'})), {'Comprar pan'})
        self.assertEqual(titles(self.call('list_tasks', {'priority': 'Alta'})), {'Comprar pan'})
        self.assertEqual(titles(self.call('list_tasks', {'due_before': '2026-01-01'})), {'Comprar pan'})
        page = self.data(self.call('list_tasks', {'limit': 1}))
        self.assertEqual(len(page['tasks']), 1)
        self.assertTrue(page['has_more'])
        for args in ({'limit': 0}, {'limit': 101}, {'priority': 'Media'}, {'due_before': 'hoy'},
                     {'search': '  '}, {'project': 99}, {'project': 'no-uuid'}):
            with self.subTest(args=args):
                self.assertTrue(self.call('list_tasks', args)['isError'])

    def test_task_detail_comments_and_history_respect_visibility(self):
        detail = self.data(self.call('get_task', {'task': 1}, token='viewer'))
        self.assertEqual((detail['title'], detail['description'], detail['can_edit']), ('Comprar pan', 'Leche y pan', False))
        comments = self.data(self.call('task_comments', {'task': 1}, token='viewer'))['comments']
        self.assertEqual([c['body'] for c in comments], ['Hola'])
        history = self.data(self.call('task_history', {'task': 1}, token='viewer'))['history']
        self.assertEqual([h['message'] for h in history], ['Creada'])
        for tool in ('get_task', 'task_comments', 'task_history'):
            with self.subTest(tool=tool):
                result = self.call(tool, {'task': 3}, token='viewer')
                self.assertTrue(result['isError'])
                self.assertIn('Tarea no encontrada', self.text(result))
        self.assertTrue(self.call('get_task', {'task': 9999}, token='viewer')['isError'])
        self.assertEqual(self.data(self.call('get_task', {'task': 3}, token='admin'))['title'], 'Secreto')


if __name__ == '__main__':
    unittest.main()
