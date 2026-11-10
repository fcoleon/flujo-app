"""Servidor de producción; la configuración y la migración se cargan en el master."""
import errno
import os
from pathlib import Path
import socket
import stat
from settings import load_settings

settings = load_settings()
bind = 'unix:' + settings['socket']
workers = 2
worker_class = 'gthread'
threads = 2
umask = 0o111  # El acceso se controla mediante el directorio privado del socket.
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
