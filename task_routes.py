import re
import uuid
from validation import integer_field, task_title, task_due_date, task_priority


def post_task_types(c,u,path,b,can_task,role,ensure_base_type,now,default_status,record,notify_assignment,metric_event,automate,respond,create_type):
            if path=='/api/tasks':
                title=task_title(b.get('title','')); parent=b.get('parent_id') or None
                if parent:
                    parent=int(parent)
                    if not can_task(c,u,parent,True): raise PermissionError('Sin permiso para editar la tarea principal')
                p=integer_field(b['project_id']) if b.get('project_id') else None
                if p:
                    col=integer_field(b['column_id'])
                    if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                    if not c.execute('SELECT 1 FROM columns WHERE id=? AND project_id=?',(col,p)).fetchone(): raise ValueError('Columna inválida')
                elif not parent and not u['admin']:
                    raise PermissionError('Solo un administrador puede crear tareas sin proyecto')
                assignee=integer_field(b['assignee_id']) if b.get('assignee_id') else None
                if assignee is not None and not c.execute('SELECT 1 FROM users WHERE id=?',(assignee,)).fetchone():
                    raise ValueError('Responsable inválido')
                due_date=task_due_date(b.get('due_date'))
                type_id=integer_field(b['type_id']) if b.get('type_id') else ensure_base_type(c,p)
                selected=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not selected or selected['project_id'] != p: raise ValueError('Tipo de tarea inválido para este proyecto')
                tid=c.execute('INSERT INTO tasks(title,created_at,parent_id,type_id,status_id,public_id,assignee_id,due_date) VALUES(?,?,?,?,?,?,?,?)',(title,now(),parent,type_id,default_status(c,type_id),str(uuid.uuid4()),assignee,due_date)).lastrowid
                if p: c.execute('INSERT INTO placements VALUES(?,?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE column_id=?))',(tid,p,col,col))
                record(c,tid,u['id'],'creó la tarea',True)
                if assignee:
                    record(c,tid,u['id'],'asignó la tarea',True)
                    notify_assignment(c,tid,u['id'])
                metric_event(c,tid,'created',u['id'])
                if parent: record(c,parent,u['id'],'agregó la subtarea '+title)
                if p: automate(c,tid,[('created',None,None)]+([('assignee_id',None,assignee)] if assignee else []),u['id'],p)
                return respond({'id':tid})
            elif path=='/api/task-types':
                p=integer_field(b['project_id']); name=str(b.get('name','')).strip()
                if role(c,u,p)!='admin': raise PermissionError('Solo un administrador del proyecto puede configurar tipos')
                if not name or len(name)>100: raise ValueError('El nombre del tipo es obligatorio y debe tener hasta 100 caracteres')
                type_id=create_type(c,p,name)
                return respond({'id':type_id})
            elif path=='/api/task-types/delete':
                type_id=integer_field(b['id']); typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ or typ['project_id'] is None or role(c,u,typ['project_id'])!='admin': raise PermissionError('Sin permiso para configurar este tipo')
                if typ['is_base']: raise ValueError('No se puede eliminar el tipo base')
                if c.execute('SELECT 1 FROM tasks WHERE type_id=? LIMIT 1',(type_id,)).fetchone(): raise ValueError('Hay tareas con este tipo. Cámbialas de tipo antes de eliminarlo, incluso si están en otro tablero o sin proyecto')
                c.execute('DELETE FROM task_types WHERE id=?',(type_id,))
            elif path=='/api/task-types/update':
                type_id=integer_field(b['id']); typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ or typ['project_id'] is None or role(c,u,typ['project_id'])!='admin': raise PermissionError('Sin permiso para configurar este tipo')
                name=str(b.get('name','')).strip(); statuses=b.get('statuses')
                if not name or len(name)>100 or not isinstance(statuses,list) or any(not isinstance(st,dict) for st in statuses): raise ValueError('Datos del tipo inválidos')
                existing={r['id']:r for r in c.execute('SELECT * FROM task_statuses WHERE type_id=?',(type_id,))}
                provided=[integer_field(x['id']) for x in statuses if x.get('id')]
                deleted=b.get('deleted_status_ids',[])
                if not isinstance(deleted,list) or any(type(x) is not int for x in deleted): raise ValueError('Lista de estados inválida')
                if len(provided)!=len(set(provided)) or len(deleted)!=len(set(deleted)) or set(provided)&set(deleted) or set(provided)|set(deleted)!=set(existing): raise ValueError('Los estados cambiaron; vuelve a cargar')
                if typ['is_base'] and (len(statuses)!=2 or deleted): raise ValueError('La tarea base debe conservar sus dos estados')
                if not any(not bool(x.get('is_done')) for x in statuses) or not any(bool(x.get('is_done')) for x in statuses): raise ValueError('Incluye al menos un estado en curso y uno finalizado')
                for status_id in deleted:
                    if c.execute('SELECT 1 FROM tasks WHERE status_id=? LIMIT 1',(status_id,)).fetchone(): raise ValueError('Hay tareas con un estado que intentas eliminar. Cambia su estado antes de eliminarlo, incluso si están en otro tablero o sin proyecto')
                for status_id in deleted:
                    c.execute('UPDATE history SET status_is_done=? WHERE status_id=?',(existing[status_id]['is_done'],status_id))
                    c.execute('DELETE FROM task_statuses WHERE id=? AND type_id=?',(status_id,type_id))
                for i,st in enumerate(statuses):
                    label=str(st.get('name','')).strip(); color=str(st.get('color',''))
                    if not label or len(label)>100 or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Nombre o color del estado inválido')
                    if st.get('id'):
                        if typ['is_base'] and bool(st.get('is_done'))!=bool(existing[integer_field(st['id'])]['is_done']): raise ValueError('No se puede cambiar la categoría de un estado base')
                        changed_category=bool(st.get('is_done'))!=bool(existing[integer_field(st['id'])]['is_done'])
                        c.execute('UPDATE task_statuses SET name=?,color=?,is_done=?,position=? WHERE id=? AND type_id=?',(label,color,int(bool(st.get('is_done'))),i,integer_field(st['id']),type_id))
                        if changed_category:
                            for task in c.execute('SELECT id FROM tasks WHERE status_id=?',(integer_field(st['id']),)).fetchall(): metric_event(c,task['id'],'completed' if st.get('is_done') else 'reopened',u['id'])
                    else: c.execute('INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?,?,?,?,?)',(type_id,label,color,int(bool(st.get('is_done'))),i))
                c.execute('UPDATE task_types SET name=? WHERE id=?',(name,type_id))
            elif path=='/api/task/type-status':
                tid=integer_field(b['id']); type_id=integer_field(b['type_id']); status_id=integer_field(b['status_id']) if b.get('status_id') else None
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para editar')
                task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ: raise ValueError('Tipo inválido')
                if type_id!=task['type_id']:
                    if typ['project_id'] is None and c.execute('SELECT 1 FROM placements WHERE task_id=?',(tid,)).fetchone(): raise ValueError('El tipo base general solo está disponible para tareas sin proyecto')
                    if typ['project_id'] is not None and (role(c,u,typ['project_id']) not in ('admin','editor') or not c.execute('SELECT 1 FROM placements WHERE task_id=? AND project_id=?',(tid,typ['project_id'])).fetchone()): raise PermissionError('El tipo debe pertenecer a un proyecto editable de la tarea')
                    old_done=c.execute('SELECT is_done FROM task_statuses WHERE id=?',(task['status_id'],)).fetchone()['is_done']
                    status_id=default_status(c,type_id)
                    c.execute('UPDATE tasks SET type_id=?,status_id=? WHERE id=?',(type_id,status_id,tid))
                    if old_done: metric_event(c,tid,'reopened',u['id'])
                    record(c,tid,u['id'],'cambió el tipo a '+typ['name']+' y reinició el estado',True)
                else:
                    status=c.execute('SELECT * FROM task_statuses WHERE id=? AND type_id=?',(status_id,type_id)).fetchone()
                    if not status: raise ValueError('Estado inválido para el tipo')
                    if status_id!=task['status_id']:
                        old_done=c.execute('SELECT is_done FROM task_statuses WHERE id=?',(task['status_id'],)).fetchone()['is_done']
                        c.execute('UPDATE tasks SET status_id=? WHERE id=?',(status_id,tid)); record(c,tid,u['id'],'cambió el estado a '+status['name'],True)
                        if bool(old_done)!=bool(status['is_done']): metric_event(c,tid,'completed' if status['is_done'] else 'reopened',u['id'])
                new_status=c.execute('SELECT status_id FROM tasks WHERE id=?',(tid,)).fetchone()['status_id']
                if new_status!=task['status_id']: automate(c,tid,[('status',task['status_id'],new_status)],u['id'])

def post_tasks(c,u,path,b,can_task,delete_task,current_user,respond,sanitize_description,record,notify_assignment,automate,role,apply_project_base):
            if path=='/api/task/delete':
                tid=integer_field(b['id'])
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para eliminar la tarea')
                delete_task(c,tid,u['id'])
            elif path=='/api/task/update':
                if 'expected_user_id' in b:
                    current=current_user(c)
                    flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(current['id'],)).fetchone() if current else None
                    if type(b['expected_user_id']) is not int or not current or current['id']!=b['expected_user_id'] or flags['blocked'] or flags['must_change_password']:
                        raise PermissionError('La sesión cambió; conserva tu borrador')
                    u=current
                tid=integer_field(b['id'])
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para editar')
                old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                if not old: raise ValueError('Tarea no encontrada')
                for key in ('title','description'):
                    if key in b:
                        version=key+'_version'
                        if type(b.get(version)) is not int or b[version]<0: raise ValueError(f'El campo {version} es obligatorio y debe ser un entero no negativo')
                        if b[version]!=old[version]: return respond({'error':'La tarea cambió; vuelve a cargar','conflict':True},409)
                events=[]
                labels={'title':'nombre','description':'descripción','assignee_id':'responsable','due_date':'fecha límite','priority':'prioridad'}
                for key,label in labels.items():
                    if key in b:
                        val=task_title(b[key]) if key=='title' else (b[key] or None if key in ('assignee_id','due_date') else str(b[key]).strip())
                        if key=='due_date': val=task_due_date(b[key])
                        if key=='priority': val=task_priority(b[key])
                        if key=='assignee_id' and val is not None: val=integer_field(val)
                        if key=='description':
                            if not isinstance(b[key],str): raise ValueError('La descripción debe ser texto')
                            fmt='html' if b.get('description_format')=='html' else 'plain'
                            if fmt=='html': val=sanitize_description(val)
                            if old['description_format']!=fmt and str(old[key] or '')==str(val or ''):
                                c.execute('UPDATE tasks SET description_format=? WHERE id=?',(fmt,tid))
                        if str(old[key] or '')!=str(val or ''):
                            if key=='description': c.execute('UPDATE tasks SET description=?,description_format=? WHERE id=?',(val,fmt,tid))
                            else:
                                sql={'title':'UPDATE tasks SET title=? WHERE id=?',
                                     'assignee_id':'UPDATE tasks SET assignee_id=? WHERE id=?',
                                     'due_date':'UPDATE tasks SET due_date=? WHERE id=?',
                                     'priority':'UPDATE tasks SET priority=? WHERE id=?'}
                                c.execute(sql[key],(val,tid))
                            record(c,tid,u['id'],f'actualizó {label}')
                            if key=='assignee_id' and val: notify_assignment(c,tid,u['id'])
                            if key in ('assignee_id','priority','due_date'): events.append((key,old[key],val))
                if events: automate(c,tid,events,u['id'])
                task=c.execute('SELECT title,description,description_format,title_version,description_version FROM tasks WHERE id=?',(tid,)).fetchone()
                return respond({'ok':True,**dict(task)})
            elif path=='/api/task/move':
                tid=integer_field(b['id']); p=integer_field(b['project_id']); col=integer_field(b['column_id']); order=b['order']
                if not isinstance(order,list) or any(type(value) is not int for value in order): raise ValueError('Orden inválido')
                if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para editar')
                if not c.execute('SELECT 1 FROM columns WHERE id=? AND project_id=?',(col,p)).fetchone(): raise ValueError('Columna inválida')
                old=c.execute('SELECT column_id FROM placements WHERE task_id=? AND project_id=?',(tid,p)).fetchone()
                if not old: raise ValueError('Tarea no encontrada en el proyecto')
                existing={x['task_id'] for x in c.execute('SELECT task_id FROM placements WHERE project_id=? AND column_id=?',(p,col))}; expected=existing|{tid}
                if len(order)!=len(expected) or set(order)!=expected: raise ValueError('El tablero cambió; vuelve a intentarlo')
                c.execute('UPDATE placements SET column_id=? WHERE task_id=? AND project_id=?',(col,tid,p))
                for i,t in enumerate(order): c.execute('UPDATE placements SET position=? WHERE task_id=? AND project_id=?',(i,t,p))
                name=c.execute('SELECT name FROM columns WHERE id=?',(col,)).fetchone()['name']; record(c,tid,u['id'],('movió a '+name) if old['column_id']!=col else 'cambió el orden en '+name)
                if old['column_id']!=col: automate(c,tid,[('column',old['column_id'],col)],u['id'],p)
            elif path=='/api/task/project':
                tid=integer_field(b['id']); p=integer_field(b['project_id'])
                if not can_task(c,u,tid,True) or role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if b.get('remove'):
                    if not c.execute('SELECT 1 FROM placements WHERE task_id=? AND project_id=?',(tid,p)).fetchone(): raise ValueError('La tarea no pertenece a este proyecto')
                    c.execute('DELETE FROM placements WHERE task_id=? AND project_id=?',(tid,p)); record(c,tid,u['id'],'quitó la tarea de un proyecto')
                else:
                    col=c.execute('SELECT id FROM columns WHERE project_id=? ORDER BY position LIMIT 1',(p,)).fetchone()
                    if not col: raise ValueError('El proyecto no tiene columnas')
                    c.execute('INSERT INTO placements VALUES(?,?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE column_id=?))',(tid,p,col['id'],col['id']))
                    changed=apply_project_base(c,tid,p)
                    record(c,tid,u['id'],'agregó la tarea a un proyecto',changed)
