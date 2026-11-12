import hashlib
import base64
import json
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import jwt
from flask import Blueprint, current_app, g, jsonify, redirect, request
from auth import current_user


blueprint = Blueprint('slack_auth', __name__)
TTL = 600


def init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS slack_oauth_states(
        state_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, user_id INTEGER NOT NULL,
        session_hash TEXT NOT NULL, expires INTEGER NOT NULL)''')
    columns = {row[1] for row in c.execute('PRAGMA table_info(slack_oauth_states)')}
    for name in ('browser_hash', 'nonce_hash'):
        if name not in columns:
            c.execute(f'ALTER TABLE slack_oauth_states ADD COLUMN {name} TEXT')
    c.execute('''CREATE TABLE IF NOT EXISTS slack_identity_tokens(
        installation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, token TEXT NOT NULL,
        PRIMARY KEY(installation_id,user_id), FOREIGN KEY(installation_id,user_id)
        REFERENCES slack_accounts(installation_id,user_id) ON DELETE CASCADE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS slack_revocations(
        token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, lease_until INTEGER NOT NULL DEFAULT 0,
        lease_id TEXT)''')
    columns = {row[1] for row in c.execute('PRAGMA table_info(slack_revocations)')}
    if 'lease_until' not in columns:
        c.execute('ALTER TABLE slack_revocations ADD COLUMN lease_until INTEGER NOT NULL DEFAULT 0')
    if 'lease_id' not in columns:
        c.execute('ALTER TABLE slack_revocations ADD COLUMN lease_id TEXT')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def settings():
    s = current_app.config['SLACK_SETTINGS']
    url = urlsplit(s['base_url'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/')):
        raise ValueError('Invalid Slack base URL')
    if not s['client_id'] or not s['client_secret']:
        raise ValueError('Missing Slack credentials')
    return s


def redirect_uri(s, kind):
    return s['base_url'].rstrip('/') + '/slack/callback/' + kind


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def slack_api(method, data=None, token=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = urlencode(data).encode() if data is not None else None
    req = Request('https://slack.com/api/' + method, data=body, headers=headers)
    with build_opener(NoRedirect).open(req, timeout=10) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ValueError('Slack request failed')
    return result


def verify_identity(id_token, access_token, client_id, nonce_hash):
    header = jwt.get_unverified_header(id_token)
    if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
        raise ValueError('Invalid token header')
    req = Request('https://slack.com/openid/connect/keys', headers={'Accept': 'application/json'})
    with build_opener(NoRedirect).open(req, timeout=10) as response:
        keys = json.load(response)['keys']
    matches = [key for key in keys if key.get('kid') == header['kid'] and key.get('kty') == 'RSA'
               and key.get('use', 'sig') == 'sig' and key.get('alg', 'RS256') == 'RS256']
    if len(matches) != 1:
        raise ValueError('Invalid signing key')
    claims = jwt.decode(id_token, jwt.PyJWK.from_dict(matches[0]).key, algorithms=['RS256'],
                        audience=client_id, issuer='https://slack.com',
                        options={'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce']})
    aud = claims['aud']
    if (not isinstance(claims['sub'], str) or not claims['sub'] or
            not isinstance(claims['nonce'], str) or not nonce_hash or
            not secrets.compare_digest(digest(claims['nonce']), nonce_hash) or
            (isinstance(aud, list) and len(aud) > 1 and claims.get('azp') != client_id) or
            ('azp' in claims and claims['azp'] != client_id)):
        raise ValueError('Invalid identity claims')
    if 'at_hash' in claims:
        expected = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest()[:16]).rstrip(b'=').decode()
        if not isinstance(claims['at_hash'], str) or not secrets.compare_digest(claims['at_hash'], expected):
            raise ValueError('Invalid access token hash')
    return claims


def queue_revocation(c, token, user_id):
    if token:
        c.execute('INSERT OR IGNORE INTO slack_revocations(token,user_id) VALUES(?,?)', (token, user_id))


def revoke_pending(user_id=None):
    with closing(current_app.config['SLACK_DB']()) as c:
        rows = c.execute('SELECT token FROM slack_revocations' + (' WHERE user_id=?' if user_id is not None else ''),
                         (user_id,) if user_id is not None else ()).fetchall()
        failed = False
        for row in rows:
            lease = secrets.token_urlsafe(24)
            with c:
                claimed = c.execute('''UPDATE slack_revocations SET lease_until=?,lease_id=?
                    WHERE token=? AND lease_until<?
                    AND NOT EXISTS (SELECT 1 FROM slack_installations WHERE bot_token=slack_revocations.token)
                    AND NOT EXISTS (SELECT 1 FROM slack_identity_tokens WHERE token=slack_revocations.token)''',
                                    (int(time.time()) + 60, lease, row['token'], int(time.time())))
            if not claimed.rowcount:
                failed = True
                continue
            try:
                result = slack_api('auth.revoke', {}, token=row['token'])
                if result.get('revoked') is not True:
                    raise ValueError('Revocation not confirmed')
            except Exception:
                failed = True
                with c:
                    c.execute('UPDATE slack_revocations SET lease_until=0,lease_id=NULL WHERE token=? AND lease_id=?', (row['token'], lease))
                continue
            with c:
                c.execute('DELETE FROM slack_revocations WHERE token=? AND lease_id=?', (row['token'], lease))
        return not failed


def retry_revocations():
    """Reintenta las revocaciones pendientes; requiere un contexto de aplicación Flask.

    El worker debe invocarla periódicamente. Mientras quede alguna pendiente se
    bloquean nuevos flujos OAuth para evitar revocar un token reutilizado.
    """
    return revoke_pending()


def active(c, user_id, session_hash, admin=False):
    user = c.execute('SELECT admin,blocked,must_change_password FROM users WHERE id=?', (user_id,)).fetchone()
    if not user or user['blocked'] or user['must_change_password'] or (admin and not user['admin']):
        return False
    return any(secrets.compare_digest(digest(row[0]), session_hash) for row in
               c.execute('SELECT token FROM sessions WHERE user_id=?', (user_id,)))


def signed_in(c, admin=False):
    user = current_user(c)
    if not user:
        return None
    return user if active(c, user['id'], digest(request.cookies.get('session', '')), admin) else None


def error(message, status):
    response = jsonify({'error': message})
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


@blueprint.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if getattr(g, 'slack_cookie', None):
        response.delete_cookie(g.slack_cookie, path=g.slack_cookie_path, secure=True, httponly=True, samesite='Lax')
    return response


@blueprint.before_request
def require_json_post():
    if request.method == 'POST' and request.mimetype != 'application/json':
        return error('Se requiere application/json', 415)


@blueprint.get('/api/slack/status')
def status():
    try:
        settings()
        configured = True
    except (KeyError, ValueError, TypeError):
        configured = False
    with closing(current_app.config['SLACK_DB']()) as c:
        user = signed_in(c)
        if not user:
            return error('No autorizado', 403)
        installation = c.execute('SELECT team_id,team_name FROM slack_installations WHERE id=1').fetchone()
        account = c.execute('SELECT slack_user_id FROM slack_accounts WHERE installation_id=1 AND user_id=?', (user['id'],)).fetchone()
        preferences = []
        summary = {'enabled': False, 'hour': 9, 'weekdays': list(range(7))}
        if account:
            rows = c.execute('''SELECT p.project_id,p.assignment,p.comment,p.completion FROM slack_preferences p
                WHERE p.installation_id=1 AND p.user_id=? AND (p.project_id IS NULL OR ? OR EXISTS
                (SELECT 1 FROM members m WHERE m.project_id=p.project_id AND m.user_id=p.user_id))''',
                             (user['id'], user['admin'])).fetchall()
            preferences = [dict(row) for row in rows]
            for pref in preferences:
                for key in ('assignment', 'comment', 'completion'):
                    pref[key] = bool(pref[key])
            row = c.execute('SELECT enabled,hour,weekdays FROM slack_digest_settings WHERE installation_id=1 AND user_id=?', (user['id'],)).fetchone()
            if row:
                summary = {'enabled': bool(row['enabled']), 'hour': row['hour'], 'weekdays': json.loads(row['weekdays'])}
        return jsonify({'configured': configured, 'installation': dict(installation) if installation else None,
                        'account': dict(account) if account else None, 'preferences': preferences, 'summary': summary})


@blueprint.post('/api/slack/preferences')
def preferences():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('preferences'), list) or not isinstance(data.get('summary'), dict):
        return error('Preferencias inválidas', 400)
    summary = data['summary']
    days = summary.get('weekdays')
    if (type(summary.get('enabled')) is not bool or type(summary.get('hour')) is not int
            or not 0 <= summary['hour'] <= 23 or not isinstance(days, list)
            or any(type(day) is not int or not 0 <= day <= 6 for day in days)
            or len(set(days)) != len(days)):
        return error('Resumen inválido', 400)
    seen = set()
    for pref in data['preferences']:
        if not isinstance(pref, dict):
            return error('Preferencias inválidas', 400)
        project = pref.get('project_id')
        if ('project_id' not in pref or (project is not None and (type(project) is not int or project <= 0))
                or project in seen or any(type(pref.get(key)) is not bool for key in ('assignment', 'comment', 'completion'))):
            return error('Preferencias inválidas', 400)
        seen.add(project)
    with closing(current_app.config['SLACK_DB']()) as c:
        with c:
            c.execute('BEGIN IMMEDIATE')
            user = signed_in(c)
            if not user:
                return error('No autorizado', 403)
            if not c.execute('SELECT 1 FROM slack_accounts WHERE installation_id=1 AND user_id=?', (user['id'],)).fetchone():
                return error('Cuenta Slack no vinculada', 409)
            for pref in data['preferences']:
                project = pref['project_id']
                role = None
                if project is not None:
                    row = c.execute('''SELECT m.role FROM projects p LEFT JOIN members m
                        ON m.project_id=p.id AND m.user_id=? WHERE p.id=?''', (user['id'], project)).fetchone()
                    if not row or (not user['admin'] and not row['role']):
                        return error('Proyecto no autorizado', 403)
                    role = row['role']
                if pref['completion'] and (project is None or not (user['admin'] or role == 'admin')):
                    return error('Solo administradores pueden activar tareas completadas', 403)
            for pref in data['preferences']:
                values = (pref['assignment'], pref['comment'], pref['completion'], user['id'], pref['project_id'])
                updated = c.execute('''UPDATE slack_preferences SET assignment=?,comment=?,completion=?
                    WHERE installation_id=1 AND user_id=? AND project_id IS ?''', values)
                if not updated.rowcount:
                    c.execute('''INSERT INTO slack_preferences(assignment,comment,completion,user_id,project_id,installation_id)
                                 VALUES(?,?,?,?,?,1)''', values)
            c.execute('''INSERT INTO slack_digest_settings(installation_id,user_id,enabled,hour,weekdays) VALUES(1,?,?,?,?)
                         ON CONFLICT(installation_id,user_id) DO UPDATE SET enabled=excluded.enabled,hour=excluded.hour,weekdays=excluded.weekdays''',
                      (user['id'], summary['enabled'], summary['hour'], json.dumps(sorted(days))))
    return jsonify({'ok': True})


@blueprint.post('/api/slack/test')
def send_test():
    with closing(current_app.config['SLACK_DB']()) as c:
        with c:
            c.execute('BEGIN IMMEDIATE')
            user = signed_in(c)
            if not user:
                return error('No autorizado', 403)
            account = c.execute('''SELECT i.team_id,a.slack_user_id FROM slack_accounts a
                JOIN slack_installations i ON i.id=a.installation_id WHERE a.installation_id=1 AND a.user_id=?''', (user['id'],)).fetchone()
            if not account:
                return error('Cuenta Slack no vinculada', 409)
            stamp = datetime.now(timezone.utc).isoformat()
            c.execute('''INSERT INTO slack_queue(installation_id,user_id,team_id,slack_user_id,kind,task_id,available_at,created_at,updated_at)
                         VALUES(1,?,?,?,'test',NULL,?,?,?)''', (user['id'], account['team_id'], account['slack_user_id'], stamp, stamp, stamp))
    return jsonify({'ok': True})


@blueprint.post('/api/slack/<kind>/start')
def start(kind):
    if kind not in ('install', 'link'):
        return error('Ruta inválida', 404)
    try:
        s = settings()
        with closing(current_app.config['SLACK_DB']()) as c:
            with c:
                c.execute('BEGIN IMMEDIATE')
                user = signed_in(c, kind == 'install')
                if not user:
                    return error('No autorizado', 403)
                if c.execute('SELECT 1 FROM slack_revocations LIMIT 1').fetchone():
                    return error('Revocación Slack pendiente', 409)
                if kind == 'link' and not c.execute('SELECT 1 FROM slack_installations WHERE id=1').fetchone():
                    return error('Slack no está instalado', 409)
                state = secrets.token_urlsafe(32)
                browser = secrets.token_urlsafe(32)
                nonce = secrets.token_urlsafe(32) if kind == 'link' else None
                c.execute('DELETE FROM slack_oauth_states WHERE expires<=?', (int(time.time()),))
                if kind == 'install':
                    c.execute("DELETE FROM slack_oauth_states WHERE kind IN ('install','used:install')")
                c.execute('''INSERT INTO slack_oauth_states(state_hash,kind,user_id,session_hash,expires,browser_hash,nonce_hash)
                             VALUES(?,?,?,?,?,?,?)''',
                          (digest(state), kind, user['id'], digest(request.cookies['session']), int(time.time()) + TTL,
                           digest(browser), digest(nonce) if nonce else None))
        params = {'client_id': s['client_id'], 'redirect_uri': redirect_uri(s, kind), 'state': state}
        if kind == 'install':
            params['scope'] = 'chat:write'
            url = 'https://slack.com/oauth/v2/authorize?'
        else:
            params['scope'] = 'openid'
            params['response_type'] = 'code'
            params['nonce'] = nonce
            url = 'https://slack.com/openid/connect/authorize?'
        response = jsonify({'url': url + urlencode(params)})
        response.set_cookie('slack_oauth_' + digest(state), browser, max_age=TTL, secure=True, httponly=True,
                            samesite='Lax', path=urlsplit(s['base_url']).path.rstrip('/') + '/slack/callback')
        return response
    except (KeyError, ValueError):
        return error('Configuración Slack inválida', 500)


@blueprint.post('/api/slack/unlink')
@blueprint.post('/api/slack/disconnect')
def remove():
    disconnect = request.path.endswith('/disconnect')
    with closing(current_app.config['SLACK_DB']()) as c:
        with c:
            c.execute('BEGIN IMMEDIATE')
            user = signed_in(c, disconnect)
            if not user:
                return error('No autorizado', 403)
            stamp = datetime.now(timezone.utc).isoformat()
            if disconnect:
                for token in c.execute('SELECT token,user_id FROM slack_identity_tokens WHERE installation_id=1').fetchall():
                    queue_revocation(c, token['token'], token['user_id'])
                installation = c.execute('SELECT bot_token FROM slack_installations WHERE id=1').fetchone()
                if installation:
                    queue_revocation(c, installation['bot_token'], user['id'])
                c.execute("UPDATE slack_queue SET state='cancelled',updated_at=? WHERE installation_id=1 AND state IN ('pending','sending')", (stamp,))
                c.execute('DELETE FROM slack_installations WHERE id=1')
                c.execute('DELETE FROM slack_oauth_states')
            else:
                token = c.execute('SELECT token FROM slack_identity_tokens WHERE installation_id=1 AND user_id=?', (user['id'],)).fetchone()
                if token:
                    queue_revocation(c, token['token'], user['id'])
                c.execute("UPDATE slack_queue SET state='cancelled',updated_at=? WHERE installation_id=1 AND user_id=? AND state IN ('pending','sending')", (stamp, user['id']))
                c.execute('DELETE FROM slack_accounts WHERE installation_id=1 AND user_id=?', (user['id'],))
                c.execute("DELETE FROM slack_oauth_states WHERE kind IN ('link','used:link') AND user_id=?", (user['id'],))
    if not revoke_pending(None if disconnect else user['id']):
        return error('Desconectado localmente; revocación Slack pendiente. Reintenta.', 502)
    return jsonify({'ok': True})


@blueprint.get('/slack/callback/<kind>')
def callback(kind):
    state = request.args.get('state', '')
    if kind not in ('install', 'link') or not state or len(state) > 512:
        return error('Estado OAuth inválido', 400)
    with closing(current_app.config['SLACK_DB']()) as c:
        with c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM slack_oauth_states WHERE state_hash=?', (digest(state),)).fetchone()
            if not row:
                return error('Estado OAuth inválido', 400)
            cookie_name = 'slack_oauth_' + digest(state)
            browser = request.cookies.get(cookie_name, '')
            if not row['browser_hash'] or not browser or not secrets.compare_digest(digest(browser), row['browser_hash']):
                return error('Estado OAuth inválido', 400)
            g.slack_cookie = cookie_name
            g.slack_cookie_path = urlsplit(settings()['base_url']).path.rstrip('/') + '/slack/callback'
            valid = row['kind'] == kind and row['expires'] > int(time.time()) and active(c, row['user_id'], row['session_hash'], kind == 'install')
            if row['kind'] == kind:
                c.execute('UPDATE slack_oauth_states SET kind=? WHERE state_hash=?', ('used:' + kind, digest(state)))
        if not valid or request.args.get('error') or not request.args.get('code'):
            return error('Autorización OAuth inválida', 400)
        try:
            s = settings()
            data = {'client_id': s['client_id'], 'client_secret': s['client_secret'],
                    'code': request.args['code'], 'redirect_uri': redirect_uri(s, kind)}
            if kind == 'install':
                result = slack_api('oauth.v2.access', data)
                team = result.get('team') or {}
                team_id, team_name, token = team.get('id'), team.get('name'), result.get('access_token')
                if not all(isinstance(x, str) and x for x in (team_id, team_name, token)) or result.get('token_type') != 'bot':
                    raise ValueError('Invalid installation')
            else:
                result = slack_api('openid.connect.token', data)
                token = result.get('access_token')
                if not isinstance(token, str) or not token or not isinstance(result.get('id_token'), str):
                    raise ValueError('Invalid identity token')
                claims = verify_identity(result['id_token'], token, s['client_id'], row['nonce_hash'])
                identity = slack_api('openid.connect.userInfo', token=token)
                team_id, slack_user = identity.get('https://slack.com/team_id'), identity.get('https://slack.com/user_id')
                if (not all(isinstance(x, str) and x for x in (team_id, slack_user)) or
                        identity.get('sub') != claims['sub'] or claims['sub'] != slack_user or
                        claims.get('https://slack.com/team_id') != team_id or claims.get('https://slack.com/user_id') != slack_user):
                    raise ValueError('Invalid identity')
            with c:
                c.execute('BEGIN IMMEDIATE')
                if not active(c, row['user_id'], row['session_hash'], kind == 'install'):
                    return error('No autorizado', 403)
                if c.execute('SELECT 1 FROM slack_revocations LIMIT 1').fetchone():
                    return error('Revocación Slack pendiente', 409)
                pending = c.execute('SELECT 1 FROM slack_oauth_states WHERE state_hash=? AND kind=? AND expires>?',
                                    (digest(state), 'used:' + kind, int(time.time()))).fetchone()
                if not pending:
                    return error('Estado OAuth inválido', 400)
                c.execute('DELETE FROM slack_oauth_states WHERE state_hash=?', (digest(state),))
                installation = c.execute('SELECT team_id,bot_token FROM slack_installations WHERE id=1').fetchone()
                if kind == 'install':
                    if installation and installation['team_id'] != team_id:
                        for old in c.execute('SELECT token,user_id FROM slack_identity_tokens WHERE installation_id=1').fetchall():
                            queue_revocation(c, old['token'], old['user_id'])
                        c.execute("UPDATE slack_queue SET state='cancelled',updated_at=? WHERE installation_id=1 AND state IN ('pending','sending')", (datetime.now(timezone.utc).isoformat(),))
                        c.execute('DELETE FROM slack_installations WHERE id=1')
                        c.execute("DELETE FROM slack_oauth_states WHERE kind IN ('link','used:link')")
                    if installation and installation['bot_token'] != token:
                        queue_revocation(c, installation['bot_token'], row['user_id'])
                    c.execute('''INSERT INTO slack_installations VALUES(1,?,?,?) ON CONFLICT(id)
                                 DO UPDATE SET team_id=excluded.team_id,team_name=excluded.team_name,bot_token=excluded.bot_token''', (team_id, team_name, token))
                else:
                    if not installation or installation['team_id'] != team_id:
                        return error('Workspace Slack incorrecto', 409)
                    old = c.execute('SELECT token FROM slack_identity_tokens WHERE installation_id=1 AND user_id=?', (row['user_id'],)).fetchone()
                    if old and old['token'] != token:
                        queue_revocation(c, old['token'], row['user_id'])
                    c.execute('''INSERT INTO slack_accounts VALUES(1,?,?) ON CONFLICT(installation_id,user_id)
                                 DO UPDATE SET slack_user_id=excluded.slack_user_id''', (row['user_id'], slack_user))
                    c.execute('''INSERT INTO slack_identity_tokens VALUES(1,?,?) ON CONFLICT(installation_id,user_id)
                                 DO UPDATE SET token=excluded.token''', (row['user_id'], token))
            if not revoke_pending():
                return error('Conexión guardada; revocación Slack pendiente. Reintenta desconectar.', 502)
            return redirect('/')
        except sqlite3.IntegrityError:
            return error('Identidad Slack ya vinculada', 409)
        except Exception:
            return error('No se pudo completar la autorización Slack', 502)
