import os
import io
import tempfile
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app
import slack_notify as slack


class SlackNotifyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        self.old_url = app.SETTINGS.get('slack_base_url')
        app.DB = os.path.join(self.temp.name, 'test.db')
        app.SETTINGS['slack_base_url'] = 'https://example.com'
        app.init()
        self.c = app.db()
        slack.init(self.c)
        slack.init(self.c)
        self.c.execute("INSERT INTO users(id,name,username,password,salt) VALUES(1,'Actor','actor','',''),(2,'Ana','ana','',''),(3,'Bea','bea','','')")
        self.c.execute("INSERT INTO projects(id,name) VALUES(1,'Proyecto')")
        self.c.execute("INSERT INTO members VALUES(1,1,'editor'),(1,2,'viewer'),(1,3,'viewer')")
        self.c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(1,1,'Columna',0)")
        status = self.c.execute('SELECT id,type_id FROM task_statuses WHERE is_done=0 LIMIT 1').fetchone()
        self.c.execute("INSERT INTO tasks(id,title,assignee_id,created_at,status_id,type_id,public_id) VALUES(1,'Tarea',2,'2026-01-01',?,?, 'task-uuid')", (status['id'], status['type_id']))
        self.c.execute('INSERT INTO placements VALUES(1,1,1,0)')
        self.c.execute("INSERT INTO slack_installations VALUES(1,'T','Equipo','token')")
        self.c.execute("INSERT INTO slack_accounts VALUES(1,1,'U1'),(1,2,'U2'),(1,3,'U3')")
        self.c.execute('''INSERT INTO slack_preferences(installation_id,user_id,project_id,assignment,comment,completion,digest)
            VALUES(1,1,1,1,1,1,1),(1,2,1,1,1,1,1),(1,3,1,1,1,1,1)''')
        self.c.commit()

    def tearDown(self):
        self.c.close()
        app.DB = self.old_db
        if self.old_url is None:
            app.SETTINGS.pop('slack_base_url', None)
        else:
            app.SETTINGS['slack_base_url'] = self.old_url
        self.temp.cleanup()

    def test_rollback_and_assignment_permissions_rechecked(self):
        self.c.execute('BEGIN')
        self.assertEqual(slack.enqueue(self.c, 'assignment', 1, 1), 1)
        self.c.rollback()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM slack_queue').fetchone()[0], 0)
        with self.c:
            slack.enqueue(self.c, 'assignment', 1, 1)
            self.c.execute('DELETE FROM members WHERE user_id=2')
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        self.assertEqual(self.c.execute('SELECT state FROM slack_queue').fetchone()[0], 'cancelled')

    def test_comments_group_and_connection_rechecked(self):
        with self.c:
            self.assertEqual(slack.enqueue(self.c, 'comment', 1, 1), 1)
            slack.enqueue(self.c, 'comment', 1, 1)
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        with self.c:
            self.c.execute("UPDATE slack_queue SET available_at='2000-01-01'")
        with patch.object(slack, '_send', return_value=('sent', 0, None)) as send:
            slack.run_once()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[1], 'U2')
            self.assertIn('(2)', send.call_args.args[2])
        with self.c:
            slack.enqueue(self.c, 'comment', 1, 1)
            self.c.execute("UPDATE slack_queue SET available_at='2000-01-01' WHERE state='pending'")
            self.c.execute('UPDATE tasks SET assignee_id=3 WHERE id=1')
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        self.assertEqual(self.c.execute('SELECT state FROM slack_queue ORDER BY id DESC').fetchone()[0], 'cancelled')

    def test_completion_requires_admin_with_preference_and_rechecks(self):
        with self.c:
            self.c.execute("UPDATE members SET role='admin' WHERE user_id=2")
            self.c.execute("INSERT INTO projects(id,name) VALUES(2,'Otro')")
            self.c.execute("INSERT INTO members VALUES(2,3,'admin')")
            self.c.execute("INSERT INTO slack_preferences(installation_id,user_id,project_id,completion) VALUES(1,3,2,1)")
            self.c.execute('UPDATE tasks SET status_id=(SELECT id FROM task_statuses WHERE is_done=1 LIMIT 1) WHERE id=1')
            self.assertEqual(slack.enqueue(self.c, 'completion', 1, 1), 1)
            self.c.execute("UPDATE members SET role='viewer' WHERE user_id=2")
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        with self.c:
            self.c.execute("UPDATE members SET role='admin' WHERE user_id=2")
            self.c.execute('UPDATE slack_preferences SET completion=0 WHERE user_id=2')
            self.assertEqual(slack.enqueue(self.c, 'completion', 1, 1), 0)
            self.c.execute('DELETE FROM placements')
            self.assertEqual(slack.enqueue(self.c, 'completion', 1, 1), 0)

    def test_test_event_targets_only_actor_and_rechecks_connection(self):
        with self.c:
            self.assertEqual(slack.enqueue(self.c, 'test', None, 2), 1)
            self.assertEqual(slack.enqueue(self.c, 'test', None, 99), 0)
        with patch.object(slack, '_send', return_value=('sent', 0, None)) as send:
            slack.run_once()
            self.assertEqual(send.call_args.args, ('token', 'U2', 'Mensaje de prueba de Slack.'))
        with self.c:
            slack.enqueue(self.c, 'test', None, 2)
            self.c.execute("UPDATE slack_accounts SET slack_user_id='NEW' WHERE user_id=2")
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        with self.assertRaises(ValueError):
            slack.enqueue(self.c, 'test', 1, 2)

    def test_orphan_and_preference_changes(self):
        with self.c:
            self.c.execute('DELETE FROM placements')
            self.c.execute('INSERT INTO slack_preferences(installation_id,user_id,project_id,assignment) VALUES(1,2,NULL,1)')
            self.assertEqual(slack.enqueue(self.c, 'assignment', 1, 1), 1)
            self.c.execute('UPDATE slack_preferences SET assignment=0 WHERE project_id IS NULL')
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()

    def test_rate_limit_and_uncertain_are_not_duplicated(self):
        with self.c:
            slack.enqueue(self.c, 'comment', 1, 1)
            self.c.execute("UPDATE slack_queue SET available_at='2000-01-01'")
        with patch.object(slack, '_send', return_value=('retry', 60, 'rate_limited')) as send:
            slack.run_once()
            self.assertEqual(send.call_count, 1)
            slack.run_once()
            self.assertEqual(send.call_count, 1)
        with self.c:
            self.c.execute("UPDATE slack_queue SET available_at='2000-01-01'")
        with patch.object(slack, '_send', return_value=('uncertain', 0, 'timeout')) as send:
            slack.run_once()
            slack.run_once()
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM slack_queue WHERE state='uncertain'").fetchone()[0], 1)

    def test_slack_responses_and_ambiguous_failures(self):
        opener = unittest.mock.MagicMock()
        with patch.object(slack, 'build_opener', return_value=opener):
            for payload in (b'[]', b'null', b'"text"', b'not json'):
                opener.open.return_value.__enter__.return_value = io.BytesIO(payload)
                self.assertEqual(slack._send('token', 'U', 'text')[0], 'uncertain')
            opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"ok":false,"error":"service_unavailable"}')
            self.assertEqual(slack._send('token', 'U', 'text')[0], 'uncertain')
            for code, expected in ((408, 'uncertain'), (500, 'uncertain'), (400, 'failed'), (429, 'retry')):
                opener.open.side_effect = HTTPError('https://slack.com', code, '', {'Retry-After': '120'}, None)
                result = slack._send('token', 'U', 'text')
                self.assertEqual(result[0], expected)
                if code == 429:
                    self.assertEqual(result[1], 120)
            opener.open.side_effect = URLError('timeout')
            self.assertEqual(slack._send('token', 'U', 'text')[0], 'uncertain')
        self.assertIsNone(slack._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_rate_limit_blocks_new_messages_and_retry_limit(self):
        with self.c:
            slack.enqueue(self.c, 'test', None, 2)
            self.c.execute('UPDATE slack_queue SET attempts=7')
        with patch.object(slack, '_send', return_value=('retry', 60, 'rate_limited')) as send:
            slack.run_once()
            with self.c:
                slack.enqueue(self.c, 'test', None, 3)
            slack.run_once()
            self.assertEqual(send.call_count, 1)
        self.assertEqual([r[0] for r in self.c.execute('SELECT state FROM slack_queue ORDER BY id')], ['failed', 'pending'])

    def test_bad_configuration_preserves_pending_and_safe_links(self):
        with self.c:
            slack.enqueue(self.c, 'assignment', 1, 1)
        for url in (None, '', 'http://example.com', 'https://user@example.com', 'https://example.com:bad', 'https://example.com/\tfoo', 'https://example.com/a|b'):
            app.SETTINGS['slack_base_url'] = url
            with patch.object(slack, '_send') as send:
                slack.run_once()
                send.assert_not_called()
            self.assertEqual(self.c.execute('SELECT state FROM slack_queue').fetchone()[0], 'pending')
        app.SETTINGS['slack_base_url'] = 'https://example.com'
        with self.c:
            self.c.execute("UPDATE tasks SET public_id='x|https://evil.test' WHERE id=1")
        with patch.object(slack, '_send', return_value=('sent', 0, None)) as send:
            slack.run_once()
            self.assertIn('/t/x%7Chttps%3A%2F%2Fevil.test|', send.call_args.args[2])

    def test_interrupted_send_is_uncertain(self):
        with self.c:
            slack.enqueue(self.c, 'test', None, 2)
            self.c.execute("UPDATE slack_queue SET state='sending'")
        with patch.object(slack, '_send') as send:
            slack.run_once()
            send.assert_not_called()
        self.assertEqual(tuple(self.c.execute('SELECT state,error FROM slack_queue').fetchone()), ('uncertain', 'worker_interrupted'))

    def test_digest_once_and_nonempty(self):
        with self.c:
            self.c.execute("INSERT INTO slack_digest_settings(installation_id,user_id,enabled,hour,weekdays) VALUES(1,2,1,0,'[0,1,2,3,4,5,6]'),(1,3,1,0,'[0,1,2,3,4,5,6]')")
        with patch.object(slack, '_send', return_value=('sent', 0, None)) as send:
            slack.run_once()
            slack.run_once()
            self.assertEqual(send.call_count, 1)
            self.assertIn('/t/task-uuid', send.call_args.args[2])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM slack_queue WHERE kind='digest'").fetchone()[0], 1)

    def test_digest_priority_breaks_equal_date_ties(self):
        with self.c:
            self.c.execute("UPDATE tasks SET priority='Baja' WHERE id=1")
            self.c.execute("""INSERT INTO tasks(id,title,assignee_id,created_at,status_id,type_id,public_id,priority)
                SELECT 2,'Alta',assignee_id,created_at,status_id,type_id,'uuid-2','Alta' FROM tasks WHERE id=1""")
            self.c.execute('INSERT INTO placements VALUES(2,1,1,0)')
        user = self.c.execute('SELECT * FROM users WHERE id=2').fetchone()
        self.assertEqual([t['id'] for t in slack._digest_tasks(self.c, user, '2026-01-01')], [2, 1])

    def test_digest_visible_projects_order_count_zone_and_weekdays(self):
        fixed = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)  # domingo en Nueva York
        with self.c:
            self.c.execute("UPDATE projects SET name='Visible <seguro>' WHERE id=1")
            self.c.execute("INSERT INTO projects(id,name) VALUES(2,'Secreto')")
            self.c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(2,2,'Oculta',0)")
            self.c.execute('INSERT INTO placements VALUES(1,2,2,0)')
            self.c.execute('UPDATE slack_preferences SET digest=0')
            self.c.execute("UPDATE tasks SET title='Normal',due_date=NULL WHERE id=1")
            for ident, title, due, priority in [(2, 'Alta', None, 'Alta'), (3, 'Hoy', '2026-01-04', 'Normal'),
                                                 (4, 'Vencida', '2026-01-03', 'Normal'), (5, 'Otra', None, 'Normal'),
                                                 (6, 'Otra más', None, 'Normal')]:
                self.c.execute('''INSERT INTO tasks(id,title,assignee_id,created_at,status_id,type_id,public_id,due_date,priority)
                    SELECT ?,?,2,created_at,status_id,type_id,?, ?,? FROM tasks WHERE id=1''',
                    (ident, title, 'uuid-'+str(ident), due, priority))
                self.c.execute('INSERT INTO placements VALUES(?,1,1,0)', (ident,))
            self.c.execute("INSERT INTO slack_digest_settings(installation_id,user_id,enabled,hour,weekdays) VALUES(1,2,1,0,'[0]')")
        with patch.object(app, 'STATS_ZONE', ZoneInfo('America/New_York')), patch.object(slack, '_now', return_value=fixed), patch.object(slack, '_send', return_value=('sent', 0, None)) as send:
            slack.run_once()
            send.assert_not_called()
            with self.c:
                self.c.execute("UPDATE slack_digest_settings SET weekdays='[6]' WHERE user_id=2")
            slack.run_once()
            text = send.call_args.args[2]
            self.assertLess(text.index('Vencida'), text.index('Hoy'))
            self.assertLess(text.index('Hoy'), text.index('Alta'))
            self.assertIn('Visible &lt;seguro&gt;', text)
            self.assertNotIn('Secreto', text)
            self.assertIn('America/New_York', text)
            self.assertIn('Restantes: 1', text)
            self.assertIn('https://example.com/my-tasks', text)

    def test_legacy_weekday_migration_is_idempotent(self):
        with self.c:
            self.c.execute('DROP TABLE slack_digest_settings')
            self.c.execute('''CREATE TABLE slack_digest_settings(installation_id INTEGER, user_id INTEGER,
                enabled INTEGER, weekday INTEGER, hour INTEGER, minute INTEGER, PRIMARY KEY(installation_id,user_id))''')
            self.c.execute('INSERT INTO slack_digest_settings VALUES(1,2,1,NULL,9,0),(1,3,1,6,10,0)')
            slack.init(self.c)
            slack.init(self.c)
        self.assertEqual([r[0] for r in self.c.execute('SELECT weekdays FROM slack_digest_settings ORDER BY user_id')],
                         ['[0,1,2,3,4,5,6]', '[6]'])


if __name__ == '__main__':
    unittest.main()
