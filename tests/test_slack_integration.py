import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import slack_notify


class SlackIntegrationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, app, 'DB', app.DB)
        app.DB = os.path.join(tmp.name, 'integration.db')
        self.url = patch.dict(app.SETTINGS, {'slack_base_url': 'https://example.test'})
        self.url.start()
        self.addCleanup(self.url.stop)
        app.init()
        with closing(app.db()) as c, c:
            for uid in range(1, 5):
                c.execute('INSERT INTO users(id,name,username,password,salt) VALUES(?,?,?,?,?)',
                          (uid, str(uid), str(uid), '', ''))
                c.execute('INSERT INTO sessions VALUES(?,?)', (str(uid), uid))
            c.execute("INSERT INTO projects(id,name) VALUES(1,'Proyecto'),(2,'Otro')")
            c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(1,1,'Entrada',0),(2,2,'Entrada',0)")
            c.execute("INSERT INTO members VALUES(1,1,'admin'),(1,2,'editor'),(1,3,'admin'),(2,4,'admin')")
            self.typ = app.ensure_base_type(c, 1)
            self.done = c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1', (self.typ,)).fetchone()[0]
            other = app.create_type(c, 1, 'Otro tipo')
            self.incompatible = c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1', (other,)).fetchone()[0]
            c.execute("INSERT INTO slack_installations VALUES(1,'T1','Equipo','token-secreto')")
            c.executemany('INSERT INTO slack_accounts VALUES(1,?,?)', [(i, 'U'+str(i)) for i in range(1, 5)])
        for uid, completion in ((1, True), (2, False), (3, True)):
            self.post('slack/preferences', {'preferences': [{'project_id': 1, 'assignment': True,
                      'comment': True, 'completion': completion}],
                      'summary': {'enabled': False, 'hour': 9, 'weekdays': [0]}}, uid)

    def request(self, method, path, data=None, user=1):
        client = app.app.test_client()
        if user is not None:
            client.set_cookie('session', str(user))
        return client.open('/api/' + path, method=method, json=data)

    def post(self, path, data, user=1, status=200):
        response = self.request('POST', path, data, user)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json()

    def task(self):
        return self.post('tasks', {'title': 'Tarea <real>', 'project_id': 1, 'column_id': 1})['id']

    def rule(self, actions):
        return self.post('automations/save', {'project_id': 1, 'name': 'Regla', 'enabled': True,
                         'trigger': {'type': 'created'}, 'conditions': [], 'actions': actions})['rule']

    def rows(self, sql, args=()):
        with closing(app.db()) as c:
            return [tuple(row) for row in c.execute(sql, args)]

    def test_blueprint_status_and_preferences(self):
        response = self.request('GET', 'slack/status', user=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json['installation'], {'team_id': 'T1', 'team_name': 'Equipo'})
        self.assertEqual(response.json['account'], {'slack_user_id': 'U2'})
        self.assertEqual(response.json['preferences'], [{'project_id': 1, 'assignment': True, 'comment': True, 'completion': False}])
        self.assertNotIn('token-secreto', response.get_data(as_text=True))
        self.assertEqual(self.request('GET', 'slack/status', user=None).status_code, 403)

    def test_real_assignment_comments_completion_and_worker(self):
        tid = self.task()
        self.post('task/update', {'id': tid, 'assignee_id': 2})
        self.post('comments', {'task_id': tid, 'body': 'Uno'})
        self.post('comments', {'task_id': tid, 'body': 'Dos'})
        self.post('task/type-status', {'id': tid, 'type_id': self.typ, 'status_id': self.done})
        self.assertEqual(self.rows('SELECT kind,user_id,count FROM slack_queue ORDER BY id'),
                         [('assignment', 2, 1), ('comment', 2, 2), ('completion', 3, 1)])
        with closing(app.db()) as c, c:
            c.execute("UPDATE slack_queue SET available_at='2000-01-01'")
        with patch.object(slack_notify, '_send', return_value=('sent', 0, None)) as send:
            self.assertEqual(slack_notify.run_once(), 3)
        self.assertEqual([call.args[1] for call in send.call_args_list], ['U2', 'U2', 'U3'])
        self.assertIn('Nuevos comentarios (2)', send.call_args_list[1].args[2])
        self.assertIn('Tarea &lt;real&gt;', send.call_args_list[0].args[2])
        self.assertEqual(self.rows('SELECT state,attempts FROM slack_queue ORDER BY id'), [('sent', 1)] * 3)

    def test_worker_rechecks_membership_and_completion_role(self):
        tid = self.task()
        self.post('task/update', {'id': tid, 'assignee_id': 2})
        self.post('task/type-status', {'id': tid, 'type_id': self.typ, 'status_id': self.done})
        with closing(app.db()) as c, c:
            c.execute('DELETE FROM members WHERE project_id=1 AND user_id=2')
            c.execute("UPDATE members SET role='viewer' WHERE project_id=1 AND user_id=3")
        with patch.object(slack_notify, '_send') as send:
            self.assertEqual(slack_notify.run_once(), 2)
            send.assert_not_called()
        self.assertEqual(self.rows('SELECT state FROM slack_queue ORDER BY id'), [('cancelled',), ('cancelled',)])

    def test_request_rollback_includes_queue(self):
        tid = self.task()
        enqueue = slack_notify.enqueue
        def fail_after_enqueue(*args):
            enqueue(*args)
            raise ValueError('fallo después de encolar')
        with patch.object(slack_notify, 'enqueue', side_effect=fail_after_enqueue):
            self.post('task/update', {'id': tid, 'assignee_id': 2}, status=400)
        self.assertEqual(self.rows('SELECT assignee_id FROM tasks WHERE id=?', (tid,)), [(None,)])
        self.assertEqual(self.rows('SELECT id FROM slack_queue'), [])
        self.assertEqual(self.rows("SELECT id FROM history WHERE message='actualizó responsable'"), [])

    def test_automation_assignment_and_completion(self):
        self.rule([{'type': 'assignee_id', 'value': 2}, {'type': 'status', 'value': self.done}])
        tid = self.task()
        self.assertEqual(self.rows('SELECT assignee_id,status_id FROM tasks WHERE id=?', (tid,)), [(2, self.done)])
        self.assertEqual(self.rows('SELECT kind,user_id FROM slack_queue ORDER BY id'), [('assignment', 2), ('completion', 3)])
        self.assertEqual(self.rows('SELECT outcome FROM automation_runs'), [('success',)])
        with patch.object(slack_notify, '_send', return_value=('sent', 0, None)) as send:
            self.assertEqual(slack_notify.run_once(), 2)
            self.assertEqual([call.args[1] for call in send.call_args_list], ['U2', 'U3'])

    def test_automation_savepoint_rolls_back_notifications(self):
        self.rule([{'type': 'assignee_id', 'value': 2}, {'type': 'status', 'value': self.done},
                   {'type': 'status', 'value': self.incompatible}])
        tid = self.task()
        self.assertEqual(self.rows('SELECT assignee_id,completed_at FROM tasks WHERE id=?', (tid,)), [(None, None)])
        self.assertEqual(self.rows('SELECT id FROM slack_queue'), [])
        self.assertEqual(self.rows('SELECT outcome FROM automation_runs'), [('error',)])
        self.assertEqual(self.rows("SELECT event FROM metric_events WHERE task_id=? ORDER BY id", (tid,)), [('created',)])
        with patch.object(slack_notify, '_send') as send:
            self.assertEqual(slack_notify.run_once(), 0)
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
