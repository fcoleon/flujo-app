import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import app


class ColumnDeleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=app.DB;app.DB=os.path.join(self.tmp.name,'test.db')
        self.addCleanup(self.tmp.cleanup);self.addCleanup(setattr,app,'DB',self.old_db)
        app.init()
        with app.db() as c:
            for uid,admin in ((1,1),(2,0),(3,0),(4,0)):
                c.execute('INSERT INTO users(id,name,username,password,salt,admin) VALUES(?,?,?,?,?,?)',(uid,str(uid),str(uid),'','',admin))
                c.execute('INSERT INTO sessions VALUES(?,?)',(str(uid),uid))
            for p in (1,2):
                c.execute('INSERT INTO projects(id,name) VALUES(?,?)',(p,str(p)));app.ensure_base_type(c,p)
            c.executemany('INSERT INTO members VALUES(?,?,?)',[(1,2,'editor'),(1,3,'viewer'),(1,4,'admin')])
            c.executemany('INSERT INTO columns(id,project_id,name,position) VALUES(?,?,?,?)',[(1,1,'Origen',0),(2,1,'Destino',1),(3,2,'Otro tablero',0)])

    def post(self,path,body,user=1,status=200):
        client=app.app.test_client();client.set_cookie('session',str(user));response=client.post('/api/'+path,json=body)
        self.assertEqual(response.status_code,status,response.get_json());return response.get_json()

    def task(self,title,column=1,**extra):
        project=2 if column==3 else 1
        return self.post('tasks',{'title':title,'project_id':project,'column_id':column,**extra})['id']

    def rule(self,name,trigger,action):
        return self.post('automations/save',{'project_id':1,'name':name,'enabled':True,'trigger':trigger,'conditions':[],'actions':[action]})['rule']

    def test_empty_and_permissions(self):
        for user in (2,3): self.post('columns/delete',{'column_id':1,'task_ids':[]},user=user,status=403)
        self.post('columns/delete',{'column_id':1,'task_ids':[]},user=4)
        with app.db() as c:
            self.assertIsNone(c.execute('SELECT id FROM columns WHERE id=1').fetchone())
            self.assertIsNotNone(c.execute('SELECT id FROM columns WHERE id=2').fetchone())

    def test_requires_choice_valid_destination_and_current_task_list(self):
        tid=self.task('Uno')
        base={'column_id':1,'task_ids':[tid]}
        for payload in (base,{**base,'action':'unknown'},{**base,'action':'move','destination_id':1},
                        {**base,'action':'move','destination_id':3},{**base,'task_ids':[],'action':'delete'},
                        {**base,'task_ids':[tid,tid],'action':'delete'}):
            self.post('columns/delete',payload,status=400)
        with app.db() as c:
            self.assertIsNotNone(c.execute('SELECT id FROM columns WHERE id=1').fetchone())
            self.assertEqual(c.execute('SELECT column_id FROM placements WHERE task_id=?',(tid,)).fetchone()[0],1)

    def test_move_preserves_order_and_triggers_valid_automations(self):
        previous=self.task('Existente',2);first=self.task('Primera');second=self.task('Segunda')
        invalid=self.rule('Origen',{'type':'column','to':'1'},{'type':'priority','value':'Baja'})
        self.rule('Destino',{'type':'column','to':'2'},{'type':'priority','value':'Alta'})
        result=self.post('columns/delete',{'column_id':1,'task_ids':[second,first],'action':'move','destination_id':2})
        self.assertEqual([r['id'] for r in result['disabled_automations']],[invalid['id']])
        with app.db() as c:
            placements=c.execute('SELECT task_id,position FROM placements WHERE project_id=1 AND column_id=2 ORDER BY position').fetchall()
            self.assertEqual([tuple(r) for r in placements],[(previous,0),(first,1),(second,2)])
            self.assertEqual([r['priority'] for r in c.execute('SELECT priority FROM tasks WHERE id IN (?,?) ORDER BY id',(first,second))],['Alta','Alta'])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_remove_from_project_preserves_shared_and_inherited_tasks(self):
        solo=self.task('Sin proyecto');shared=self.task('Compartida');parent=self.task('Padre',2)
        child=self.task('Hija',1,parent_id=parent)
        self.post('task/project',{'id':shared,'project_id':2})
        self.post('columns/delete',{'column_id':1,'task_ids':[solo,shared,child],'action':'remove'})
        with app.db() as c:
            self.assertFalse(c.execute('SELECT 1 FROM placements WHERE task_id=?',(solo,)).fetchone())
            self.assertEqual([r['project_id'] for r in c.execute('SELECT project_id FROM placements WHERE task_id=?',(shared,))],[2])
            self.assertEqual(app.task_projects(c,child),{1})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks WHERE id IN (?,?,?)',(solo,shared,child)).fetchone()[0],3)

    def test_delete_tasks_is_global_and_preserves_subtasks_outside_column(self):
        parent=self.task('Padre');child=self.task('Subtarea',2,parent_id=parent)
        self.post('task/project',{'id':parent,'project_id':2})
        self.post('comments',{'task_id':parent,'body':'Comentario'})
        self.post('columns/delete',{'column_id':1,'task_ids':[parent],'action':'delete'})
        with app.db() as c:
            self.assertIsNone(c.execute('SELECT id FROM tasks WHERE id=?',(parent,)).fetchone())
            self.assertIsNone(c.execute('SELECT parent_id FROM tasks WHERE id=?',(child,)).fetchone()['parent_id'])
            self.assertFalse(c.execute('SELECT 1 FROM placements WHERE task_id=?',(parent,)).fetchone())
            self.assertFalse(c.execute('SELECT 1 FROM comments WHERE task_id=?',(parent,)).fetchone())

    def test_failure_rolls_back_all_task_moves(self):
        first=self.task('Primera');second=self.task('Segunda');original=app.record;calls=0
        def fail(c,tid,uid,message):
            nonlocal calls
            calls+=1
            if calls==2: raise sqlite3.IntegrityError('Fallo de prueba')
            return original(c,tid,uid,message)
        with patch.object(app,'record',side_effect=fail):
            self.post('columns/delete',{'column_id':1,'task_ids':[first,second],'action':'move','destination_id':2},status=400)
        with app.db() as c:
            self.assertEqual([r['column_id'] for r in c.execute('SELECT column_id FROM placements WHERE task_id IN (?,?) ORDER BY task_id',(first,second))],[1,1])
            self.assertIsNotNone(c.execute('SELECT id FROM columns WHERE id=1').fetchone())

    def test_concurrent_delete_serializes(self):
        barrier=Barrier(2)
        def remove(_):
            client=app.app.test_client();client.set_cookie('session','1');barrier.wait()
            return client.post('/api/columns/delete',json={'column_id':1,'task_ids':[]}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(remove,range(2))),[200,400])


if __name__=='__main__': unittest.main()
