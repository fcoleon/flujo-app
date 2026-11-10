#!/usr/bin/env python3
"""Genera e instala la configuración de Flujo para Ubuntu, Supervisor y Nginx."""
import argparse
import configparser
import errno
import ipaddress
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
APP_DIR = DEPLOY_DIR.parent.resolve()
sys.path.insert(0, str(APP_DIR))
from settings import load_settings

PROGRAM = 'flujo-app'
SUPERVISOR_FILE = Path('/etc/supervisor/conf.d/flujo-app.conf')
NGINX_FILE = Path('/etc/nginx/sites-available/flujo-app')
NGINX_LINK = Path('/etc/nginx/sites-enabled/flujo-app')
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


def supervisor_process_pid():
    result = subprocess.run(['supervisorctl', 'pid', PROGRAM], capture_output=True, text=True)
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode == 0 and output.isdigit():
        return int(output)
    if 'no such process' in output.lower():
        return None
    fail('No se pudo consultar Supervisor. Comprueba que el servicio esté activo: ' + output)


def check_program_identity():
    if SUPERVISOR_FILE.is_symlink() or (SUPERVISOR_FILE.exists() and not SUPERVISOR_FILE.is_file()):
        fail(f'La configuración de Supervisor no es un archivo regular: {SUPERVISOR_FILE}')
    if SUPERVISOR_FILE.exists():
        config = configparser.RawConfigParser(interpolation=None)
        try:
            with SUPERVISOR_FILE.open(encoding='utf-8') as stream:
                config.read_file(stream)
            section = config['program:' + PROGRAM]
            expected = f'{APP_DIR}/.venv/bin/python -u {APP_DIR}/app.py'
            if section.get('directory') != str(APP_DIR) or section.get('command') != expected:
                raise ValueError()
        except (configparser.Error, KeyError, ValueError):
            fail(f'{SUPERVISOR_FILE} pertenece a otra configuración o ruta. Revísala antes de continuar.')
    pid = supervisor_process_pid()
    if pid is not None and not SUPERVISOR_FILE.exists():
        fail(f'Supervisor ya tiene un programa llamado {PROGRAM} registrado desde otra configuración.')
    return pid


def check_app_port(port, own_pid):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
        return
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
    if not shutil.which('ss'):
        fail(f'El puerto {port} está ocupado y no se puede identificar su proceso; instala iproute2 o cambia port en config.ini.')
    result = subprocess.run(['ss', '-H', '-ltnp', f'sport = :{port}'], capture_output=True, text=True, check=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    # Rechaza también resultados sin PID o ambiguos: no se detienen otros procesos.
    if own_pid and lines and all(set(re.findall(r'pid=(\d+)', line)) == {str(own_pid)} for line in lines):
        return
    fail(f'El puerto local {port} ya está ocupado por otro proceso o no se pudo verificar su propietario. '
         f'Cambia port en config.ini. Consulta: sudo ss -ltnp "sport = :{port}"')


def prepare_venv(user, db_path, timezone, create=True):
    if not (APP_DIR / 'app.py').is_file() or not (APP_DIR / 'settings.py').is_file() or not (APP_DIR / 'static' / 'index.html').is_file():
        fail(f'No se encontró la aplicación completa en {APP_DIR}. Copia sus archivos antes de continuar.')
    for path in (APP_DIR, APP_DIR / 'app.py', APP_DIR / 'settings.py', APP_DIR / 'config.ini', APP_DIR / 'static' / 'index.html'):
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


def installed_supervisor_settings():
    if not SUPERVISOR_FILE.is_file() or SUPERVISOR_FILE.is_symlink():
        fail('No se encontró la configuración de Supervisor de Flujo. Ejecuta primero install.')
    config = configparser.RawConfigParser(interpolation=None)
    with SUPERVISOR_FILE.open(encoding='utf-8') as stream:
        config.read_file(stream)
    section = config['program:' + PROGRAM]
    env = section.get('environment', '')
    expected_path = f'APP_CONFIG="{APP_DIR}/config.ini"'
    if expected_path not in env or 'FLUJO_CONFIG_ONLY="1"' not in env:
        fail('La configuración de Supervisor es de una versión anterior. Copia sus valores a config.ini '
             'y ejecuta install --skip-nginx para actualizarla sin modificar Nginx.')
    return section['user']


def check_nginx_port(port):
    if not NGINX_FILE.is_file():
        print('Aviso: verifica que tu proxy Nginx apunte al puerto configurado.', flush=True)
        return
    text = NGINX_FILE.read_text(encoding='utf-8')
    ports = set(re.findall(r'proxy_pass\s+http://127\.0\.0\.1:(\d+)/?\s*;', text))
    if ports and ports != {str(port)}:
        fail(f'Nginx apunta a otro puerto ({", ".join(sorted(ports))}), pero config.ini indica {port}. '
             'Actualiza Nginx antes de continuar.')
    if not ports:
        print('Aviso: verifica manualmente que Nginx apunte al puerto configurado.', flush=True)


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
    if not skip_nginx:
        run(['systemctl', 'reload', 'nginx'])


def main(argv=None):
    parser = argparse.ArgumentParser(description='Instala o actualiza Flujo en Ubuntu.')
    modes = parser.add_subparsers(dest='mode', required=True)
    install = modes.add_parser('install', help='Primera instalación de Supervisor y Nginx')
    install.add_argument('--server-name', help='Dominio DNS o IPv4 pública')
    install.add_argument('--user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name,
                         help='Usuario Linux sin privilegios que ejecutará la aplicación')
    install.add_argument('--skip-nginx', action='store_true', help='Conserva el Nginx existente')
    install.add_argument('--dry-run', action='store_true', help='Solo genera los archivos para revisión')
    install.add_argument('--output-dir', default=str(DEPLOY_DIR / 'generated'), help='Carpeta de archivos generados')
    modes.add_parser('update', help='Reinicia la instalación existente sin modificar Nginx')
    args = parser.parse_args(argv)
    if args.mode == 'install' and not args.skip_nginx and not args.server_name:
        parser.error('install requiere --server-name, excepto con --skip-nginx')
    config_path = APP_DIR / 'config.ini'
    settings = load_settings(config_path=config_path, environ={'FLUJO_CONFIG_ONLY': '1'})
    if settings['port'] < 1024: fail('Para el despliegue el puerto debe estar entre 1024 y 65535.')
    app_dir = check_path(APP_DIR, 'La ruta de la aplicación')
    config_file = check_path(config_path, 'La ruta de configuración')
    db_path = Path(settings['database'])
    if args.mode == 'install':
        if not SAFE_USER.fullmatch(args.user) or args.user == 'root':
            fail('Indica un usuario Linux sin privilegios con --user.')
        try: pwd.getpwnam(args.user)
        except KeyError: fail(f'No existe el usuario Linux {args.user}.')
        values = {'APP_DIR': app_dir, 'APP_USER': args.user, 'PORT': settings['port'], 'CONFIG_PATH': config_file}
        supervisor_config = render('supervisor.conf.template', values)
        nginx_config = ''
        if not args.skip_nginx:
            values['SERVER_NAME'] = check_server_name(args.server_name)
            nginx_config = render('nginx.conf.template', values)
        output = Path(args.output_dir).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        write_atomic(output / 'supervisor.conf', supervisor_config)
        if not args.skip_nginx: write_atomic(output / 'nginx.conf', nginx_config)
        print(f'Configuraciones generadas en {output}', flush=True)
        if args.dry_run: return 0
    if os.geteuid() != 0: fail('Este modo requiere sudo.')
    for binary in ('runuser', 'supervisorctl'):
        if not shutil.which(binary): fail(f'Falta {binary}; revisa deploy/README.md.')
    own_pid = check_program_identity()
    if args.mode == 'update':
        user = installed_supervisor_settings()
        if not SAFE_USER.fullmatch(user) or user == 'root': fail('Usuario de Supervisor inválido.')
        try: pwd.getpwnam(user)
        except KeyError: fail(f'No existe el usuario Linux {user}.')
    else:
        user = args.user
    check_app_port(settings['port'], own_pid)
    if args.mode == 'update' or args.skip_nginx: check_nginx_port(settings['port'])
    prepare_venv(user, db_path, settings['timezone'], create=args.mode == 'install')
    if args.mode == 'install':
        apply_config(supervisor_config, nginx_config, args.skip_nginx)
    else:
        run(['supervisorctl', 'restart', PROGRAM])
        run(['supervisorctl', 'status', PROGRAM])
    print('Flujo está en ejecución.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, configparser.Error, subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
