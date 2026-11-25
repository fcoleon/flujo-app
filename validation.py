import re
from datetime import datetime


def text_field(body,key):
    value=body.get(key)
    if not isinstance(value,str): raise ValueError(f"El campo '{key}' debe ser texto")
    return value


def integer_field(value):
    try: return int(value)
    except (TypeError,ValueError,OverflowError): raise ValueError('Identificador inválido') from None


def task_title(value):
    if not isinstance(value,str) or not value.strip(): raise ValueError('El nombre es obligatorio')
    if re.search(r'[\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]',value): raise ValueError('El nombre de la tarea no puede contener saltos de línea')
    return value.strip()


def task_due_date(value):
    if value is None or value=='': return None
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): raise ValueError('Fecha límite inválida')
    try: datetime.strptime(value,'%Y-%m-%d')
    except ValueError: raise ValueError('Fecha límite inválida') from None
    return value


def task_priority(value):
    if value not in ('Baja','Normal','Alta'): raise ValueError('Prioridad inválida')
    return value
