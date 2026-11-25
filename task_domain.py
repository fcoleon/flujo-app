from datetime import datetime, timezone

from flask import g
import analytics as metrics
import automations


def create_type(c, project_id, name, is_base=False):
    # Historical metrics retain type IDs even after a type is deleted.
    # Never reuse an ID that is still referenced by those snapshots.
    current=c.execute('SELECT COALESCE(MAX(id),0) FROM task_types').fetchone()[0]
    has_metrics=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_events'").fetchone()
    historic=c.execute('SELECT COALESCE(MAX(type_id),0) FROM metric_events').fetchone()[0] if has_metrics else 0
    has_sequence=c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
    seq=c.execute("SELECT seq FROM sqlite_sequence WHERE name='task_types'").fetchone() if has_sequence else None
    tid=max(current,historic,seq[0] if seq else 0)+1
    c.execute('INSERT INTO task_types(id,project_id,name,is_base) VALUES(?,?,?,?)',(tid,project_id,name,int(is_base)))
    for i,(label,color,done) in enumerate([('En curso','#8b85a3',0),('Finalizado','#65aa86',1)]):
        c.execute('INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?,?,?,?,?)',(tid,label,color,done,i))
    return tid

def ensure_base_type(c, project_id):
    row=c.execute('SELECT id FROM task_types WHERE project_id IS ? AND is_base=1',(project_id,)).fetchone()
    return row['id'] if row else create_type(c,project_id,'Tarea base',True)

def apply_project_base(c,task_id,project_id):
    task=c.execute('SELECT ty.project_id,ty.is_base,s.is_done FROM tasks t JOIN task_types ty ON ty.id=t.type_id JOIN task_statuses s ON s.id=t.status_id WHERE t.id=?',(task_id,)).fetchone()
    if not task or task['project_id'] is not None or not task['is_base']: return False
    type_id=ensure_base_type(c,project_id)
    status=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=? ORDER BY position,id LIMIT 1',(type_id,task['is_done'])).fetchone()
    if not status: raise ValueError('El tipo base del proyecto no tiene un estado equivalente')
    c.execute('UPDATE tasks SET type_id=?,status_id=? WHERE id=?',(type_id,status['id'],task_id))
    return True

def default_status(c,type_id):
    row=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=0 ORDER BY position,id LIMIT 1',(type_id,)).fetchone()
    if not row: raise ValueError('El tipo debe tener un estado en curso')
    return row['id']

def type_data(c,row):
    item=dict(row); item['statuses']=[dict(s) for s in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position,id',(row['id'],))]; return item

def metric_event(c, task_id, event, actor_id):
    timestamp=now()
    if event in ('completed','reopened'):
        c.execute('UPDATE tasks SET completed_at=? WHERE id=?',(timestamp if event=='completed' else None,task_id))
    result=metrics.metric_event(c,task_id,event,actor_id,lambda:timestamp)
    if event=='completed':
        import slack_notify
        slack_notify.enqueue(c,'completion',task_id,actor_id)
    return result

def now(): return datetime.now(timezone.utc).isoformat()
def record(c,tid,uid,msg,status=False):
    if status:
        c.execute('''INSERT INTO history(task_id,user_id,message,created_at,status_id,status_name,type_id,type_name,status_is_done)
                     SELECT t.id,?,?,?,s.id,s.name,ty.id,ty.name,s.is_done FROM tasks t
                     JOIN task_statuses s ON s.id=t.status_id JOIN task_types ty ON ty.id=t.type_id WHERE t.id=?''',(uid,msg,now(),tid))
    else: c.execute('INSERT INTO history(task_id,user_id,message,created_at) VALUES(?,?,?,?)',(tid,uid,msg,now()))

def automate(c,tid,events,uid,project=None):
    if project is None:
        row=c.execute('SELECT ty.project_id FROM tasks t JOIN task_types ty ON ty.id=t.type_id WHERE t.id=?',(tid,)).fetchone()
        project=row['project_id'] if row else None
    g.disabled_automations.extend(automations.run(c,tid,project,events,uid,now,record,metric_event,notify_assignment))

def notify_assignment(c,tid,uid):
    import slack_notify
    slack_notify.enqueue(c,'assignment',tid,uid)

def delete_task(c,tid,uid):
    task=c.execute('SELECT title,parent_id FROM tasks WHERE id=?',(tid,)).fetchone()
    if task['parent_id']: record(c,task['parent_id'],uid,'eliminó la subtarea '+task['title'])
    c.execute('DELETE FROM tasks WHERE id=?',(tid,))

