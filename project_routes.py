import re
import uuid
import automations
from validation import integer_field, text_field
from flask import g
from permissions import role
from task_domain import ensure_base_type, record, delete_task, automate
from web_helpers import respond


COLUMN_COLORS = ('#a5a3ab', '#e7aa69', '#aa91dd', '#78b99a')


def post(c,u,path,b):
    if path=='/api/projects':
        if not u['admin']: raise PermissionError('Solo el administrador del workspace puede crear proyectos')
        name=text_field(b,'name').strip()
        if not name: raise ValueError('El nombre es obligatorio')
        color=b.get('color','#7765d7'); description=b.get('description','')
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Color inválido')
        if not isinstance(description,str): raise ValueError('La descripción debe ser texto')
        public_id=str(uuid.uuid4())
        p=c.execute('INSERT INTO projects(name,color,description,public_id) VALUES(?,?,?,?)',(name,color,description,public_id)).lastrowid
        c.execute('INSERT INTO members VALUES(?,?,?)',(p,u['id'],'admin'))
        ensure_base_type(c,p)
        for i,n in enumerate(['Por hacer','En progreso','En revisión','Completado']): c.execute('INSERT INTO columns(project_id,name,position,color) VALUES(?,?,?,?)',(p,n,i,COLUMN_COLORS[i]))
        return respond({'id':p,'public_id':public_id})
    elif path=='/api/projects/update':
        p=integer_field(b['project_id']); project=c.execute('SELECT id,color FROM projects WHERE id=?',(p,)).fetchone()
        if not project: raise ValueError('Proyecto no encontrado')
        if role(c,u,p)!='admin': raise PermissionError('Solo un administrador del proyecto puede editarlo')
        name=str(b.get('name','')).strip(); description=str(b.get('description','')).strip()
        if not name or len(name)>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
        if len(description)>5000: raise ValueError('La descripción debe tener hasta 5000 caracteres')
        color=b.get('color',project['color'])
        if 'color' in b and (not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color)): raise ValueError('Color inválido')
        c.execute('UPDATE projects SET name=?,description=?,color=? WHERE id=?',(name,description,color,p))
    elif path=='/api/members':
        p=integer_field(b['project_id'])
        if role(c,u,p)!='admin': raise PermissionError('No tienes permisos para administrar este proyecto')
        if b.get('role')=='remove': c.execute('DELETE FROM members WHERE project_id=? AND user_id=?',(p,integer_field(b['user_id'])))
        elif b.get('role') in ('admin','editor','viewer'): c.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role',(p,integer_field(b['user_id']),b['role']))
        else: raise ValueError('Rol inválido')
    elif path=='/api/columns':
        p=integer_field(b['project_id']); name=text_field(b,'name').strip()
        if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
        if not name: raise ValueError('El nombre es obligatorio')
        c.execute('''INSERT INTO columns(project_id,name,position,color)
                     SELECT ?,?,COALESCE(MAX(position),-1)+1,
                     CASE COUNT(*) % 4 WHEN 0 THEN ? WHEN 1 THEN ? WHEN 2 THEN ? ELSE ? END
                     FROM columns WHERE project_id=?''',(p,name,*COLUMN_COLORS,p))
    elif path in ('/api/columns/rename','/api/columns/update'):
        col=integer_field(b['column_id']); name=b.get('name')
        column=c.execute('SELECT * FROM columns WHERE id=?',(col,)).fetchone()
        if not column: raise ValueError('Columna no encontrada')
        if role(c,u,column['project_id'])!='admin': raise PermissionError('Solo un administrador del proyecto puede editar la columna')
        if not isinstance(name,str): raise ValueError('El nombre es obligatorio')
        name=name.strip();color=b.get('color',column['color'] or COLUMN_COLORS[0])
        if not name or len(name)>100: raise ValueError('El nombre debe tener entre 1 y 100 caracteres')
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Color inválido')
        c.execute('UPDATE columns SET name=?,color=? WHERE id=?',(name,color,col))
    elif path=='/api/columns/delete':
        col=integer_field(b['column_id']);column=c.execute('SELECT * FROM columns WHERE id=?',(col,)).fetchone()
        if not column: raise ValueError('Columna no encontrada')
        project=column['project_id']
        if role(c,u,project)!='admin': raise PermissionError('Solo un administrador del proyecto puede eliminar la columna')
        expected=b.get('task_ids')
        if not isinstance(expected,list) or any(type(x) is not int for x in expected) or len(expected)!=len(set(expected)): raise ValueError('Lista de tareas inválida')
        tasks=[r['task_id'] for r in c.execute('SELECT task_id FROM placements WHERE project_id=? AND column_id=? ORDER BY position,task_id',(project,col))]
        if set(tasks)!=set(expected): raise ValueError('Las tareas de la columna cambiaron; vuelve a abrir la confirmación')
        action=b.get('action')
        if tasks and action not in ('move','remove','delete'): raise ValueError('Elige qué hacer con las tareas')
        if not tasks and action not in (None,'move','remove','delete'): raise ValueError('Opción inválida')
        if action=='move':
            dest=b.get('destination_id')
            if type(dest) is not int or dest==col or not c.execute('SELECT 1 FROM columns WHERE id=? AND project_id=?',(dest,project)).fetchone(): raise ValueError('Selecciona otra columna del mismo tablero')
            position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE project_id=? AND column_id=?',(project,dest)).fetchone()[0]
            destination=c.execute('SELECT name FROM columns WHERE id=?',(dest,)).fetchone()['name']
            for tid in tasks:
                c.execute('UPDATE placements SET column_id=?,position=? WHERE task_id=? AND project_id=?',(dest,position,tid,project));position+=1
                record(c,tid,u['id'],'movió a '+destination+' al eliminar la columna '+column['name'])
        elif action=='remove':
            for tid in tasks:
                c.execute('DELETE FROM placements WHERE task_id=? AND project_id=?',(tid,project))
                record(c,tid,u['id'],'quitó la tarea de un proyecto al eliminar la columna '+column['name'])
        elif action=='delete':
            for tid in tasks: delete_task(c,tid,u['id'])
        c.execute('DELETE FROM columns WHERE id=?',(col,))
        g.disabled_automations.extend(automations.disable_invalid(c))
        if action=='move':
            for tid in tasks: automate(c,tid,[('column',col,dest)],u['id'],project)
    elif path=='/api/columns/reorder':
        p=integer_field(b['project_id']); order=b.get('order')
        if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
        if not isinstance(order,list) or any(type(x) is not int for x in order): raise ValueError('Orden inválido')
        current={row['id'] for row in c.execute('SELECT id FROM columns WHERE project_id=?',(p,))}
        if len(order)!=len(current) or set(order)!=current: raise ValueError('El tablero cambió; vuelve a intentarlo')
        for position,col in enumerate(order): c.execute('UPDATE columns SET position=? WHERE project_id=? AND id=?',(position,p,col))
