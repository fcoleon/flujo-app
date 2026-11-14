"""Cola Slack independiente. El llamador debe invocar init y enqueue en su transacción.

Resumen: slack_digest_settings(installation_id,user_id,enabled,weekdays,hour,minute).
weekdays es JSON con días 0-6 (lunes-domingo). El worker requiere
app.SETTINGS['slack_base_url'].
"""
import fcntl
import json
import logging
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import app

LOG = logging.getLogger(__name__)
KINDS = ('assignment', 'comment', 'completion')


def init(c):
    if 'digest' not in {r[1] for r in c.execute('PRAGMA table_info(slack_preferences)')}:
        c.execute('ALTER TABLE slack_preferences ADD COLUMN digest INTEGER NOT NULL DEFAULT 0 CHECK(digest IN (0,1))')
    c.execute('''CREATE TABLE IF NOT EXISTS slack_digest_settings(
        installation_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        weekdays TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
        hour INTEGER NOT NULL DEFAULT 9 CHECK(hour BETWEEN 0 AND 23),
        minute INTEGER NOT NULL DEFAULT 0 CHECK(minute BETWEEN 0 AND 59),
        PRIMARY KEY(installation_id,user_id), FOREIGN KEY(installation_id,user_id)
        REFERENCES slack_accounts(installation_id,user_id) ON DELETE CASCADE)''')
    columns = {r[1] for r in c.execute('PRAGMA table_info(slack_digest_settings)')}
    if 'minute' not in columns:
        c.execute('ALTER TABLE slack_digest_settings ADD COLUMN minute INTEGER NOT NULL DEFAULT 0 CHECK(minute BETWEEN 0 AND 59)')
    if 'weekdays' not in columns:
        c.execute("ALTER TABLE slack_digest_settings ADD COLUMN weekdays TEXT NOT NULL DEFAULT '[0,1,2,3,4]'")
        if 'weekday' in columns:
            c.execute("UPDATE slack_digest_settings SET weekdays=CASE WHEN weekday IS NULL THEN '[0,1,2,3,4,5,6]' ELSE '[' || weekday || ']' END")
    c.execute('''CREATE TABLE IF NOT EXISTS slack_queue(
        id INTEGER PRIMARY KEY AUTOINCREMENT, installation_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, team_id TEXT NOT NULL, slack_user_id TEXT NOT NULL,
        kind TEXT NOT NULL, task_id INTEGER, actor_id INTEGER, digest_day TEXT,
        available_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        count INTEGER NOT NULL DEFAULT 1, error TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS slack_queue_ready ON slack_queue(state,available_at,id)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS slack_queue_digest ON slack_queue(installation_id,user_id,digest_day) WHERE digest_day IS NOT NULL')


def _now():
    return datetime.now(timezone.utc)


def _stamp(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _eligible(c, user, installation, task, kind):
    if user['blocked'] or not app.can_task(c, user, task['id']):
        return False
    projects = app.task_projects(c, task['id'])
    if projects:
        projects = [p for p in projects if app.role(c, user, p) in
                    (('admin',) if kind == 'completion' else ('admin', 'editor', 'viewer'))]
    else:
        projects = [] if kind == 'completion' else [None]
    return any(c.execute(f'''SELECT 1 FROM slack_preferences WHERE installation_id=?
        AND user_id=? AND project_id IS ? AND {kind}=1''',
        (installation, user['id'], p)).fetchone() for p in projects)


def enqueue(c, kind, task_id, actor_id):
    """Encola después del cambio, sin commit; devuelve el número de destinatarios.

    assignment y comment avisan al responsable; completion a administradores
    de proyecto. test usa task_id=None y actor_id como destinatario de prueba.
    """
    if kind not in (*KINDS, 'test'):
        raise ValueError('Evento Slack inválido')
    if kind == 'test':
        if task_id is not None:
            raise ValueError('La prueba no admite tarea')
        account = c.execute('''SELECT a.*,i.team_id FROM slack_accounts a
            JOIN slack_installations i ON i.id=a.installation_id
            JOIN users u ON u.id=a.user_id WHERE a.user_id=? AND u.blocked=0''', (actor_id,)).fetchone()
        if not account:
            return 0
        stamp = _stamp(_now())
        c.execute('''INSERT INTO slack_queue(installation_id,user_id,team_id,slack_user_id,kind,actor_id,
            available_at,created_at,updated_at) VALUES(?,?,?,?,'test',?,?,?,?)''',
            (account['installation_id'], actor_id, account['team_id'], account['slack_user_id'], actor_id, stamp, stamp, stamp))
        return 1
    task = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task:
        return 0
    now = _now(); stamp = _stamp(now); count = 0
    for account in c.execute('''SELECT u.*,a.installation_id,a.slack_user_id,i.team_id
        FROM slack_accounts a JOIN users u ON u.id=a.user_id
        JOIN slack_installations i ON i.id=a.installation_id''').fetchall():
        if account['id'] == actor_id or (kind in ('assignment', 'comment') and task['assignee_id'] != account['id']):
            continue
        if not _eligible(c, account, account['installation_id'], task, kind):
            continue
        if kind == 'comment':
            existing = c.execute('''SELECT id FROM slack_queue WHERE state='pending' AND kind='comment'
                AND task_id=? AND user_id=? AND installation_id=? AND team_id=? AND slack_user_id=?
                AND available_at>? ORDER BY id LIMIT 1''',
                (task_id, account['id'], account['installation_id'], account['team_id'], account['slack_user_id'], stamp)).fetchone()
            if existing:
                c.execute('UPDATE slack_queue SET count=count+1,updated_at=? WHERE id=?', (stamp, existing['id']))
                count += 1
                continue
        c.execute('''INSERT INTO slack_queue(installation_id,user_id,team_id,slack_user_id,kind,task_id,actor_id,
            available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (account['installation_id'], account['id'], account['team_id'], account['slack_user_id'], kind,
             task_id, actor_id, _stamp(now + timedelta(minutes=5) if kind == 'comment' else now), stamp, stamp))
        count += 1
    return count


def _digest_tasks(c, user, today):
    tasks = c.execute('''SELECT t.* FROM tasks t JOIN task_statuses s ON s.id=t.status_id
        WHERE t.assignee_id=? AND s.is_done=0''', (user['id'],)).fetchall()
    tasks = [t for t in tasks if app.can_task(c, user, t['id'])]
    def order(t):
        due = t['due_date'] or ''
        group = 0 if due and due < today else 1 if due == today else 2 if t['priority'] == 'Alta' else 3
        return group, due or '9999-99-99', {'Alta': 0, 'Normal': 1, 'Baja': 2}.get(t['priority'], 3), t['id']
    return sorted(tasks, key=order)


def _scheduled(setting, local):
    try:
        days = json.loads(setting['weekdays'])
    except (ValueError, TypeError):
        return False
    return (isinstance(days, list) and all(type(day) is int and 0 <= day <= 6 for day in days)
            and local.weekday() in days and (local.hour, local.minute) >= (setting['hour'], setting['minute']))


def _schedule(c, now):
    local = now.astimezone(app.STATS_ZONE)
    for row in c.execute('''SELECT d.*,a.slack_user_id,i.team_id FROM slack_digest_settings d
        JOIN slack_accounts a USING(installation_id,user_id) JOIN slack_installations i ON i.id=d.installation_id
        WHERE d.enabled=1''').fetchall():
        if not _scheduled(row, local):
            continue
        user = c.execute('SELECT * FROM users WHERE id=? AND blocked=0', (row['user_id'],)).fetchone()
        if not user or not _digest_tasks(c, user, local.date().isoformat()):
            continue
        stamp = _stamp(now)
        c.execute('''INSERT OR IGNORE INTO slack_queue(installation_id,user_id,team_id,slack_user_id,kind,digest_day,
            available_at,created_at,updated_at) VALUES(?,?,?,?,'digest',?,?,?,?)''',
            (row['installation_id'], row['user_id'], row['team_id'], row['slack_user_id'], local.date().isoformat(), stamp, stamp, stamp))


def _base_url():
    base = app.SETTINGS.get('slack_base_url')
    try:
        if not isinstance(base, str) or any(ord(ch) <= 32 or ord(ch) == 127 or ch in '<>|\\' for ch in base):
            raise ValueError()
        base = base.rstrip('/')
        parts = urlsplit(base)
        if parts.scheme != 'https' or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError()
        parts.port  # valida también el puerto
    except ValueError:
        raise ValueError('Configura app.SETTINGS["slack_base_url"] con una URL HTTPS segura') from None
    return base


def _message(c, row):
    account = c.execute('''SELECT u.*,a.slack_user_id,i.team_id,i.bot_token FROM users u
        JOIN slack_accounts a ON a.user_id=u.id JOIN slack_installations i ON i.id=a.installation_id
        WHERE u.id=? AND a.installation_id=? AND u.blocked=0''', (row['user_id'], row['installation_id'])).fetchone()
    if not account or account['team_id'] != row['team_id'] or account['slack_user_id'] != row['slack_user_id']:
        return None
    if row['kind'] == 'test':
        text = 'Mensaje de prueba de Slack.'
    elif row['kind'] == 'digest':
        setting = c.execute('SELECT * FROM slack_digest_settings WHERE installation_id=? AND user_id=? AND enabled=1', (row['installation_id'], row['user_id'])).fetchone()
        local = _now().astimezone(app.STATS_ZONE)
        if not setting or row['digest_day'] != local.date().isoformat() or not _scheduled(setting, local):
            return None
        tasks = _digest_tasks(c, account, local.date().isoformat())
        if not tasks:
            return None
        lines = []
        for task in tasks[:5]:
            projects = app.task_projects(c, task['id'])
            names = [r['name'] for r in c.execute('SELECT id,name FROM projects ORDER BY name,id')
                     if r['id'] in projects and app.role(c, account, r['id'])]
            lines.append(_task_text(task) + ' — ' + (', '.join(_escape(n) for n in names) if names else 'Sin proyecto'))
        text = f'Resumen de tus tareas pendientes ({_escape(str(app.STATS_ZONE))}):\n' + '\n'.join(lines)
        text += f'\nRestantes: {max(0, len(tasks)-5)}\n<{_base_url()}/my-tasks|Ver mis tareas>'
    else:
        task = c.execute('''SELECT t.*,s.is_done FROM tasks t LEFT JOIN task_statuses s ON s.id=t.status_id
            WHERE t.id=?''', (row['task_id'],)).fetchone()
        if not task or not _eligible(c, account, row['installation_id'], task, row['kind']):
            return None
        if row['kind'] in ('assignment', 'comment') and task['assignee_id'] != row['user_id']:
            return None
        if row['kind'] == 'completion' and not task['is_done']:
            return None
        label = {'assignment': 'Te asignaron una tarea', 'comment': 'Nuevos comentarios', 'completion': 'Tarea completada'}[row['kind']]
        text = label + (f" ({row['count']})" if row['kind'] == 'comment' and row['count'] > 1 else '') + ': ' + _task_text(task)
    return account['bot_token'], account['slack_user_id'], text


def _task_text(task):
    return '<' + _base_url() + '/t/' + quote(task['public_id'], safe='') + '|' + _escape(task['title']) + '>'


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(token, channel, text):
    request = Request('https://slack.com/api/chat.postMessage',
        data=json.dumps({'channel': channel, 'text': text, 'unfurl_links': False}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json; charset=utf-8'})
    try:
        with build_opener(_NoRedirect).open(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code == 429:
            try:
                delay = max(1, min(86400, int(exc.headers.get('Retry-After', '60'))))
            except (ValueError, TypeError, AttributeError):
                delay = 60
            return 'retry', delay, 'rate_limited'
        return ('failed' if 400 <= exc.code < 500 and exc.code != 408 else 'uncertain'), 0, 'http_' + str(exc.code)
    except (URLError, OSError, ValueError):
        return 'uncertain', 0, 'network_or_invalid_response'
    if not isinstance(result, dict):
        return 'uncertain', 0, 'invalid_response'
    if result.get('ok') is True:
        return 'sent', 0, None
    error = str(result.get('error', 'invalid_response'))[:100]
    if error in ('ratelimited', 'rate_limited'):
        return 'retry', 60, error
    if error in ('internal_error', 'fatal_error', 'request_timeout', 'service_unavailable', 'invalid_response'):
        return 'uncertain', 0, error
    return 'failed', 0, error


def run_once():
    """Una pasada exclusiva; nunca mantiene una transacción durante HTTP."""
    # ponytail: flock coordina procesos en un solo host; usar un coordinador externo para varios hosts.
    with open(app.DB + '.slack-worker.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        with closing(app.db()) as c:
            now = _now(); stamp = _stamp(now)
            with c:
                c.execute("UPDATE slack_queue SET state='uncertain',error='worker_interrupted',updated_at=? WHERE state='sending'", (stamp,))
                c.execute("DELETE FROM slack_queue WHERE state NOT IN ('pending','sending') AND updated_at<?", (_stamp(now - timedelta(days=30)),))
                c.execute("UPDATE slack_queue SET state='failed',error='expired',updated_at=? WHERE state='pending' AND created_at<?", (stamp, _stamp(now - timedelta(days=7))))
                _schedule(c, now)
            processed = 0
            # Límite por pasada evita monopolizar el worker con una cola grande.
            for _ in range(100):
                with c:
                    stamp = _stamp(_now())
                    row = c.execute("""SELECT * FROM slack_queue q WHERE state='pending' AND available_at<=?
                        AND NOT EXISTS (SELECT 1 FROM slack_queue r WHERE r.installation_id=q.installation_id
                            AND r.error IN ('rate_limited','ratelimited','retry_limit') AND r.available_at>?)
                        ORDER BY id LIMIT 1""", (stamp, stamp)).fetchone()
                    if not row:
                        break
                    try:
                        message = _message(c, row)
                    except ValueError:
                        LOG.error('Configuración de URL Slack inválida; se conservan los avisos pendientes')
                        break
                    if not message:
                        c.execute("UPDATE slack_queue SET state='cancelled',updated_at=? WHERE id=?", (_stamp(_now()), row['id']))
                        processed += 1
                        continue
                    c.execute("UPDATE slack_queue SET state='sending',attempts=attempts+1,updated_at=? WHERE id=?", (_stamp(_now()), row['id']))
                state, delay, error = _send(*message)
                with c:
                    if state == 'retry' and row['attempts'] + 1 >= 8:
                        state, error = 'failed', 'retry_limit'
                    c.execute("UPDATE slack_queue SET state=?,error=?,available_at=?,updated_at=? WHERE id=? AND state='sending'",
                        ('pending' if state == 'retry' else state, error, _stamp(_now() + timedelta(seconds=delay)), _stamp(_now()), row['id']))
                    if state == 'retry':
                        c.execute("UPDATE slack_queue SET available_at=MAX(available_at,?) WHERE state='pending' AND installation_id=?", (_stamp(_now() + timedelta(seconds=delay)), row['installation_id']))
                processed += 1
                if state == 'retry':
                    break
            return processed


def main():
    logging.basicConfig(level=logging.INFO)
    with closing(app.db()) as c, c:
        init(c)
    while True:
        try:
            import slack_auth
            with app.app.app_context():
                slack_auth.retry_revocations()
            run_once()
        except Exception:
            LOG.exception('Error en worker Slack')
        time.sleep(30)


if __name__ == '__main__':
    main()
