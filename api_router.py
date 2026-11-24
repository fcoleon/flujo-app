"""Explicit JSON API routes and common request handling."""
import hmac
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import wraps
from typing import Callable

from flask import Blueprint, g, make_response, request

import automation_routes
import automations
import comment_routes
import project_routes
import task_routes
import workspace_routes
from auth import create_session, current_user, hashpw, rotate_session, session_response
from mcp_tokens import mcp_token_hash, mcp_tokens
from permissions import role, can_task
from task_domain import now
from validation import text_field
from web_helpers import respond


@dataclass(frozen=True)
class ApiServices:
    db: Callable
    settings: dict
    version: str | None
    data_response: Callable
    analytics: Callable


def register_api(app, services):
    """Register the JSON API with its explicit application services."""
    api = Blueprint('json_api', __name__, url_prefix='/api')

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
                    with closing(services.db()) as c, c:
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
                        pending=[item for item in g.disabled_automations if role(c,u,item['project_id']) in ('admin','editor')]
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
        return respond({'user':dict(u) if u else None,'blocked':bool(flags and flags['blocked']),'must_change_password':bool(flags and flags['must_change_password']),'allow_self_registration':services.settings['allow_self_registration'],'needs_setup':not bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())})

    @route('/revision')
    def revision(c,u,b):
        return respond({'revision':c.execute('SELECT revision FROM data_revision WHERE id=1').fetchone()[0],'version':services.version})

    @route('/tokens')
    def tokens(c,u,b): return respond({'tokens':mcp_tokens(c,u['id'])})

    @route('/data', snapshot=True)
    def data(c,u,b):
        return services.data_response(c,u)

    @route('/analytics')
    def analytics(c,u,b):
        params=request.args.to_dict(flat=False); p=int(params['project_id'][0]); month=params['month'][0]
        if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return respond({'error':'Proyecto no encontrado'},404)
        if role(c,u,p)!='admin': return respond({'error':'Solo los administradores del proyecto pueden ver estadísticas'},403)
        type_filter=int(params['type_id'][0]) if params.get('type_id') else None
        return respond(services.analytics(c,p,month,type_filter))

    @route('/automations')
    def automation_list(c,u,b): return automation_routes.list_rules(c,u)

    @route('/automations/runs')
    def automation_runs(c,u,b): return automation_routes.list_runs(c,u)

    @route('/comments/<path:task_id>')
    def comments(c,u,b,task_id):
        tid=int(task_id.split('/')[-1])
        if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
        return respond([dict(x) for x in c.execute('SELECT cm.*,COALESCE(u.name,"Usuario eliminado") AS author FROM comments cm LEFT JOIN users u ON u.id=cm.user_id WHERE task_id=? ORDER BY cm.id',(tid,))])

    @route('/history/<path:task_id>')
    def history(c,u,b,task_id):
        tid=int(task_id.split('/')[-1])
        if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
        return respond([dict(x) for x in c.execute('''SELECT h.*,CASE WHEN h.user_id IS NULL AND h.message='inicio del registro de tiempo por estado' THEN 'Sistema' ELSE COALESCE(u.name,'Usuario eliminado') END AS actor FROM history h LEFT JOIN users u ON u.id=h.user_id WHERE task_id=? ORDER BY h.id DESC''',(tid,))])

    def authenticate(c,u,b,kind):
        if kind=='register' and not services.settings['allow_self_registration']: return respond({'error':'El registro está deshabilitado'},403)
        username=text_field(b,'username').strip().lower(); pw=text_field(b,'password')
        if kind in ('setup','register'):
            c.execute('BEGIN IMMEDIATE')
            exists=bool(c.execute('SELECT 1 FROM users').fetchone())
            if kind=='setup' and exists: raise ValueError('La aplicación ya está configurada')
            if kind=='register' and not exists: raise ValueError('Primero se debe crear la cuenta administradora')
            name=text_field(b,'name').strip()
            if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
            s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin) VALUES(?,?,?,?,?)',(name,username,hashpw(pw,s),s,int(kind=='setup')))
        row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        password_hash=hashpw(pw,row['salt'] if row else '00'*16)
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
        if not hmac.compare_digest(credentials['password'],hashpw(current,credentials['salt'])): raise ValueError('La contraseña actual es incorrecta')
        if hmac.compare_digest(credentials['password'],hashpw(new,credentials['salt'])): raise ValueError('La nueva contraseña debe ser distinta de la actual')
        salt=secrets.token_hex(16);c.execute('UPDATE users SET password=?,salt=?,must_change_password=0 WHERE id=?',(hashpw(new,salt),salt,u['id']))
        token=rotate_session(c,u['id']);c.commit()
        return session_response(token)

    @route('/avatar/delete', method='POST')
    def avatar_delete(c,u,b):
        import avatars
        avatars.remove(c,u['id'],services.settings['backblaze'])
        return respond({'ok':True})

    def operation(rule, handler, *, immediate=False, validate=False):
        def view(c,u,b): return handler(c,u,b)
        view.__name__='post_'+rule.strip('/').replace('/','_').replace('-','_')
        route(rule,method='POST',immediate=immediate,notices='validate' if validate else True)(view)

    def create_token(c,u,b):
        name=b.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>60:
            raise ValueError('El nombre debe tener entre 1 y 60 caracteres')
        token='flj_'+secrets.token_urlsafe(32)
        tid=c.execute('INSERT INTO mcp_tokens(user_id,name,token_hash,created_at) VALUES(?,?,?,?)',
                      (u['id'],name.strip(),mcp_token_hash(token),now())).lastrowid
        return respond({'id':tid,'name':name.strip(),'token':token})

    def revoke_token(c,u,b):
        tid=automations.ident(b.get('id'))
        if not c.execute('UPDATE mcp_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL',
                         (now(),tid,u['id'])).rowcount: raise ValueError('Token no encontrado')
    for rule,handler,immediate,validate in (
        ('/workspace/update',workspace_routes.update_workspace,False,False),('/workspace/block',workspace_routes.block_user,True,True),('/profile',workspace_routes.update_profile,False,False),('/users',workspace_routes.create_user,False,False),
        ('/projects',project_routes.create_project,False,False),('/projects/update',project_routes.update_project,False,False),('/members',project_routes.change_member,False,False),('/columns',project_routes.create_column,False,False),('/columns/rename',project_routes.update_column,False,False),('/columns/update',project_routes.update_column,False,False),('/columns/reorder',project_routes.reorder_columns,False,False),('/columns/delete',project_routes.delete_column,True,False),
        ('/tasks',task_routes.create_task,True,False),('/task-types',task_routes.create_task_type,False,False),('/task-types/delete',task_routes.delete_task_type,True,True),('/task-types/update',task_routes.update_task_type,True,True),('/task/type-status',task_routes.change_task_type_status,True,False),
        ('/task/delete',task_routes.delete_task_request,False,False),('/task/update',task_routes.update_task,True,False),('/task/move',task_routes.move_task,True,False),('/task/project',task_routes.change_task_project,False,False),
        ('/comments',comment_routes.create_comment,False,False),('/comments/update',comment_routes.update_comment,False,False),
        ('/automations/save',automation_routes.save_rule,True,False),('/automations/delete',automation_routes.delete_rule,True,False),('/automations/reorder',automation_routes.reorder_rules,True,False),
        ('/tokens',create_token,True,False),('/tokens/revoke',revoke_token,True,False),
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
