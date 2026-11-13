"""Servidor MCP de solo lectura de Flujo.

Se ejecuta como proceso propio (uvicorn sobre un socket Unix) y expone el
endpoint Streamable HTTP que Nginx enruta a /mcp. Cada petición debe traer un
token personal en `Authorization: Bearer`; los datos que devuelven las tools
son los del usuario dueño de ese token, calculados con las mismas `role()` y
`can_task()` que usa la aplicación web.
"""
import contextvars
import json
import re
from contextlib import closing
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from typing import Annotated

import app

MAX_LIMIT = 100
CURRENT_USER = contextvars.ContextVar('flujo_mcp_user')

mcp = MCPServer(name='flujo',
                instructions='Herramientas de solo lectura para consultar proyectos y tareas de Flujo. '
                             'Solo se ven los datos a los que tiene acceso el usuario dueño del token.')


def user():
    try:
        return CURRENT_USER.get()
    except LookupError:
        raise ToolError('No hay un usuario autenticado')


async def reply(send, status, payload, headers=()):
    body = json.dumps(payload).encode()
    sent = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())] + list(headers)
    await send({'type': 'http.response.start', 'status': status, 'headers': sent})
    await send({'type': 'http.response.body', 'body': body})


class BearerAuth:
    """Exige un token personal válido en cada petición HTTP y fija su usuario."""

    def __init__(self, asgi):
        self.app = asgi

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        authorization = dict(scope.get('headers') or {}).get(b'authorization', b'').decode('latin-1')
        scheme, _, token = authorization.partition(' ')
        u = reason = None
        if scheme.lower() == 'bearer' and token.strip():
            # mcp_token_user escribe last_used_at: la transacción se confirma aquí.
            with closing(app.db()) as c, c:
                u, reason = app.mcp_token_user(c, token.strip())
        if u is None:
            return await reply(send, 401, {'error': 'Token inválido o revocado'},
                               [(b'www-authenticate', b'Bearer realm="flujo"')])
        if reason == 'blocked':
            return await reply(send, 403, {'error': 'Has sido bloqueado de este workspace, contacta a tu admin para más información', 'blocked': True})
        if reason == 'password':
            return await reply(send, 403, {'error': 'Debes cambiar tu contraseña temporal', 'password_change_required': True})
        state = CURRENT_USER.set(u)
        try:
            return await self.app(scope, receive, send)
        finally:
            CURRENT_USER.reset(state)


def project_row(c, ref):
    text = str(ref).strip()
    if re.fullmatch(r'[0-9a-fA-F-]{36}', text):
        return c.execute('SELECT * FROM projects WHERE public_id=?', (text,)).fetchone()
    if text.isdigit() and int(text) > 0:
        return c.execute('SELECT * FROM projects WHERE id=?', (int(text),)).fetchone()
    return None


def task_row(c, ref):
    text = str(ref).strip()
    if re.fullmatch(r'[0-9a-fA-F-]{36}', text):
        return c.execute('SELECT * FROM tasks WHERE public_id=?', (text,)).fetchone()
    if text.isdigit() and int(text) > 0:
        return c.execute('SELECT * FROM tasks WHERE id=?', (int(text),)).fetchone()
    return None


def readable(c, u, tid):
    if not app.can_task(c, u, tid):
        raise ToolError('Tarea no encontrada')
    return c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()


def task_summary(c, t):
    status = c.execute('SELECT name,is_done FROM task_statuses WHERE id=?', (t['status_id'],)).fetchone() if t['status_id'] else None
    kind = c.execute('SELECT name FROM task_types WHERE id=?', (t['type_id'],)).fetchone() if t['type_id'] else None
    who = c.execute('SELECT name,username FROM users WHERE id=?', (t['assignee_id'],)).fetchone() if t['assignee_id'] else None
    projects = [r['name'] for r in c.execute('''SELECT p.name FROM placements pl JOIN projects p ON p.id=pl.project_id
                                                WHERE pl.task_id=? ORDER BY p.name''', (t['id'],))]
    return {'id': t['id'], 'public_id': t['public_id'], 'title': t['title'], 'priority': t['priority'],
            'due_date': t['due_date'], 'created_at': t['created_at'], 'completed_at': t['completed_at'],
            'status': {'name': status['name'], 'done': bool(status['is_done'])} if status else None,
            'type': kind['name'] if kind else None,
            'assignee': {'name': who['name'], 'username': who['username']} if who else None,
            'projects': projects}


@mcp.tool()
async def list_projects() -> dict:
    """Lista los proyectos a los que tienes acceso, con tu rol en cada uno (admin, editor o viewer)."""
    u = user()
    projects = []
    with closing(app.db()) as c:
        for p in c.execute('SELECT * FROM projects ORDER BY name'):
            access = app.role(c, u, p['id'])
            if not access:
                continue
            count = c.execute('''SELECT COUNT(*) FROM tasks t JOIN placements pl ON pl.task_id=t.id
                                 WHERE pl.project_id=?''', (p['id'],)).fetchone()[0]
            projects.append({'id': p['id'], 'public_id': p['public_id'], 'name': p['name'], 'color': p['color'],
                             'description': p['description'], 'role': access, 'task_count': count})
    return {'projects': projects}


@mcp.tool()
async def get_project(project: str | int) -> dict:
    """Detalle de un proyecto por id o enlace público: columnas con sus tareas, tipos, miembros y tu rol."""
    u = user()
    with closing(app.db()) as c:
        p = project_row(c, project)
        # Un proyecto ajeno se informa como inexistente para no revelar que existe.
        access = app.role(c, u, p['id']) if p else None
        if not access:
            raise ToolError('Proyecto no encontrado')
        columns = []
        for col in c.execute('SELECT * FROM columns WHERE project_id=? ORDER BY position,id', (p['id'],)):
            item = dict(col)
            item['task_count'] = c.execute('SELECT COUNT(*) FROM placements WHERE project_id=? AND column_id=?',
                                           (p['id'], col['id'])).fetchone()[0]
            columns.append(item)
        return {'id': p['id'], 'public_id': p['public_id'], 'name': p['name'], 'color': p['color'],
                'description': p['description'], 'role': access, 'columns': columns,
                'types': [app.type_data(c, row) for row in c.execute('SELECT * FROM task_types WHERE project_id=? ORDER BY is_base DESC,id', (p['id'],))],
                'members': [dict(row) for row in c.execute('''SELECT u.id,u.name,u.username,m.role FROM members m
                                                               JOIN users u ON u.id=m.user_id
                                                               WHERE m.project_id=? ORDER BY u.name''', (p['id'],))]}


@mcp.tool()
async def list_tasks(project: str | int | None = None, assignee: str | None = None,
                     priority: str | None = None, due_before: str | None = None,
                     search: str | None = None, done: bool | None = None,
                     limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 30) -> dict:
    """Lista tareas que puedes ver, de más próxima fecha a más antigua.

    Filtros opcionales: `project` (id o enlace público, solo proyectos donde eres miembro),
    `assignee` (usuario o "me"), `priority` (Alta, Normal o Baja), `due_before` (fecha
    límite YYYY-MM-DD), `search` (texto en título o descripción), `done` (solo tareas
    finalizadas o en curso) y `limit` (máximo 100).
    """
    u = user()
    conditions, params = [], []
    check_access = True
    if project is not None:
        with closing(app.db()) as c:
            p = project_row(c, project)
            pid = p['id'] if p and app.role(c, u, p['id']) else None
        if pid is None:
            raise ToolError('Proyecto no encontrado')
        conditions.append('EXISTS(SELECT 1 FROM placements pl WHERE pl.task_id=t.id AND pl.project_id=?)')
        params.append(pid)
        # Con filtro de proyecto la pertenencia ya garantiza la lectura.
        check_access = False
    if assignee is not None:
        if assignee == 'me':
            params.append(u['id'])
        else:
            with closing(app.db()) as c:
                who = c.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', (str(assignee).strip(),)).fetchone()
            if not who:
                raise ToolError('Usuario no encontrado')
            params.append(who['id'])
        conditions.append('t.assignee_id=?')
    if priority is not None:
        if priority not in ('Alta', 'Normal', 'Baja'):
            raise ToolError('Prioridad inválida: usa Alta, Normal o Baja')
        conditions.append('t.priority=?')
        params.append(priority)
    if due_before is not None:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(due_before)):
            raise ToolError('Fecha inválida: usa YYYY-MM-DD')
        conditions.append('t.due_date IS NOT NULL AND t.due_date<=?')
        params.append(str(due_before))
    if search is not None:
        term = str(search).strip()
        if not term:
            raise ToolError('El texto de búsqueda no puede estar vacío')
        conditions.append('(t.title LIKE ? OR t.description LIKE ?)')
        params.extend([f'%{term[:100]}%'] * 2)
    if done is not None:
        conditions.append('t.status_id IN (SELECT id FROM task_statuses WHERE is_done=?)')
        params.append(int(done))
    sql = 'SELECT t.* FROM tasks t'
    if conditions:
        sql += ' WHERE ' + ' AND '.join(conditions)
    sql += ' ORDER BY (t.due_date IS NULL), t.due_date, t.id DESC'
    collected, has_more = [], False
    with closing(app.db()) as c:
        for t in c.execute(sql, params):
            if check_access and not app.can_task(c, u, t['id']):
                continue
            collected.append(task_summary(c, t))
            if len(collected) > limit:
                has_more = True
                break
    return {'tasks': collected[:limit], 'has_more': has_more}


@mcp.tool()
async def get_task(task: str | int) -> dict:
    """Detalle de una tarea por id o enlace público: descripción, estado, responsable, proyecto y subtareas."""
    u = user()
    with closing(app.db()) as c:
        t = readable(c, u, ident_task(c, task))
        item = task_summary(c, t)
        item['description'] = t['description']
        item['description_format'] = t['description_format']
        item['parent_id'] = t['parent_id']
        item['can_edit'] = app.can_task(c, u, t['id'], True)
        item['comment_count'] = c.execute('SELECT COUNT(*) FROM comments WHERE task_id=?', (t['id'],)).fetchone()[0]
        item['columns'] = [dict(row) for row in c.execute('''SELECT pl.project_id,pl.column_id,p.name AS project,c.name AS column
                                                              FROM placements pl JOIN projects p ON p.id=pl.project_id
                                                              JOIN columns c ON c.id=pl.column_id
                                                              WHERE pl.task_id=? ORDER BY p.name''', (t['id'],))]
        item['subtasks'] = [{'id': row['id'], 'public_id': row['public_id'], 'title': row['title']}
                            for row in c.execute('SELECT id,public_id,title FROM tasks WHERE parent_id=? ORDER BY id', (t['id'],))]
        return item


def ident_task(c, ref):
    t = task_row(c, ref)
    if not t:
        raise ToolError('Tarea no encontrada')
    return t['id']


@mcp.tool()
async def task_comments(task: str | int) -> dict:
    """Comentarios de una tarea que puedes ver, en orden cronológico."""
    u = user()
    with closing(app.db()) as c:
        t = readable(c, u, ident_task(c, task))
        rows = c.execute('''SELECT cm.id,cm.body,cm.created_at,u.name AS author FROM comments cm
                            LEFT JOIN users u ON u.id=cm.user_id WHERE cm.task_id=? ORDER BY cm.id''', (t['id'],)).fetchall()
        return {'comments': [dict(row) for row in rows]}


@mcp.tool()
async def task_history(task: str | int) -> dict:
    """Historial de cambios de una tarea que puedes ver, del más reciente al más antiguo."""
    u = user()
    with closing(app.db()) as c:
        t = readable(c, u, ident_task(c, task))
        rows = c.execute('''SELECT h.id,h.message,h.created_at,u.name AS actor FROM history h
                            LEFT JOIN users u ON u.id=h.user_id WHERE h.task_id=? ORDER BY h.id DESC''', (t['id'],)).fetchall()
        return {'history': [dict(row) for row in rows]}


def asgi_app():
    """Aplicación ASGI del endpoint /mcp con autenticación por token."""
    # shortcut: sin protección DNS-rebinding del SDK (Nginx y el Bearer son la barrera),
    # activar cuando el servidor se exponga en TCP directo a navegador.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return BearerAuth(mcp.streamable_http_app(streamable_http_path='/mcp', json_response=True,
                                              stateless_http=True, transport_security=security))


def socket_path():
    """Socket Unix del servidor, junto al de la aplicación para compartir permisos."""
    return Path(app.SETTINGS['socket']).with_name('mcp.sock')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(asgi_app(), uds=str(socket_path()), log_level='info')
