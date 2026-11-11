import json
import sqlite3
import subprocess
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from deploy.migrate_autoincrement import migrate


class AutoincrementMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'test.db')
        with patch.object(app, 'DB', self.path):
            app.init()

    def legacy(self):
        # Construir una base antigua con el mismo esquema, índices y referencias reales.
        with sqlite3.connect(self.path) as source:
            dump = list(source.iterdump())
        legacy = str(Path(self.tmp.name) / 'legacy.db')
        with sqlite3.connect(legacy) as c:
            for statement in dump:
                if 'sqlite_sequence' in statement: continue
                c.execute(statement.replace(' AUTOINCREMENT', ''))
        return legacy

    def test_existing_database_preserves_data_and_reserves_historical_ids(self):
        path = self.legacy()
        with sqlite3.connect(path) as c:
            c.execute("INSERT INTO projects(id,name) VALUES(1,'Tablero')")
            c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(1,1,'Entrada',0)")
            c.execute("INSERT INTO automation_rules(project_id,name,enabled,\"trigger\",conditions,actions,position) VALUES(1,'Regla',1,?,?,?,0)",
                      (json.dumps({'type':'status','to':'900'}), json.dumps([{'field':'type','op':'eq','value':'800'}]), json.dumps([{'type':'column','value':'700'},{'type':'assignee_id','value':'600'}])))
            c.execute("INSERT INTO metric_events(project_id,task_id,event,occurred_at,task_created_at,type_id,type_name) VALUES(1,500,'created','2024','2024',1000,'Antiguo')")
        with sqlite3.connect(path) as c:
            migrate(c)
            migrate(c)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            for table in ('users','projects','columns','tasks','history','comments','task_types','task_statuses','metric_events','automation_rules','automation_runs'):
                self.assertIn('AUTOINCREMENT', c.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0])
            self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='tasks_public_id_unique'").fetchone())
            self.assertEqual(c.execute('SELECT name FROM projects WHERE id=1').fetchone()[0], 'Tablero')
            self.assertGreater(c.execute("INSERT INTO columns(project_id,name,position) VALUES(1,'Nueva',1)").lastrowid, 700)
            self.assertGreater(c.execute("INSERT INTO users(name,username,password,salt) VALUES('U','u','','')").lastrowid, 600)
            self.assertGreater(c.execute("INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(1,'Nuevo','#000000',0,1)").lastrowid, 900)
        with patch.object(app, 'DB', path):
            with app.db() as c:
                tid = app.create_type(c, 1, 'Nuevo')
                self.assertGreater(tid, 1000)
                c.execute('DELETE FROM task_types WHERE id=?', (tid,))
                self.assertGreater(app.create_type(c, 1, 'Otro'), tid)

    def test_rollback_on_invalid_rules(self):
        path = self.legacy()
        with sqlite3.connect(path) as c:
            c.execute("INSERT INTO projects(id,name) VALUES(1,'Tablero')")
            c.execute("INSERT INTO automation_rules(project_id,name,enabled,\"trigger\",conditions,actions,position) VALUES(1,'R',1,'mal','[]','[]',0)")
        with sqlite3.connect(path) as c:
            with self.assertRaises(ValueError): migrate(c)
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
            self.assertNotIn('AUTOINCREMENT', sql)
            self.assertEqual(c.execute('SELECT name FROM projects').fetchone()[0], 'Tablero')

    def test_rollback_after_schema_changes(self):
        path = self.legacy()
        with sqlite3.connect(path) as c:
            c.execute('DROP TABLE comments')
            c.execute('CREATE TABLE comments(id TEXT PRIMARY KEY)')
            with self.assertRaisesRegex(ValueError, 'comments'): migrate(c)
            self.assertNotIn('AUTOINCREMENT', c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '_autoincrement_%'").fetchone()[0], 0)

    def test_script_uses_production_configuration_and_does_not_create_missing_db(self):
        config = Path(self.tmp.name) / 'config.ini'
        config.write_text('[app]\ndatabase = '+self.path+'\n', encoding='utf-8')
        script = str(Path(app.__file__).parent / 'deploy' / 'migrate_autoincrement.py')
        env = {**os.environ, 'TASKS_DB': str(Path(self.tmp.name) / 'wrong.db')}
        result = subprocess.run([sys.executable, script, '--config', str(config)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.path, result.stdout)
        self.assertFalse(Path(env['TASKS_DB']).exists())
        config.write_text('[app]\ndatabase = '+str(Path(self.tmp.name) / 'missing.db')+'\n', encoding='utf-8')
        result = subprocess.run([sys.executable, script, '--config', str(config)], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((Path(self.tmp.name) / 'missing.db').exists())

    def test_new_database_does_not_reuse_deleted_ids(self):
        with patch.object(app, 'DB', self.path):
            with app.db() as c:
                first = c.execute("INSERT INTO projects(name) VALUES('Primero')").lastrowid
                c.execute('DELETE FROM projects WHERE id=?', (first,))
                self.assertGreater(c.execute("INSERT INTO projects(name) VALUES('Segundo')").lastrowid, first)


if __name__ == '__main__':
    unittest.main()
