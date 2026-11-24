"""Explicit JSON API routes; app-specific dependencies are supplied at registration."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from functools import wraps

from flask import Blueprint, g, make_response, request

import automation_routes
import automations
import comment_routes
import project_routes
import task_routes
import workspace_routes
from auth import create_session, current_user, rotate_session, session_response
from validation import text_field


def register_api(app, deps):
    """Register the JSON API using the existing app module's shared helpers."""
    api = Blueprint('json_api', __name__, url_prefix='/api')
    respond = deps['respond']

    def route(rule, *, method='GET', public=False, logout=False, password=False, snapshot=False, immediate=False, notices=False):
        def decorate(handler):
            @api.route(rule, methods=[method], endpoint=handler.__name__)
            @wraps(handler)
            def wrapped(**kwargs):
                try:
                    body = None
                    if method == 'POST':
                        raw = request.get_data(cache=False)
                        if request.mimetype != 'application/json': return respond({'error':'Se requiere application/json'},415)
                        body = json.loads(raw)
                        if not isinstance(body,dict): raise ValueError('Datos inválidos')
                    with closing(deps['db']()) as c, c:
                        if snapshot: c.execute('BEGIN')
                        u = current_user(c)
                        if public:
                            return handler(c,u,body,**kwargs)
                        if not u: return respond({'error':'Inicia sesión'},401)
                        if logout: return handler(c,u,body,**kwargs)
                        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
                        if flags['blocked']:
                            return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
                        if flags['must_change_password'] and not password:
                            return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
                        if method == 'POST' and not password:
                            g.disabled_automations=[]
                        if immediate: c.execute('BEGIN IMMEDIATE')
                        result=handler(c,u,body,**kwargs)
                        if method != 'POST' or password or not notices: return result
                        if notices == 'validate': g.disabled_automations.extend(automations.disable_invalid(c))
                        pending=[item for item in g.disabled_automations if deps['role'](c,u,item['project_id']) in ('admin','editor')]
                        for item in pending:
                            item['project_name']=c.execute('SELECT name FROM projects WHERE id=?',(item['project_id'],)).fetchone()['name']
                        c.commit()
                        response=make_response(result if result is not None else respond({'ok':True}))
                        if pending:
                            payload=response.get_json(); payload['disabled_automations']=pending; response.set_data(app.json.dumps(payload))
                        return response
                except PermissionError as e: return respond({'error':str(e)},403)
                except (ValueError, KeyError, sqlite3.IntegrityError) as e: return respond({'error':str(e) or ('Datos inválidos' if method == 'POST' else '')},400)
            return wrapped
        return decorate

    @route('/session', public=True)
    def session(c,u,b):
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone() if u else None
        return respond({'user':dict(u) if u else None,'blocked':bool(flags and flags['blocked']),'must_change_password':bool(flags and flags['must_change_password']),'allow_self_registration':deps['SETTINGS']['allow_self_registration'],'needs_setup':not bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())})

    @route('/revision')
    def revision(c,u,b):
        return respond({'revision':c.execute('SELECT revision FROM data_revision WHERE id=1').fetchone()[0],'version':deps['VERSION']})

    @route('/tokens')
    def tokens(c,u,b): return respond({'tokens':deps['mcp_tokens'](c,u['id'])})

    @route('/data', snapshot=True)
    def data(c,u,b):
        revision=c.execute('SELECT revision FROM data_revision WHERE id=1').fetchone()[0]
        stat=os.stat(deps['DB'])
        identity=[deps['CACHE_EPOCH'],stat.st_dev,stat.st_ino,u['id'],revision,str(deps['STATS_ZONE']),all(deps['SETTINGS']['backblaze'].values())]
        key='flujo:data:'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        payload=deps['CACHE'].get_or_create(key,lambda:app.json.dumps(deps['workspace_data'](c,u,revision)),60)
        response=app.response_class(payload,mimetype='application/json')
        response.headers['Cache-Control']='private, no-store'
        return response

    @route('/analytics')
    def analytics(c,u,b):
        params=request.args.to_dict(flat=False); p=int(params['project_id'][0]); month=params['month'][0]
        if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return respond({'error':'Proyecto no encontrado'},404)
        if deps['role'](c,u,p)!='admin': return respond({'error':'Solo los administradores del proyecto pueden ver estadísticas'},403)
        type_filter=int(params['type_id'][0]) if params.get('type_id') else None
        return respond(deps['analytics'](c,p,month,type_filter))

    @route('/automations')
    def automation_list(c,u,b): return automation_routes.get(c,u,'/api/automations')

    @route('/automations/runs')
    def automation_runs(c,u,b): return automation_routes.get(c,u,'/api/automations/runs')

    @route('/comments/<path:task_id>')
    def comments(c,u,b,task_id):
        tid=int(task_id.split('/')[-1])
        if not deps['can_task'](c,u,tid): return respond({'error':'Sin acceso'},403)
        return respond([dict(x) for x in c.execute('SELECT cm.*,COALESCE(u.name,"Usuario eliminado") AS author FROM comments cm LEFT JOIN users u ON u.id=cm.user_id WHERE task_id=? ORDER BY cm.id',(tid,))])

    @route('/history/<path:task_id>')
    def history(c,u,b,task_id):
        tid=int(task_id.split('/')[-1])
        if not deps['can_task'](c,u,tid): return respond({'error':'Sin acceso'},403)
        return respond([dict(x) for x in c.execute('''SELECT h.*,CASE WHEN h.user_id IS NULL AND h.message='inicio del registro de tiempo por estado' THEN 'Sistema' ELSE COALESCE(u.name,'Usuario eliminado') END AS actor FROM history h LEFT JOIN users u ON u.id=h.user_id WHERE task_id=? ORDER BY h.id DESC''',(tid,))])

    def authenticate(c,u,b,kind):
        if kind=='register' and not deps['SETTINGS']['allow_self_registration']: return respond({'error':'El registro está deshabilitado'},403)
        username=text_field(b,'username').strip().lower(); pw=text_field(b,'password')
        if kind in ('setup','register'):
            c.execute('BEGIN IMMEDIATE')
            exists=bool(c.execute('SELECT 1 FROM users').fetchone())
            if kind=='setup' and exists: raise ValueError('La aplicación ya está configurada')
            if kind=='register' and not exists: raise ValueError('Primero se debe crear la cuenta administradora')
            name=text_field(b,'name').strip()
            if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
            s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin) VALUES(?,?,?,?,?)',(name,username,deps['hashpw'](pw,s),s,int(kind=='setup')))
        row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        password_hash=deps['hashpw'](pw,row['salt'] if row else '00'*16)
        if not hmac.compare_digest(row['password'] if row else '0'*64,password_hash) or not row: return respond({'error':'Usuario o contraseña incorrectos'},401)
        token=create_session(c,row['id']); c.commit()
        return session_response(token)

    @route('/login', method='POST', public=True)
    def login(c,u,b): return authenticate(c,u,b,'login')
    @route('/setup', method='POST', public=True)
    def setup(c,u,b): return authenticate(c,u,b,'setup')
    @route('/register', method='POST', public=True)
    def register(c,u,b): return authenticate(c,u,b,'register')

    @route('/logout', method='POST', logout=True)
    def logout(c,u,b):
        c.execute('DELETE FROM sessions WHERE token=?',(request.cookies.get('session',''),)); response=make_response(respond({'ok':True})); response.delete_cookie('session',path='/'); return response

    @route('/password/change', method='POST', password=True, immediate=True)
    def password_change(c,u,b):
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        credentials=c.execute('SELECT password,salt,blocked FROM users WHERE id=?',(u['id'],)).fetchone()
        if credentials['blocked']: return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
        current=b.get('current_password','');new=b.get('new_password','');confirmation=b.get('confirm_password','')
        if not all(isinstance(value,str) for value in (current,new,confirmation)): raise ValueError('Contraseña inválida')
        if len(new)<8: raise ValueError('La nueva contraseña debe tener al menos 8 caracteres')
        if new!=confirmation: raise ValueError('Las contraseñas nuevas no coinciden')
        if not hmac.compare_digest(credentials['password'],deps['hashpw'](current,credentials['salt'])): raise ValueError('La contraseña actual es incorrecta')
        if hmac.compare_digest(credentials['password'],deps['hashpw'](new,credentials['salt'])): raise ValueError('La nueva contraseña debe ser distinta de la actual')
        salt=secrets.token_hex(16);c.execute('UPDATE users SET password=?,salt=?,must_change_password=0 WHERE id=?',(deps['hashpw'](new,salt),salt,u['id']))
        token=rotate_session(c,u['id']);c.commit()
        return session_response(token)

    @route('/avatar/delete', method='POST')
    def avatar_delete(c,u,b):
        import avatars
        avatars.remove(c,u['id'],deps['SETTINGS']['backblaze'])
        return respond({'ok':True})

    def operation(rule, handler, *, immediate=False, validate=False):
        # Each URL gets its own Flask rule; legacy operation modules still accept the URL.
        def view(c,u,b): return handler(c,u,'/api'+rule,b)
        view.__name__='post_'+rule.strip('/').replace('/','_').replace('-','_')
        route(rule,method='POST',immediate=immediate,notices='validate' if validate else True)(view)

    def workspace(c,u,path,b): return workspace_routes.post(c,u,path,b)
    def project(c,u,path,b): return project_routes.post(c,u,path,b)
    def task_types(c,u,path,b): return task_routes.post_task_types(c,u,path,b)
    def tasks(c,u,path,b): return task_routes.post_tasks(c,u,path,b)
    def comment(c,u,path,b): return comment_routes.post(c,u,path,b)
    def automation(c,u,path,b): return automation_routes.post(c,u,path,b)
    def token(c,u,path,b): return deps['post_tokens'](c,u,path,b)
    for rule,handler,immediate,validate in (
        ('/workspace/update',workspace,False,False),('/workspace/block',workspace,True,True),('/profile',workspace,False,False),('/users',workspace,False,False),
        ('/projects',project,False,False),('/projects/update',project,False,False),('/members',project,False,False),('/columns',project,False,False),('/columns/rename',project,False,False),('/columns/update',project,False,False),('/columns/reorder',project,False,False),('/columns/delete',project,True,False),
        ('/tasks',task_types,True,False),('/task-types',task_types,False,False),('/task-types/delete',task_types,True,True),('/task-types/update',task_types,True,True),('/task/type-status',task_types,True,False),
        ('/task/delete',tasks,False,False),('/task/update',tasks,True,False),('/task/move',tasks,True,False),('/task/project',tasks,False,False),
        ('/comments',comment,False,False),('/comments/update',comment,False,False),
        ('/automations/save',automation,True,False),('/automations/delete',automation,True,False),('/automations/reorder',automation,True,False),
        ('/tokens',token,True,False),('/tokens/revoke',token,True,False),
    ): operation(rule,handler,immediate=immediate,validate=validate)

    @route('/<path:unused>', public=True)
    def unknown_get(c,u,b,unused):
        if not u: return respond({'error':'Inicia sesión'},401)
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
        if flags['blocked']: return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
        if flags['must_change_password']: return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
        return respond({'error':'No encontrado'},404)

    @route('/<path:unused>', method='POST')
    def unknown_post(c,u,b,unused): return respond({'error':'No encontrado'},404)

    app.register_blueprint(api)
