"""Configuración compartida por la aplicación y el despliegue."""
import configparser
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    return {'database': str(database.resolve()), 'port': port, 'timezone': values['timezone'], 'socket': str(socket_path), 'config_path': str(path)}
