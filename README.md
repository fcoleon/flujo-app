# Flujo

Aplicación interna de gestión de tareas con tableros Kanban, un único workspace, Python y SQLite. Utiliza la biblioteca estándar de Python; no requiere instalar paquetes adicionales.

## Requisitos e inicio

Se requiere Python 3.9 o superior y una base de datos de zonas horarias del sistema. Desde la raíz del proyecto:

```sh
python3 -m venv .venv
source .venv/bin/activate
python app.py
```

Abre [http://localhost:8000](http://localhost:8000) y crea la primera cuenta, que será administradora del workspace. El servidor escucha solo en `127.0.0.1` y guarda los datos en `tasks.db`. La base de datos y el entorno virtual no se incluyen en Git.

Las siguientes variables de entorno son opcionales:

| Variable | Valor predeterminado | Uso |
| --- | --- | --- |
| `PORT` | `8000` | Puerto del servidor local. |
| `TASKS_DB` | `tasks.db` | Ruta del archivo SQLite. |
| `TASKS_TIMEZONE` | `America/Santiago` | Zona horaria IANA usada para agrupar estadísticas mensuales. |

Por ejemplo, para ejecutar una instancia con otra base y puerto:

```sh
PORT=8001 TASKS_DB=pruebas.db python app.py
```

Para ejecutar las pruebas automatizadas:

```sh
python -m unittest discover -s tests -v
```

## Funcionalidades

- Proyectos con tableros Kanban, columnas configurables y tareas que pueden pertenecer a varios proyectos, con posición independiente en cada uno.
- Responsables, fechas, prioridad, descripción enriquecida, subtareas, comentarios, historial y enlaces directos a tareas. Los enlaces respetan los permisos existentes.
- Búsqueda por nombre y descripción en el tablero actual, sin distinguir mayúsculas ni acentos.
- Tipos de tarea configurables por proyecto, con estados en curso o finalizados. El tipo y el estado son compartidos entre los proyectos de una tarea e independientes de las columnas. Al cambiar el tipo, el estado vuelve al primer estado en curso del nuevo tipo.
- Estadísticas mensuales para administradores del proyecto, con filtros por tipo y desglose por persona.

## Usuarios y permisos

Los usuarios pertenecen al workspace. Un administrador general crea usuarios y proyectos, y tiene permisos de administrador en todos ellos. Cada proyecto puede otorgar a otros usuarios el rol de administrador, editor o solo lectura. El acceso y los datos del tablero se administran desde el icono de configuración junto a su nombre.

Cada persona puede editar su nombre y cambiar su contraseña desde «Mi perfil». Para cambiar la contraseña debe ingresar la actual y confirmar la nueva.

Una tarea con proyectos es accesible a quien tenga acceso a alguno de ellos. Una tarea sin proyecto está disponible en «Sin proyecto» para los administradores y la persona responsable, que puede editarla. Una subtarea sin proyecto también hereda el acceso de su tarea principal.

El tipo base de cada proyecto tiene un estado en curso y otro finalizado. Los tipos personalizados permiten agregar estados, modificar su nombre y color, y ordenarlos. Debe existir al menos un estado en curso y uno finalizado. Se pueden eliminar tipos y estados personalizados cuando ninguna tarea los utiliza, incluidas tareas de otros proyectos o sin proyecto. El tipo base conserva sus dos estados. Las tareas con el tipo base general adoptan el tipo base equivalente al incorporarse a un proyecto, manteniendo su categoría de finalización.

## Estadísticas

Los eventos de creación, finalización y reapertura se agrupan por mes, tipo y proyectos de la tarea al ocurrir el evento. Una tarea finalizada varias veces cuenta en cada finalización. La duración se mide desde la creación hasta cada finalización, incluidos los períodos de reapertura, y se expresa en días decimales.

El desglose por persona atribuye las creaciones a su autor y las finalizaciones al responsable asignado en ese momento. Los eventos anteriores al registro de identidades aparecen como «Sin datos históricos»; las finalizaciones sin responsable aparecen como «Sin responsable». La aplicación indica desde cuándo se registran los eventos y las identidades; los anteriores no se reconstruyen.

## Acceso y despliegue

El acceso mediante usuario y contraseña es provisional. Las contraseñas se almacenan con PBKDF2 y sal, y las sesiones usan cookies HttpOnly. Antes de exponer la aplicación en una red o en producción se necesita HTTPS, endurecimiento de sesiones y una estrategia de autenticación adecuada. Los enlaces generados con `localhost` solo funcionan en el mismo computador; para compartirlos entre dispositivos se necesita una dirección accesible para el equipo.
