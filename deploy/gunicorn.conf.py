"""Servidor de producción; la configuración y la migración se cargan en el master."""
import errno
import os
from pathlib import Path
import socket
import stat
import re
import subprocess
from settings import load_settings

settings = load_settings()
bind = 'unix:' + settings['socket']
workers = 2
worker_class = 'gthread'
threads = 2
umask = 0o177  # Inicialmente solo el usuario de la aplicación accede al socket.
accesslog = '-'
errorlog = '-'


def on_starting(server):
    path = Path(settings['socket'])
    if path.is_symlink():
        raise RuntimeError(f'La ruta del socket es un enlace simbólico: {path}')
    if path.exists():
        info = path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError(f'La ruta del socket pertenece a otro proceso o no es un socket: {path}')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            result = probe.connect_ex(str(path))
        if result != errno.ECONNREFUSED:
            raise RuntimeError(f'El socket ya está en uso o no puede verificarse: {path}')
    from app import init
    init()


def when_ready(server):
    group = os.environ.get('FLUJO_SOCKET_GROUP')
    if not group:
        # Compatibilidad con la configuración de Supervisor anterior.
        runtime_config = Path('/etc/tmpfiles.d/flujo-app.conf')
        if runtime_config.is_file():
            text = runtime_config.read_text(encoding='utf-8')
            match = re.search(r'^# flujo-app user=\S+ group=(\S+)$', text, re.MULTILINE)
            if match: group = match.group(1)
            else:
                parts = text.split()
                if len(parts) == 6 and parts[0] == 'd': group = parts[4]
    if group:
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', group):
            raise RuntimeError('El grupo del socket no es válido')
        subprocess.run(['setfacl', '-m', f'g::---,g:{group}:rw,o::---', settings['socket']], check=True)
