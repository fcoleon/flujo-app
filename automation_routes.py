from flask import request
import automations
from permissions import role
from web_helpers import respond


def get(c,u,path):
    p=automations.ident(request.args.get('project_id'))
    if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return respond({'error':'Proyecto no encontrado'},404)
    if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin acceso a automatizaciones')
    if path=='/api/automations':
        return respond({'rules':[automations.rule_data(r) for r in c.execute('SELECT * FROM automation_rules WHERE project_id=? ORDER BY position,id',(p,))]})
    return respond({'runs':[dict(r) for r in c.execute('SELECT * FROM automation_runs WHERE project_id=? ORDER BY id DESC LIMIT 100',(p,))]})


def post(c,u,path,b):
    p=automations.ident(b.get('project_id'))
    if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): raise ValueError('Proyecto no encontrado')
    if role(c,u,p)!='admin': raise PermissionError('Solo administradores pueden configurar automatizaciones')
    if path=='/api/automations/save':
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
    if path=='/api/automations/delete':
        rid=automations.ident(b.get('id'))
        if not c.execute('DELETE FROM automation_rules WHERE id=? AND project_id=?',(rid,p)).rowcount: raise ValueError('Regla no encontrada')
    else:
        order=b.get('order')
        if not isinstance(order,list): raise ValueError('Orden inválido')
        order=[automations.ident(x) for x in order]
        current={r['id'] for r in c.execute('SELECT id FROM automation_rules WHERE project_id=?',(p,))}
        if len(order)!=len(current) or set(order)!=current: raise ValueError('Las reglas cambiaron; vuelve a cargar')
        for position,rid in enumerate(order): c.execute('UPDATE automation_rules SET position=? WHERE id=? AND project_id=?',(position,rid,p))
