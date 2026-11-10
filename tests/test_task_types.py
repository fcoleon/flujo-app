import io
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class TaskTypesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = os.path.join(self.tmp.name, 'test.db')
        app.init()
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(1,'Admin','admin','','',1)")
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(2,'Editor','editor','','',0)")
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(3,'Lector','lector','','',0)")
            for project in (1, 2):
                c.execute('INSERT INTO projects(id,name) VALUES(?,?)',(project, f'Proyecto {project}'))
                c.execute('INSERT INTO columns(id,project_id,name,position) VALUES(?,?,?,0)',(project,project,'Por hacer'))
                app.ensure_base_type(c,project)
            c.execute("INSERT INTO members VALUES(1,2,'editor')")
            c.execute("INSERT INTO members VALUES(1,3,'viewer')")

    def tearDown(self):
        app.DB = self.old_db
        self.tmp.cleanup()

    def client_for(self,user):
        token=f'test-session-{user}'
        with app.db() as c: c.execute('INSERT OR IGNORE INTO sessions VALUES(?,?)',(token,user))
        client=app.app.test_client();client.set_cookie('session',token);return client

    def post(self,path,data,user=1):
        response=self.client_for(user).post('/api/'+path,json=data)
        return response.status_code,response.get_json()

    def get(self,path,user=1):
        response=self.client_for(user).get('/api/'+path)
        return response.status_code,response.get_json()

    def test_types_state_reset_permissions_and_history(self):
        status, res = self.post('task-types', {'project_id': 1, 'name': 'Incidencia'})
        self.assertEqual(status, 200); typ=res['id']
        self.assertEqual(self.post('task-types', {'project_id': 1, 'name': 'No'}, user=2)[0],403)
        with app.db() as c:
            sts=[dict(r) for r in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position',(typ,))]
        sts.append({'name':'Finalizado con error','color':'#ff0000','is_done':1})
        sts.reverse()
        self.assertEqual(self.post('task-types/update',{'id':typ,'name':'Incidencia','statuses':sts})[0],200)
        with app.db() as c:
            sts=[dict(r) for r in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position',(typ,))]
            self.assertEqual(sts[0]['name'],'Finalizado con error')
            error_id=sts[0]['id']; progress_id=next(x['id'] for x in sts if not x['is_done'])
        status,res=self.post('tasks',{'title':'Prueba','project_id':1,'column_id':1,'type_id':typ},user=2)
        self.assertEqual(status,200); task=res['id']
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':typ,'status_id':error_id},user=3)[0],403)
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':typ,'status_id':error_id},user=2)[0],200)
        with app.db() as c: base=app.ensure_base_type(c,1)
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':base,'status_id':error_id},user=2)[0],200)
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':typ,'status_id':error_id},user=2)[0],200)
        with app.db() as c:
            row=c.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
            self.assertEqual(row['status_id'],progress_id)
            self.assertEqual(c.execute('SELECT count(*) FROM history WHERE task_id=?',(task,)).fetchone()[0],4)
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':typ,'status_id':999},user=2)[0],400)
        with app.db() as c: other=app.ensure_base_type(c,2)
        self.assertEqual(self.post('task/type-status',{'id':task,'type_id':other},user=2)[0],403)

    def test_base_and_validation(self):
        with app.db() as c:
            base=app.ensure_base_type(c,1)
            sts=[dict(x) for x in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position',(base,))]
        self.assertEqual(self.post('task-types/update',{'id':base,'name':'Base','statuses':sts+[{'name':'Extra','color':'#112233'}]})[0],400)
        sts[0]['is_done']=1
        self.assertEqual(self.post('task-types/update',{'id':base,'name':'Base','statuses':sts})[0],400)
        sts[0]['is_done']=0; sts[0]['color']='red'
        self.assertEqual(self.post('task-types/update',{'id':base,'name':'Base','statuses':sts})[0],400)

    def test_delete_task_permissions_projects_and_subtasks(self):
        task=self.post('tasks',{'title':'Principal','project_id':1,'column_id':1})[1]['id']
        child=self.post('tasks',{'title':'Subtarea','parent_id':task})[1]['id']
        self.post('task/project',{'id':task,'project_id':2})
        self.post('comments',{'task_id':task,'body':'Comentario'})
        self.assertEqual(self.post('task/delete',{'id':task},user=3)[0],403)
        with app.db() as c:
            self.assertIsNotNone(c.execute('SELECT id FROM tasks WHERE id=?',(task,)).fetchone())
        self.assertEqual(self.post('task/delete',{'id':task},user=2)[0],200)
        with app.db() as c:
            self.assertIsNone(c.execute('SELECT id FROM tasks WHERE id=?',(task,)).fetchone())
            self.assertIsNone(c.execute('SELECT parent_id FROM tasks WHERE id=?',(child,)).fetchone()['parent_id'])
            for table in ('placements','comments','history'):
                self.assertEqual(c.execute(f'SELECT count(*) FROM {table} WHERE task_id=?',(task,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM metric_events WHERE task_id=?',(task,)).fetchone()[0],1)

    def test_delete_subtask_records_parent_activity(self):
        task=self.post('tasks',{'title':'Principal','project_id':1,'column_id':1})[1]['id']
        child=self.post('tasks',{'title':'Subtarea','parent_id':task},user=2)[1]['id']
        self.assertEqual(self.post('task/delete',{'id':child},user=2)[0],200)
        with app.db() as c:
            self.assertIsNotNone(c.execute('SELECT 1 FROM history WHERE task_id=? AND message=?',(task,'eliminó la subtarea Subtarea')).fetchone())

    def test_edit_own_profile_and_password(self):
        salt='ab'*16
        with app.db() as c:
            c.execute('UPDATE users SET salt=?,password=? WHERE id=3',(salt,app.hashpw('anterior123',salt)))
        self.assertEqual(self.post('profile',{'id':1,'name':'  Nuevo nombre  '},user=3)[0],200)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT name FROM users WHERE id=3').fetchone()['name'],'Nuevo nombre')
            self.assertEqual(c.execute('SELECT name FROM users WHERE id=1').fetchone()['name'],'Admin')
        for fields in (
            {'name':' '},
            {'name':'No guardar','current_password':'incorrecta','new_password':'nueva1234','confirm_password':'nueva1234'},
            {'name':'No guardar','current_password':'anterior123','new_password':'corta','confirm_password':'corta'},
            {'name':'No guardar','current_password':'anterior123','new_password':'nueva1234','confirm_password':'distinta'},
        ):
            self.assertEqual(self.post('profile',fields,user=3)[0],400)
        with app.db() as c:
            row=c.execute('SELECT * FROM users WHERE id=3').fetchone()
            self.assertEqual(row['name'],'Nuevo nombre')
            self.assertEqual(row['password'],app.hashpw('anterior123',row['salt']))
        self.assertEqual(self.post('profile',{'name':'Nombre final','current_password':'anterior123','new_password':'nueva1234','confirm_password':'nueva1234'},user=3)[0],200)
        with app.db() as c:
            row=c.execute('SELECT * FROM users WHERE id=3').fetchone()
            self.assertEqual(row['name'],'Nombre final')
            self.assertNotEqual(row['salt'],salt)
            self.assertEqual(row['password'],app.hashpw('nueva1234',row['salt']))
            self.assertNotEqual(row['password'],app.hashpw('anterior123',row['salt']))

    def test_workspace_settings_and_blocked_access(self):
        self.assertEqual(self.post('workspace/update',{'name':'Equipo'},user=2)[0],403)
        self.assertEqual(self.post('workspace/update',{'name':'  Equipo  '})[0],200)
        self.assertEqual(self.post('workspace/block',{'user_id':2,'blocked':True},user=3)[0],403)
        self.assertEqual(self.post('workspace/block',{'user_id':1,'blocked':True})[0],400)
        self.assertEqual(self.post('workspace/block',{'user_id':2,'blocked':1})[0],400)
        self.assertEqual(self.post('workspace/block',{'user_id':2,'blocked':True})[0],200)
        with app.db() as c:
            salt='ab'*16
            c.execute('UPDATE users SET salt=?,password=? WHERE id=2',(salt,app.hashpw('clave123',salt)))
        client=app.app.test_client()
        login=client.post('/api/login',json={'username':'editor','password':'clave123'})
        self.assertEqual(login.status_code,200)
        session=client.get('/api/session').get_json()
        self.assertEqual(session['user']['id'],2);self.assertTrue(session['blocked'])
        get=self.get
        self.assertTrue(get('session',2)[1]['blocked'])
        status,body=get('data',2)
        self.assertEqual(status,403);self.assertTrue(body['blocked']);self.assertNotIn('tasks',body)
        self.assertEqual(self.post('profile',{'name':'No permitido'},user=2)[0],403)
        self.assertEqual(self.post('tasks',{'title':'No permitido','project_id':1,'column_id':1},user=2)[0],403)
        self.assertEqual(get('data',1)[1]['workspace_name'],'Equipo')
        self.assertEqual(self.post('workspace/block',{'user_id':2,'blocked':False})[0],200)
        self.assertFalse(get('session',2)[1]['blocked'])
        self.assertEqual(get('data',2)[0],200)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT role FROM members WHERE project_id=1 AND user_id=2').fetchone()['role'],'editor')

    def test_board_data_includes_comment_counts_for_visible_tasks(self):
        task=self.post('tasks',{'title':'Con comentarios','project_id':1,'column_id':1})[1]['id']
        empty=self.post('tasks',{'title':'Sin comentarios','project_id':1,'column_id':1})[1]['id']
        private=self.post('tasks',{'title':'Privada','project_id':2,'column_id':2})[1]['id']
        for body in ('Uno','Dos'):
            self.assertEqual(self.post('comments',{'task_id':task,'body':body})[0],200)
        self.post('comments',{'task_id':private,'body':'Privado'})
        status,result=self.get('data',3)
        self.assertEqual(status,200)
        counts={t['id']:t['comment_count'] for t in result['tasks']}
        self.assertEqual(counts[task],2);self.assertEqual(counts[empty],0);self.assertNotIn(private,counts)

    def test_delete_type_rejects_in_use_and_preserves_metrics(self):
        typ=self.post('task-types',{'project_id':1,'name':'Personalizado'})[1]['id']
        base=None
        with app.db() as c: base=app.ensure_base_type(c,1)
        self.assertEqual(self.post('task-types/delete',{'id':base})[0],400)
        self.assertEqual(self.post('task-types/delete',{'id':typ},user=2)[0],403)
        tid=self.post('tasks',{'project_id':1,'column_id':1,'type_id':typ,'title':'Usada'})[1]['id']
        self.assertEqual(self.post('task/project',{'id':tid,'project_id':1,'remove':True})[0],200)
        self.assertEqual(self.post('task-types/delete',{'id':typ})[0],400)
        self.assertEqual(self.post('task/type-status',{'id':tid,'type_id':base})[0],403)
        with app.db() as c: global_base=app.ensure_base_type(c,None)
        self.assertEqual(self.post('task/type-status',{'id':tid,'type_id':global_base})[0],200)
        self.assertEqual(self.post('task-types/delete',{'id':typ})[0],200)
        with app.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM task_types WHERE id=?',(typ,)).fetchone())
            self.assertEqual(c.execute('SELECT COUNT(*) FROM metric_events WHERE type_id=?',(typ,)).fetchone()[0],1)
        replacement=self.post('task-types',{'project_id':1,'name':'Otro'})[1]['id']
        self.assertNotEqual(replacement,typ)

    def test_delete_status_checks_use_and_stale_updates(self):
        typ=self.post('task-types',{'project_id':1,'name':'Personalizado'})[1]['id']
        with app.db() as c:
            extra=c.execute("INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?, 'Error','#ff0000',1,2)",(typ,)).lastrowid
            sts=[dict(x) for x in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position',(typ,))]
        payload={'id':typ,'name':'Personalizado','statuses':[x for x in sts if x['id']!=extra],'deleted_status_ids':[extra]}
        self.assertEqual(self.post('task-types/update',payload,user=2)[0],403)
        tid=self.post('tasks',{'project_id':1,'column_id':1,'type_id':typ,'title':'Tarea'})[1]['id']
        self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':extra})
        self.assertEqual(self.post('task-types/update',payload)[0],400)
        with app.db() as c: self.assertIsNotNone(c.execute('SELECT 1 FROM task_statuses WHERE id=?',(extra,)).fetchone())
        self.assertEqual(self.post('task-types/update',{'id':typ,'name':'Personalizado','statuses':payload['statuses']})[0],400)
        other_done=next(x['id'] for x in sts if x['is_done'] and x['id']!=extra)
        self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':other_done})
        self.assertEqual(self.post('task-types/update',payload)[0],200)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM task_statuses WHERE type_id=?',(typ,)).fetchone()[0],2)
        self.assertEqual(self.post('task-types/update',payload)[0],400)
        with app.db() as c:
            base=app.ensure_base_type(c,1)
            base_sts=[dict(x) for x in c.execute('SELECT * FROM task_statuses WHERE type_id=?',(base,))]
        self.assertEqual(self.post('task-types/update',{'id':base,'name':'Base','statuses':[base_sts[0]],'deleted_status_ids':[base_sts[1]['id']]})[0],400)

    def test_edit_project_requires_project_admin(self):
        payload={'project_id':1,'name':'  Plan actualizado  ','description':'  Nueva descripción  ','color':'#12ab34'}
        self.assertEqual(self.post('projects/update',payload,user=2)[0],403)
        self.assertEqual(self.post('projects/update',payload,user=3)[0],403)
        with app.db() as c: c.execute("UPDATE members SET role='admin' WHERE project_id=1 AND user_id=2")
        self.assertEqual(self.post('projects/update',payload,user=2)[0],200)
        with app.db() as c:
            project=c.execute('SELECT name,description,color FROM projects WHERE id=1').fetchone()
            self.assertEqual((project['name'],project['description'],project['color']),('Plan actualizado','Nueva descripción','#12ab34'))
        self.assertEqual(self.post('projects/update',{'project_id':1,'name':' ','description':''},user=2)[0],400)
        self.assertEqual(self.post('projects/update',{'project_id':1,'name':'A','description':'x'*5001},user=2)[0],400)
        self.assertEqual(self.post('projects/update',{'project_id':1,'name':'A','color':'red'},user=2)[0],400)
        self.assertEqual(self.post('projects/update',{'project_id':999,'name':'A'},user=1)[0],400)
        self.assertEqual(self.post('projects/update',{'project_id':1,'name':'Nuevo','description':''},user=1)[0],200)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT color FROM projects WHERE id=1').fetchone()['color'],'#12ab34')

    def test_column_rename_and_order_permissions(self):
        with app.db() as c:
            c.execute("INSERT INTO columns(id,project_id,name,position) VALUES(3,1,'Revisión',1)")
        self.assertEqual(self.post('columns/rename',{'column_id':1,'name':'Pendientes'},user=2)[0],200)
        self.assertEqual(self.post('columns/rename',{'column_id':1,'name':'No'},user=3)[0],403)
        self.assertEqual(self.post('columns/rename',{'column_id':1,'name':'  '},user=2)[0],400)
        self.assertEqual(self.post('columns/reorder',{'project_id':1,'order':[3,1]},user=3)[0],403)
        self.assertEqual(self.post('columns/reorder',{'project_id':1,'order':[3,3]},user=2)[0],400)
        self.assertEqual(self.post('columns/reorder',{'project_id':1,'order':[3,2]},user=2)[0],400)
        self.assertEqual(self.post('columns/reorder',{'project_id':1,'order':[3,1]},user=2)[0],200)
        with app.db() as c:
            columns=c.execute('SELECT id,name FROM columns WHERE project_id=1 ORDER BY position').fetchall()
            self.assertEqual([x['id'] for x in columns],[3,1])
            self.assertEqual(columns[1]['name'],'Pendientes')

    def test_monthly_analytics_and_access(self):
        with app.db() as c:
            typ=app.ensure_base_type(c,1)
            states=c.execute('SELECT id,is_done FROM task_statuses WHERE type_id=?',(typ,)).fetchall()
            ongoing=next(x['id'] for x in states if not x['is_done']); done=next(x['id'] for x in states if x['is_done'])
        with patch('app.now',return_value='2026-11-03T12:00:00+00:00'):
            status,result=self.post('tasks',{'title':'Medible','project_id':1,'column_id':1,'type_id':typ},user=2)
        self.assertEqual(status,200); tid=result['id']
        self.assertEqual(self.post('task/project',{'id':tid,'project_id':2})[0],200)
        with patch('app.now',return_value='2026-11-06T00:00:00+00:00'):
            self.assertEqual(self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':done},user=2)[0],200)
        with patch('app.now',return_value='2026-11-07T00:00:00+00:00'):
            self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':ongoing},user=2)
        with patch('app.now',return_value='2026-11-08T00:00:00+00:00'):
            self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':done},user=2)
        with app.db() as c:
            report=app.analytics(c,1,'2026-11',typ)
            self.assertEqual(report['totals'],{'created':1,'completed':2,'reopened':1})
            self.assertEqual(report['average_days'],3.5)
            self.assertEqual(report['median_days'],3.5)
            self.assertEqual(report['duration_count'],2)
            self.assertEqual(app.analytics(c,2,'2026-11')['totals'],{'created':0,'completed':2,'reopened':1})
            self.assertEqual(app.analytics(c,1,'2026-11',999)['totals']['completed'],0)
            with self.assertRaises(ValueError): app.analytics(c,1,'2026-13')
        def get(user): return self.get('analytics?project_id=1&month=2026-11',user)
        self.assertEqual(get(2)[0],403)
        self.assertEqual(get(3)[0],403)
        self.assertEqual(get(1)[1]['totals']['completed'],2)

    def test_person_breakdown_uses_event_snapshots(self):
        with app.db() as c:
            typ=app.ensure_base_type(c,1)
            done=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1',(typ,)).fetchone()['id']
        with patch('app.now',return_value='2026-11-03T12:00:00+00:00'):
            tid=self.post('tasks',{'title':'Atribución','project_id':1,'column_id':1,'type_id':typ},user=2)[1]['id']
        self.assertEqual(self.post('task/update',{'id':tid,'assignee_id':3},user=2)[0],200)
        with patch('app.now',return_value='2026-11-06T00:00:00+00:00'):
            self.assertEqual(self.post('task/type-status',{'id':tid,'type_id':typ,'status_id':done},user=2)[0],200)
        self.post('task/update',{'id':tid,'assignee_id':2},user=2)
        with app.db() as c:
            c.execute("INSERT INTO metric_events(project_id,task_id,event,occurred_at,task_created_at,type_id,type_name) VALUES(1,?,'completed','2026-11-07T12:00:00+00:00','2026-11-03T12:00:00+00:00',?,'Anterior')",(tid,typ))
            report=app.analytics(c,1,'2026-11',typ)
            people={p['name']:p for p in report['people']}
            self.assertEqual((people['Editor']['created'],people['Editor']['completed']),(1,0))
            self.assertEqual((people['Lector']['created'],people['Lector']['completed']),(0,1))
            self.assertEqual(people['Lector']['average_days'],2.5)
            self.assertEqual(people['Sin datos históricos']['completed'],1)
            self.assertEqual(sum(p['completed'] for p in people.values()),report['totals']['completed'])
            self.assertEqual(app.analytics(c,1,'2026-11',999)['people'],[])
        with patch('app.now',return_value='2026-11-03T12:00:00+00:00'):
            unassigned=self.post('tasks',{'title':'No asignada','project_id':1,'column_id':1,'type_id':typ},user=2)[1]['id']
        with patch('app.now',return_value='2026-11-06T00:00:00+00:00'):
            self.post('task/type-status',{'id':unassigned,'type_id':typ,'status_id':done},user=2)
        with app.db() as c:
            people={p['name']:p for p in app.analytics(c,1,'2026-11')['people']}
            self.assertEqual(people['Sin responsable']['completed'],1)
            self.assertEqual(people['Sin responsable']['average_days'],2.5)

    def test_category_edits_register_transitions(self):
        typ=self.post('task-types',{'project_id':1,'name':'Especial'})[1]['id']
        tid=self.post('tasks',{'title':'Caso','project_id':1,'column_id':1,'type_id':typ})[1]['id']
        with app.db() as c:
            sts=[dict(row) for row in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position',(typ,))]
        sts[0]['is_done']=1;sts.append({'name':'En curso nuevo','color':'#112233','is_done':0})
        self.assertEqual(self.post('task-types/update',{'id':typ,'name':'Especial','statuses':sts})[0],200)
        with app.db() as c:
            self.assertEqual([r['event'] for r in c.execute('SELECT event FROM metric_events WHERE task_id=? ORDER BY id',(tid,))],['created','completed'])

    def test_global_base_adopts_project_base_without_changing_completion(self):
        with app.db() as c:
            global_type=app.ensure_base_type(c,None); project_type=app.ensure_base_type(c,1)
            global_done=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1',(global_type,)).fetchone()['id']
            project_done=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1',(project_type,)).fetchone()['id']
            c.execute("UPDATE task_types SET name='Base personalizada' WHERE id=?",(project_type,))
        tid=self.post('tasks',{'title':'Sin proyecto'})[1]['id']
        self.post('task/type-status',{'id':tid,'type_id':global_type,'status_id':global_done})
        with app.db() as c: before=c.execute('SELECT COUNT(*) FROM metric_events').fetchone()[0]
        self.assertEqual(self.post('task/project',{'id':tid,'project_id':1})[0],200)
        with app.db() as c:
            row=c.execute('SELECT type_id,status_id FROM tasks WHERE id=?',(tid,)).fetchone()
            self.assertEqual((row['type_id'],row['status_id']),(project_type,project_done))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM metric_events').fetchone()[0],before)
        self.assertEqual(self.post('task/type-status',{'id':tid,'type_id':global_type})[0],400)

    def test_migrate_legacy_general_base_and_preserve_custom_types(self):
        with app.db() as c:
            global_type=app.ensure_base_type(c,None); project_type=app.ensure_base_type(c,1)
            global_done=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1',(global_type,)).fetchone()['id']
            project_done=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=1',(project_type,)).fetchone()['id']
            tid=c.execute("INSERT INTO tasks(title,created_at,type_id,status_id) VALUES('Antigua',?,?,?)",(app.now(),global_type,global_done)).lastrowid
            c.execute('INSERT INTO placements VALUES(?,?,?,0)',(tid,1,1))
            orphan=c.execute("INSERT INTO tasks(title,created_at,type_id,status_id) VALUES('Huérfana',?,?,?)",(app.now(),global_type,global_done)).lastrowid
            custom=app.create_type(c,1,'Personalizado')
            custom_tid=c.execute("INSERT INTO tasks(title,created_at,type_id,status_id) VALUES('Personalizada',?,?,?)",(app.now(),custom,app.default_status(c,custom))).lastrowid
            c.execute('INSERT INTO placements VALUES(?,?,?,1)',(custom_tid,1,1))
        app.init();app.init()
        with app.db() as c:
            migrated=c.execute('SELECT type_id,status_id FROM tasks WHERE id=?',(tid,)).fetchone()
            self.assertEqual((migrated['type_id'],migrated['status_id']),(project_type,project_done))
            self.assertEqual(c.execute('SELECT type_id FROM tasks WHERE id=?',(orphan,)).fetchone()['type_id'],global_type)
            self.assertEqual(c.execute('SELECT type_id FROM tasks WHERE id=?',(custom_tid,)).fetchone()['type_id'],custom)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM metric_events WHERE task_id=?',(tid,)).fetchone()[0],0)

    def test_assignee_access_only_for_projectless_tasks(self):
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(4,'Responsable','responsable','','',0)")
        orphan=self.post('tasks',{'title':'Sin proyecto'})[1]['id']
        self.post('task/update',{'id':orphan,'assignee_id':4})
        with_project=self.post('tasks',{'title':'Proyecto privado','project_id':2,'column_id':2})[1]['id']
        self.post('task/update',{'id':with_project,'assignee_id':4})
        unrelated=self.post('tasks',{'title':'Otra sin proyecto'})[1]['id']
        def accessible(user): return {t['id']:t for t in self.get('data',user)[1]['tasks']}
        tasks=accessible(4)
        self.assertIn(orphan,tasks);self.assertFalse(tasks[orphan]['has_projects']);self.assertTrue(tasks[orphan]['can_edit'])
        self.assertNotIn(with_project,tasks);self.assertNotIn(unrelated,tasks)
        self.assertEqual(self.post('task/update',{'id':orphan,'title':'Responsable puede editar'},user=4)[0],200)
        self.assertEqual(self.post('task/update',{'id':with_project,'title':'No permitido'},user=4)[0],403)
        with app.db() as c: c.execute("INSERT INTO members VALUES(2,4,'viewer')")
        self.assertIn(with_project,accessible(4))
        self.assertFalse(accessible(4)[with_project]['can_edit'])
        self.assertEqual(self.post('task/update',{'id':with_project,'title':'No permitido'},user=4)[0],403)
        with app.db() as c: c.execute('DELETE FROM members WHERE project_id=2 AND user_id=4')
        self.assertEqual(self.post('task/project',{'id':with_project,'project_id':2,'remove':True})[0],200)
        self.assertIn(with_project,accessible(4))
        self.assertEqual(self.post('task/update',{'id':orphan,'assignee_id':''})[0],200)
        self.assertNotIn(orphan,accessible(4))

    def test_projectless_subtask_inherits_parent_access(self):
        parent=self.post('tasks',{'title':'Principal','project_id':1,'column_id':1},user=2)[1]['id']
        sub=self.post('tasks',{'title':'Subtarea','parent_id':parent},user=2)[1]['id']
        with app.db() as c:
            editor=c.execute('SELECT * FROM users WHERE id=2').fetchone()
            viewer=c.execute('SELECT * FROM users WHERE id=3').fetchone()
            self.assertTrue(app.can_task(c,editor,sub,True))
            self.assertTrue(app.can_task(c,viewer,sub))
            self.assertFalse(app.can_task(c,viewer,sub,True))

    def test_subtasks_inherit_projects_dynamically_and_enforce_access(self):
        with app.db() as c:
            c.execute("INSERT INTO users(id,name,username,password,salt,admin) VALUES(4,'Responsable','responsable','','',0)")
        parent=self.post('tasks',{'title':'Principal','project_id':1,'column_id':1})[1]['id']
        child=self.post('tasks',{'title':'Hija','parent_id':parent})[1]['id']
        grandchild=self.post('tasks',{'title':'Nieta','parent_id':child})[1]['id']
        self.post('task/update',{'id':child,'assignee_id':4})
        def data_for(user): return {t['id']:t for t in self.get('data',user)[1]['tasks']}
        self.assertNotIn(child,data_for(4))
        visible=data_for(3)
        self.assertEqual(visible[child]['inherited_project_ids'],[1]);self.assertTrue(visible[child]['has_projects'])
        self.assertEqual(visible[grandchild]['inherited_project_ids'],[1]);self.assertEqual(visible[child]['placements'],[])
        self.post('task/project',{'id':parent,'project_id':2})
        self.assertEqual(data_for(1)[child]['inherited_project_ids'],[1,2])
        self.assertEqual(data_for(3)[child]['inherited_project_ids'],[1])
        self.post('task/project',{'id':parent,'project_id':1,'remove':True})
        self.assertNotIn(child,data_for(3));self.assertEqual(data_for(1)[grandchild]['inherited_project_ids'],[2])
        self.post('task/project',{'id':parent,'project_id':2,'remove':True})
        self.assertFalse(data_for(4)[child]['has_projects'])
        self.assertEqual(self.post('task/delete',{'id':parent})[0],200)
        self.assertIsNone(data_for(4)[child]['parent_id'])

    def test_public_task_ids_migrate_and_remain_private(self):
        visible=self.post('tasks',{'title':'Visible','project_id':1,'column_id':1},user=2)[1]['id']
        private=self.post('tasks',{'title':'Privada','project_id':2,'column_id':2})[1]['id']
        subtask=self.post('tasks',{'title':'Subtarea','parent_id':visible},user=2)[1]['id']
        with app.db() as c:
            before={r['id']:r['public_id'] for r in c.execute('SELECT id,public_id FROM tasks WHERE id IN (?,?,?)',(visible,private,subtask))}
            self.assertEqual(len(set(before.values())),3)
            for value in before.values(): self.assertEqual(uuid.UUID(value).version,4)
            legacy=c.execute("INSERT INTO tasks(title,created_at) VALUES('Antigua',?)",(app.now(),)).lastrowid
        app.init();app.init()
        with app.db() as c:
            after={r['id']:r['public_id'] for r in c.execute('SELECT id,public_id FROM tasks WHERE id IN (?,?,?)',(visible,private,subtask))}
            self.assertEqual(before,after)
            self.assertEqual(uuid.UUID(c.execute('SELECT public_id FROM tasks WHERE id=?',(legacy,)).fetchone()[0]).version,4)
        ids={task['public_id'] for task in self.get('data',2)[1]['tasks']}
        self.assertIn(before[visible],ids);self.assertIn(before[subtask],ids);self.assertNotIn(before[private],ids)

    def test_task_link_route_serves_application(self):
        client=app.app.test_client()
        response=client.get('/t/'+str(uuid.uuid4())+'?project=1')
        self.assertEqual(response.status_code,200);self.assertIn(b'<div id="app">',response.data)
        for path in ('/','/app.js','/style.css','/favicon.svg'):
            self.assertEqual(client.get(path).status_code,200)
        self.assertEqual(client.get('/t/no-es-uuid').status_code,404)

    def test_flask_authentication_cookie_and_request_limits(self):
        client=app.app.test_client()
        self.assertEqual(client.get('/api/data').status_code,401)
        self.assertEqual(client.post('/api/tasks',json={'title':'No'}).status_code,401)
        with app.db() as c:
            salt='ab'*16
            c.execute('UPDATE users SET salt=?,password=? WHERE id=2',(salt,app.hashpw('clave123',salt)))
        login=client.post('/api/login',json={'username':'editor','password':'clave123'},base_url='https://localhost')
        self.assertEqual(login.status_code,200)
        cookie=login.headers['Set-Cookie']
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie);self.assertIn('Secure',cookie)
        self.assertEqual(client.get('/api/data',base_url='https://localhost').status_code,200)
        too_large=client.post('/api/comments',data='x'*100001,base_url='https://localhost')
        self.assertEqual(too_large.status_code,413)
        self.assertEqual(client.post('/api/logout',json={},base_url='https://localhost').status_code,200)
        self.assertEqual(client.get('/api/data',base_url='https://localhost').status_code,401)

    def test_temporary_password_requires_change_before_workspace_access(self):
        self.assertEqual(self.post('users',{'name':'Invitado','username':'invitado','password':'temporal123'})[0],200)
        with app.db() as c:
            invited=c.execute("SELECT id,must_change_password FROM users WHERE username='invitado'").fetchone()
            self.assertEqual(invited['must_change_password'],1)
        self.assertEqual(self.post('workspace/block',{'user_id':invited['id'],'blocked':True})[0],200)
        blocked=app.app.test_client();blocked.post('/api/login',json={'username':'invitado','password':'temporal123'})
        self.assertTrue(blocked.get('/api/session').get_json()['blocked'])
        self.assertTrue(blocked.post('/api/password/change',json={}).get_json()['blocked'])
        self.assertEqual(self.post('workspace/block',{'user_id':invited['id'],'blocked':False})[0],200)
        first=app.app.test_client();second=app.app.test_client()
        for client in (first,second):
            self.assertEqual(client.post('/api/login',json={'username':'invitado','password':'temporal123'}).status_code,200)
            self.assertTrue(client.get('/api/session').get_json()['must_change_password'])
            self.assertTrue(client.get('/api/data').get_json()['password_change_required'])
            self.assertEqual(client.get('/api/data').status_code,403)
            self.assertEqual(client.post('/api/profile',json={'name':'Intento'}).status_code,403)
            self.assertEqual(client.post('/api/tasks',json={'title':'Intento'}).status_code,403)
        password={'current_password':'incorrecta','new_password':'nueva1234','confirm_password':'nueva1234'}
        self.assertEqual(first.post('/api/password/change',json=password).status_code,400)
        password['current_password']='temporal123';password['confirm_password']='distinta'
        self.assertEqual(first.post('/api/password/change',json=password).status_code,400)
        password.update(new_password='temporal123',confirm_password='temporal123')
        self.assertEqual(first.post('/api/password/change',json=password).status_code,400)
        self.assertTrue(first.get('/api/session').get_json()['must_change_password'])
        password.update(new_password='nueva1234',confirm_password='nueva1234')
        self.assertEqual(first.post('/api/password/change',json=password).status_code,200)
        self.assertFalse(first.get('/api/session').get_json()['must_change_password'])
        self.assertEqual(first.get('/api/data').status_code,200)
        self.assertIsNone(second.get('/api/session').get_json()['user'])
        self.assertEqual(second.post('/api/login',json={'username':'invitado','password':'temporal123'}).status_code,401)
        self.assertEqual(second.post('/api/login',json={'username':'invitado','password':'nueva1234'}).status_code,200)

    def test_registration_policy_and_first_admin(self):
        anonymous=app.app.test_client()
        registration={'name':'Nueva','username':'nueva','password':'clave123'}
        with patch.dict(app.SETTINGS,{'allow_self_registration':False}):
            self.assertFalse(anonymous.get('/api/session').get_json()['allow_self_registration'])
            self.assertEqual(anonymous.post('/api/register',json=registration).status_code,403)
            self.assertEqual(self.post('users',{'name':'Invitado','username':'creado','password':'clave123'},user=1)[0],200)
            self.assertEqual(self.post('users',{'name':'No','username':'no','password':'clave123'},user=2)[0],403)
        with patch.dict(app.SETTINGS,{'allow_self_registration':True}):
            self.assertTrue(anonymous.get('/api/session').get_json()['allow_self_registration'])
            self.assertEqual(anonymous.post('/api/register',json=registration).status_code,200)
            self.assertFalse(anonymous.get('/api/session').get_json()['must_change_password'])
            self.assertFalse(anonymous.get('/api/session').get_json()['user']['admin'])
        old=app.DB
        with tempfile.TemporaryDirectory() as temp:
            try:
                app.DB=os.path.join(temp,'new.db');app.init();client=app.app.test_client()
                with patch.dict(app.SETTINGS,{'allow_self_registration':True}):
                    self.assertEqual(client.post('/api/register',json=registration).status_code,400)
                    self.assertEqual(client.post('/api/setup',json=registration).status_code,200)
                session=client.get('/api/session').get_json()
                self.assertTrue(session['user']['admin']);self.assertFalse(session['must_change_password'])
            finally: app.DB=old

    def test_simultaneous_temporary_password_changes_only_one_session_succeeds(self):
        self.assertEqual(self.post('users',{'name':'Invitado','username':'invitado','password':'temporal123'})[0],200)
        clients=[app.app.test_client(),app.app.test_client()]
        for client in clients:
            self.assertEqual(client.post('/api/login',json={'username':'invitado','password':'temporal123'}).status_code,200)
        barrier=Barrier(2)
        def change(index):
            barrier.wait();password='nuevaclave'+str(index)
            return clients[index].post('/api/password/change',json={'current_password':'temporal123','new_password':password,'confirm_password':password}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(change,range(2))),[200,401])

    def test_first_account_creation_is_serialized_between_requests(self):
        old=app.DB
        with tempfile.TemporaryDirectory() as temp:
            try:
                app.DB=os.path.join(temp,'new.db');app.init();barrier=Barrier(2)
                def create(name):
                    client=app.app.test_client();barrier.wait()
                    return client.post('/api/setup',json={'name':name,'username':name,'password':'clave123'}).status_code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    statuses=list(pool.map(create,['uno','dos']))
                self.assertEqual(sorted(statuses),[200,400])
                with app.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],1)
            finally:
                app.DB=old

    def test_existing_tasks_migration(self):
        with app.db() as c:
            c.execute("INSERT INTO tasks(title,created_at) VALUES('Anterior','2020')")
            tid=c.execute('SELECT max(id) FROM tasks').fetchone()[0]
            c.execute('INSERT INTO placements VALUES(?,?,?,0)',(tid,1,1))
        app.init()
        with app.db() as c:
            task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            self.assertEqual(task['type_id'],app.ensure_base_type(c,1))
            self.assertEqual(task['status_id'],app.default_status(c,task['type_id']))

    def test_existing_users_are_not_marked_as_temporary_during_migration(self):
        old=app.DB
        with tempfile.TemporaryDirectory() as temp:
            try:
                app.DB=os.path.join(temp,'legacy.db')
                with sqlite3.connect(app.DB) as c:
                    c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,salt TEXT NOT NULL,admin INTEGER NOT NULL DEFAULT 0)')
                    c.execute("INSERT INTO users VALUES(1,'Anterior','anterior','hash','salt',1)")
                app.init();app.init()
                with app.db() as c:
                    user=c.execute('SELECT * FROM users WHERE id=1').fetchone()
                    self.assertEqual(user['must_change_password'],0)
                    self.assertEqual((user['username'],user['password']),('anterior','hash'))
            finally: app.DB=old

if __name__ == '__main__': unittest.main()
