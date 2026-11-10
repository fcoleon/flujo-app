import hashlib, hmac, json, os, secrets, sqlite3, re, uuid, subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from statistics import median
from contextlib import closing
from flask import Flask, jsonify, make_response, request, send_from_directory, abort, render_template_string
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlparse, parse_qs
from html.parser import HTMLParser
from html import escape
from pathlib import Path
from settings import load_settings

SETTINGS = load_settings()
DB = SETTINGS['database']
PORT = SETTINGS['port']
STATS_ZONE = ZoneInfo(SETTINGS['timezone'])
STATIC_DIR = Path(__file__).resolve().parent / 'static'
TEMPLATE_DIR = Path(__file__).resolve().parent / 'templates'

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

def init():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, salt TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#7765d7', description TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS members(project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')), PRIMARY KEY(project_id,user_id));
        CREATE TABLE IF NOT EXISTS columns(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL, due_date TEXT, priority TEXT NOT NULL DEFAULT 'Normal', parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS placements(task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, column_id INTEGER NOT NULL REFERENCES columns(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(task_id,project_id));
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);''')
        if 'blocked' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
        if 'must_change_password' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
        c.executescript('''CREATE TABLE IF NOT EXISTS task_types(id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, is_base INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS task_statuses(id INTEGER PRIMARY KEY, type_id INTEGER NOT NULL REFERENCES task_types(id) ON DELETE CASCADE, name TEXT NOT NULL, color TEXT NOT NULL, is_done INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL);''')
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
        c.execute('CREATE TABLE IF NOT EXISTS metric_events(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, task_id INTEGER NOT NULL, event TEXT NOT NULL, occurred_at TEXT NOT NULL, task_created_at TEXT NOT NULL, type_id INTEGER NOT NULL, type_name TEXT NOT NULL)')
        metric_columns={row['name'] for row in c.execute('PRAGMA table_info(metric_events)')}
        for name,definition in [('actor_id','INTEGER'),('actor_name','TEXT'),('assignee_id','INTEGER'),('assignee_name','TEXT'),('identity_recorded','INTEGER NOT NULL DEFAULT 0')]:
            if name not in metric_columns: c.execute(f'ALTER TABLE metric_events ADD COLUMN {name} {definition}')
        c.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('metrics_identity_started_at',?)",(now(),))
        c.execute('CREATE INDEX IF NOT EXISTS metric_events_project_date ON metric_events(project_id,occurred_at)')
        c.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('metrics_started_at',?)",(now(),))
        if 'description_format' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute("ALTER TABLE tasks ADD COLUMN description_format TEXT NOT NULL DEFAULT 'plain'")

def create_type(c, project_id, name, is_base=False):
    # Historical metrics retain type IDs even after a type is deleted.
    # Never reuse an ID that is still referenced by those snapshots.
    current=c.execute('SELECT COALESCE(MAX(id),0) FROM task_types').fetchone()[0]
    has_metrics=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_events'").fetchone()
    historic=c.execute('SELECT COALESCE(MAX(type_id),0) FROM metric_events').fetchone()[0] if has_metrics else 0
    tid=max(current,historic)+1
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
    row=c.execute('SELECT t.created_at,t.type_id,t.assignee_id,ty.name,assignee.name AS assignee_name FROM tasks t JOIN task_types ty ON ty.id=t.type_id LEFT JOIN users assignee ON assignee.id=t.assignee_id WHERE t.id=?',(task_id,)).fetchone()
    if not row: return
    actor=c.execute('SELECT name FROM users WHERE id=?',(actor_id,)).fetchone()
    timestamp=now()
    for project in c.execute('SELECT project_id FROM placements WHERE task_id=?',(task_id,)).fetchall():
        c.execute('INSERT INTO metric_events(project_id,task_id,event,occurred_at,task_created_at,type_id,type_name,actor_id,actor_name,assignee_id,assignee_name,identity_recorded) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)',(project['project_id'],task_id,event,timestamp,row['created_at'],row['type_id'],row['name'],actor_id,actor['name'] if actor else None,row['assignee_id'],row['assignee_name']))

def analytics(c, project_id, month, type_filter=None):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): raise ValueError('Mes inválido')
    year,mon=map(int,month.split('-'))
    if year<2 or year>9998: raise ValueError('Mes inválido')
    start=datetime(year,mon,1,tzinfo=STATS_ZONE)
    end=datetime(year+1,1,1,tzinfo=STATS_ZONE) if mon==12 else datetime(year,mon+1,1,tzinfo=STATS_ZONE)
    rows=c.execute('SELECT * FROM metric_events WHERE project_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at,id',(project_id,start.astimezone(timezone.utc).isoformat(),end.astimezone(timezone.utc).isoformat())).fetchall()
    type_rows=c.execute('SELECT id,name FROM task_types WHERE project_id=? ORDER BY is_base DESC,name',(project_id,)).fetchall()
    types={r['id']:r['name'] for r in type_rows}
    for r in c.execute('SELECT type_id,type_name FROM metric_events WHERE project_id=? ORDER BY id',(project_id,)):
        types.setdefault(r['type_id'],r['type_name'])
    if type_filter is not None: rows=[row for row in rows if row['type_id']==type_filter]
    weeks=[{'label':f'{day}–{min(day+6,(end-start).days)}','created':0,'completed':0} for day in range(1,(end-start).days+1,7)]
    totals={'created':0,'completed':0,'reopened':0};durations=[];people={}
    def person(key,name):
        if key not in people: people[key]={'name':name,'created':0,'completed':0,'durations':[]}
        elif name and key.startswith('user:'): people[key]['name']=name
        return people[key]
    for row in rows:
        event=row['event'];totals[event]+=1
        stamp=datetime.fromisoformat(row['occurred_at']);day=stamp.astimezone(STATS_ZONE).day
        if event in ('created','completed'): weeks[(day-1)//7][event]+=1
        if event=='created':
            if not row['identity_recorded']: p=person('unknown','Sin datos históricos')
            elif row['actor_id'] is None: p=person('unknown','Sin datos históricos')
            else: p=person('user:'+str(row['actor_id']),row['actor_name'] or 'Usuario eliminado')
            p['created']+=1
        if event=='completed':
            if not row['identity_recorded']: p=person('unknown','Sin datos históricos')
            elif row['assignee_id'] is None: p=person('unassigned','Sin responsable')
            else: p=person('user:'+str(row['assignee_id']),row['assignee_name'] or 'Usuario eliminado')
            p['completed']+=1
            try:
                created=datetime.fromisoformat(row['task_created_at'])
                if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
                elapsed=(stamp-created).total_seconds()/86400
                if elapsed>=0: durations.append(elapsed);p['durations'].append(elapsed)
            except (ValueError,TypeError): pass
    buckets=[{'label':'Hasta 1 día','count':0},{'label':'1–3 días','count':0},{'label':'3–7 días','count':0},{'label':'Más de 7 días','count':0}]
    for days in durations: buckets[0 if days<=1 else 1 if days<=3 else 2 if days<=7 else 3]['count']+=1
    people_rows=[]
    for key,p in people.items():
        times=p.pop('durations');p['average_days']=sum(times)/len(times) if times else None;p['duration_count']=len(times);p['special']=not key.startswith('user:');people_rows.append(p)
    people_rows.sort(key=lambda p:(p['special'],p['name'].casefold()))
    return {'people':people_rows,'identity_started_at':c.execute("SELECT value FROM app_meta WHERE key='metrics_identity_started_at'").fetchone()['value'],'month':month,'timezone':str(STATS_ZONE),'started_at':c.execute("SELECT value FROM app_meta WHERE key='metrics_started_at'").fetchone()['value'],'types':[{'id':k,'name':v} for k,v in types.items()],'totals':totals,'average_days':sum(durations)/len(durations) if durations else None,'median_days':median(durations) if durations else None,'duration_count':len(durations),'weeks':weeks,'durations':buckets}

class DescriptionSanitizer(HTMLParser):
    allowed = {'p','div','br','strong','b','em','i','u','s','ul','ol','li','blockquote','h2','h3','a'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed: return
        if tag == 'a':
            href=dict(attrs).get('href','').strip()
            if urlparse(href).scheme.lower() not in ('http','https','mailto'): self.parts.append('<a>'); return
            self.parts.append('<a href="'+escape(href,quote=True)+'" target="_blank" rel="noopener noreferrer">'); return
        self.parts.append('<'+tag+'>')
    def handle_endtag(self, tag):
        if tag in self.allowed and tag != 'br': self.parts.append('</'+tag+'>')
    def handle_data(self, data): self.parts.append(escape(data))
def sanitize_description(value):
    parser=DescriptionSanitizer(); parser.feed(value); parser.close(); return ''.join(parser.parts)

def now(): return datetime.now(timezone.utc).isoformat()
def hashpw(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),260000).hex()
def record(c,tid,uid,msg): c.execute('INSERT INTO history(task_id,user_id,message,created_at) VALUES(?,?,?,?)',(tid,uid,msg,now()))
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
app.config['MAX_CONTENT_LENGTH'] = 100000
if os.environ.get('FLUJO_BEHIND_PROXY') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)

def respond(data,status=200): return jsonify(data),status

def current_user(c):
    return c.execute('SELECT u.id,u.name,u.username,u.admin FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?',(request.cookies.get('session',''),)).fetchone()

def session_response(token):
    response=make_response(jsonify({'ok':True}))
    response.set_cookie('session',token,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
    return response

@app.errorhandler(413)
def too_large(error): return respond({'error':'Solicitud demasiado grande'},413)

@app.get('/')
@app.get('/t/<public_id>')
def index(public_id=None):
    if public_id is not None and not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',public_id): abort(404)
    html=(TEMPLATE_DIR/'index.html').read_text(encoding='utf-8')
    response=make_response(render_template_string(html,style_version=asset_version('style.css'),
                           script_version=asset_version('app.js'),favicon_version=asset_version('favicon.svg')))
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

@app.get('/static/<any(app.js,style.css,favicon.svg):filename>')
@app.get('/<any(app.js,style.css,favicon.svg):filename>')
def static_asset(filename):
    response=send_from_directory(STATIC_DIR,filename)
    response.cache_control.no_cache=True
    return response

@app.route('/api/<path:route>',methods=['GET','POST'])
def api_dispatch(route):
    return api_get() if request.method=='GET' else api_post()

def api_get():
    path=request.path
    try:
        with closing(db()) as c, c:
            u=current_user(c)
            flags=c.execute('SELECT blocked,must_change_password FROM users WHERE id=?',(u['id'],)).fetchone() if u else None
            blocked=bool(flags and flags['blocked']);must_change=bool(flags and flags['must_change_password'])
            if path=='/api/session': return respond({'user':dict(u) if u else None,'blocked':blocked,'must_change_password':must_change,'allow_self_registration':SETTINGS['allow_self_registration'],'needs_setup':not bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())})
            if not u: return respond({'error':'Inicia sesión'},401)
            if blocked: return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
            if must_change: return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
            if path=='/api/data':
                projects=[]
                for p in c.execute('SELECT * FROM projects ORDER BY id').fetchall():
                    r=role(c,u,p['id'])
                    if r:
                        d=dict(p); d['role']=r
                        d['columns']=[dict(x) for x in c.execute('SELECT * FROM columns WHERE project_id=? ORDER BY position,id',(p['id'],))]
                        d['types']=[type_data(c,x) for x in c.execute('SELECT * FROM task_types WHERE project_id=? ORDER BY is_base DESC,id',(p['id'],))]
                        d['members']=[dict(x) for x in c.execute('SELECT u.id,u.name,u.username,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.project_id=?',(p['id'],))]
                        projects.append(d)
                allowed={p['id'] for p in projects}; tasks=[]
                for t in c.execute('''SELECT t.*,COALESCE(cm.comment_count,0) AS comment_count FROM tasks t
                                      LEFT JOIN (SELECT task_id,COUNT(*) AS comment_count FROM comments GROUP BY task_id) cm ON cm.task_id=t.id
                                      ORDER BY t.id DESC''').fetchall():
                    placements=[dict(x) for x in c.execute('SELECT * FROM placements WHERE task_id=?',(t['id'],)) if x['project_id'] in allowed]
                    if can_task(c,u,t['id']):
                        projects_for_task=task_projects(c,t['id']); explicit={r['project_id'] for r in c.execute('SELECT project_id FROM placements WHERE task_id=?',(t['id'],))}
                        d=dict(t); d['placements']=placements; d['inherited_project_ids']=sorted((projects_for_task-explicit)&allowed); d['can_edit']=can_task(c,u,t['id'],True); d['has_projects']=bool(projects_for_task); type_row=c.execute('SELECT * FROM task_types WHERE id=?',(t['type_id'],)).fetchone(); d['task_type']=type_data(c,type_row) if type_row else None; tasks.append(d)
                return respond({'user':dict(u),'version':VERSION,'workspace_name':c.execute("SELECT value FROM app_meta WHERE key='workspace_name'").fetchone()['value'],'users':[dict(x) for x in c.execute('SELECT id,name,username,admin,blocked FROM users ORDER BY name')], 'projects':projects,'tasks':tasks,'stats_timezone':str(STATS_ZONE),'base_type':type_data(c,c.execute('SELECT * FROM task_types WHERE project_id IS NULL AND is_base=1').fetchone())})
            if path=='/api/analytics':
                params=request.args.to_dict(flat=False); p=int(params['project_id'][0]); month=params['month'][0]
                if not c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone(): return respond({'error':'Proyecto no encontrado'},404)
                if role(c,u,p)!='admin': return respond({'error':'Solo los administradores del proyecto pueden ver estadísticas'},403)
                type_filter=int(params['type_id'][0]) if params.get('type_id') else None
                return respond(analytics(c,p,month,type_filter))
            if path.startswith('/api/comments/'):
                tid=int(path.split('/')[-1])
                if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
                return respond([dict(x) for x in c.execute('SELECT cm.*,COALESCE(u.name,"Usuario eliminado") AS author FROM comments cm LEFT JOIN users u ON u.id=cm.user_id WHERE task_id=? ORDER BY cm.id',(tid,))])
            if path.startswith('/api/history/'):
                tid=int(path.split('/')[-1])
                if not can_task(c,u,tid): return respond({'error':'Sin acceso'},403)
                return respond([dict(x) for x in c.execute('SELECT h.*,COALESCE(u.name,"Usuario eliminado") AS actor FROM history h LEFT JOIN users u ON u.id=h.user_id WHERE task_id=? ORDER BY h.id DESC',(tid,))])
            return respond({'error':'No encontrado'},404)
    except (ValueError, KeyError, sqlite3.IntegrityError) as e: return respond({'error':str(e)},400)

def api_post():
    path=request.path
    try:
        raw=request.get_data(cache=False)
        b=json.loads(raw or b'{}')
        if not isinstance(b,dict): raise ValueError('Datos inválidos')
        with closing(db()) as c, c:
            u=current_user(c)
            if path in ('/api/login','/api/setup','/api/register'):
                if path=='/api/register' and not SETTINGS['allow_self_registration']:
                    return respond({'error':'El registro está deshabilitado'},403)
                if path in ('/api/setup','/api/register'):
                    # Serializa la creación de la primera cuenta entre workers.
                    c.execute('BEGIN IMMEDIATE')
                    exists=bool(c.execute('SELECT 1 FROM users').fetchone())
                    if path=='/api/setup' and exists: raise ValueError('La aplicación ya está configurada')
                    if path=='/api/register' and not exists: raise ValueError('Primero se debe crear la cuenta administradora')
                    name=b.get('name','').strip(); username=b.get('username','').strip().lower(); pw=b.get('password','')
                    if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
                    s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin) VALUES(?,?,?,?,?)',(name,username,hashpw(pw,s),s,int(path=='/api/setup')))
                row=c.execute('SELECT * FROM users WHERE username=?',(b.get('username','').strip().lower(),)).fetchone()
                if not row or not hmac.compare_digest(row['password'],hashpw(b.get('password',''),row['salt'])): return respond({'error':'Usuario o contraseña incorrectos'},401)
                token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?)',(token,row['id'])); c.commit()
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
                c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));token=secrets.token_urlsafe(32);c.execute('INSERT INTO sessions VALUES(?,?)',(token,u['id']));c.commit()
                return session_response(token)
            if flags['must_change_password']:
                return respond({'error':'Debes cambiar tu contraseña temporal','password_change_required':True},403)
            if path=='/api/workspace/update':
                if not u['admin']: raise PermissionError('Solo un administrador del workspace puede cambiar el nombre')
                name=b.get('name')
                if not isinstance(name,str) or not name.strip() or len(name.strip())>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
                c.execute("UPDATE app_meta SET value=? WHERE key='workspace_name'",(name.strip(),))
            elif path=='/api/workspace/block':
                if not u['admin']: raise PermissionError('Solo un administrador del workspace puede bloquear usuarios')
                target=int(b['user_id']); blocked=b.get('blocked')
                if type(blocked) is not bool: raise ValueError('Estado de bloqueo inválido')
                if target==u['id']: raise ValueError('No puedes bloquear tu propia cuenta')
                if not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): raise ValueError('Usuario no encontrado')
                c.execute('UPDATE users SET blocked=? WHERE id=?',(int(blocked),target))
            elif path=='/api/profile':
                name=b.get('name')
                if not isinstance(name,str) or not name.strip() or len(name.strip())>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
                current=b.get('current_password',''); new=b.get('new_password',''); confirmation=b.get('confirm_password','')
                if not all(isinstance(value,str) for value in (current,new,confirmation)): raise ValueError('Contraseña inválida')
                if current or new or confirmation:
                    if len(new)<8: raise ValueError('La nueva contraseña debe tener al menos 8 caracteres')
                    if new!=confirmation: raise ValueError('Las contraseñas nuevas no coinciden')
                    credentials=c.execute('SELECT password,salt FROM users WHERE id=?',(u['id'],)).fetchone()
                    if not hmac.compare_digest(credentials['password'],hashpw(current,credentials['salt'])): raise ValueError('La contraseña actual es incorrecta')
                    salt=secrets.token_hex(16)
                    c.execute('UPDATE users SET password=?,salt=? WHERE id=?',(hashpw(new,salt),salt,u['id']))
                c.execute('UPDATE users SET name=? WHERE id=?',(name.strip(),u['id']))
            elif path=='/api/users':
                if not u['admin']: raise PermissionError('Solo el administrador del workspace puede crear usuarios')
                name=b.get('name','').strip(); username=b.get('username','').strip().lower(); pw=b.get('password','')
                if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
                s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin,must_change_password) VALUES(?,?,?,?,?,1)',(name,username,hashpw(pw,s),s,int(bool(b.get('admin')))))
            elif path=='/api/projects':
                if not u['admin']: raise PermissionError('Solo el administrador del workspace puede crear proyectos')
                name=b.get('name','').strip()
                if not name: raise ValueError('El nombre es obligatorio')
                p=c.execute('INSERT INTO projects(name,color,description) VALUES(?,?,?)',(name,b.get('color','#7765d7'),b.get('description',''))).lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(p,u['id'],'admin'))
                ensure_base_type(c,p)
                for i,n in enumerate(['Por hacer','En progreso','En revisión','Completado']): c.execute('INSERT INTO columns(project_id,name,position) VALUES(?,?,?)',(p,n,i))
                c.commit(); return respond({'id':p})
            elif path=='/api/projects/update':
                p=int(b['project_id']); project=c.execute('SELECT id,color FROM projects WHERE id=?',(p,)).fetchone()
                if not project: raise ValueError('Proyecto no encontrado')
                if role(c,u,p)!='admin': raise PermissionError('Solo un administrador del proyecto puede editarlo')
                name=str(b.get('name','')).strip(); description=str(b.get('description','')).strip()
                if not name or len(name)>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
                if len(description)>5000: raise ValueError('La descripción debe tener hasta 5000 caracteres')
                color=b.get('color',project['color'])
                if 'color' in b and (not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color)): raise ValueError('Color inválido')
                c.execute('UPDATE projects SET name=?,description=?,color=? WHERE id=?',(name,description,color,p))
            elif path=='/api/members':
                p=int(b['project_id'])
                if role(c,u,p)!='admin': raise PermissionError('No tienes permisos para administrar este proyecto')
                if b.get('role')=='remove': c.execute('DELETE FROM members WHERE project_id=? AND user_id=?',(p,int(b['user_id'])))
                elif b.get('role') in ('admin','editor','viewer'): c.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role',(p,int(b['user_id']),b['role']))
                else: raise ValueError('Rol inválido')
            elif path=='/api/columns':
                p=int(b['project_id']); name=b.get('name','').strip()
                if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if not name: raise ValueError('El nombre es obligatorio')
                c.execute('INSERT INTO columns(project_id,name,position) VALUES(?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM columns WHERE project_id=?))',(p,name,p))
            elif path=='/api/columns/rename':
                col=int(b['column_id']); name=str(b.get('name','')).strip()
                column=c.execute('SELECT * FROM columns WHERE id=?',(col,)).fetchone()
                if not column: raise ValueError('Columna no encontrada')
                if role(c,u,column['project_id']) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if not name or len(name)>100: raise ValueError('El nombre debe tener entre 1 y 100 caracteres')
                c.execute('UPDATE columns SET name=? WHERE id=?',(name,col))
            elif path=='/api/columns/reorder':
                p=int(b['project_id']); order=b.get('order')
                if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if not isinstance(order,list) or any(type(x) is not int for x in order): raise ValueError('Orden inválido')
                current={row['id'] for row in c.execute('SELECT id FROM columns WHERE project_id=?',(p,))}
                if len(order)!=len(current) or set(order)!=current: raise ValueError('El tablero cambió; vuelve a intentarlo')
                for position,col in enumerate(order): c.execute('UPDATE columns SET position=? WHERE project_id=? AND id=?',(position,p,col))
            elif path=='/api/tasks':
                title=b.get('title','').strip(); parent=b.get('parent_id') or None
                if not title: raise ValueError('El nombre es obligatorio')
                if parent:
                    parent=int(parent)
                    if not can_task(c,u,parent,True): raise PermissionError('Sin permiso para editar la tarea principal')
                p=int(b['project_id']) if b.get('project_id') else None
                if p:
                    col=int(b['column_id'])
                    if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                    if not c.execute('SELECT 1 FROM columns WHERE id=? AND project_id=?',(col,p)).fetchone(): raise ValueError('Columna inválida')
                elif not parent and not u['admin']:
                    raise PermissionError('Solo un administrador puede crear tareas sin proyecto')
                type_id=int(b['type_id']) if b.get('type_id') else ensure_base_type(c,p)
                selected=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not selected or selected['project_id'] != p: raise ValueError('Tipo de tarea inválido para este proyecto')
                tid=c.execute('INSERT INTO tasks(title,created_at,parent_id,type_id,status_id,public_id) VALUES(?,?,?,?,?,?)',(title,now(),parent,type_id,default_status(c,type_id),str(uuid.uuid4()))).lastrowid
                if p: c.execute('INSERT INTO placements VALUES(?,?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE column_id=?))',(tid,p,col,col))
                record(c,tid,u['id'],'creó la tarea')
                metric_event(c,tid,'created',u['id'])
                if parent: record(c,parent,u['id'],'agregó la subtarea '+title)
                c.commit(); return respond({'id':tid})
            elif path=='/api/task-types':
                p=int(b['project_id']); name=str(b.get('name','')).strip()
                if role(c,u,p)!='admin': raise PermissionError('Solo un administrador del proyecto puede configurar tipos')
                if not name or len(name)>100: raise ValueError('El nombre del tipo es obligatorio y debe tener hasta 100 caracteres')
                type_id=create_type(c,p,name)
                c.commit(); return respond({'id':type_id})
            elif path=='/api/task-types/delete':
                type_id=int(b['id']); typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ or typ['project_id'] is None or role(c,u,typ['project_id'])!='admin': raise PermissionError('Sin permiso para configurar este tipo')
                if typ['is_base']: raise ValueError('No se puede eliminar el tipo base')
                if c.execute('SELECT 1 FROM tasks WHERE type_id=? LIMIT 1',(type_id,)).fetchone(): raise ValueError('Hay tareas con este tipo. Cámbialas de tipo antes de eliminarlo, incluso si están en otro tablero o sin proyecto')
                c.execute('DELETE FROM task_types WHERE id=?',(type_id,))
            elif path=='/api/task-types/update':
                type_id=int(b['id']); typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ or typ['project_id'] is None or role(c,u,typ['project_id'])!='admin': raise PermissionError('Sin permiso para configurar este tipo')
                name=str(b.get('name','')).strip(); statuses=b.get('statuses')
                if not name or len(name)>100 or not isinstance(statuses,list): raise ValueError('Datos del tipo inválidos')
                existing={r['id']:r for r in c.execute('SELECT * FROM task_statuses WHERE type_id=?',(type_id,))}
                provided=[int(x['id']) for x in statuses if x.get('id')]
                deleted=b.get('deleted_status_ids',[])
                if not isinstance(deleted,list) or any(type(x) is not int for x in deleted): raise ValueError('Lista de estados inválida')
                if len(provided)!=len(set(provided)) or len(deleted)!=len(set(deleted)) or set(provided)&set(deleted) or set(provided)|set(deleted)!=set(existing): raise ValueError('Los estados cambiaron; vuelve a cargar')
                if typ['is_base'] and (len(statuses)!=2 or deleted): raise ValueError('La tarea base debe conservar sus dos estados')
                if not any(not bool(x.get('is_done')) for x in statuses) or not any(bool(x.get('is_done')) for x in statuses): raise ValueError('Incluye al menos un estado en curso y uno finalizado')
                for status_id in deleted:
                    if c.execute('SELECT 1 FROM tasks WHERE status_id=? LIMIT 1',(status_id,)).fetchone(): raise ValueError('Hay tareas con un estado que intentas eliminar. Cambia su estado antes de eliminarlo, incluso si están en otro tablero o sin proyecto')
                for status_id in deleted: c.execute('DELETE FROM task_statuses WHERE id=? AND type_id=?',(status_id,type_id))
                for i,st in enumerate(statuses):
                    label=str(st.get('name','')).strip(); color=str(st.get('color',''))
                    if not label or len(label)>100 or not re.fullmatch(r'#[0-9a-fA-F]{6}',color): raise ValueError('Nombre o color del estado inválido')
                    if st.get('id'):
                        if typ['is_base'] and bool(st.get('is_done'))!=bool(existing[int(st['id'])]['is_done']): raise ValueError('No se puede cambiar la categoría de un estado base')
                        changed_category=bool(st.get('is_done'))!=bool(existing[int(st['id'])]['is_done'])
                        c.execute('UPDATE task_statuses SET name=?,color=?,is_done=?,position=? WHERE id=? AND type_id=?',(label,color,int(bool(st.get('is_done'))),i,int(st['id']),type_id))
                        if changed_category:
                            for task in c.execute('SELECT id FROM tasks WHERE status_id=?',(int(st['id']),)).fetchall(): metric_event(c,task['id'],'completed' if st.get('is_done') else 'reopened',u['id'])
                    else: c.execute('INSERT INTO task_statuses(type_id,name,color,is_done,position) VALUES(?,?,?,?,?)',(type_id,label,color,int(bool(st.get('is_done'))),i))
                c.execute('UPDATE task_types SET name=? WHERE id=?',(name,type_id))
            elif path=='/api/task/type-status':
                tid=int(b['id']); type_id=int(b['type_id']); status_id=int(b['status_id']) if b.get('status_id') else None
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para editar')
                task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                typ=c.execute('SELECT * FROM task_types WHERE id=?',(type_id,)).fetchone()
                if not typ: raise ValueError('Tipo inválido')
                if type_id!=task['type_id']:
                    if typ['project_id'] is None and c.execute('SELECT 1 FROM placements WHERE task_id=?',(tid,)).fetchone(): raise ValueError('El tipo base general solo está disponible para tareas sin proyecto')
                    if typ['project_id'] is not None and (role(c,u,typ['project_id']) not in ('admin','editor') or not c.execute('SELECT 1 FROM placements WHERE task_id=? AND project_id=?',(tid,typ['project_id'])).fetchone()): raise PermissionError('El tipo debe pertenecer a un proyecto editable de la tarea')
                    old_done=c.execute('SELECT is_done FROM task_statuses WHERE id=?',(task['status_id'],)).fetchone()['is_done']
                    status_id=default_status(c,type_id)
                    c.execute('UPDATE tasks SET type_id=?,status_id=? WHERE id=?',(type_id,status_id,tid))
                    if old_done: metric_event(c,tid,'reopened',u['id'])
                    record(c,tid,u['id'],'cambió el tipo a '+typ['name']+' y reinició el estado')
                else:
                    status=c.execute('SELECT * FROM task_statuses WHERE id=? AND type_id=?',(status_id,type_id)).fetchone()
                    if not status: raise ValueError('Estado inválido para el tipo')
                    if status_id!=task['status_id']:
                        old_done=c.execute('SELECT is_done FROM task_statuses WHERE id=?',(task['status_id'],)).fetchone()['is_done']
                        c.execute('UPDATE tasks SET status_id=? WHERE id=?',(status_id,tid)); record(c,tid,u['id'],'cambió el estado a '+status['name'])
                        if bool(old_done)!=bool(status['is_done']): metric_event(c,tid,'completed' if status['is_done'] else 'reopened',u['id'])
            elif path=='/api/comments':
                tid=int(b['task_id']); body=str(b.get('body','')).strip()
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para comentar')
                if not body: raise ValueError('Escribe un comentario')
                if len(body)>10000: raise ValueError('El comentario es demasiado largo')
                c.execute('INSERT INTO comments(task_id,user_id,body,created_at) VALUES(?,?,?,?)',(tid,u['id'],body,now()))
            elif path=='/api/comments/update':
                comment=c.execute('SELECT id,task_id,user_id,body FROM comments WHERE id=?',(int(b['id']),)).fetchone()
                if not comment: return respond({'error':'Comentario no encontrado'},404)
                if comment['user_id']!=u['id'] or not can_task(c,u,comment['task_id'],True):
                    raise PermissionError('Sin permiso para editar este comentario')
                body=b.get('body')
                if not isinstance(body,str) or not body.strip(): raise ValueError('Escribe un comentario')
                body=body.strip()
                if len(body)>10000: raise ValueError('El comentario es demasiado largo')
                if body!=comment['body']:
                    c.execute('UPDATE comments SET body=? WHERE id=?',(body,comment['id']))
                    record(c,comment['task_id'],u['id'],'editó un comentario')
                return respond({'ok':True,'body':body})
            elif path=='/api/task/delete':
                tid=int(b['id'])
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para eliminar la tarea')
                task=c.execute('SELECT title,parent_id FROM tasks WHERE id=?',(tid,)).fetchone()
                if task['parent_id']: record(c,task['parent_id'],u['id'],'eliminó la subtarea '+task['title'])
                c.execute('DELETE FROM tasks WHERE id=?',(tid,))
            elif path=='/api/task/update':
                tid=int(b['id'])
                if not can_task(c,u,tid,True): raise PermissionError('Sin permiso para editar')
                old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                if not old: raise ValueError('Tarea no encontrada')
                labels={'title':'nombre','description':'descripción','assignee_id':'responsable','due_date':'fecha límite','priority':'prioridad'}
                for key,label in labels.items():
                    if key in b:
                        val=b[key] or None if key in ('assignee_id','due_date') else str(b[key]).strip()
                        if key=='title' and not val: raise ValueError('El nombre es obligatorio')
                        if key=='description':
                            fmt='html' if b.get('description_format')=='html' else 'plain'
                            if fmt=='html': val=sanitize_description(val)
                            c.execute('UPDATE tasks SET description_format=? WHERE id=?',(fmt,tid))
                        if str(old[key] or '')!=str(val or ''): c.execute(f'UPDATE tasks SET {key}=? WHERE id=?',(val,tid)); record(c,tid,u['id'],f'actualizó {label}')
            elif path=='/api/task/move':
                tid=int(b['id']); p=int(b['project_id']); col=int(b['column_id']); order=b['order']
                if role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if not c.execute('SELECT 1 FROM columns WHERE id=? AND project_id=?',(col,p)).fetchone(): raise ValueError('Columna inválida')
                old=c.execute('SELECT column_id FROM placements WHERE task_id=? AND project_id=?',(tid,p)).fetchone()
                if not old: raise ValueError('Tarea no encontrada en el proyecto')
                existing={x['task_id'] for x in c.execute('SELECT task_id FROM placements WHERE project_id=? AND column_id=?',(p,col))}; expected=existing|{tid}
                if len(order)!=len(expected) or set(order)!=expected: raise ValueError('El tablero cambió; vuelve a intentarlo')
                c.execute('UPDATE placements SET column_id=? WHERE task_id=? AND project_id=?',(col,tid,p))
                for i,t in enumerate(order): c.execute('UPDATE placements SET position=? WHERE task_id=? AND project_id=?',(i,t,p))
                name=c.execute('SELECT name FROM columns WHERE id=?',(col,)).fetchone()['name']; record(c,tid,u['id'],('movió a '+name) if old['column_id']!=col else 'cambió el orden en '+name)
            elif path=='/api/task/project':
                tid=int(b['id']); p=int(b['project_id'])
                if not can_task(c,u,tid,True) or role(c,u,p) not in ('admin','editor'): raise PermissionError('Sin permiso para editar')
                if b.get('remove'):
                    if not c.execute('SELECT 1 FROM placements WHERE task_id=? AND project_id=?',(tid,p)).fetchone(): raise ValueError('La tarea no pertenece a este proyecto')
                    c.execute('DELETE FROM placements WHERE task_id=? AND project_id=?',(tid,p)); record(c,tid,u['id'],'quitó la tarea de un proyecto')
                else:
                    col=c.execute('SELECT id FROM columns WHERE project_id=? ORDER BY position LIMIT 1',(p,)).fetchone()
                    if not col: raise ValueError('El proyecto no tiene columnas')
                    c.execute('INSERT INTO placements VALUES(?,?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE column_id=?))',(tid,p,col['id'],col['id']))
                    apply_project_base(c,tid,p)
                    record(c,tid,u['id'],'agregó la tarea a un proyecto')
            else: return respond({'error':'No encontrado'},404)
            c.commit(); return respond({'ok':True})
    except PermissionError as e: return respond({'error':str(e)},403)
    except (ValueError, KeyError, sqlite3.IntegrityError, json.JSONDecodeError) as e: return respond({'error':str(e) or 'Datos inválidos'},400)

if __name__=='__main__':
    init(); app.run(host='127.0.0.1',port=PORT,debug=False,threaded=True)
