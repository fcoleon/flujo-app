#!/usr/bin/env python3
"""Migración manual de IDs; ejecutar con la aplicación detenida."""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import load_settings

TABLES = ('users', 'projects', 'columns', 'tasks', 'history', 'comments',
          'task_types', 'task_statuses', 'metric_events', 'automation_rules', 'automation_runs')
REFERENCES = {'column': 'columns', 'status': 'task_statuses', 'type': 'task_types', 'assignee_id': 'users'}


def migrate(connection):
    c = connection
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute('BEGIN EXCLUSIVE')
    try:
        schemas = {row[0]: row[1] for row in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
        if not {'users', 'projects', 'columns', 'tasks', 'task_types', 'task_statuses'} <= schemas.keys():
            raise ValueError('La base no contiene las tablas esperadas de Flujo')
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('La base contiene referencias foráneas inválidas')
        high = {table: 0 for table in TABLES}

        def reserve(table, value):
            if value is None or value in ('', 'none'): return
            if isinstance(value, bool) or not str(value).isdigit() or int(value) > 9223372036854775806:
                raise ValueError(f'ID histórico inválido para {table}: {value!r}')
            high[table] = max(high[table], int(value))

        if 'automation_rules' in schemas:
            for trigger, conditions, actions in c.execute('SELECT "trigger",conditions,actions FROM automation_rules'):
                trigger, conditions, actions = json.loads(trigger), json.loads(conditions), json.loads(actions)
                if trigger.get('type') in REFERENCES:
                    for key in ('from', 'to'): reserve(REFERENCES[trigger['type']], trigger.get(key))
                for condition in conditions:
                    if condition.get('field') in REFERENCES: reserve(REFERENCES[condition['field']], condition.get('value'))
                for action in actions:
                    if action.get('type') in REFERENCES: reserve(REFERENCES[action['type']], action.get('value'))

        if 'metric_events' in schemas:
            columns = {r[1] for r in c.execute('PRAGMA table_info(metric_events)')}
            for field, table in [('type_id', 'task_types'), ('task_id', 'tasks'), ('actor_id', 'users'), ('assignee_id', 'users')]:
                if field in columns: reserve(table, c.execute(f'SELECT MAX("{field}") FROM metric_events').fetchone()[0])

        for table in TABLES:
            if table not in schemas: continue
            sql = schemas[table]
            if not re.search(r'\bid\s+INTEGER\s+PRIMARY\s+KEY\b', sql, re.I):
                raise ValueError(f'Esquema inesperado en {table}')
            if not re.search(r'\bid\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', sql, re.I):
                temporary = '_autoincrement_' + table
                if temporary in schemas: raise ValueError(f'Ya existe la tabla temporal {temporary}')
                objects = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))]
                create = re.sub(r'^(CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?(?:"[^"]+"|`[^`]+`|\[[^]]+\]|\w+)',
                                lambda m: m.group(1) + '"' + temporary + '"', sql, count=1, flags=re.I)
                create = re.sub(r'\bid\s+INTEGER\s+PRIMARY\s+KEY\b', 'id INTEGER PRIMARY KEY AUTOINCREMENT', create, count=1, flags=re.I)
                c.execute(create)
                c.execute(f'INSERT INTO "{temporary}" SELECT * FROM "{table}"')
                c.execute(f'DROP TABLE "{table}"')
                c.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
                for statement in objects: c.execute(statement)
            current = c.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (table,)).fetchone()
            maximum = c.execute(f'SELECT COALESCE(MAX(id),0) FROM "{table}"').fetchone()[0]
            seq = max(high[table], maximum, current[0] if current else 0)
            c.execute('DELETE FROM sqlite_sequence WHERE name=?', (table,))
            c.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', (table, seq))
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('La migración produciría referencias foráneas inválidas')
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.execute('PRAGMA foreign_keys=ON')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Ruta al config.ini de producción')
    args = parser.parse_args()
    settings = load_settings(config_path=args.config, environ={'FLUJO_CONFIG_ONLY': '1'})
    path = Path(settings['database'])
    print(f'Base de datos: {path}', flush=True)
    with sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30) as connection:
        migrate(connection)
    print('Migración AUTOINCREMENT completada.')


if __name__ == '__main__':
    main()
