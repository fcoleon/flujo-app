import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import app


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = app.DB
        app.DB = os.path.join(self.tmp.name, 'test.db')
        app.init()
        with app.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(1,'Admin','admin','','',1)")
            c.execute("INSERT INTO sessions VALUES('token',1)")
            c.execute("INSERT INTO projects(id,name,public_id) VALUES(1,'Antes','project-1')")
            app.ensure_base_type(c, 1)
            self.tid = c.execute("INSERT INTO tasks(title,created_at,type_id,status_id,public_id) VALUES('Inicial',?,?,?,'task-1')", (app.now(), app.ensure_base_type(c, None), app.default_status(c, app.ensure_base_type(c, None)))).lastrowid

    def tearDown(self):
        app.DB = self.old
        self.tmp.cleanup()

    def client(self):
        client = app.app.test_client()
        client.set_cookie('session', 'token')
        return client

    def test_revision_commit_rollback_and_migration(self):
        client = self.client()
        self.assertEqual(app.app.test_client().get('/api/revision').status_code, 401)
        revision = client.get('/api/revision').json['revision']
        self.assertEqual(client.get('/api/revision').json['version'], app.VERSION)
        with app.db() as c:
            c.execute("UPDATE tasks SET title='Temporal' WHERE id=?", (self.tid,))
            c.rollback()
        self.assertEqual(client.get('/api/revision').json['revision'], revision)
        with app.db() as c:
            c.execute("UPDATE tasks SET title='Cambio' WHERE id=?", (self.tid,))
        after = client.get('/api/revision').json['revision']
        self.assertGreater(after, revision)
        app.init(); app.init()
        self.assertEqual(client.get('/api/revision').json['revision'], after)
        self.assertEqual(client.get('/api/data').json['revision'], after)

    def test_snapshot_is_consistent_with_concurrent_commit(self):
        client = self.client()
        before = client.get('/api/revision').json['revision']
        original = app.role
        def change(c, u, p):
            with app.db() as writer:
                writer.execute("UPDATE projects SET name='Después' WHERE id=1")
            return original(c, u, p)
        with patch.object(app, 'role', side_effect=change):
            data = client.get('/api/data').json
        self.assertEqual(data['projects'][0]['name'], 'Antes')
        self.assertEqual(data['revision'], before)
        self.assertGreater(client.get('/api/revision').json['revision'], before)

    def test_conflicts_independent_fields_and_atomicity(self):
        client = self.client()
        url = '/api/task/update'
        self.assertEqual(client.post(url, json={'id':self.tid,'title':'X'}).status_code, 400)
        first = client.post(url, json={'id':self.tid,'title':'  Nuevo  ','title_version':0}).json
        self.assertEqual((first['title'], first['title_version'], first['description_version']), ('Nuevo', 1, 0))
        desc = client.post(url, json={'id':self.tid,'description':'<b>Hola</b><script>x</script>','description_format':'html','description_version':0}).json
        self.assertEqual((desc['description'], desc['description_version']), ('<b>Hola</b>x', 1))
        conflict = client.post(url, json={'id':self.tid,'title':'Obsoleto','title_version':0,'priority':'Alta'})
        self.assertEqual(conflict.status_code, 409)
        self.assertTrue(conflict.json['conflict'])
        self.assertEqual(client.get('/api/data').json['tasks'][0]['priority'], 'Normal')
        self.assertEqual(client.post(url, json={'id':self.tid,'description':'X','description_version':0}).status_code, 409)
        self.assertEqual(client.post(url, json={'id':self.tid,'priority':'Alta'}).status_code, 200)

    def test_concurrent_edits_have_one_winner(self):
        barrier = Barrier(2)
        def edit(title):
            client = self.client()
            barrier.wait()
            return client.post('/api/task/update', json={'id':self.tid,'title':title,'title_version':0}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(edit, ('Uno','Dos'))), [200, 409])

    def test_legacy_columns_are_added_once(self):
        legacy = os.path.join(self.tmp.name, 'legacy.db')
        with sqlite3.connect(legacy) as c:
            c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', assignee_id INTEGER, due_date TEXT, priority TEXT NOT NULL DEFAULT 'Normal', parent_id INTEGER, created_at TEXT NOT NULL)")
            c.execute("INSERT INTO tasks(id,title,created_at) VALUES(1,'Antigua','2020')")
        app.DB = legacy
        app.init()
        with app.db() as c:
            self.assertEqual(tuple(c.execute('SELECT title_version,description_version FROM tasks').fetchone()), (0,0))
            c.execute("UPDATE tasks SET title='Editada' WHERE id=1")
        app.init()
        with app.db() as c:
            self.assertEqual(c.execute('SELECT title_version FROM tasks').fetchone()[0], 1)
