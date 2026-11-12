import json
import sqlite3
from collections import deque
from datetime import date, timedelta

FIELDS = {'column', 'status', 'type', 'assignee_id', 'priority', 'due_date', 'task_kind', 'completion'}
TRIGGERS = {'created', 'column', 'status', 'assignee_id', 'priority', 'due_date'}
ACTIONS = {'column', 'status', 'assignee_id', 'priority', 'due_date', 'clear_due_date'}
IDS = {'column', 'status', 'type', 'assignee_id'}
OPS = {'eq', 'ne', 'has', 'empty', 'overdue', 'today', 'before', 'after'}


def init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS automation_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL, enabled INTEGER NOT NULL, trigger TEXT NOT NULL, conditions TEXT NOT NULL,
        actions TEXT NOT NULL, position INTEGER NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS automation_rules_project ON automation_rules(project_id,position,id)')
    if 'disabled_reason' not in {row[1] for row in c.execute('PRAGMA table_info(automation_rules)')}:
        c.execute('ALTER TABLE automation_rules ADD COLUMN disabled_reason TEXT')
    c.execute('''CREATE TABLE IF NOT EXISTS automation_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        rule_id INTEGER REFERENCES automation_rules(id) ON DELETE SET NULL, rule_name TEXT NOT NULL,
        task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL, actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        trigger_type TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL)''')


def rule_data(row):
    d = dict(row)
    d['enabled'] = bool(d['enabled'])
    for key in ('trigger', 'conditions', 'actions'): d[key] = json.loads(d[key])
    return d


def ident(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit() or int(value) < 1:
        raise ValueError('ID inválido')
    return int(value)


def value(c, project, field, v, empty=False, relative=False):
    if v == 'none' and field in ('assignee_id', 'due_date'): return 'none'
    if v is None or v == '':
        if empty: return ''
        raise ValueError(f'Valor obligatorio para {field}')
    if field in IDS:
        n = ident(v)
        queries = {
            'column': ('SELECT 1 FROM columns WHERE id=? AND project_id=?', (n, project)),
            'type': ('SELECT 1 FROM task_types WHERE id=? AND project_id=?', (n, project)),
            'status': ('SELECT 1 FROM task_statuses s JOIN task_types t ON t.id=s.type_id WHERE s.id=? AND t.project_id=?', (n, project)),
            'assignee_id': ('SELECT 1 FROM users WHERE id=? AND blocked=0', (n,)),
        }
        if not c.execute(*queries[field]).fetchone():
            label = {'column': 'La columna', 'type': 'El tipo de tarea', 'status': 'El estado', 'assignee_id': 'El responsable'}[field]
            raise ValueError(f'{label} (ID {n}) ya no está disponible')
        return str(n)
    if field == 'due_date':
        if relative and isinstance(v, str) and v.lstrip('-').isdigit() and -3650 <= int(v) <= 3650:
            return v
        if relative and isinstance(v, dict) and set(v) == {'days'} and type(v['days']) is int and -3650 <= v['days'] <= 3650:
            return v
        if not isinstance(v, str): raise ValueError('Fecha inválida: usa AAAA-MM-DD o {"days": entero}')
        try:
            if date.fromisoformat(v).isoformat() != v: raise ValueError()
        except ValueError: raise ValueError('Fecha inválida: usa AAAA-MM-DD')
        return v
    if field == 'priority' and v not in ('Baja', 'Normal', 'Alta'): raise ValueError('Prioridad inválida')
    if field == 'task_kind' and v not in ('parent', 'subtask'): raise ValueError('task_kind debe ser parent o subtask')
    if field == 'completion' and v not in ('done', 'ongoing'): raise ValueError('completion debe ser done u ongoing')
    if not isinstance(v, str): raise ValueError(f'Valor inválido para {field}')
    return v


def validate(c, project, b):
    name = b.get('name'); enabled = b.get('enabled'); trigger = b.get('trigger')
    conditions = b.get('conditions'); actions = b.get('actions')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120: raise ValueError('Nombre inválido')
    if type(enabled) is not bool: raise ValueError('enabled debe ser booleano')
    if not isinstance(trigger, dict) or not isinstance(trigger.get('type'), str) or trigger['type'] not in TRIGGERS: raise ValueError('Gatillante inválido')
    kind = trigger['type']; t = {'type': kind}
    if kind == 'created' and any(k in trigger for k in ('from', 'to')): raise ValueError('created no admite from ni to')
    if kind != 'created':
        for k in ('from', 'to'):
            if k in trigger: t[k] = value(c, project, kind, trigger[k], empty=True)
    if not isinstance(conditions, list) or len(conditions) > 20: raise ValueError('Máximo 20 condiciones')
    cs = []
    for item in conditions:
        if not isinstance(item, dict) or not isinstance(item.get('field'), str) or item['field'] not in FIELDS or not isinstance(item.get('op'), str) or item['op'] not in OPS: raise ValueError('Condición inválida')
        f = item['field']; op = item['op']
        if f == 'completion' and (op not in ('eq', 'ne') or item.get('value') not in ('done', 'ongoing')): raise ValueError('completion requiere eq o ne y valor done u ongoing')
        if op in ('before', 'after', 'overdue', 'today') and f != 'due_date': raise ValueError('Operador exclusivo de due_date')
        if op in ('has', 'empty') and f not in ('due_date', 'assignee_id'): raise ValueError('Operador exclusivo de fecha o responsable')
        v = '' if op in ('empty', 'has', 'overdue', 'today') else value(c, project, f, item.get('value'), empty=op in ('eq', 'ne'))
        if op in ('before', 'after') and v == 'none': raise ValueError('La comparación requiere una fecha')
        cs.append({'field': f, 'op': op, 'value': v})
    if not isinstance(actions, list) or not 1 <= len(actions) <= 20: raise ValueError('Se requieren entre 1 y 20 acciones')
    ac = []
    for item in actions:
        if not isinstance(item, dict) or not isinstance(item.get('type'), str) or item['type'] not in ACTIONS: raise ValueError('Acción inválida')
        f = item['type']
        v = '' if f == 'clear_due_date' else value(c, project, f, item.get('value'), empty=f == 'assignee_id', relative=f == 'due_date')
        if f == 'due_date' and v == 'none': raise ValueError('Usa clear_due_date para quitar la fecha')
        ac.append({'type': f, 'value': v})
    return name.strip(), int(enabled), json.dumps(t), json.dumps(cs), json.dumps(ac)


def disable(c, rule, reason):
    if not c.execute('UPDATE automation_rules SET enabled=0,disabled_reason=? WHERE id=? AND enabled=1', (reason, rule['id'])).rowcount:
        return None
    return {'id': rule['id'], 'project_id': rule['project_id'], 'name': rule['name'], 'reason': reason}


def disable_invalid(c):
    disabled = []
    for row in c.execute('SELECT * FROM automation_rules WHERE enabled=1 ORDER BY project_id,position,id').fetchall():
        rule = rule_data(row)
        try:
            validate(c, rule['project_id'], rule)
        except ValueError as exc:
            notice = disable(c, rule, str(exc))
            if notice: disabled.append(notice)
    return disabled


def snapshot(c, tid, project):
    row = c.execute('''SELECT t.*, p.column_id, s.is_done FROM tasks t JOIN task_types ty ON ty.id=t.type_id
        JOIN placements p ON p.task_id=t.id AND p.project_id=ty.project_id
        JOIN task_statuses s ON s.id=t.status_id
        WHERE t.id=? AND ty.project_id=?''', (tid, project)).fetchone()
    if not row: return None
    return {'column': str(row['column_id']), 'status': str(row['status_id']), 'type': str(row['type_id']),
            'assignee_id': str(row['assignee_id'] or ''), 'priority': row['priority'],
            'due_date': row['due_date'] or '', 'task_kind': 'subtask' if row['parent_id'] else 'parent',
            'completion': 'done' if row['is_done'] else 'ongoing'}


def matches(conditions, state, today):
    for item in conditions:
        a, b, op = state[item['field']], item['value'], item['op']
        if b == 'none': b = ''
        if op == 'eq' and a != b or op == 'ne' and a == b: return False
        if op == 'empty' and a or op == 'has' and not a: return False
        if op == 'before' and (not a or a >= b) or op == 'after' and (not a or a <= b): return False
        if op == 'today' and a != today or op == 'overdue' and (not a or a >= today): return False
    return True


def run(c, tid, project, events, actor, now, record, metric_event, notify_assignment):
    initial = snapshot(c, tid, project) if project else None
    if not initial: return []
    rules = [rule_data(r) for r in c.execute('SELECT * FROM automation_rules WHERE project_id=? AND enabled=1 ORDER BY position,id', (project,))]
    queue = deque((kind, before, after, initial) for kind, before, after in events); fired = set(); processed = 0; disabled = []
    # ponytail: cap chains at 100 events and 100 rules; raise limits if real boards need more.
    while queue and processed < 100 and len(fired) < 100:
        kind, before, after, event_state = queue.popleft(); processed += 1
        for rule in rules:
            if len(fired) >= 100: break
            trigger = rule['trigger']
            if rule['id'] in fired or trigger['type'] != kind: continue
            if kind != 'created' and (trigger.get('from') not in (None, '', str(before or '') or 'none') or trigger.get('to') not in (None, '', str(after or '') or 'none')): continue
            if not snapshot(c, tid, project) or not matches(rule['conditions'], event_state, now()[:10]): continue
            fired.add(rule['id']); generated = []
            c.execute('SAVEPOINT automation_rule')
            try:
                for action in rule['actions']:
                    field = action['type']; v = action['value']
                    if field == 'clear_due_date': field, v = 'due_date', ''
                    if field == 'due_date' and isinstance(v, dict): v = (date.fromisoformat(now()[:10]) + timedelta(days=v['days'])).isoformat()
                    elif field == 'due_date' and isinstance(v, str) and v.lstrip('-').isdigit(): v = (date.fromisoformat(now()[:10]) + timedelta(days=int(v))).isoformat()
                    if v == 'none': v = ''
                    state = snapshot(c, tid, project); old = state[field]
                    if old == v: continue
                    # References can have been deleted or changed since the rule was saved.
                    value(c, project, field, v, empty=field in ('assignee_id', 'due_date'))
                    if field == 'column':
                        c.execute('UPDATE placements SET column_id=?,position=(SELECT COALESCE(MAX(position),-1)+1 FROM placements WHERE column_id=?) WHERE task_id=? AND project_id=?', (int(v), int(v), tid, project))
                    elif field == 'status':
                        status = c.execute('SELECT is_done FROM task_statuses WHERE id=? AND type_id=?', (int(v), int(state['type']))).fetchone()
                        if not status: raise ValueError('Estado incompatible con el tipo de la tarea')
                        old_done = c.execute('SELECT is_done FROM task_statuses WHERE id=?', (int(old),)).fetchone()['is_done']
                        c.execute('UPDATE tasks SET status_id=? WHERE id=?', (int(v), tid))
                        if bool(old_done) != bool(status['is_done']): metric_event(c, tid, 'completed' if status['is_done'] else 'reopened', actor)
                    else:
                        c.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (int(v) if field == 'assignee_id' and v else v or None, tid))
                        if field == 'assignee_id' and v: notify_assignment(c,tid,actor)
                    record(c, tid, actor, f'automatización «{rule["name"]}»: cambió {field} a {v or "vacío"}')
                    generated.append((field, old, v, snapshot(c, tid, project)))
                c.execute('RELEASE automation_rule')
                outcome, error = 'success', None
                queue.extend(generated)
            except (ValueError, sqlite3.IntegrityError) as exc:
                c.execute('ROLLBACK TO automation_rule'); c.execute('RELEASE automation_rule')
                outcome, error = 'error', str(exc)[:1000]
                notice = disable(c, rule, error)
                if notice: disabled.append(notice)
            c.execute('INSERT INTO automation_runs(project_id,rule_id,rule_name,task_id,actor_id,trigger_type,outcome,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (project, rule['id'], rule['name'], tid, actor, kind, outcome, error, now()))
            if error: record(c, tid, actor, f'automatización «{rule["name"]}» falló: {error}')
    if queue:
        record(c, tid, actor, 'automatización: cadena detenida por superar el límite de eventos o reglas')
    return disabled
