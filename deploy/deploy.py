#!/usr/bin/env python3
"""Genera e instala la configuración de Flujo para Ubuntu, Supervisor y Nginx."""
import argparse
import configparser
import errno
import grp
import ipaddress
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import stat
import textwrap
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
APP_DIR = DEPLOY_DIR.parent.resolve()
sys.path.insert(0, str(APP_DIR))
from settings import load_settings

PROGRAM = 'flujo-app'
SLACK_PROGRAM = 'flujo-slack'
MCP_PROGRAM = 'flujo-mcp'
SUPERVISOR_FILE = Path('/etc/supervisor/conf.d/flujo-app.conf')
NGINX_FILE = Path('/etc/nginx/sites-available/flujo-app')
NGINX_LINK = Path('/etc/nginx/sites-enabled/flujo-app')
TMPFILES_FILE = Path('/etc/tmpfiles.d/flujo-app.conf')
LEGACY_NGINX_STATIC_FILE = Path('/etc/nginx/snippets/flujo-app-static.conf')
PREVIOUS_STATIC_BLOCK = r'''# BEGIN flujo-app assets
location ~ "^/assets/(style\.[0-9a-f]{16}\.css|app\.[0-9a-f]{16}\.js|favicon\.[0-9a-f]{16}\.svg)$" {
    root /var/lib/flujo-app;
    try_files $uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable";
    access_log off;
}
# END flujo-app assets'''
PREVIOUS_STATIC_PREFIX_BLOCK = r'''# BEGIN flujo-app assets
location ^~ /assets/ {
    root /var/lib/flujo-app;
    if ($uri !~ "^/assets/(style\.[0-9a-f]{16}\.css|app\.[0-9a-f]{16}\.js|favicon\.[0-9a-f]{16}\.svg)$") {
        return 404;
    }
    try_files $uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable";
}
# END flujo-app assets'''
SAFE_PATH = re.compile(r'^/[A-Za-z0-9_./+@-]+$')
SAFE_USER = re.compile(r'^[a-z_][a-z0-9_-]*[$]?$')


def fail(message):
    raise ValueError(message)


def check_server_name(value):
    try:
        ipaddress.IPv4Address(value)
        return value
    except ValueError:
        pass
    labels = value.rstrip('.').split('.')
    if (len(value) > 253 or not value or any(
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
        for label in labels
    )):
        fail('El nombre del servidor debe ser un dominio o una dirección IPv4 válida.')
    return value


def check_path(value, label):
    path = str(value)
    if not SAFE_PATH.fullmatch(path):
        fail(f'{label} debe ser una ruta absoluta sin espacios ni caracteres especiales de configuración.')
    return path


def render(template, values):
    source = (DEPLOY_DIR / template).read_text(encoding='utf-8')
    for key, value in values.items():
        source = source.replace('@@' + key + '@@', str(value))
    if re.search(r'@@[A-Z_]+@@', source):
        fail(f'Faltan valores para {template}.')
    return source


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def as_user(user, *command):
    return run(['runuser', '-u', user, '--', *map(str, command)])


def check_writable(user, path, description):
    try:
        as_user(user, 'test', '-w', path)
    except subprocess.CalledProcessError:
        fail(f'{user} necesita permiso de escritura para {description}: {path}')


def supervisor_process_pid(program=PROGRAM):
    result = subprocess.run(['supervisorctl', 'pid', program], capture_output=True, text=True)
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode == 0 and output.isdigit():
        return int(output)
    if 'no such process' in output.lower():
        return None
    fail('No se pudo consultar Supervisor. Comprueba que el servicio esté activo: ' + output)


def supervisor_config_path():
    candidates=[]
    for path in SUPERVISOR_FILE.parent.glob('*.conf'):
        if path.is_symlink() or not path.is_file(): continue
        config=configparser.RawConfigParser(interpolation=None)
        try:
            with path.open(encoding='utf-8') as stream: config.read_file(stream)
        except (configparser.Error,UnicodeError): continue
        if config.has_section('program:'+PROGRAM): candidates.append(path)
    if len(candidates)>1:
        fail('Hay varias configuraciones de Supervisor para flujo-app: '+', '.join(map(str,sorted(candidates))))
    if candidates: return candidates[0]
    return SUPERVISOR_FILE


def check_program_identity():
    config_path=supervisor_config_path()
    if config_path.is_symlink() or (config_path.exists() and not config_path.is_file()):
        fail(f'La configuración de Supervisor no es un archivo regular: {config_path}')
    if config_path.exists():
        config = configparser.RawConfigParser(interpolation=None)
        try:
            with config_path.open(encoding='utf-8') as stream:
                config.read_file(stream)
            section = config['program:' + PROGRAM]
            expected = {f'{APP_DIR}/.venv/bin/python -u {APP_DIR}/app.py',
                        f'{APP_DIR}/.venv/bin/gunicorn --config {APP_DIR}/deploy/gunicorn.conf.py app:app'}
            if section.get('directory') != str(APP_DIR) or section.get('command') not in expected:
                raise ValueError()
        except (configparser.Error, KeyError, ValueError):
            fail(f'{config_path} pertenece a otra configuración o ruta. Revísala antes de continuar.')
    pid = supervisor_process_pid()
    if pid is not None and not config_path.exists():
        fail(f'Supervisor tiene registrado {PROGRAM}, pero no se encontró {SUPERVISOR_FILE}. '
             'La configuración puede tener otro nombre o haberse movido. '
             f'Busca el archivo con: sudo grep -rlF "[program:{PROGRAM}]" /etc/supervisor/')
    return pid


def validate_socket_path(value):
    raw = check_path(value, 'La ruta del socket')
    path = Path(raw)
    if '..' in path.parts or path == Path('/') or len(os.fsencode(raw)) > 100:
        fail('El socket debe tener una ruta absoluta normal de hasta 100 bytes.')
    return path


def check_socket(path, user, own_pid):
    if path.is_symlink(): fail(f'La ruta del socket es un enlace simbólico: {path}')
    if not path.exists(): return
    info = path.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != pwd.getpwnam(user).pw_uid:
        fail(f'La ruta del socket está ocupada por otro archivo o usuario: {path}')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        result = probe.connect_ex(str(path))
    if result == errno.ECONNREFUSED: return
    if result == 0 and own_pid and shutil.which('ss'):
        output = subprocess.run(['ss','-xlnp'],capture_output=True,text=True,check=True).stdout
        for line in output.splitlines():
            if str(path) in line.split() and str(own_pid) in re.findall(r'pid=(\d+)',line): return
    fail(f'El socket está en uso o no se puede verificar su propietario: {path}')


def read_socket_runtime(user):
    if TMPFILES_FILE.is_symlink() or not TMPFILES_FILE.is_file():
        fail('Falta la configuración del socket; ejecuta primero install.')
    lines = [line.strip() for line in TMPFILES_FILE.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(lines)==1 and lines[0].startswith('d '):
        parts=lines[0].split()
        if len(parts)==6 and parts[0]=='d' and parts[2]=='0750' and parts[3]==user and parts[5]=='-':
            return parts[4],Path(check_path(parts[1],'El directorio del socket'))
    match=re.fullmatch(r'# flujo-app user=([a-z_][a-z0-9_-]*[$]?) group=([a-z_][a-z0-9_-]*[$]?)',lines[0]) if lines else None
    if not match or match.group(1)!=user or len(lines)>2:
        fail('La configuración tmpfiles del socket no coincide con esta instalación.')
    managed=None
    if len(lines)==2:
        parts=lines[1].split()
        if len(parts)!=6 or parts[0]!='d' or parts[2]!='0750' or parts[3]!=user or parts[4]!=match.group(2) or parts[5]!='-':
            fail('La configuración tmpfiles del socket no coincide con esta instalación.')
        managed=Path(check_path(parts[1],'El directorio del socket'))
    return match.group(2),managed


def ensure_socket_directory(user, group, socket_path, install):
    parent=socket_path.parent;uid=pwd.getpwnam(user).pw_uid;gid=grp.getgrnam(group).gr_gid
    previous=None
    if TMPFILES_FILE.is_symlink() or (TMPFILES_FILE.exists() and not TMPFILES_FILE.is_file()):
        fail(f'La configuración tmpfiles no es un archivo regular: {TMPFILES_FILE}')
    if TMPFILES_FILE.exists():
        previous_group,previous=read_socket_runtime(user)
        if previous_group!=group: fail(f'{TMPFILES_FILE} ya existe con otra configuración de grupo.')
    elif not install:
        fail('Falta la configuración del socket; ejecuta primero install.')
    if parent.exists() and not parent.is_dir(): fail(f'La carpeta del socket no es un directorio: {parent}')
    if not parent.exists() and not parent.parent.is_dir():
        fail(f'El directorio superior debe existir: {parent.parent}')
    managed = parent if not parent.exists() or previous==parent else None
    if managed and parent.exists():
        if parent.is_symlink(): fail(f'El directorio administrado no puede ser un enlace simbólico: {parent}')
        info=parent.stat()
        if (info.st_uid,info.st_gid)!=(uid,gid):
            fail(f'{parent} pertenece a otro usuario o grupo; revísalo antes de continuar.')
    rule=f'd {parent} 0750 {user} {group} -' if managed else ''
    content=render('tmpfiles.conf.template',{'APP_USER':user,'NGINX_GROUP':group,'SOCKET_DIRECTORY_RULE':rule})
    old_content = TMPFILES_FILE.read_text(encoding='utf-8') if TMPFILES_FILE.exists() else None
    try:
        if old_content != content: write_atomic(TMPFILES_FILE, content)
        if managed: run(['systemd-tmpfiles','--create',str(TMPFILES_FILE)])
        info = parent.stat()
        if info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o007:
            fail(f'El directorio {parent} debe pertenecer a {user}:{group} y no permitir acceso a otros usuarios. '
                 'Usa un directorio privado para el socket o configura sus permisos manualmente.')
        for flag in ('-x','-w'):
            try: as_user(user,'test',flag,parent)
            except subprocess.CalledProcessError: fail(f'{user} necesita permisos de acceso y escritura en {parent}.')
        try:
            account=pwd.getpwnam(group)
        except KeyError:
            account=None
        if account and account.pw_gid==gid:
            try: as_user(group,'test','-x',parent)
            except subprocess.CalledProcessError: fail(f'Nginx necesita permiso para atravesar {parent}.')
        else:
            for directory in (parent,*parent.parents):
                info=directory.stat()
                if not (info.st_mode & stat.S_IXOTH or (info.st_gid==gid and info.st_mode & stat.S_IXGRP)):
                    fail(f'El grupo {group} necesita permiso para atravesar {directory}.')
    except BaseException:
        if old_content != content:
            if old_content is None: TMPFILES_FILE.unlink(missing_ok=True)
            else: write_atomic(TMPFILES_FILE, old_content)
        raise


def installed_socket_group(user, socket_path=None, requested_group=None):
    if TMPFILES_FILE.is_symlink() or (TMPFILES_FILE.exists() and not TMPFILES_FILE.is_file()):
        fail(f'La configuración tmpfiles no es un archivo regular: {TMPFILES_FILE}')
    if TMPFILES_FILE.exists():
        group=read_socket_runtime(user)[0]
        if requested_group and requested_group!=group:
            fail(f'El grupo configurado para el socket es {group}; revisa {TMPFILES_FILE}.')
        return group
    if socket_path is None: fail('Falta la configuración del socket; indica la ruta para reconstruirla.')
    parent=socket_path.parent
    group=requested_group or 'www-data'
    if parent.is_dir():
        info=parent.stat()
        if info.st_uid!=pwd.getpwnam(user).pw_uid:
            fail(f'El directorio del socket {parent} no pertenece a {user}; revisa sus permisos.')
        actual=grp.getgrgid(info.st_gid).gr_name
        if actual!=group:
            fail(f'El directorio del socket usa el grupo {actual}, pero Nginx se espera en {group}. '
                 'Si Nginx usa ese grupo, ejecuta update --nginx-group '+actual+'.')
    print(f'No se encontró {TMPFILES_FILE}; se preparará la configuración del socket con el grupo {group}.',flush=True)
    return group


def prepare_venv(user, db_path, timezone, create=True):
    modules = [APP_DIR / name for name in ('app.py', 'settings.py', 'analytics.py', 'auth.py', 'descriptions.py', 'slack_notify.py', 'slack_auth.py', 'automations.py', 'mcp_server.py')]
    if not all(path.is_file() for path in (*modules, APP_DIR / 'templates' / 'index.html')):
        fail(f'No se encontró la aplicación completa en {APP_DIR}. Copia sus archivos antes de continuar.')
    for path in (APP_DIR, *modules, APP_DIR / 'config.ini', APP_DIR / 'templates' / 'index.html', *(APP_DIR / 'static' / name for name in ('app.js', 'style.css', 'favicon.svg'))):
        try:
            as_user(user, 'test', '-x' if path.is_dir() else '-r', path)
        except subprocess.CalledProcessError:
            fail(f'{user} no puede acceder a {path}. Revisa los permisos de la ruta.')
    python = APP_DIR / '.venv' / 'bin' / 'python'
    if not python.exists():
        if not create:
            fail('No se encontró .venv. Ejecuta primero el modo install.')
        if (APP_DIR / '.venv').exists():
            fail('Existe .venv, pero no contiene bin/python. Recréalo en Ubuntu con el usuario de la app.')
        print(f'Creando {APP_DIR / ".venv"} como {user}.', flush=True)
        as_user(user, 'python3', '-m', 'venv', APP_DIR / '.venv')
    if not python.is_file():
        fail('No se encontró el Python del entorno virtual. Recréalo en Ubuntu.')
    # También detecta un .venv copiado de otro sistema o una zona horaria inexistente.
    try:
        as_user(user, python, '-c', 'import sys; from zoneinfo import ZoneInfo; '
                'assert sys.version_info >= (3, 9), "Se requiere Python 3.9 o superior"; '
                'ZoneInfo(sys.argv[1])', timezone)
    except (subprocess.CalledProcessError, OSError):
        fail('El Python de .venv no funciona, es anterior a 3.9 o no reconoce la zona horaria. '
             'Revisa python3-venv y tzdata; recrea .venv en Ubuntu si fue copiado.')
    if not db_path.parent.is_dir():
        fail(f'No existe el directorio de la base de datos: {db_path.parent}')
    check_writable(user, db_path.parent, 'el directorio de SQLite')
    if db_path.exists():
        check_writable(user, db_path, 'la base de datos SQLite')
        as_user(user, 'test', '-r', db_path)
    as_user(user, python, '-m', 'pip', 'install', '-r', APP_DIR / 'requirements.txt')


def installed_supervisor_settings():
    config_path=supervisor_config_path()
    if not config_path.is_file() or config_path.is_symlink():
        fail('No se encontró la configuración de Supervisor de Flujo. Ejecuta primero install.')
    config = configparser.RawConfigParser(interpolation=None)
    with config_path.open(encoding='utf-8') as stream:
        config.read_file(stream)
    section = config['program:' + PROGRAM]
    env = section.get('environment', '')
    expected_path = f'APP_CONFIG="{APP_DIR}/config.ini"'
    if expected_path not in env or 'FLUJO_CONFIG_ONLY="1"' not in env:
        fail('La configuración de Supervisor es de una versión anterior. Copia sus valores a config.ini '
             'y ejecuta install --skip-nginx para actualizarla sin modificar Nginx.')
    expected = f'{APP_DIR}/.venv/bin/gunicorn --config {APP_DIR}/deploy/gunicorn.conf.py app:app'
    if section.get('command') != expected:
        fail('Supervisor usa el servidor anterior. Sigue los pasos de migración en deploy/README.md.')
    return section['user']


def check_nginx_socket(socket_path, mcp_socket_path=None):
    if not NGINX_FILE.is_file():
        print('Aviso: verifica que tu proxy Nginx apunte al socket configurado.', flush=True)
        return
    text = NGINX_FILE.read_text(encoding='utf-8')
    upstreams = set(re.findall(r'proxy_pass\s+(http://(?:127\.0\.0\.1:\d+/?|unix:[^;\s]+))\s*;', text))
    upstreams = {upstream.removesuffix(':') for upstream in upstreams}
    expected = f'http://unix:{socket_path}'
    allowed = {expected}
    if mcp_socket_path: allowed.add(f'http://unix:{mcp_socket_path}')
    if upstreams and (expected not in upstreams or not upstreams <= allowed):
        fail('Nginx apunta a otro destino. Actualiza proxy_pass al socket de config.ini antes de continuar.')
    if not upstreams:
        print('Aviso: verifica manualmente que Nginx apunte al socket configurado.', flush=True)
    elif f'http://unix:{mcp_socket_path}' not in upstreams:
        print('Aviso: Nginx no enruta /mcp; agrega el location de deploy/nginx.conf.template.', flush=True)


def plan_nginx_socket_update(socket_path, mcp_socket_path=None):
    if not NGINX_FILE.is_file():
        print('Aviso: verifica manualmente que Nginx apunte al socket configurado.', flush=True)
        return None
    if NGINX_FILE.is_symlink():
        fail(f'Edita manualmente el destino de Nginx en {NGINX_FILE}; es un enlace simbólico.')
    original = NGINX_FILE.read_text(encoding='utf-8')
    pattern = re.compile(r'(?m)^(\s*proxy_pass\s+)([^;\s]+)(\s*;)')
    matches = list(pattern.finditer(original))
    if not matches:
        print('Aviso: verifica manualmente que Nginx apunte al socket configurado.', flush=True)
        return None
    # El destino MCP se reconoce por su nombre; el resto son destinos de la aplicación.
    is_mcp = lambda value: value.removesuffix(':').endswith('/mcp.sock')
    app_upstreams = {m.group(2).removesuffix(':') for m in matches if not is_mcp(m.group(2))}
    if len(app_upstreams) != 1 or not re.fullmatch(r'http://(?:127\.0\.0\.1:\d+/?|unix:[^;\s]+)', next(iter(app_upstreams))):
        fail('El sitio tiene varios destinos o un destino personalizado; actualiza proxy_pass manualmente.')
    expected = f'http://unix:{socket_path}'
    def replace(match):
        if is_mcp(match.group(2)):
            target = f'http://unix:{mcp_socket_path}' if mcp_socket_path else match.group(2)
        else:
            target = expected
        return match.group(1) + target + match.group(3)
    updated = pattern.sub(replace, original)
    return (original, updated) if updated != original else None


def warn_missing_mcp_location(change, mcp_socket_path):
    if not change and not NGINX_FILE.is_file():
        print('Aviso: verifica manualmente que Nginx enrute /mcp; consulta deploy/README.md.', flush=True)
        return
    text = change[1] if change else NGINX_FILE.read_text(encoding='utf-8')
    text = re.sub(r'(?m)#.*$', '', text)
    blocks = re.findall(r'location\s*=\s*/mcp\s*\{([^{}]*)\}', text)
    target = f'http://unix:{mcp_socket_path}'
    if not any(re.search(r'\bproxy_pass\s+' + re.escape(target) + r':?\s*;', block) for block in blocks):
        print('Aviso: Nginx no enruta /mcp al socket MCP; consulta deploy/README.md.', flush=True)
def write_atomic(path, content):
    # No seguir enlaces ni reemplazar archivos inesperados como root.
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail(f'La ruta de configuración no es un archivo regular: {path}')
    temp = path.with_name(path.name + '.flujo-tmp')
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def nginx_static_block():
    template=(DEPLOY_DIR/'nginx.conf.template').read_text(encoding='utf-8')
    match=re.search(r'(?ms)^([ \t]*)# BEGIN flujo-app assets\n.*?^\1# END flujo-app assets$',template)
    if not match: fail('Falta el bloque de archivos estáticos en la plantilla de Nginx.')
    return textwrap.dedent(match.group(0)).replace('@@APP_DIR@@',check_path(APP_DIR,'La ruta de la aplicación'))


def plan_nginx_static_update(change):
    if not NGINX_FILE.is_file():
        print('Aviso: configura Nginx para servir los archivos de /static/; consulta deploy/README.md.', flush=True)
        return change
    original,updated=change if change else (NGINX_FILE.read_text(encoding='utf-8'),NGINX_FILE.read_text(encoding='utf-8'))
    pattern=re.compile(r'(?m)^(?P<indent>[ \t]*)location[ \t]+/[ \t]*\{(?P<body>[^{}]*)\}')
    matches=[m for m in pattern.finditer(updated) if re.search(r'\bproxy_pass\s+',m.group('body'))]
    legacy=re.compile(r'(?m)^(?P<indent>[ \t]*)include[ \t]+'+re.escape(str(LEGACY_NGINX_STATIC_FILE))+r';[ \t]*(?:#.*)?$')
    legacy_count=len(legacy.findall(updated))
    block=nginx_static_block()
    count=len(re.findall(r'(?m)^[ \t]*# BEGIN flujo-app assets[ \t]*$',updated))
    if not matches or count+legacy_count not in (0,len(matches)):
        fail('No se pudo actualizar automáticamente Nginx para archivos estáticos; consulta deploy/README.md.')
    if legacy_count:
        if LEGACY_NGINX_STATIC_FILE.exists():
            expected='\n'.join(PREVIOUS_STATIC_BLOCK.splitlines()[1:-1]).strip()
            actual=LEGACY_NGINX_STATIC_FILE.read_text(encoding='utf-8').strip()
            if textwrap.dedent(expected)!=textwrap.dedent(actual):
                fail(f'El snippet anterior {LEGACY_NGINX_STATIC_FILE} fue modificado; migra su contenido manualmente.')
        updated=legacy.sub(lambda m:textwrap.indent(block,m.group('indent')),updated)
    elif count==0:
        updated=pattern.sub(lambda m:textwrap.indent(block,m.group('indent'))+'\n'+m.group(0) if re.search(r'\bproxy_pass\s+',m.group('body')) else m.group(0),updated)
    if count:
        marked=re.compile(r'(?ms)^(?P<indent>[ \t]*)# BEGIN flujo-app assets\n.*?^(?P=indent)# END flujo-app assets$')
        def upgrade(match):
            existing=textwrap.dedent(match.group(0))
            if existing!=block and re.search(r'(?m)^location\s+\^~\s+/static/\s*\{',existing):
                return match.group(0)
            if existing not in (block,PREVIOUS_STATIC_BLOCK,PREVIOUS_STATIC_PREFIX_BLOCK):
                fail('El bloque de archivos estáticos fue modificado; actualízalo manualmente.')
            return textwrap.indent(block,match.group('indent'))
        updated=marked.sub(upgrade,updated)
    return (original,updated) if updated!=original else None


def supervisor_start_worker(program):
    """Aplica la configuración de un programa auxiliar y lo deja en ejecución."""
    run(['supervisorctl', 'update', program])
    if supervisor_process_pid(program) == 0:
        run(['supervisorctl', 'start', program])
    run(['supervisorctl', 'status', program])


def apply_config(supervisor_config, nginx_config, skip_nginx):
    if os.geteuid() != 0:
        fail('Para instalar la configuración usa sudo.')
    for binary in (('supervisorctl', 'runuser') if skip_nginx else ('nginx', 'supervisorctl', 'systemctl', 'runuser')):
        if not shutil.which(binary):
            fail(f'Falta {binary}. Instala los paquetes indicados en deploy/README.md.')
    if not SUPERVISOR_FILE.parent.is_dir():
        fail(f'No existe {SUPERVISOR_FILE.parent}; revisa la instalación de Supervisor.')
    if not skip_nginx:
        if not NGINX_FILE.parent.is_dir() or not NGINX_LINK.parent.is_dir():
            fail('No se encontraron sites-available y sites-enabled de Nginx.')
        if NGINX_FILE.is_symlink() or (NGINX_FILE.exists() and not NGINX_FILE.is_file()):
            fail(f'La configuración existente no es un archivo regular: {NGINX_FILE}')
        if NGINX_FILE.exists() and NGINX_FILE.read_text(encoding='utf-8') != nginx_config:
            fail(f'{NGINX_FILE} ya existe y es diferente. Para preservar HTTPS u otros cambios, '
                 'revísalo y usa --skip-nginx, o edítalo manualmente.')
        if os.path.lexists(NGINX_LINK) and (not NGINX_LINK.is_symlink() or NGINX_LINK.resolve() != NGINX_FILE.resolve()):
            fail(f'{NGINX_LINK} ya existe y no apunta al archivo esperado.')
        created_file = not NGINX_FILE.exists()
        created_link = not os.path.lexists(NGINX_LINK)
        try:
            if created_file:
                write_atomic(NGINX_FILE, nginx_config)
            if created_link:
                NGINX_LINK.symlink_to(NGINX_FILE)
            result = run(['nginx', '-t'], capture_output=True, text=True)
            if result.stdout: print(result.stdout, end='', flush=True)
            if result.stderr: print(result.stderr, end='', file=sys.stderr, flush=True)
            server_name = re.search(r'^\s*server_name\s+(\S+);', nginx_config, re.MULTILINE).group(1)
            conflicts = re.findall(r'conflicting server name "([^"]+)"', result.stdout + result.stderr, re.IGNORECASE)
            if any(name.rstrip('.').lower() == server_name.rstrip('.').lower() for name in conflicts):
                fail(f'Nginx ya tiene un sitio con el nombre {server_name} en el mismo puerto. '
                     'Usa otro dominio o revisa la configuración existente.')
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            if created_link:
                NGINX_LINK.unlink(missing_ok=True)
            if created_file:
                NGINX_FILE.unlink(missing_ok=True)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                print(exc.stderr, file=sys.stderr, end='', flush=True)
            raise
    Path('/var/log/supervisor').mkdir(parents=True, exist_ok=True)
    write_atomic(SUPERVISOR_FILE, supervisor_config)
    run(['supervisorctl', 'reread'])
    run(['supervisorctl', 'update', PROGRAM])
    run(['supervisorctl', 'restart', PROGRAM])
    run(['supervisorctl', 'status', PROGRAM])
    for program in (SLACK_PROGRAM, MCP_PROGRAM):
        supervisor_start_worker(program)
    if not skip_nginx:
        run(['systemctl', 'reload', 'nginx'])


def prepare_worker_supervisor(user):
    """Añade los programas auxiliares que falten, sin tocar los ya registrados."""
    path = supervisor_config_path()
    text = path.read_text(encoding='utf-8')
    config = configparser.RawConfigParser(interpolation=None)
    config.read_string(text)
    expected = render('supervisor.conf.template', {'APP_DIR': APP_DIR, 'APP_USER': user,
                      'CONFIG_PATH': APP_DIR / 'config.ini'})
    slack_start = expected.index('[program:' + SLACK_PROGRAM + ']')
    mcp_start = expected.index('[program:' + MCP_PROGRAM + ']')
    workers = {SLACK_PROGRAM: (expected[slack_start:mcp_start], f'{APP_DIR}/.venv/bin/python {APP_DIR}/slack_notify.py'),
               MCP_PROGRAM: (expected[mcp_start:], f'{APP_DIR}/.venv/bin/python {APP_DIR}/mcp_server.py')}
    for other in path.parent.glob('*.conf'):
        if other == path or other.is_symlink() or not other.is_file():
            continue
        candidate = configparser.RawConfigParser(interpolation=None)
        try:
            candidate.read_string(other.read_text(encoding='utf-8'))
        except (configparser.Error, UnicodeError):
            continue
        for name in workers:
            if candidate.has_section('program:' + name):
                fail(f'{name} ya está configurado en {other}; revísalo antes de continuar.')
    additions = []
    for name, (block, command) in workers.items():
        if config.has_section('program:' + name):
            section = config['program:' + name]
            if (section.get('directory') != str(APP_DIR) or section.get('user') != user or
                section.get('command') != command or
                f'APP_CONFIG="{APP_DIR}/config.ini"' not in section.get('environment', '') or
                'FLUJO_CONFIG_ONLY="1"' not in section.get('environment', '')):
                fail(f'{name} tiene otra configuración; revísala antes de continuar.')
        else:
            if supervisor_process_pid(name) is not None:
                fail(f'Supervisor ya tiene registrado {name} sin configuración verificada.')
            additions.append(block.strip())
    if additions:
        write_atomic(path, text.rstrip() + '\n\n' + '\n\n'.join(additions))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Instala o actualiza Flujo en Ubuntu.')
    modes = parser.add_subparsers(dest='mode', required=True)
    install = modes.add_parser('install', help='Primera instalación de Supervisor y Nginx')
    install.add_argument('--server-name', help='Dominio DNS o IPv4 pública')
    install.add_argument('--user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name,
                         help='Usuario Linux sin privilegios que ejecutará la aplicación')
    install.add_argument('--skip-nginx', action='store_true', help='Conserva el Nginx existente')
    install.add_argument('--nginx-group', default='www-data', help='Grupo del proceso Nginx (predeterminado: www-data)')
    install.add_argument('--dry-run', action='store_true', help='Solo genera los archivos para revisión')
    install.add_argument('--output-dir', default=str(DEPLOY_DIR / 'generated'), help='Carpeta de archivos generados')
    update=modes.add_parser('update', help='Actualiza la instalación existente')
    update.add_argument('--nginx-group', help='Grupo de Nginx si falta la configuración del socket (predeterminado: www-data)')
    args = parser.parse_args(argv)
    if args.mode == 'install' and not args.skip_nginx and not args.server_name:
        parser.error('install requiere --server-name, excepto con --skip-nginx')
    config_path = APP_DIR / 'config.ini'
    settings = load_settings(config_path=config_path, environ={'FLUJO_CONFIG_ONLY': '1'})
    app_dir = check_path(APP_DIR, 'La ruta de la aplicación')
    config_file = check_path(config_path, 'La ruta de configuración')
    db_path = Path(settings['database'])
    socket_path = validate_socket_path(settings['socket'])
    mcp_socket_path = socket_path.with_name('mcp.sock')
    if mcp_socket_path == socket_path:
        fail('El socket de la aplicación no puede llamarse mcp.sock.')
    if args.mode == 'install':
        if not SAFE_USER.fullmatch(args.user) or args.user == 'root':
            fail('Indica un usuario Linux sin privilegios con --user.')
        try: pwd.getpwnam(args.user)
        except KeyError: fail(f'No existe el usuario Linux {args.user}.')
        if not SAFE_USER.fullmatch(args.nginx_group): fail('Grupo de Nginx inválido.')
        rule = f'd {socket_path.parent} 0750 {args.user} {args.nginx_group} -' if not socket_path.parent.exists() else ''
        values = {'APP_DIR': app_dir, 'APP_USER': args.user, 'SOCKET_PATH': socket_path, 'MCP_SOCKET_PATH': mcp_socket_path,
                  'CONFIG_PATH': config_file, 'NGINX_GROUP': args.nginx_group, 'SOCKET_DIRECTORY_RULE': rule}
        supervisor_config = render('supervisor.conf.template', values)
        tmpfiles_config = render('tmpfiles.conf.template', values)
        nginx_config = ''
        if not args.skip_nginx:
            values['SERVER_NAME'] = check_server_name(args.server_name)
            nginx_config = render('nginx.conf.template', values)
        output = Path(args.output_dir).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        write_atomic(output / 'supervisor.conf', supervisor_config)
        write_atomic(output / 'tmpfiles.conf', tmpfiles_config)
        if not args.skip_nginx: write_atomic(output / 'nginx.conf', nginx_config)
        print(f'Configuraciones generadas en {output}', flush=True)
        if args.dry_run: return 0
    if os.geteuid() != 0: fail('Este modo requiere sudo.')
    for binary in ('runuser', 'supervisorctl', 'systemd-tmpfiles'):
        if not shutil.which(binary): fail(f'Falta {binary}; revisa deploy/README.md.')
    own_pid = check_program_identity()
    if args.mode=='install' and supervisor_config_path()!=SUPERVISOR_FILE:
        fail('Ya existe una configuración de Supervisor con otro nombre; usa update para conservarla.')
    if args.mode == 'update':
        user = installed_supervisor_settings()
        if not SAFE_USER.fullmatch(user) or user == 'root': fail('Usuario de Supervisor inválido.')
        try: pwd.getpwnam(user)
        except KeyError: fail(f'No existe el usuario Linux {user}.')
    else:
        user = args.user
    if args.nginx_group and not SAFE_USER.fullmatch(args.nginx_group): fail('Grupo de Nginx inválido.')
    missing_socket_config = not TMPFILES_FILE.exists()
    group = installed_socket_group(user,socket_path,args.nginx_group) if args.mode == 'update' else args.nginx_group
    try: grp.getgrnam(group)
    except KeyError: fail(f'No existe el grupo de Nginx {group}.')
    check_socket(socket_path,user,own_pid)
    nginx_update = plan_nginx_socket_update(socket_path, mcp_socket_path) if args.mode == 'update' else None
    if args.mode == 'update': nginx_update = plan_nginx_static_update(nginx_update)
    if args.mode == 'update': warn_missing_mcp_location(nginx_update, mcp_socket_path)
    if args.mode == 'install' and args.skip_nginx: check_nginx_socket(socket_path, mcp_socket_path)
    if args.mode == 'update' or args.skip_nginx:
        print('Aviso: verifica el límite de acceso a /api/login en Nginx; consulta deploy/README.md.', flush=True)
    prepare_venv(user, db_path, settings['timezone'], create=args.mode == 'install')
    ensure_socket_directory(user,group,socket_path,install=args.mode == 'install' or missing_socket_config)
    if args.mode=='update' and missing_socket_config and socket_path.parent.is_dir() and str(socket_path.parent).startswith('/run/'):
        _,managed=read_socket_runtime(user)
        if managed is None:
            print(f'Aviso: asegura que {socket_path.parent} se recree al reiniciar el servidor.',flush=True)
    config_exists = supervisor_config_path().exists()
    if config_exists:
        prepare_worker_supervisor(user)
    pids = {program: supervisor_process_pid(program) for program in (SLACK_PROGRAM, MCP_PROGRAM)}
    for program, pid in pids.items():
        if pid is not None and not config_exists:
            fail(f'Supervisor ya tiene registrado {program} sin configuración verificada.')
    try:
        for program, pid in pids.items():
            if pid:
                run(['supervisorctl', 'stop', program])
        if args.mode == 'install':
            apply_config(supervisor_config, nginx_config, args.skip_nginx)
        else:
            run(['supervisorctl', 'reread'])
            if nginx_update:
                original, updated = nginx_update
                write_atomic(NGINX_FILE, updated)
                try:
                    run(['nginx', '-t'])
                    run(['supervisorctl', 'start' if own_pid == 0 else 'restart', PROGRAM])
                    run(['supervisorctl', 'status', PROGRAM])
                except BaseException:
                    write_atomic(NGINX_FILE, original)
                    raise
                run(['systemctl', 'reload', 'nginx'])
            else:
                run(['supervisorctl', 'start' if own_pid == 0 else 'restart', PROGRAM])
                run(['supervisorctl', 'status', PROGRAM])
            for program in (SLACK_PROGRAM, MCP_PROGRAM):
                supervisor_start_worker(program)
    except BaseException:
        if pids[SLACK_PROGRAM]:
            try:
                if supervisor_process_pid(SLACK_PROGRAM) == 0:
                    run(['supervisorctl', 'start', SLACK_PROGRAM])
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                print(f'No se pudo recuperar {SLACK_PROGRAM}; revísalo manualmente: {exc}', file=sys.stderr, flush=True)
        raise
    print('Flujo está en ejecución.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, configparser.Error, subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
