from validation import integer_field
from permissions import can_task
from task_domain import now, record
from web_helpers import respond

def create_comment(c,u,b):
    tid = integer_field(b['task_id'])
    body = str(b.get('body', '')).strip()
    if not can_task(c, u, tid, True):
        raise PermissionError('Sin permiso para comentar')
    if not body:
        raise ValueError('Escribe un comentario')
    if len(body) > 10000:
        raise ValueError('El comentario es demasiado largo')
    c.execute('INSERT INTO comments(task_id,user_id,body,created_at) VALUES(?,?,?,?)', (tid, u['id'], body, now()))
    import slack_notify
    slack_notify.enqueue(c, 'comment', tid, u['id'])


def update_comment(c,u,b):
    comment = c.execute('SELECT id,task_id,user_id,body FROM comments WHERE id=?', (integer_field(b['id']),)).fetchone()
    if not comment:
        return respond({'error': 'Comentario no encontrado'}, 404)
    if comment['user_id'] != u['id'] or not can_task(c, u, comment['task_id'], True):
        raise PermissionError('Sin permiso para editar este comentario')
    body = b.get('body')
    if not isinstance(body, str) or not body.strip():
        raise ValueError('Escribe un comentario')
    body = body.strip()
    if len(body) > 10000:
        raise ValueError('El comentario es demasiado largo')
    if body != comment['body']:
        c.execute('UPDATE comments SET body=? WHERE id=?', (body, comment['id']))
        record(c, comment['task_id'], u['id'], 'editó un comentario')
    return respond({'ok': True, 'body': body})
