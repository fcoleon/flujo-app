import hashlib, hmac, json, os, secrets, sqlite3, re, uuid, subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import analytics as metrics
import automations
import automation_routes
import workspace_routes
import project_routes
import comment_routes
import task_routes
from contextlib import closing
from flask import Flask, g, jsonify, make_response, request, send_from_directory, send_file, abort, render_template_string, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
from descriptions import DescriptionSanitizer, sanitize_description
from validation import text_field, integer_field, task_title, task_due_date, task_priority
from auth import hashpw, current_user, session_response, create_session, rotate_session, SESSION_SECONDS
import time
from pathlib import Path
from settings import load_settings
from cache import Cache

SETTINGS = load_settings()
CACHE = Cache(SETTINGS['cache'])
CACHE_EPOCH = secrets.token_hex(16)
DB = SETTINGS['database']
PORT = SETTINGS['port']
STATS_ZONE = ZoneInfo(SETTINGS['timezone'])
STATIC_DIR = Path(__file__).resolve().parent / 'static'
TEMPLATE_DIR = Path(__file__).resolve().parent / 'templates'
COLUMN_COLORS = ('#a5a3ab', '#e7aa69', '#aa91dd', '#78b99a')

def running_version():
    try:
        result=subprocess.run(['git','rev-parse','--short=8','--verify','HEAD'],cwd=Path(__file__).resolve().parent,
                              capture_output=True,text=True,check=True,timeout=2)
        value=result.stdout.strip()
        return value if re.fullmatch(r'[0-9a-f]{8,64}',value) else None
    except (OSError,subprocess.SubprocessError):
        return None

# Se fija al arrancar para identificar el código cargado por este proceso.
VERSION = running_version()

def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def mcp_token_hash(token): return hashlib.sha256(token.encode()).hexdigest()

def mcp_tokens(c,uid):
    return [dict(r) for r in c.execute('SELECT id,name,created_at,last_used_at,revoked_at FROM mcp_tokens WHERE user_id=? ORDER BY id DESC',(uid,))]

def mcp_token_user(c,token):
    """Resuelve un token Bearer de un agente MCP a su usuario. Devuelve (usuario, motivo_de_rechazo)."""
    digest=mcp_token_hash(token)
    row=c.execute('''SELECT u.id,u.name,u.username,u.admin,u.blocked,u.must_change_password
                     FROM mcp_tokens t JOIN users u ON u.id=t.user_id
                     WHERE t.token_hash=? AND t.revoked_at IS NULL''',(digest,)).fetchone()
    if not row: return None,'invalid'
    u={'id':row['id'],'name':row['name'],'username':row['username'],'admin':row['admin']}
    if row['blocked']: return u,'blocked'
    if row['must_change_password']: return u,'password'
    return u,None

def init():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, salt TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#7765d7', description TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS members(project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')), PRIMARY KEY(project_id,user_id));
        CREATE TABLE IF NOT EXISTS columns(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL, color TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL, due_date TEXT, priority TEXT NOT NULL DEFAULT 'Normal', parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL, created_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS placements(task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, column_id INTEGER NOT NULL REFERENCES columns(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(task_id,project_id));
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);''')
        if 'expires_at' not in {r['name'] for r in c.execute('PRAGMA table_info(sessions)')}:
            c.execute('ALTER TABLE sessions ADD COLUMN expires_at INTEGER')
        c.execute('UPDATE sessions SET expires_at=? WHERE expires_at IS NULL',(int(time.time())+SESSION_SECONDS,))
        c.execute('CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions(expires_at)')
        if 'color' not in {r['name'] for r in c.execute('PRAGMA table_info(columns)')}:
            c.execute('ALTER TABLE columns ADD COLUMN color TEXT')
        for project in c.execute('SELECT DISTINCT project_id FROM columns WHERE color IS NULL').fetchall():
            for i,column in enumerate(c.execute('SELECT id,color FROM columns WHERE project_id=? ORDER BY position,id',(project['project_id'],)).fetchall()):
                if column['color'] is None: c.execute('UPDATE columns SET color=? WHERE id=? AND color IS NULL',(COLUMN_COLORS[i%len(COLUMN_COLORS)],column['id']))
        if 'public_id' not in {r['name'] for r in c.execute('PRAGMA table_info(projects)')}:
            c.execute('ALTER TABLE projects ADD COLUMN public_id TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS projects_public_id_unique ON projects(public_id)')
        for project in c.execute("SELECT id FROM projects WHERE public_id IS NULL OR public_id=''").fetchall():
            c.execute('UPDATE projects SET public_id=? WHERE id=?',(str(uuid.uuid4()),project['id']))
        if 'blocked' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
        if 'must_change_password' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
        if 'avatar_key' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN avatar_key TEXT')
        c.execute('CREATE TABLE IF NOT EXISTS avatar_cleanup(key TEXT PRIMARY KEY, created_at INTEGER NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS task_attachments(
                     id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                     user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, name TEXT NOT NULL,
                     storage_key TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, created_at TEXT NOT NULL)''')
        c.execute('''CREATE TRIGGER IF NOT EXISTS attachment_cleanup AFTER DELETE ON task_attachments
                     BEGIN INSERT OR IGNORE INTO avatar_cleanup(key,created_at) VALUES(OLD.storage_key,CAST(strftime('%s','now') AS INTEGER)); END''')
        c.executescript('''CREATE TABLE IF NOT EXISTS task_types(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, is_base INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS task_statuses(id INTEGER PRIMARY KEY AUTOINCREMENT, type_id INTEGER NOT NULL REFERENCES task_types(id) ON DELETE CASCADE, name TEXT NOT NULL, color TEXT NOT NULL, is_done INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL);''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(tasks)')}
        if 'type_id' not in cols: c.execute('ALTER TABLE tasks ADD COLUMN type_id INTEGER REFERENCES task_types(id)')
        if 'status_id' not in cols: c.execute('ALTER TABLE tasks ADD COLUMN status_id INTEGER REFERENCES task_statuses(id)')
        if 'public_id' not in cols: c.execute('ALTER TABLE tasks ADD COLUMN public_id TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS tasks_public_id_unique ON tasks(public_id)')
        for task in c.execute("SELECT id FROM tasks WHERE public_id IS NULL OR public_id=''").fetchall():
            c.execute('UPDATE tasks SET public_id=? WHERE id=?',(str(uuid.uuid4()),task['id']))
        ensure_base_type(c, None)
        for project in c.execute('SELECT id FROM projects').fetchall(): ensure_base_type(c, project['id'])
        for task in c.execute('SELECT id FROM tasks WHERE type_id IS NULL OR status_id IS NULL').fetchall():
            placement=c.execute('SELECT project_id FROM placements WHERE task_id=? ORDER BY project_id LIMIT 1',(task['id'],)).fetchone()
            type_id=ensure_base_type(c, placement['project_id'] if placement else None)
            status_id=default_status(c,type_id)
            c.execute('UPDATE tasks SET type_id=?, status_id=? WHERE id=?',(type_id,status_id,task['id']))
        # Normalize older project tasks that were assigned the workspace-only base type.
        for task in c.execute('''SELECT t.id,MIN(p.project_id) AS project_id FROM tasks t
                                 JOIN task_types ty ON ty.id=t.type_id
                                 JOIN placements p ON p.task_id=t.id
                                 WHERE ty.project_id IS NULL AND ty.is_base=1 GROUP BY t.id''').fetchall():
            apply_project_base(c,task['id'],task['project_id'])
        c.execute('CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        c.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('workspace_name','Mi workspace')")
        history_columns={row['name'] for row in c.execute('PRAGMA table_info(history)')}
        for name,definition in [('status_id','INTEGER'),('status_name','TEXT'),('type_id','INTEGER'),('type_name','TEXT'),('status_is_done','INTEGER')]:
            if name not in history_columns: c.execute(f'ALTER TABLE history ADD COLUMN {name} {definition}')
        c.execute('''UPDATE history SET status_is_done=(SELECT is_done FROM task_statuses WHERE id=history.status_id)
                     WHERE status_id IS NOT NULL AND status_is_done IS NULL''')
        if not c.execute("SELECT 1 FROM app_meta WHERE key='status_history_started_at'").fetchone():
            started=now()
            c.execute('''INSERT INTO history(task_id,user_id,message,created_at,status_id,status_name,type_id,type_name,status_is_done)
                         SELECT t.id,NULL,'inicio del registro de tiempo por estado',?,s.id,s.name,ty.id,ty.name,s.is_done
                         FROM tasks t JOIN task_statuses s ON s.id=t.status_id JOIN task_types ty ON ty.id=t.type_id''',(started,))
            c.execute("INSERT INTO app_meta(key,value) VALUES('status_history_started_at',?)",(started,))
        c.execute('CREATE INDEX IF NOT EXISTS history_task_status ON history(task_id,created_at,id) WHERE status_id IS NOT NULL')
        c.execute('CREATE TABLE IF NOT EXISTS metric_events(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, task_id INTEGER NOT NULL, event TEXT NOT NULL, occurred_at TEXT NOT NULL, task_created_at TEXT NOT NULL, type_id INTEGER NOT NULL, type_name TEXT NOT NULL)')
        metric_columns={row['name'] for row in c.execute('PRAGMA table_info(metric_events)')}
        for name,definition in [('actor_id','INTEGER'),('actor_name','TEXT'),('assignee_id','INTEGER'),('assignee_name','TEXT'),('identity_recorded','INTEGER NOT NULL DEFAULT 0')]:
            if name not in metric_columns: c.execute(f'ALTER TABLE metric_events ADD COLUMN {name} {definition}')
        c.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('metrics_identity_started_at',?)",(now(),))
        c.execute('CREATE INDEX IF NOT EXISTS metric_events_project_date ON metric_events(project_id,occurred_at)')
        c.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('metrics_started_at',?)",(now(),))
        if 'completed_at' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute('ALTER TABLE tasks ADD COLUMN completed_at TEXT')
            c.execute('''UPDATE tasks SET completed_at=(SELECT CASE WHEN event='completed' THEN occurred_at END
                         FROM metric_events WHERE task_id=tasks.id AND event IN ('completed','reopened')
                         ORDER BY occurred_at DESC,id DESC LIMIT 1)
                         WHERE status_id IN (SELECT id FROM task_statuses WHERE is_done=1)''')
        if 'description_format' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute("ALTER TABLE tasks ADD COLUMN description_format TEXT NOT NULL DEFAULT 'plain'")
        automations.init(c)
        for name in ('title_version','description_version'):
            if name not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
                c.execute(f'ALTER TABLE tasks ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE TABLE IF NOT EXISTS data_revision(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)')
        c.execute('INSERT OR IGNORE INTO data_revision VALUES(1,0)')
        for table in ('users','projects','members','columns','tasks','placements','history','comments','task_types','task_statuses','automation_rules','automation_runs','metric_events','app_meta','task_attachments'):
            for action in ('INSERT','UPDATE','DELETE'):
                c.execute(f'''CREATE TRIGGER IF NOT EXISTS revision_{table}_{action.lower()} AFTER {action} ON {table}
                              BEGIN UPDATE data_revision SET revision=revision+1 WHERE id=1; END''')
        c.execute('''CREATE TRIGGER IF NOT EXISTS task_content_versions AFTER UPDATE OF title,description,description_format ON tasks
                     WHEN OLD.title IS NOT NEW.title OR OLD.description IS NOT NEW.description OR OLD.description_format IS NOT NEW.description_format
                     BEGIN UPDATE tasks SET title_version=title_version+(OLD.title IS NOT NEW.title),
                         description_version=description_version+(OLD.description IS NOT NEW.description OR OLD.description_format IS NOT NEW.description_format)
                         WHERE id=NEW.id; END''')
        c.execute('''CREATE TABLE IF NOT EXISTS slack_installations(
                     id INTEGER PRIMARY KEY CHECK(id=1), team_id TEXT NOT NULL UNIQUE,
                     team_name TEXT NOT NULL, bot_token TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS slack_accounts(
                     installation_id INTEGER NOT NULL REFERENCES slack_installations(id) ON DELETE CASCADE,
                     user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                     slack_user_id TEXT NOT NULL, PRIMARY KEY(installation_id,user_id),
                     UNIQUE(installation_id,slack_user_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS slack_preferences(
                     installation_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                     project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                     assignment INTEGER NOT NULL DEFAULT 0 CHECK(assignment IN (0,1)),
                     comment INTEGER NOT NULL DEFAULT 0 CHECK(comment IN (0,1)),
                     completion INTEGER NOT NULL DEFAULT 0 CHECK(completion IN (0,1)),
                     FOREIGN KEY(installation_id,user_id) REFERENCES slack_accounts(installation_id,user_id) ON DELETE CASCADE)''')
        c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS slack_preferences_project ON slack_preferences(installation_id,user_id,project_id)
                     WHERE project_id IS NOT NULL''')
        c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS slack_preferences_orphan ON slack_preferences(installation_id,user_id)
                     WHERE project_id IS NULL''')
        c.execute('''CREATE TABLE IF NOT EXISTS mcp_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                     name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                     created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)''')
        import slack_auth, slack_notify
        slack_auth.init(c)
        slack_notify.init(c)

def create_type(c, project_id, name, is_base=False):
    # Historical metrics retain type IDs even after a type is deleted.
    # Never reuse an ID that is still referenced by those snapshots.
    current=c.execute('SELECT COALESCE(MAX(id),0) FROM task_types').fetchone()[0]
    has_metrics=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_events'").fetchone()
    historic=c.execute('SELECT COALESCE(MAX(type_id),0) FROM metric_events').fetchone()[0] if has_metrics else 0
    has_sequence=c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
    seq=c.execute("SELECT seq FROM sqlite_sequence WHERE name='task_types'").fetchone() if has_sequence else None
    tid=max(current,historic,seq[0] if seq else 0)+1
    c.execute('INSERT INTO task_types(id,project_id,name,is_base) VALUES(?,?,?,?)',(tid,project_id,name,int(is_base)))
    for i,(label,color,done) in enumerate([('En curso','#8b85a3',0),('Finalizado','#65aa86',1)]):
        c.execute('INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?,?,?,?,?)',(tid,label,color,done,i))
    return tid

def ensure_base_type(c, project_id):
    row=c.execute('SELECT id FROM task_types WHERE project_id IS ? AND is_base=1',(project_id,)).fetchone()
    return row['id'] if row else create_type(c,project_id,'Tarea base',True)

def apply_project_base(c,task_id,project_id):
    task=c.execute('SELECT ty.project_id,ty.is_base,s.is_done FROM tasks t JOIN task_types ty ON ty.id=t.type_id JOIN task_statuses s ON s.id=t.status_id WHERE t.id=?',(task_id,)).fetchone()
    if not task or task['project_id'] is not None or not task['is_base']: return False
    type_id=ensure_base_type(c,project_id)
    status=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=? ORDER BY position,id LIMIT 1',(type_id,task['is_done'])).fetchone()
    if not status: raise ValueError('El tipo base del proyecto no tiene un estado equivalente')
    c.execute('UPDATE tasks SET type_id=?,status_id=? WHERE id=?',(type_id,status['id'],task_id))
    return True

def default_status(c,type_id):
    row=c.execute('SELECT id FROM task_statuses WHERE type_id=? AND is_done=0 ORDER BY position,id LIMIT 1',(type_id,)).fetchone()
    if not row: raise ValueError('El tipo debe tener un estado en curso')
    return row['id']

def type_data(c,row):
    item=dict(row); item['statuses']=[dict(s) for s in c.execute('SELECT * FROM task_statuses WHERE type_id=? ORDER BY position,id',(row['id'],))]; return item

def metric_event(c, task_id, event, actor_id):
    timestamp=now()
    if event in ('completed','reopened'):
        c.execute('UPDATE tasks SET completed_at=? WHERE id=?',(timestamp if event=='completed' else None,task_id))
    result=metrics.metric_event(c,task_id,event,actor_id,lambda:timestamp)
    if event=='completed':
        import slack_notify
        slack_notify.enqueue(c,'completion',task_id,actor_id)
    return result

def analytics(c, project_id, month, type_filter=None):
    return metrics.analytics(c,project_id,month,type_filter,STATS_ZONE)

def now(): return datetime.now(timezone.utc).isoformat()
def record(c,tid,uid,msg,status=False):
    if status:
        c.execute('''INSERT INTO history(task_id,user_id,message,created_at,status_id,status_name,type_id,type_name,status_is_done)
                     SELECT t.id,?,?,?,s.id,s.name,ty.id,ty.name,s.is_done FROM tasks t
                     JOIN task_statuses s ON s.id=t.status_id JOIN task_types ty ON ty.id=t.type_id WHERE t.id=?''',(uid,msg,now(),tid))
    else: c.execute('INSERT INTO history(task_id,user_id,message,created_at) VALUES(?,?,?,?)',(tid,uid,msg,now()))
def role(c,u,p):
    if u['admin']: return 'admin'
    r=c.execute('SELECT role FROM members WHERE project_id=? AND user_id=?',(p,u['id'])).fetchone(); return r['role'] if r else None
def task_projects(c,tid):
    return {r['project_id'] for r in c.execute('''WITH RECURSIVE ancestors(id) AS (
        SELECT ? UNION SELECT t.parent_id FROM tasks t JOIN ancestors a ON t.id=a.id WHERE t.parent_id IS NOT NULL
    ) SELECT DISTINCT p.project_id FROM placements p JOIN ancestors a ON p.task_id=a.id''',(tid,))}
def can_task(c,u,tid,write=False):
    task=c.execute('SELECT parent_id,assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()
    if not task: return False
    if u['admin']: return True
    # Project membership includes inherited projects and takes precedence over assignment.
    projects=task_projects(c,tid)
    if projects:
        return any(role(c,u,p) in (('admin','editor') if write else ('admin','editor','viewer')) for p in projects)
    if task['assignee_id']==u['id']: return True
    return bool(task['parent_id'] and can_task(c,u,task['parent_id'],write))

app = Flask(__name__, static_folder=None)
import slack_auth
app.config['SLACK_SETTINGS'] = SETTINGS['slack']
app.config['SLACK_DB'] = db
app.register_blueprint(slack_auth.blueprint)
app.config['MAX_CONTENT_LENGTH'] = 100000
if os.environ.get('FLUJO_BEHIND_PROXY') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)

def respond(data,status=200): return jsonify(data),status

@app.errorhandler(413)
def too_large(error): return respond({'error':'Solicitud demasiado grande'},413)

@app.get('/')
@app.get('/my-tasks')
@app.get('/t/<public_id>')
@app.get('/board/<board_id>')
@app.get('/board/<board_id>/task/<public_id>')
def index(public_id=None,board_id=None):
    if public_id is not None and not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',public_id): abort(404)
    if board_id is not None and board_id!='no-project' and not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',board_id): abort(404)
    html=(TEMPLATE_DIR/'index.html').read_text(encoding='utf-8')
    response=make_response(render_template_string(html,style_version=asset_version('style.css'),
                           script_version=asset_version('app.js'),automations_version=asset_version('automations.js'),
                           navigation_version=asset_version('navigation.js'),slack_version=asset_version('slack.js'),
                           project_filters_version=asset_version('project-filters.js'),attachments_version=asset_version('attachments.js'),
                           task_detail_version=asset_version('task-detail.js'),
                           favicon_version=asset_version('favicon.svg')))
    response.cache_control.no_cache=True
    return response

def content_version(filename):
    return hashlib.sha256((STATIC_DIR/filename).read_bytes()).hexdigest()[:16]

def asset_version(filename):
    return content_version(filename)

@app.get('/assets/<filename>')
def versioned_asset(filename):
    match=re.fullmatch(r'(style|app|favicon)\.([0-9a-f]{16})\.(css|js|svg)',filename)
    if not match: abort(404)
    name,version,extension=match.groups()
    source=f'{name}.{extension}'
    if source not in ('style.css','app.js','favicon.svg'): abort(404)
    response=send_from_directory(STATIC_DIR,source)
    response.cache_control.no_cache=True
    return response

@app.get('/static/<any(app.js,automations.js,navigation.js,slack.js,"project-filters.js",attachments.js,"task-detail.js",style.css,favicon.svg):filename>')
@app.get('/<any(app.js,style.css,favicon.svg):filename>')
def static_asset(filename):
    response=send_from_directory(STATIC_DIR,filename)
    response.cache_control.no_cache=True
    return response

@app.route('/api/<path:route>',methods=['GET','POST'])
def api_dispatch(route):
    return api_get() if request.method=='GET' else api_post()

@app.route('/api/avatar', methods=['POST'])
def avatar_upload():
    import avatars
    request.max_content_length=2*1024*1024+65536
    request.max_form_parts=4
    if request.headers.get('X-Flujo-Request')!='avatar': return respond({'error':'Solicitud inválida'},403)
    with closing(db()) as c:
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
        if flags['blocked'] or flags['must_change_password']: return respond({'error':'Sin acceso'},403)
        if not all(SETTINGS['backblaze'].values()): return respond({'error':'Fotos de perfil no configuradas'},503)
        if request.mimetype!='multipart/form-data' or set(request.files)!= {'avatar'}: return respond({'error':'Selecciona una imagen'},400)
        try:
            image=avatars.process(request.files['avatar'].stream)
            avatars.save(c,u['id'],request.cookies.get('session',''),image,SETTINGS['backblaze'])
            return respond({'ok':True})
        except ValueError as e: return respond({'error':str(e)},400)
        except avatars.StorageError:
            app.logger.exception('Error de almacenamiento de avatar')
            return respond({'error':'No se pudo guardar la foto'},503)

@app.get('/api/avatar/<int:user_id>')
def avatar_get(user_id):
    import avatars
    with closing(db()) as c:
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
        if flags['blocked'] or flags['must_change_password']: return respond({'error':'Sin acceso'},403)
        row=c.execute('SELECT avatar_key FROM users WHERE id=?',(user_id,)).fetchone()
        if not row or not row['avatar_key'] or not all(SETTINGS['backblaze'].values()): abort(404)
        storage=SETTINGS['backblaze']
        cache_key='flujo:avatar:'+hashlib.sha256(json.dumps([storage[k] for k in ('endpoint','region','bucket','key_id','application_key')]+[row['avatar_key']]).encode()).hexdigest()
        try: url=CACHE.get_or_create(cache_key,lambda:avatars.signed_url(row['avatar_key'],storage),3600)
        except avatars.StorageError: return respond({'error':'Foto no disponible'},503)
        response=redirect(url,302)
        response.headers['Cache-Control']='private, no-store'
        response.headers['Referrer-Policy']='no-referrer'
        return response

@app.route('/api/tasks/<int:task_id>/attachments', methods=['GET','POST'])
def task_attachments(task_id):
    import avatars
    if request.method=='POST':
        request.max_content_length=10*1024*1024+65536
        request.max_form_parts=4
        if request.headers.get('X-Flujo-Request')!='attachment': return respond({'error':'Solicitud inválida'},403)
    with closing(db()) as c:
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
        if flags['blocked'] or flags['must_change_password']: return respond({'error':'Sin acceso'},403)
        if not all(SETTINGS['backblaze'].values()): return respond({'error':'Adjuntos no configurados'},503)
        if not can_task(c,u,task_id,request.method=='POST'): return respond({'error':'Sin acceso'},403)
        if request.method=='GET':
            return respond({'attachments':[dict(r) for r in c.execute('SELECT id,name,size,created_at FROM task_attachments WHERE task_id=? ORDER BY created_at,id',(task_id,))]})
        if request.mimetype!='multipart/form-data' or set(request.files)!={'file'} or len(request.files.getlist('file'))!=1:
            return respond({'error':'Selecciona un archivo'},400)
        f=request.files['file']
        name=re.sub(r'[\x00-\x1f\x7f-\x9f/\\\\]','_',f.filename or '').strip()
        if not name or len(name)>255: return respond({'error':'Nombre de archivo inválido'},400)
        content=f.stream.read(10*1024*1024+1)
        if not content: return respond({'error':'El archivo está vacío'},400)
        if len(content)>10*1024*1024: return respond({'error':'El archivo supera 10 MiB'},413)
        key='attachments/'+secrets.token_hex(32)
        c.execute('INSERT INTO avatar_cleanup VALUES(?,?)',(key,int(time.time()))); c.commit()
        try:
            avatars.client(SETTINGS['backblaze']).put_object(Bucket=SETTINGS['backblaze']['bucket'],Key=key,Body=content,ContentType='application/octet-stream',CacheControl='private, no-store')
            c.execute('BEGIN IMMEDIATE')
            u=current_user(c)
            flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone() if u else None
            if not u or flags['blocked'] or flags['must_change_password'] or not can_task(c,u,task_id,True):
                c.rollback(); return respond({'error':'Sin acceso'},403)
            attachment_id=str(uuid.uuid4())
            c.execute('INSERT INTO task_attachments VALUES(?,?,?,?,?,?,?)',(attachment_id,task_id,u['id'],name,key,len(content),now()))
            count=c.execute('SELECT COUNT(*) FROM task_attachments WHERE task_id=?',(task_id,)).fetchone()[0]
            c.execute('DELETE FROM avatar_cleanup WHERE key=?',(key,)); c.commit()
            avatars.cleanup(c,SETTINGS['backblaze'])
            return respond({'id':attachment_id,'name':name,'attachment_count':count})
        except avatars.StorageError:
            app.logger.exception('Error al guardar adjunto')
            return respond({'error':'No se pudo guardar el archivo'},503)

@app.route('/api/attachments/<attachment_id>',methods=['GET','POST'])
def attachment(attachment_id):
    import io, avatars
    with closing(db()) as c:
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
        if flags['blocked'] or flags['must_change_password']: return respond({'error':'Sin acceso'},403)
        if not all(SETTINGS['backblaze'].values()): return respond({'error':'Adjuntos no configurados'},503)
        row=c.execute('SELECT * FROM task_attachments WHERE id=?',(attachment_id,)).fetchone()
        if not row: abort(404)
        if request.method=='POST':
            if request.mimetype!='application/json': return respond({'error':'Se requiere application/json'},415)
            c.execute('BEGIN IMMEDIATE')
            u=current_user(c); row=c.execute('SELECT * FROM task_attachments WHERE id=?',(attachment_id,)).fetchone()
            flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone() if u else None
            if not row or not u or flags['blocked'] or flags['must_change_password'] or not can_task(c,u,row['task_id'],True):
                c.rollback(); return respond({'error':'Sin acceso'},403)
            c.execute('DELETE FROM task_attachments WHERE id=?',(attachment_id,))
            count=c.execute('SELECT COUNT(*) FROM task_attachments WHERE task_id=?',(row['task_id'],)).fetchone()[0]; c.commit()
            avatars.cleanup(c,SETTINGS['backblaze'])
            return respond({'ok':True,'attachment_count':count})
        if not can_task(c,u,row['task_id']): return respond({'error':'Sin acceso'},403)
        try:
            obj=avatars.client(SETTINGS['backblaze']).get_object(Bucket=SETTINGS['backblaze']['bucket'],Key=row['storage_key'])
            body=obj['Body']
            try: content=body.read(10*1024*1024+1)
            finally: body.close()
            if len(content)>10*1024*1024: return respond({'error':'Archivo demasiado grande'},503)
            preview=request.args.get('preview')=='1'
            mimetype='application/octet-stream'
            if preview:
                from PIL import Image, UnidentifiedImageError
                formats={'jpg':('JPEG','image/jpeg'),'jpeg':('JPEG','image/jpeg'),'png':('PNG','image/png'),'gif':('GIF','image/gif'),'webp':('WEBP','image/webp')}
                extension=row['name'].rsplit('.',1)[-1].lower() if '.' in row['name'] else ''
                expected=formats.get(extension)
                if not expected: return respond({'error':'Formato de imagen no compatible'},415)
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        if image.format!=expected[0]: return respond({'error':'Imagen inválida'},415)
                        image.verify()
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    return respond({'error':'Imagen inválida'},415)
                mimetype=expected[1]
            response=send_file(io.BytesIO(content),mimetype=mimetype,as_attachment=not preview,download_name=row['name'])
            if preview: response.headers['Content-Security-Policy']="default-src 'none'; sandbox"
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Cache-Control']='private, no-store'
            return response
        except avatars.StorageError:
            app.logger.exception('Error al descargar adjunto')
            return respond({'error':'No se pudo descargar el archivo'},503)

def workspace_data(c,u,revision):
    types={}
    for row in c.execute('SELECT * FROM task_types'):
        types[row['id']]={**dict(row),'statuses':[]}
    for row in c.execute('SELECT * FROM task_statuses ORDER BY position,id'):
        if row['type_id'] in types: types[row['type_id']]['statuses'].append(dict(row))
    projects=[]
    for p in c.execute('SELECT * FROM projects ORDER BY id').fetchall():
        r=role(c,u,p['id'])
        if r:
            d=dict(p); d['role']=r
            d['columns']=[dict(x) for x in c.execute('SELECT * FROM columns WHERE project_id=? ORDER BY position,id',(p['id'],))]
            d['types']=sorted((t for t in types.values() if t['project_id']==p['id']),key=lambda t:(-t['is_base'],t['id']))
            d['members']=[dict(x) for x in c.execute('SELECT u.id,u.name,u.username,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.project_id=?',(p['id'],))]
            projects.append(d)
    allowed={p['id'] for p in projects}; writable={p['id'] for p in projects if p['role'] in ('admin','editor')}; tasks=[]
    rows=c.execute('''SELECT t.*,COALESCE(cm.comment_count,0) AS comment_count,COALESCE(a.attachment_count,0) AS attachment_count FROM tasks t
                          LEFT JOIN (SELECT task_id,COUNT(*) AS comment_count FROM comments GROUP BY task_id) cm ON cm.task_id=t.id
                          LEFT JOIN (SELECT task_id,COUNT(*) AS attachment_count FROM task_attachments GROUP BY task_id) a ON a.task_id=t.id
                          ORDER BY t.id DESC''').fetchall()
    by_id={t['id']:t for t in rows}; placements={}; histories={}; contexts={}
    for row in c.execute('SELECT * FROM placements'):
        placements.setdefault(row['task_id'],[]).append(dict(row))
    for row in c.execute('SELECT task_id,status_id,created_at,message FROM history WHERE status_id IS NOT NULL ORDER BY task_id,created_at,id'):
        histories.setdefault(row['task_id'],[]).append(row)
    def context(tid):
        chain=[]; seen=set()
        while tid in by_id and tid not in contexts and tid not in seen:
            seen.add(tid); chain.append(tid); tid=by_id[tid]['parent_id']
        inherited,assigned=contexts.get(tid,(set(),False))
        for item in reversed(chain):
            inherited=inherited|{p['project_id'] for p in placements.get(item,[])}
            assigned=assigned or by_id[item]['assignee_id']==u['id']
            contexts[item]=(inherited,assigned)
        return contexts[chain[0]] if chain else contexts.get(tid,(set(),False))
    for t in rows:
        projects_for_task,assigned=context(t['id'])
        if u['admin'] or (bool(projects_for_task & allowed) if projects_for_task else assigned):
            own=placements.get(t['id'],[]); explicit={p['project_id'] for p in own}
            d=dict(t); d['placements']=[p for p in own if p['project_id'] in allowed]; d['inherited_project_ids']=sorted((projects_for_task-explicit)&allowed); d['can_edit']=bool(u['admin'] or (projects_for_task & writable if projects_for_task else assigned)); d['has_projects']=bool(projects_for_task); d['task_type']=types.get(t['type_id'])
            history=histories.get(t['id'],[]); status_entry=history[-1] if history else None
            d['status_since']=status_entry['created_at'] if status_entry else None; d['status_since_partial']=bool(status_entry and status_entry['message']=='inicio del registro de tiempo por estado')
            d['status_previous_seconds'],d['status_total_partial']=metrics.status_time_from_rows(history,t['status_id']); tasks.append(d)
    base=next(t for t in types.values() if t['project_id'] is None and t['is_base'])
    return {'attachments_enabled':all(SETTINGS['backblaze'].values()),'user':dict(u),'version':VERSION,'revision':revision,'workspace_name':c.execute("SELECT value FROM app_meta WHERE key='workspace_name'").fetchone()['value'],'users':[dict(x) for x in c.execute('SELECT id,name,username,admin,blocked,substr(avatar_key,9,64) AS avatar_version FROM users ORDER BY name')], 'projects':projects,'tasks':tasks,'stats_timezone':str(STATS_ZONE),'base_type':base}

def api_get():
    path=request.path
    try:
        with closing(db()) as c, c:
            if path=='/api/data': c.execute('BEGIN')
            u=current_user(c)
            flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone() if u else None
            blocked=bool(flags and flags['blocked']);must_change=bool(flags and flags['must_change_password'])
            if path=='/api/session': return respond({'user':dict(u) if u else None,'blocked':blocked,'must_change_password':must_change,'allow_self_registration':SETTINGS['allow_self_registration'],'needs_setup':not bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())})
            if not u: return respond({'error':'Inicia sesión'},401)
            if blocked: return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
            if must_change: return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
            if path=='/api/revision': return respond({'revision':c.execute('SELECT revision FROM data_revision WHERE id=1').fetchone()[0],'version':VERSION})
            if path=='/api/tokens': return respond({'tokens':mcp_tokens(c,u['id'])})
            if path=='/api/data':
                revision=c.execute('SELECT revision FROM data_revision WHERE id=1').fetchone()[0]
                stat=os.stat(DB)
                identity=[CACHE_EPOCH,stat.st_dev,stat.st_ino,u['id'],revision,str(STATS_ZONE),all(SETTINGS['backblaze'].values())]
                key='flujo:data:'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                payload=CACHE.get_or_create(key,lambda:app.json.dumps(workspace_data(c,u,revision)),60)
                response=app.response_class(payload,mimetype='application/json')
                response.headers['Cache-Control']='private, no-store'
                return response
            if path=='/api/analytics':
                params=request.args.to_dict(flat=False); p=int(params['project_id'][0]); month=params['month'][0]
                if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return respond({'error':'Proyecto no encontrado'},404)
                if role(c,u,p)!='admin': return respond({'error':'Solo los administradores del proyecto pueden ver estadísticas'},403)
                type_filter=int(params['type_id'][0]) if params.get('type_id') else None
                return respond(analytics(c,p,month,type_filter))
            if path in ('/api/automations','/api/automations/runs'):
                return automation_routes.get(c,u,path,role,respond)
            if path.startswith('/api/comments/'):
                tid=int(path.split('/')[-1])
                if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
                return respond([dict(x) for x in c.execute('SELECT cm.*,COALESCE(u.name,"Usuario eliminado") AS author FROM comments cm LEFT JOIN users u ON u.id=cm.user_id WHERE task_id=? ORDER BY cm.id',(tid,))])
            if path.startswith('/api/history/'):
                tid=int(path.split('/')[-1])
                if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
                return respond([dict(x) for x in c.execute('''SELECT h.*,CASE WHEN h.user_id IS NULL AND h.message='inicio del registro de tiempo por estado' THEN 'Sistema' ELSE COALESCE(u.name,'Usuario eliminado') END AS actor FROM history h LEFT JOIN users u ON u.id=h.user_id WHERE task_id=? ORDER BY h.id DESC''',(tid,))])
            return respond({'error':'No encontrado'},404)
    except PermissionError as e: return respond({'error':str(e)},403)
    except (ValueError, KeyError, sqlite3.IntegrityError) as e: return respond({'error':str(e)},400)

def api_post():
    path=request.path
    try:
        raw=request.get_data(cache=False)
        if request.mimetype!='application/json': return respond({'error':'Se requiere application/json'},415)
        b=json.loads(raw)
        if not isinstance(b,dict): raise ValueError('Datos inválidos')
        with closing(db()) as c, c:
            u=current_user(c)
            if path in ('/api/login','/api/setup','/api/register'):
                if path=='/api/register' and not SETTINGS['allow_self_registration']:
                    return respond({'error':'El registro está deshabilitado'},403)
                username=text_field(b,'username').strip().lower(); pw=text_field(b,'password')
                if path in ('/api/setup','/api/register'):
                    # Serializa la creación de la primera cuenta entre workers.
                    c.execute('BEGIN IMMEDIATE')
                    exists=bool(c.execute('SELECT 1 FROM users').fetchone())
                    if path=='/api/setup' and exists: raise ValueError('La aplicación ya está configurada')
                    if path=='/api/register' and not exists: raise ValueError('Primero se debe crear la cuenta administradora')
                    name=text_field(b,'name').strip()
                    if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
                    s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin) VALUES(?,?,?,?,?)',(name,username,hashpw(pw,s),s,int(path=='/api/setup')))
                row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                password_hash=hashpw(pw,row['salt'] if row else '00'*16)
                if not hmac.compare_digest(row['password'] if row else '0'*64,password_hash) or not row: return respond({'error':'Usuario o contraseña incorrectos'},401)
                token=create_session(c,row['id']); c.commit()
                return session_response(token)
            if not u: return respond({'error':'Inicia sesión'},401)
            if path=='/api/logout':
                c.execute('DELETE FROM sessions WHERE token=?',(request.cookies.get('session',''),)); response=make_response(jsonify({'ok':True})); response.delete_cookie('session',path='/'); return response
            flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone()
            if flags['blocked']:
                return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
            if path=='/api/password/change':
                c.execute('BEGIN IMMEDIATE')
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
            if flags['must_change_password']:
                return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
            if path=='/api/avatar/delete':
                import avatars
                avatars.remove(c,u['id'],SETTINGS['backblaze'])
                return respond({'ok':True})
            g.disabled_automations=[]
            if path in ('/api/tasks','/api/task/type-status','/api/task/update','/api/task/move','/api/automations/save','/api/automations/delete','/api/automations/reorder','/api/task-types/delete','/api/task-types/update','/api/workspace/block','/api/columns/delete','/api/tokens','/api/tokens/revoke'):
                c.execute('BEGIN IMMEDIATE')
            if path in ('/api/workspace/update','/api/workspace/block','/api/profile','/api/users'):
                result=workspace_routes.post(c,u,path,b,current_user,respond,hashpw,rotate_session,session_response)
            elif path in ('/api/projects','/api/projects/update','/api/members','/api/columns','/api/columns/rename','/api/columns/update','/api/columns/reorder','/api/columns/delete'):
                result=project_routes.post(c,u,path,b,role,respond,ensure_base_type,COLUMN_COLORS,record,delete_task,automate)
            elif path in ('/api/tasks','/api/task-types','/api/task-types/delete','/api/task-types/update','/api/task/type-status'):
                result=task_routes.post_task_types(c,u,path,b,can_task,role,ensure_base_type,now,default_status,record,notify_assignment,metric_event,automate,respond,create_type)
            elif path in ('/api/comments','/api/comments/update'):
                result=comment_routes.post(c,u,path,b,can_task,respond,now,record)
            elif path in ('/api/task/delete','/api/task/update','/api/task/move','/api/task/project'):
                result=task_routes.post_tasks(c,u,path,b,can_task,delete_task,current_user,respond,sanitize_description,record,notify_assignment,automate,role,apply_project_base)
            elif path in ('/api/automations/save','/api/automations/delete','/api/automations/reorder'):
                result=automation_routes.post(c,u,path,b,role,respond)
            elif path in ('/api/tokens','/api/tokens/revoke'):
                result=post_tokens(c,u,path,b)
            else: return respond({'error':'No encontrado'},404)
            if path in ('/api/task-types/delete','/api/task-types/update','/api/workspace/block'):
                g.disabled_automations.extend(automations.disable_invalid(c))
            notices=[item for item in g.disabled_automations if role(c,u,item['project_id']) in ('admin','editor')]
            for item in notices:
                item['project_name']=c.execute('SELECT name FROM projects WHERE id=?',(item['project_id'],)).fetchone()['name']
            c.commit()
            response=make_response(result if result is not None else respond({'ok':True}))
            if notices:
                payload=response.get_json();payload['disabled_automations']=notices;response.set_data(app.json.dumps(payload))
            return response
    except PermissionError as e: return respond({'error':str(e)},403)
    except (ValueError, KeyError, sqlite3.IntegrityError, json.JSONDecodeError) as e: return respond({'error':str(e) or 'Datos inválidos'},400)

def post_tokens(c,u,path,b):
    if path=='/api/tokens':
        name=b.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>60:
            raise ValueError('El nombre debe tener entre 1 y 60 caracteres')
        token='flj_'+secrets.token_urlsafe(32)
        tid=c.execute('INSERT INTO mcp_tokens(user_id,name,token_hash,created_at) VALUES(?,?,?,?)',
                      (u['id'],name.strip(),mcp_token_hash(token),now())).lastrowid
        return respond({'id':tid,'name':name.strip(),'token':token})
    tid=automations.ident(b.get('id'))
    if not c.execute('UPDATE mcp_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL',
                     (now(),tid,u['id'])).rowcount: raise ValueError('Token no encontrado')

def automate(c,tid,events,uid,project=None):
    if project is None:
        row=c.execute('SELECT ty.project_id FROM tasks t JOIN task_types ty ON ty.id=t.type_id WHERE t.id=?',(tid,)).fetchone()
        project=row['project_id'] if row else None
    g.disabled_automations.extend(automations.run(c,tid,project,events,uid,now,record,metric_event,notify_assignment))

def notify_assignment(c,tid,uid):
    import slack_notify
    slack_notify.enqueue(c,'assignment',tid,uid)

def delete_task(c,tid,uid):
    task=c.execute('SELECT title,parent_id FROM tasks WHERE id=?',(tid,)).fetchone()
    if task['parent_id']: record(c,task['parent_id'],uid,'eliminó la subtarea '+task['title'])
    c.execute('DELETE FROM tasks WHERE id=?',(tid,))

if __name__=='__main__':
    init(); app.run(host='127.0.0.1',port=PORT,debug=False,threaded=True)
