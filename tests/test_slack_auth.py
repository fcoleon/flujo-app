import sqlite3
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import slack_auth


class SlackAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.app = Flask(__name__)
        self.app.config.update(SLACK_DB=self.db, SLACK_SETTINGS={
            'client_id': 'test-client', 'client_secret': 'test-secret', 'base_url': 'https://app.example'})
        self.app.register_blueprint(slack_auth.blueprint)
        with self.db() as c:
            c.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,username TEXT,admin INTEGER,blocked INTEGER,must_change_password INTEGER);
                CREATE TABLE sessions(token TEXT PRIMARY KEY,user_id INTEGER);
                CREATE TABLE slack_installations(id INTEGER PRIMARY KEY,team_id TEXT,team_name TEXT,bot_token TEXT);
                CREATE TABLE slack_accounts(installation_id INTEGER REFERENCES slack_installations(id) ON DELETE CASCADE,user_id INTEGER,slack_user_id TEXT,PRIMARY KEY(installation_id,user_id),UNIQUE(installation_id,slack_user_id));
                CREATE TABLE projects(id INTEGER PRIMARY KEY);
                CREATE TABLE members(project_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(project_id,user_id));
                CREATE TABLE slack_preferences(installation_id INTEGER,user_id INTEGER,project_id INTEGER,assignment INTEGER,comment INTEGER,completion INTEGER,FOREIGN KEY(installation_id,user_id) REFERENCES slack_accounts(installation_id,user_id) ON DELETE CASCADE);
                CREATE TABLE slack_digest_settings(installation_id INTEGER,user_id INTEGER,enabled INTEGER,hour INTEGER,weekdays TEXT,PRIMARY KEY(installation_id,user_id),FOREIGN KEY(installation_id,user_id) REFERENCES slack_accounts(installation_id,user_id) ON DELETE CASCADE);
                CREATE TABLE slack_queue(id INTEGER PRIMARY KEY,installation_id INTEGER,user_id INTEGER,team_id TEXT,slack_user_id TEXT,kind TEXT,task_id INTEGER,available_at TEXT,created_at TEXT,updated_at TEXT,state TEXT DEFAULT 'pending');
                INSERT INTO projects VALUES(1),(2);
                INSERT INTO users VALUES(1,'Admin','admin',1,0,0);
                INSERT INTO sessions VALUES('session-secret',1);''')
            slack_auth.init(c)
            slack_auth.init(c)
        self.client = self.app.test_client()
        self.client.set_cookie('session', 'session-secret')

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c

    def start(self, kind):
        response = self.client.post('/api/slack/' + kind + '/start', json={})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Secure; HttpOnly; Path=/slack/callback; SameSite=Lax', response.headers['Set-Cookie'])
        return parse_qs(urlsplit(response.json['url']).query)

    def callback(self, kind, state):
        client = self.app.test_client()
        name = 'slack_oauth_' + slack_auth.digest(state)
        cookie = self.client.get_cookie(name, path='/slack/callback')
        if cookie:
            client.set_cookie(name, cookie.value, path='/slack/callback', secure=True)
        return client.get('/slack/callback/' + kind, query_string={'state': state, 'code': 'code'}, base_url='https://localhost')

    def install(self, team='T1'):
        params = self.start('install')
        self.assertEqual(params['scope'], ['chat:write'])
        def api(method, *args, **kwargs):
            return {'ok': True, 'revoked': True} if method == 'auth.revoke' else {'ok': True, 'team': {'id': team, 'name': 'Team'}, 'access_token': 'bot-secret', 'token_type': 'bot'}
        with patch('slack_auth.slack_api', side_effect=api):
            return self.callback('install', params['state'][0])

    def test_install_link_and_team_change(self):
        response = self.install()
        self.assertEqual((response.status_code, response.headers['Location']), (302, '/'))
        params = self.start('link')
        self.assertEqual(params['scope'], ['openid'])
        self.assertEqual(params['response_type'], ['code'])
        claims = {'sub': 'U1', 'https://slack.com/team_id': 'T1', 'https://slack.com/user_id': 'U1'}
        with patch('slack_auth.verify_identity', return_value=claims), patch('slack_auth.slack_api', side_effect=[{'access_token': 'identity-token', 'id_token': 'jwt'}, dict(claims)]) as api:
            self.assertEqual(self.callback('link', params['state'][0]).status_code, 302)
            self.assertEqual(api.call_args_list[1].kwargs['token'], 'identity-token')
        status = self.client.get('/api/slack/status').json
        self.assertEqual(status['account'], {'slack_user_id': 'U1'})
        self.assertNotIn('bot-secret', str(status))
        self.assertEqual(self.install('T2').status_code, 302)
        self.assertIsNone(self.client.get('/api/slack/status').json['account'])

    def test_single_use_session_and_permissions(self):
        state = self.start('install')['state'][0]
        with self.db() as c:
            c.execute('DELETE FROM sessions')
        self.assertEqual(self.callback('install', state).status_code, 400)
        with self.db() as c:
            c.execute("INSERT INTO sessions VALUES('session-secret',1)")
        self.assertEqual(self.callback('install', state).status_code, 400)
        state = self.start('install')['state'][0]
        with self.db() as c:
            c.execute('UPDATE users SET admin=0')
        self.assertEqual(self.callback('install', state).status_code, 400)
        self.assertEqual(self.client.post('/api/slack/disconnect', json={}).status_code, 403)

    def test_wrong_team_expiry_and_provider_failure(self):
        self.assertEqual(self.install().status_code, 302)
        state = self.start('link')['state'][0]
        claims = {'sub': 'U1', 'https://slack.com/team_id': 'T2', 'https://slack.com/user_id': 'U1'}
        with patch('slack_auth.verify_identity', return_value=claims), patch('slack_auth.slack_api', side_effect=[{'access_token': 'x', 'id_token': 'jwt'}, dict(claims)]):
            self.assertEqual(self.callback('link', state).status_code, 409)
        state = self.start('link')['state'][0]
        with self.db() as c:
            c.execute('UPDATE slack_oauth_states SET expires=0')
        self.assertEqual(self.callback('link', state).status_code, 400)
        state = self.start('install')['state'][0]
        with patch('slack_auth.slack_api', side_effect=ValueError('test-secret')):
            response = self.callback('install', state)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('test-secret', response.get_data(as_text=True))
        self.assertEqual(self.callback('install', state).status_code, 400)

    def test_preferences_permissions_and_queue(self):
        self.assertEqual(self.install().status_code, 302)
        with self.db() as c:
            c.execute("INSERT INTO slack_accounts VALUES(1,1,'U1')")
            c.execute("INSERT INTO members VALUES(1,1,'viewer')")
            c.execute('UPDATE users SET admin=0 WHERE id=1')
        summary = {'enabled': True, 'hour': 13, 'minute': 30, 'weekdays': [4, 0]}
        prefs = [{'project_id': 1, 'assignment': True, 'comment': False, 'completion': False},
                 {'project_id': None, 'assignment': False, 'comment': True, 'completion': False}]
        payload = {'preferences': prefs, 'summary': summary}
        self.assertEqual(self.client.post('/api/slack/preferences', json=payload).status_code, 200)
        status = self.client.get('/api/slack/status').json
        self.assertTrue(status['configured'])
        self.assertEqual(status['summary'], {'enabled': True, 'hour': 13, 'minute': 30, 'weekdays': [0, 4]})
        self.assertEqual(status['preferences'], prefs)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT weekdays FROM slack_digest_settings').fetchone()[0], '[0, 4]')
        prefs[0]['comment'] = True
        prefs[1]['completion'] = True
        self.assertEqual(self.client.post('/api/slack/preferences', json=payload).status_code, 403)
        self.assertFalse(self.client.get('/api/slack/status').json['preferences'][0]['comment'])
        prefs[1]['completion'] = False
        prefs[0]['project_id'] = 2
        self.assertEqual(self.client.post('/api/slack/preferences', json=payload).status_code, 403)
        prefs[0]['project_id'] = 1
        with self.db() as c:
            c.execute("UPDATE members SET role='admin' WHERE project_id=1 AND user_id=1")
        prefs[0]['completion'] = True
        self.assertEqual(self.client.post('/api/slack/preferences', json=payload).status_code, 200)
        self.assertTrue(self.client.get('/api/slack/status').json['preferences'][0]['completion'])
        summary['weekdays'] = [True]
        self.assertEqual(self.client.post('/api/slack/preferences', json=payload).status_code, 400)
        with patch('slack_auth.slack_api', side_effect=AssertionError('unexpected network')):
            self.assertEqual(self.client.post('/api/slack/test', json={}).status_code, 200)
        with self.db() as c:
            row = c.execute('SELECT team_id,slack_user_id,kind,task_id FROM slack_queue').fetchone()
            self.assertEqual(tuple(row), ('T1', 'U1', 'test', None))

    def test_status_unconfigured(self):
        self.app.config.pop('SLACK_SETTINGS')
        response = self.client.get('/api/slack/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'configured': False, 'installation': None, 'account': None,
                                       'preferences': [], 'summary': {'enabled': False, 'hour': 9, 'minute': 0, 'weekdays': list(range(7))}})

    def test_post_rejects_csrf_form_content_types(self):
        paths = ('install/start', 'link/start', 'preferences', 'test', 'unlink', 'disconnect')
        for path in paths:
            for content_type in ('application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain'):
                with self.subTest(path=path, content_type=content_type):
                    response = self.client.post('/api/slack/' + path, data='{}', content_type=content_type,
                                                headers={'Origin': 'https://attacker.example'})
                    self.assertEqual(response.status_code, 415)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_oauth_states').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_queue').fetchone()[0], 0)
        self.assertEqual(self.client.post('/api/slack/install/start', json={}).status_code, 200)

    def test_base_url_rejects_paths(self):
        for url in ('https://app.example/sub', 'https://app.example//', 'https://app.example/%2F'):
            self.app.config['SLACK_SETTINGS']['base_url'] = url
            self.assertFalse(self.client.get('/api/slack/status').json['configured'])
            self.assertEqual(self.client.post('/api/slack/install/start', json={}).status_code, 500)
        self.app.config['SLACK_SETTINGS']['base_url'] = 'https://app.example/'
        self.assertTrue(self.client.get('/api/slack/status').json['configured'])

    def test_worker_retry_preserves_live_token(self):
        self.assertEqual(self.install().status_code, 302)
        with self.db() as c:
            c.execute("INSERT INTO slack_revocations(token,user_id) VALUES('bot-secret',1),('old-token',1)")
        with self.app.app_context(), patch('slack_auth.slack_api', return_value={'ok': True, 'revoked': True}) as api:
            self.assertFalse(slack_auth.retry_revocations())
            api.assert_called_once_with('auth.revoke', {}, token='old-token')
        with self.db() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT token FROM slack_revocations')], ['bot-secret'])
        self.assertEqual(self.client.post('/api/slack/install/start', json={}).status_code, 409)
        with self.db() as c:
            c.execute('DELETE FROM slack_installations')
        with self.app.app_context(), patch('slack_auth.slack_api', side_effect=OSError('offline')):
            self.assertFalse(slack_auth.retry_revocations())
        self.assertEqual(self.client.post('/api/slack/install/start', json={}).status_code, 409)
        with self.app.app_context(), patch('slack_auth.slack_api', return_value={'ok': True, 'revoked': True}) as api:
            self.assertTrue(slack_auth.retry_revocations())
            api.assert_called_once_with('auth.revoke', {}, token='bot-secret')
        self.assertEqual(self.client.post('/api/slack/install/start', json={}).status_code, 200)

    def test_browser_binding_and_latest_install(self):
        old = self.start('install')['state'][0]
        new = self.start('install')['state'][0]
        self.assertEqual(self.callback('install', old).status_code, 400)
        stranger = self.app.test_client().get('/slack/callback/install', query_string={'state': new, 'code': 'code'}, base_url='https://localhost')
        self.assertEqual(stranger.status_code, 400)
        with patch('slack_auth.slack_api', return_value={'team': {'id': 'T1', 'name': 'Team'}, 'access_token': 'token', 'token_type': 'bot'}):
            self.assertEqual(self.callback('install', new).status_code, 302)
        self.assertEqual(self.callback('install', new).status_code, 400)

    def test_parallel_links_and_identity_mismatch(self):
        self.assertEqual(self.install().status_code, 302)
        first = self.start('link')
        second = self.start('link')
        self.assertNotEqual(first['nonce'], second['nonce'])
        claims = {'sub': 'U1', 'https://slack.com/team_id': 'T1', 'https://slack.com/user_id': 'U1'}
        with patch('slack_auth.verify_identity', return_value=claims), patch('slack_auth.slack_api', side_effect=[{'access_token': 'x', 'id_token': 'jwt'}, dict(claims, sub='U2')]):
            self.assertEqual(self.callback('link', first['state'][0]).status_code, 502)
        with patch('slack_auth.verify_identity', return_value=claims), patch('slack_auth.slack_api', side_effect=[{'access_token': 'x', 'id_token': 'jwt'}, dict(claims)]):
            self.assertEqual(self.callback('link', second['state'][0]).status_code, 302)

    def test_revocation_retry_and_queue_cancellation(self):
        self.assertEqual(self.install().status_code, 302)
        with self.db() as c:
            c.execute("INSERT INTO slack_accounts VALUES(1,1,'U1')")
            c.execute("INSERT INTO slack_identity_tokens VALUES(1,1,'identity-secret')")
        self.assertEqual(self.client.post('/api/slack/test', json={}).status_code, 200)
        with patch('slack_auth.slack_api', side_effect=OSError('secret')):
            response = self.client.post('/api/slack/unlink', json={})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.get_data(as_text=True))
        with self.db() as c:
            self.assertEqual(c.execute('SELECT state FROM slack_queue').fetchone()[0], 'cancelled')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_revocations').fetchone()[0], 1)
        self.assertEqual(self.client.post('/api/slack/link/start', json={}).status_code, 409)
        with patch('slack_auth.slack_api', return_value={'ok': True, 'revoked': True}) as api:
            self.assertEqual(self.client.post('/api/slack/unlink', json={}).status_code, 200)
            api.assert_called_once_with('auth.revoke', {}, token='identity-secret')
            self.assertEqual(self.client.post('/api/slack/disconnect', json={}).status_code, 200)

    def test_signed_oidc_claims(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        jwk.update(kid='test-key', alg='RS256', use='sig')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args):
                import json
                return json.dumps({'keys': [jwk]}).encode()
        claims = {'iss': 'https://slack.com', 'sub': 'U1', 'aud': 'test-client', 'iat': int(time.time()),
                  'exp': int(time.time()) + 300, 'nonce': 'nonce'}
        with patch('slack_auth.build_opener') as opener:
            opener.return_value.open.return_value = Response()
            signed = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})
            self.assertEqual(slack_auth.verify_identity(signed, 'access', 'test-client', slack_auth.digest('nonce'))['sub'], 'U1')
            for field, bad in [('nonce', 'wrong'), ('aud', 'other'), ('iss', 'https://evil.example'), ('exp', 1)]:
                invalid = jwt.encode(dict(claims, **{field: bad}), key, algorithm='RS256', headers={'kid': 'test-key'})
                with self.assertRaises((ValueError, jwt.InvalidTokenError)):
                    slack_auth.verify_identity(invalid, 'access', 'test-client', slack_auth.digest('nonce'))


if __name__ == '__main__':
    unittest.main()
