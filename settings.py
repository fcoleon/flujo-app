"""Configuración compartida por la aplicación y el despliegue."""
import configparser
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def load_settings(config_path=None, environ=None):
    env = os.environ if environ is None else environ
    explicit = config_path is not None or bool(env.get('APP_CONFIG'))
    path = Path(config_path or env.get('APP_CONFIG') or ROOT / 'config.ini').expanduser().resolve()
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        with path.open(encoding='utf-8') as file:
            parser.read_file(file)
        if not parser.has_section('app'):
            raise ValueError(f'Falta la sección [app] en {path}')
    elif explicit:
        raise ValueError(f'No existe el archivo de configuración: {path}')
    values = {'database': parser.get('app', 'database', fallback='tasks.db'),
              'timezone': parser.get('app', 'timezone', fallback='America/Santiago'),
              'port': parser.get('app', 'port', fallback='8000'),
              'socket': parser.get('app', 'socket', fallback='/run/flujo/flujo.sock')}
    try:
        allow_self_registration = parser.getboolean('app', 'allow_self_registration', fallback=False)
    except ValueError:
        raise ValueError('allow_self_registration debe ser true o false') from None
    if env.get('FLUJO_CONFIG_ONLY') != '1':
        for key, var in (('database', 'TASKS_DB'), ('timezone', 'TASKS_TIMEZONE'), ('port', 'PORT')):
            if var in env: values[key] = env[var]
    if not values['database'].strip(): raise ValueError('La ruta de la base de datos no puede estar vacía')
    database = Path(values['database']).expanduser()
    if not database.is_absolute(): database = ROOT / database
    try:
        port = int(values['port'])
        if not 1 <= port <= 65535: raise ValueError()
    except ValueError:
        raise ValueError('El puerto debe estar entre 1 y 65535') from None
    try:
        ZoneInfo(values['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Zona horaria inválida: ' + values['timezone']) from None
    socket_path = Path(values['socket'])
    if not socket_path.is_absolute() or not values['socket'].strip():
        raise ValueError('La ruta del socket debe ser absoluta')
    slack = {key: parser.get('slack', key, fallback='').strip() for key in ('client_id', 'client_secret', 'base_url')}
    backblaze = {key: parser.get('backblaze', key, fallback='').strip() for key in ('endpoint', 'region', 'bucket', 'key_id', 'application_key')}
    if any(backblaze.values()) and not all(backblaze.values()): raise ValueError('Completa toda la configuración de Backblaze')
    cache_type=parser.get('app','cache',fallback='').strip().lower()
    if cache_type not in ('','redis','memcached'): raise ValueError('cache debe ser redis, memcached o vacío')
    cache={'type':cache_type}
    if cache_type=='redis':
        cache['url']=parser.get('redis','url',fallback='').strip()
        try:
            url=urlsplit(cache['url'])
            if url.scheme not in ('redis','rediss','unix') or (url.scheme=='unix' and not url.path.startswith('/')) or (url.scheme!='unix' and not url.hostname): raise ValueError()
            if url.scheme!='unix': url.port
        except ValueError: raise ValueError('Configura una URL válida en [redis]') from None
    elif cache_type=='memcached':
        cache['host']=parser.get('memcached','host',fallback='').strip()
        try:
            cache['port']=parser.getint('memcached','port',fallback=11211)
            if not cache['host'] or not 1<=cache['port']<=65535: raise ValueError()
        except ValueError: raise ValueError('Configura host y puerto válidos en [memcached]') from None
    return {'database': str(database.resolve()), 'port': port, 'timezone': values['timezone'], 'socket': str(socket_path), 'allow_self_registration': allow_self_registration, 'config_path': str(path), 'slack': slack, 'slack_base_url': slack['base_url'], 'backblaze': backblaze, 'cache':cache}
