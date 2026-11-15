import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app
from auth import SESSION_SECONDS


class SessionExpiryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = os.path.join(self.temp.name, 'test.db')
        app.init()
        self.client = app.app.test_client()
        self.clock = patch('auth.time.time', return_value=2000000000)
        self.clock.start()
        response = self.client.post('/api/setup', json={'name':'Prueba','username':'prueba','password':'clave123'})
        self.assertEqual(response.status_code, 200)
        self.cookie = response.headers['Set-Cookie']
        self.token = self.client.get_cookie('session').value

    def tearDown(self):
        self.clock.stop()
        app.DB = self.old_db
        self.temp.cleanup()

    def test_persistent_cookie_and_expiration(self):
        self.assertIn(f'Max-Age={SESSION_SECONDS}', self.cookie)
        self.assertIn('HttpOnly', self.cookie)
        self.assertIn('SameSite=Strict', self.cookie)
        self.assertIn('Expires=', self.cookie)
        self.assertIsNotNone(self.client.get('/api/session').get_json()['user'])
        with patch('auth.time.time', return_value=2000000000+SESSION_SECONDS):
            self.assertIsNone(self.client.get('/api/session').get_json()['user'])
            self.assertEqual(self.client.get('/api/data').status_code, 401)

    def test_login_cleans_expired_sessions_and_logout_revokes(self):
        with app.db() as c:
            c.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('expired',1,1999999999)")
        response = self.client.post('/api/login', json={'username':'prueba','password':'clave123'})
        self.assertEqual(response.status_code, 200)
        token = self.client.get_cookie('session').value
        with app.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sessions WHERE token='expired'").fetchone())
        self.assertEqual(self.client.post('/api/logout', json={}).status_code, 200)
        with app.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM sessions WHERE token=?',(token,)).fetchone())
            self.assertIsNotNone(c.execute('SELECT 1 FROM sessions WHERE token=?',(self.token,)).fetchone())

    def test_existing_session_migration_is_idempotent(self):
        with app.db() as c:
            c.execute('UPDATE sessions SET expires_at=NULL WHERE token=?',(self.token,))
        app.init()
        with app.db() as c:
            expiry = c.execute('SELECT expires_at FROM sessions WHERE token=?',(self.token,)).fetchone()[0]
        self.assertEqual(expiry, 2000000000+SESSION_SECONDS)
        with patch('auth.time.time', return_value=2000001000):
            app.init()
        with app.db() as c:
            self.assertEqual(c.execute('SELECT expires_at FROM sessions WHERE token=?',(self.token,)).fetchone()[0], expiry)

    def test_migrates_legacy_schema(self):
        app.DB = os.path.join(self.temp.name, 'legacy.db')
        with sqlite3.connect(app.DB) as c:
            c.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,salt TEXT NOT NULL,admin INTEGER NOT NULL DEFAULT 0);
                               CREATE TABLE sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE);
                               INSERT INTO users VALUES(1,'Legacy','legacy','','',1);
                               INSERT INTO sessions VALUES('legacy-token',1);""")
        app.init()
        app.init()
        with app.db() as c:
            self.assertEqual(c.execute("SELECT expires_at FROM sessions WHERE token='legacy-token'").fetchone()[0],2000000000+SESSION_SECONDS)


if __name__ == '__main__':
    unittest.main()
