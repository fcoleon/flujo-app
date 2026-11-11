import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class AutomationsIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = os.path.join(self.tmp.name, 'test.db')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, app, 'DB', self.old_db)
        app.init()
        with app.db() as c:
            for uid, admin in ((1, 1), (2, 0), (3, 0), (4, 0)):
                c.execute('INSERT INTO users(id,name,username,password,salt,admin) VALUES(?,?,?,?,?,?)',
                          (uid, str(uid), str(uid), '', '', admin))
                c.execute('INSERT INTO sessions VALUES(?,?)', (str(uid), uid))
            for p in (1, 2):
                c.execute('INSERT INTO projects(id,name) VALUES(?,?)', (p, str(p)))
                c.execute('INSERT INTO columns(id,project_id,name,position) VALUES(?,?,?,0)', (p, p, str(p)))
                app.ensure_base_type(c, p)
            c.execute("INSERT INTO columns VALUES(3,1,'Destino',1)")
            c.execute("INSERT INTO members VALUES(1,2,'editor')")
            c.execute("INSERT INTO members VALUES(1,3,'viewer')")
            self.typ = app.ensure_base_type(c, 1)
            self.done = c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1', (self.typ,)).fetchone()[0]

    def request(self, method, path, data=None, user=1):
        client = app.app.test_client()
        client.set_cookie('session', str(user))
        return client.open('/api/' + path, method=method, json=data)

    def post(self, path, data, user=1, status=200):
        response = self.request('POST', path, data, user)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def rule(self, name, trigger, actions, conditions=None, project=1, **extra):
        payload = dict(project_id=project, name=name, enabled=True, trigger=trigger,
                       conditions=conditions or [], actions=actions, **extra)
        return self.post('automations/save', payload)['rule']

    def task(self, **data):
        return self.post('tasks', {'title': 'Tarea', **data})['id']

    def runs(self, project=1, user=1):
        return self.request('GET', f'automations/runs?project_id={project}', user=user).get_json()['runs']

    def test_crud_validation_and_permissions(self):
        payload = dict(project_id=1, name='  Regla  ', enabled=True, trigger={'type': 'created'},
                       conditions=[], actions=[{'type': 'priority', 'value': 'Alta'}])
        for user in (2, 3, 4):
            self.post('automations/save', payload, user, 403)
        first = self.post('automations/save', payload)['rule']
        self.assertEqual(first['name'], 'Regla')
        second = self.rule('Otra', {'type': 'created'}, [{'type': 'priority', 'value': 'Baja'}])
        for user, expected in ((1, 200), (2, 200), (3, 403), (4, 403)):
            for endpoint in ('automations', 'automations/runs'):
                self.assertEqual(self.request('GET', f'{endpoint}?project_id=1', user=user).status_code, expected)
        for order in ([first['id']], [first['id'], first['id']], [first['id'], 999]):
            self.post('automations/reorder', {'project_id': 1, 'order': order}, status=400)
        self.post('automations/reorder', {'project_id': 1, 'order': [second['id'], first['id']]})
        rules = self.request('GET', 'automations?project_id=1').get_json()['rules']
        self.assertEqual([r['id'] for r in rules], [second['id'], first['id']])
        self.post('automations/save', {**payload, 'id': first['id'], 'enabled': False, 'name': 'Editada'})
        for bad in ({'enabled': 1}, {'trigger': {'type': 'created', 'to': 'Alta'}},
                    {'actions': [{'type': 'column', 'value': 2}]},
                    {'conditions': [{'field': 'priority', 'op': 'today'}]},
                    {'actions': [{'type': 'due_date', 'value': '2026-02-30'}]}):
            self.post('automations/save', {**payload, **bad}, status=400)
        self.post('automations/delete', {'project_id': 2, 'id': first['id']}, status=400)
        self.post('automations/delete', {'project_id': 1, 'id': first['id']}, user=2, status=403)
        self.post('automations/delete', {'project_id': 1, 'id': first['id']})
        self.assertEqual(len(self.request('GET', 'automations?project_id=1').get_json()['rules']), 1)

    def test_chain_fires_each_rule_once_and_records_history_metrics(self):
        a = self.rule('Inicio', {'type': 'created'}, [{'type': 'priority', 'value': 'Alta'}])
        b = self.rule('Finalizar', {'type': 'priority', 'from': 'Normal', 'to': 'Alta'},
                      [{'type': 'status', 'value': self.done}])
        d = self.rule('Ciclo', {'type': 'status', 'to': self.done},
                      [{'type': 'priority', 'value': 'Normal'}])
        with patch('app.now', return_value='2026-11-03T12:00:00+00:00'):
            tid = self.task(project_id=1, column_id=1)
        runs = self.runs(user=2)
        self.assertEqual([r['rule_id'] for r in reversed(runs)], [a['id'], b['id'], d['id']])
        self.assertTrue(all(r['outcome'] == 'success' and r['actor_id'] == 1 for r in runs))
        with app.db() as c:
            task = c.execute('SELECT priority,status_id FROM tasks WHERE id=?', (tid,)).fetchone()
            self.assertEqual(tuple(task), ('Normal', self.done))
            self.assertEqual([r[0] for r in c.execute('SELECT event FROM metric_events WHERE task_id=? ORDER BY id', (tid,))], ['created', 'completed'])
            self.assertEqual(app.analytics(c, 1, '2026-11')['totals'], {'created': 1, 'completed': 1, 'reopened': 0})
        history = self.request('GET', f'history/{tid}').get_json()
        self.assertEqual(sum('automatización' in h['message'] for h in history), 3)
        self.post('automations/delete', {'project_id': 1, 'id': b['id']})
        self.assertIsNone(next(r for r in self.runs() if r['rule_name'] == 'Finalizar')['rule_id'])

    def test_direct_placement_type_board_and_moves(self):
        self.rule('Uno', {'type': 'priority', 'to': 'Alta'}, [{'type': 'assignee_id', 'value': 2}])
        self.rule('Dos', {'type': 'priority', 'to': 'Alta'}, [{'type': 'assignee_id', 'value': 3}], project=2)
        self.rule('Mover', {'type': 'column', 'from': 1, 'to': 3}, [{'type': 'priority', 'value': 'Alta'}])
        parent = self.task(project_id=1, column_id=1)
        child = self.task(parent_id=parent)
        self.post('task/update', {'id': child, 'priority': 'Alta'}, user=2)
        self.assertEqual(self.runs(), [])
        self.post('task/project', {'id': parent, 'project_id': 2})
        self.post('task/move', {'id': parent, 'project_id': 1, 'column_id': 3, 'order': [parent]}, user=2)
        with app.db() as c:
            self.assertEqual(tuple(c.execute('SELECT priority,assignee_id FROM tasks WHERE id=?', (parent,)).fetchone()), ('Alta', 2))
            self.assertEqual(c.execute('SELECT column_id FROM placements WHERE task_id=? AND project_id=2', (parent,)).fetchone()[0], 2)
        self.assertEqual([r['rule_name'] for r in self.runs()], ['Uno', 'Mover'])
        self.assertEqual(self.runs(2), [])
        self.post('task/move', {'id': parent, 'project_id': 1, 'column_id': 3, 'order': [parent]}, user=2)
        self.assertEqual(len(self.runs()), 2)
        self.post('task/project', {'id': parent, 'project_id': 1, 'remove': True})
        self.post('task/update', {'id': parent, 'priority': 'Baja'})
        self.post('task/update', {'id': parent, 'priority': 'Alta'})
        self.assertEqual(len(self.runs()), 2)
        self.assertEqual(self.runs(2), [])

    def test_rule_failure_rolls_back_actions_history_metrics_and_chain(self):
        with app.db() as c:
            other = app.create_type(c, 1, 'Otro')
            incompatible = c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1', (other,)).fetchone()[0]
        self.rule('Falla', {'type': 'created'}, [{'type': 'status', 'value': self.done},
                                                  {'type': 'status', 'value': incompatible}])
        self.rule('No encadenar', {'type': 'status', 'to': self.done}, [{'type': 'priority', 'value': 'Alta'}])
        self.rule('Continúa', {'type': 'created'}, [{'type': 'priority', 'value': 'Baja'}])
        result = self.post('tasks', {'title':'Tarea', 'project_id':1, 'column_id':1})
        tid = result['id']
        self.assertEqual([r['name'] for r in result['disabled_automations']], ['Falla'])
        self.assertIn('incompatible', result['disabled_automations'][0]['reason'])
        rule = next(r for r in self.request('GET', 'automations?project_id=1').get_json()['rules'] if r['name']=='Falla')
        self.assertFalse(rule['enabled'])
        self.assertIn('incompatible', rule['disabled_reason'])
        second = self.post('tasks', {'title':'Otra', 'project_id':1, 'column_id':1})
        self.assertNotIn('disabled_automations', second)
        runs = [r for r in self.runs() if r['task_id']==tid]
        self.assertEqual([(r['rule_name'], r['outcome']) for r in runs], [('Continúa', 'success'), ('Falla', 'error')])
        self.assertIn('incompatible', runs[1]['error'])
        with app.db() as c:
            task = c.execute('SELECT status_id,priority FROM tasks WHERE id=?', (tid,)).fetchone()
            self.assertNotEqual(task['status_id'], self.done)
            self.assertEqual(task['priority'], 'Baja')
            self.assertEqual([r[0] for r in c.execute('SELECT event FROM metric_events WHERE task_id=?', (tid,))], ['created'])
        messages = [h['message'] for h in self.request('GET', f'history/{tid}').get_json()]
        self.assertTrue(any('falló' in m for m in messages))
        self.assertFalse(any('cambió status' in m for m in messages))

    def test_removed_status_disables_only_active_rules_and_can_be_corrected(self):
        with app.db() as c:
            typ = app.create_type(c, 1, 'Revisión')
            deleted = c.execute("INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?, 'Extra','#123456',0,2)", (typ,)).lastrowid
            statuses = [dict(row) for row in c.execute('SELECT * FROM task_statuses WHERE type_id=? AND id!=? ORDER BY position', (typ,deleted))]
        rules = [
            self.rule('Gatillante', {'type':'status','to':deleted}, [{'type':'priority','value':'Alta'}]),
            self.rule('Condición', {'type':'created'}, [{'type':'priority','value':'Alta'}], [{'field':'status','op':'ne','value':deleted}]),
            self.rule('Acción', {'type':'created'}, [{'type':'status','value':deleted}]),
            self.rule('Pausada', {'type':'status','to':deleted}, [{'type':'priority','value':'Alta'}]),
        ]
        paused = rules[-1]
        self.post('automations/save', {**paused, 'enabled':False})
        payload = {'id':typ,'name':'Revisión','statuses':statuses,'deleted_status_ids':[deleted]}
        result = self.post('task-types/update', payload)
        self.assertEqual({r['name'] for r in result['disabled_automations']}, {'Gatillante','Condición','Acción'})
        current = self.request('GET','automations?project_id=1').get_json()['rules']
        self.assertTrue(all(not r['enabled'] for r in current))
        self.assertTrue(all(r['disabled_reason'] for r in current if r['name']!='Pausada'))
        self.assertIsNone(next(r for r in current if r['name']=='Pausada')['disabled_reason'])
        self.assertNotIn('disabled_automations', self.post('workspace/block', {'user_id':4,'blocked':True}))
        rule = next(r for r in current if r['name']=='Acción')
        self.post('automations/save', {**rule, 'enabled':True}, status=400)
        corrected = self.post('automations/save', {**rule, 'enabled':True, 'actions':[{'type':'priority','value':'Alta'}]})['rule']
        self.assertTrue(corrected['enabled'])
        self.assertIsNone(corrected['disabled_reason'])

    def test_blocked_user_disables_references_and_does_not_repeat_notice(self):
        a = self.rule('Asignar', {'type':'created'}, [{'type':'assignee_id','value':4}])
        b = self.rule('Condición', {'type':'created'}, [{'type':'priority','value':'Alta'}], [{'field':'assignee_id','op':'eq','value':4}])
        result = self.post('workspace/block', {'user_id':4,'blocked':True})
        self.assertEqual({r['id'] for r in result['disabled_automations']}, {a['id'],b['id']})
        self.assertNotIn('disabled_automations', self.post('workspace/block', {'user_id':4,'blocked':True}))
        self.post('workspace/block', {'user_id':4,'blocked':False})
        current = self.request('GET','automations?project_id=1').get_json()['rules']
        self.assertTrue(all(not r['enabled'] for r in current))

    def test_deleted_type_disables_references_to_type_and_its_states(self):
        with app.db() as c:
            typ = app.create_type(c, 1, 'Temporal')
            status = c.execute('SELECT id FROM task_statuses WHERE type_id=? ORDER BY id', (typ,)).fetchone()[0]
        a = self.rule('Tipo', {'type':'created'}, [{'type':'priority','value':'Alta'}], [{'field':'type','op':'eq','value':typ}])
        b = self.rule('Estado', {'type':'created'}, [{'type':'status','value':status}])
        result = self.post('task-types/delete', {'id':typ})
        self.assertEqual({r['id'] for r in result['disabled_automations']}, {a['id'],b['id']})
        self.assertTrue(all(r['project_name']=='1' for r in result['disabled_automations']))

    def test_failed_change_does_not_disable_rules(self):
        rule = self.rule('Estado', {'type':'status','to':self.done}, [{'type':'priority','value':'Alta'}])
        self.post('task-types/delete', {'id':self.typ}, status=400)
        current = self.request('GET','automations?project_id=1').get_json()['rules'][0]
        self.assertEqual(current, rule)

    def test_automatic_move_appends_and_triggers_column_rule(self):
        existing = self.task(project_id=1, column_id=3)
        self.rule('Mover', {'type': 'created'}, [{'type': 'column', 'value': 3}])
        self.rule('Al llegar', {'type': 'column', 'from': 1, 'to': 3},
                  [{'type': 'priority', 'value': 'Alta'}])
        tid = self.task(project_id=1, column_id=1)
        with app.db() as c:
            placements = c.execute('SELECT task_id,position FROM placements WHERE column_id=3 ORDER BY position').fetchall()
            self.assertEqual([tuple(r) for r in placements], [(existing, 0), (tid, 1)])
            self.assertEqual(c.execute('SELECT priority FROM tasks WHERE id=?', (tid,)).fetchone()[0], 'Alta')
        self.assertEqual([r['rule_name'] for r in reversed(self.runs())], ['Mover', 'Al llegar'])

    def test_dates_conditions_and_relative_actions(self):
        self.rule('Fecha', {'type': 'created'}, [{'type': 'due_date', 'value': {'days': -1}}])
        self.rule('Vencida', {'type': 'due_date'}, [{'type': 'priority', 'value': 'Alta'}],
                  [{'field': 'due_date', 'op': 'overdue'}, {'field': 'due_date', 'op': 'before', 'value': '2026-11-11'}])
        self.rule('Hoy', {'type': 'due_date', 'to': '2026-11-11'}, [{'type': 'clear_due_date'}],
                  [{'field': 'due_date', 'op': 'today'}, {'field': 'due_date', 'op': 'has'}])
        self.rule('Vacía', {'type': 'due_date', 'to': 'none'}, [{'type': 'assignee_id', 'value': 2}],
                  [{'field': 'due_date', 'op': 'empty'}])
        with patch('app.now', return_value='2026-11-11T10:00:00+00:00'):
            tid = self.task(project_id=1, column_id=1)
            self.post('task/update', {'id': tid, 'due_date': '2026-11-11'})
        with app.db() as c:
            row = c.execute('SELECT due_date,priority,assignee_id FROM tasks WHERE id=?', (tid,)).fetchone()
            self.assertEqual(tuple(row), (None, 'Alta', 2))
        self.assertEqual([r['rule_name'] for r in reversed(self.runs())], ['Fecha', 'Vencida', 'Hoy', 'Vacía'])

    def test_same_event_conditions_share_snapshot_and_chained_event_has_new_state(self):
        self.rule('Cambia', {'type': 'created'}, [{'type': 'priority', 'value': 'Alta'}])
        self.rule('Estado inicial', {'type': 'created'}, [{'type': 'assignee_id', 'value': 2}],
                  [{'field': 'priority', 'op': 'eq', 'value': 'Normal'}])
        self.rule('No estado posterior', {'type': 'created'}, [{'type': 'due_date', 'value': '2026-11-12'}],
                  [{'field': 'priority', 'op': 'eq', 'value': 'Alta'}])
        self.rule('Evento encadenado', {'type': 'priority', 'to': 'Alta'},
                  [{'type': 'due_date', 'value': '2026-11-13'}],
                  [{'field': 'priority', 'op': 'eq', 'value': 'Alta'}])
        tid = self.task(project_id=1, column_id=1)
        self.assertEqual([r['rule_name'] for r in reversed(self.runs())],
                         ['Cambia', 'Estado inicial', 'Evento encadenado'])
        with app.db() as c:
            row = c.execute('SELECT priority,assignee_id,due_date FROM tasks WHERE id=?', (tid,)).fetchone()
            self.assertEqual(tuple(row), ('Alta', 2, '2026-11-13'))

    def test_completion_conditions_follow_status_category(self):
        ongoing = self.rule('En curso', {'type': 'status'}, [{'type': 'priority', 'value': 'Baja'}],
                            [{'field': 'completion', 'op': 'eq', 'value': 'ongoing'}])
        done = self.rule('Finalizada', {'type': 'status'}, [{'type': 'priority', 'value': 'Alta'}],
                         [{'field': 'completion', 'op': 'eq', 'value': 'done'}])
        tid = self.task(project_id=1, column_id=1)
        with app.db() as c:
            start = app.default_status(c, self.typ)
        self.post('task/type-status', {'id': tid, 'type_id': self.typ, 'status_id': self.done})
        self.post('task/type-status', {'id': tid, 'type_id': self.typ, 'status_id': start})
        self.assertEqual([r['rule_id'] for r in reversed(self.runs())], [done['id'], ongoing['id']])
        with app.db() as c:
            self.assertEqual(c.execute('SELECT priority FROM tasks WHERE id=?', (tid,)).fetchone()[0], 'Baja')
        payload = {'project_id': 1, 'name': 'Inválida', 'enabled': True, 'trigger': {'type': 'created'},
                   'conditions': [{'field': 'completion', 'op': 'eq', 'value': 'other'}],
                   'actions': [{'type': 'priority', 'value': 'Alta'}]}
        self.post('automations/save', payload, status=400)

    def test_automation_migration_is_idempotent_and_preserves_data(self):
        tid = self.task(project_id=1, column_id=1)
        with app.db() as c:
            c.execute('DROP TABLE automation_runs')
            c.execute('DROP TABLE automation_rules')
        app.init()
        rule = self.rule('Conservada', {'type': 'priority', 'to': 'Alta'},
                         [{'type': 'assignee_id', 'value': 2}])
        self.post('task/update', {'id': tid, 'priority': 'Alta'})
        before = self.runs()
        app.init()
        app.init()
        self.assertEqual(self.runs(), before)
        self.assertEqual(self.request('GET', 'automations?project_id=1').get_json()['rules'], [rule])
        with app.db() as c:
            self.assertEqual(c.execute('SELECT assignee_id FROM tasks WHERE id=?', (tid,)).fetchone()[0], 2)

    def test_triggers_require_matching_type_board_and_direct_placement(self):
        self.rule('Tablero uno', {'type': 'column'}, [{'type': 'priority', 'value': 'Alta'}])
        self.rule('Tablero dos', {'type': 'column'}, [{'type': 'priority', 'value': 'Baja'}], project=2)
        tid = self.task(project_id=1, column_id=1)
        self.post('task/project', {'id': tid, 'project_id': 2})
        with app.db() as c:
            c.execute("INSERT INTO columns VALUES(4,2,'Destino',1)")
            type_two = app.ensure_base_type(c, 2)
        self.post('task/move', {'id': tid, 'project_id': 2, 'column_id': 4, 'order': [tid]})
        self.assertEqual(self.runs(2), [])
        self.post('task/type-status', {'id': tid, 'type_id': type_two})
        self.post('task/move', {'id': tid, 'project_id': 1, 'column_id': 3, 'order': [tid]})
        self.assertEqual(self.runs(1), [])
        self.post('task/move', {'id': tid, 'project_id': 2, 'column_id': 2, 'order': [tid]})
        self.assertEqual([r['rule_name'] for r in self.runs(2)], ['Tablero dos'])
        self.post('task/project', {'id': tid, 'project_id': 2, 'remove': True})
        self.post('task/move', {'id': tid, 'project_id': 1, 'column_id': 1, 'order': [tid]})
        self.assertEqual(self.runs(1), [])
        self.assertEqual(len(self.runs(2)), 1)


if __name__ == '__main__':
    unittest.main()
