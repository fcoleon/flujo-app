from flask import request
import automations
from permissions import role
from web_helpers import respond


def project_for_read(c,u):
    p=automations.ident(request.args.get('project_id'))
    if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return None
    if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin acceso a automatizaciones')
    return p

def list_rules(c,u):
    p=project_for_read(c,u)
    if p is None: return respond({'error':'Proyecto no encontrado'},404)
    return respond({'rules':[automations.rule_data(r) for r in c.execute('SELECT * FROM automation_rules WHERE project_id=? ORDER BY position,id',(p,))]})

def list_runs(c,u):
    p=project_for_read(c,u)
    if p is None: return respond({'error':'Proyecto no encontrado'},404)
    return respond({'runs':[dict(r) for r in c.execute('SELECT * FROM automation_runs WHERE project_id=? ORDER BY id DESC LIMIT 100',(p,))]})


def project_for_write(c,u,b):
    p=automations.ident(b.get('project_id'))
    if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): raise ValueError('Proyecto no encontrado')
    if role(c,u,p)!='admin': raise PermissionError('Solo administradores pueden configurar automatizaciones')
    return p

def save_rule(c,u,b):
    p=project_for_write(c,u,b)
    data=automations.validate(c,p,b)
    if b.get('id'):
        rid=automations.ident(b['id'])
        if not c.execute('SELECT 1 FROM automation_rules WHERE id=? AND project_id=?',(rid,p)).fetchone(): raise ValueError('Regla no encontrada')
        c.execute('UPDATE automation_rules SET name=?,enabled=?,trigger=?,conditions=?,actions=?,disabled_reason=NULL WHERE id=? AND project_id=?',(*data,rid,p))
    else:
        if c.execute('SELECT COUNT(*) FROM automation_rules WHERE project_id=?',(p,)).fetchone()[0]>=100: raise ValueError('Máximo 100 reglas por tablero')
        rid=c.execute('INSERT INTO automation_rules(project_id,name,enabled,trigger,conditions,actions,position) VALUES(?,?,?,?,?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM automation_rules WHERE project_id=?))',(p,*data,p)).lastrowid
    result=automations.rule_data(c.execute('SELECT * FROM automation_rules WHERE id=?',(rid,)).fetchone())
    return respond({'rule':result})

def delete_rule(c,u,b):
    p=project_for_write(c,u,b)
    rid=automations.ident(b.get('id'))
    if not c.execute('DELETE FROM automation_rules WHERE id=? AND project_id=?',(rid,p)).rowcount: raise ValueError('Regla no encontrada')

def reorder_rules(c,u,b):
    p=project_for_write(c,u,b)
    order=b.get('order')
    if not isinstance(order,list): raise ValueError('Orden inválido')
    order=[automations.ident(x) for x in order]
    current={r['id'] for r in c.execute('SELECT id FROM automation_rules WHERE project_id=?',(p,))}
    if len(order)!=len(current) or set(order)!=current: raise ValueError('Las reglas cambiaron; vuelve a cargar')
    for position,rid in enumerate(order): c.execute('UPDATE automation_rules SET position=? WHERE id=? AND project_id=?',(position,rid,p))
