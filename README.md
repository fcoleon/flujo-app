# Flujo

Aplicación interna de gestión de tareas con tableros Kanban, un único workspace, Flask y SQLite. En producción usa Gunicorn detrás de Nginx.

## Requisitos e inicio

Se requiere Python 3.9 o superior y una base de datos de zonas horarias del sistema. Desde la raíz del proyecto:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abre [http://localhost:8000](http://localhost:8000) y crea la primera cuenta, que será administradora del workspace. El servidor escucha solo en `127.0.0.1` y guarda los datos en `tasks.db`. La base de datos y el entorno virtual no se incluyen en Git.

Para personalizar la base de datos, la zona horaria, el puerto local o el socket de producción, copia `config.example.ini` a `config.ini` y edita la sección `[app]`. Las rutas relativas de la base se interpretan desde la raíz del proyecto. Sin `config.ini`, se usan los valores predeterminados del ejemplo. `config.ini` está excluido de Git. El servidor local usa TCP; Gunicorn usa el socket Unix en producción.

En ejecución local, las siguientes variables opcionales tienen prioridad sobre el archivo:

| Variable | Valor predeterminado | Uso |
| --- | --- | --- |
| `PORT` | `8000` | Puerto del servidor local. |
| `TASKS_DB` | `tasks.db` | Ruta del archivo SQLite. |
| `TASKS_TIMEZONE` | `America/Santiago` | Zona horaria IANA usada para agrupar estadísticas mensuales. |
| `APP_CONFIG` | `config.ini` en la raíz | Archivo de configuración alternativo. |

Por ejemplo, para ejecutar una instancia con otra base y puerto:

```sh
PORT=8001 TASKS_DB=pruebas.db python app.py
```

El despliegue con Supervisor usa exclusivamente el `config.ini` de la aplicación para evitar que variables heredadas cambien su configuración.

Para ejecutar las pruebas automatizadas:

```sh
python -m unittest discover -s tests -v
```

Las peticiones POST a la API usan `Content-Type: application/json` y un objeto JSON como cuerpo; para operaciones sin campos se envía `{}`. Un tipo de contenido incorrecto devuelve `415` y JSON inválido o una raíz que no sea un objeto devuelve `400`, con un mensaje en `error`. Al agregar o modificar operaciones, valida los tipos y valores de sus campos antes de usarlos y conserva el mismo formato de error; los campos desconocidos se ignoran.

## Funcionalidades

- Proyectos con tableros Kanban, columnas configurables y tareas que pueden pertenecer a varios proyectos, con posición independiente en cada uno.
- Responsables, fechas, prioridad, descripción enriquecida, subtareas, comentarios, historial y enlaces directos a tareas. Los enlaces respetan los permisos existentes.
- Búsqueda por nombre y descripción en el tablero actual, sin distinguir mayúsculas ni acentos.
- Tipos de tarea configurables por proyecto, con estados en curso o finalizados. El tipo y el estado son compartidos entre los proyectos de una tarea e independientes de las columnas. Al cambiar el tipo, el estado vuelve al primer estado en curso del nuevo tipo.
- Estadísticas mensuales para administradores del proyecto, con filtros por tipo y desglose por persona.

## Usuarios y permisos

Los usuarios pertenecen al workspace. Un administrador general crea usuarios y proyectos, y tiene permisos de administrador en todos ellos. Cada proyecto puede otorgar a otros usuarios el rol de administrador, editor o solo lectura. El acceso y los datos del tablero se administran desde el icono de configuración junto a su nombre.

Cada persona puede editar su nombre y cambiar su contraseña desde «Mi perfil». Para cambiar la contraseña debe ingresar la actual y confirmar la nueva.

Desde el nombre del workspace se puede consultar la lista de usuarios. Los administradores generales pueden cambiar el nombre y bloquear o desbloquear cuentas. Una persona bloqueada puede iniciar sesión, pero no acceder a los datos ni operar en el workspace; sus permisos de proyecto se conservan para cuando sea desbloqueada. El bloqueo también se comprueba en las sesiones existentes al hacer una solicitud.

El registro por cuenta propia está deshabilitado por defecto (`allow_self_registration = false` en `config.ini`); la primera cuenta administradora siempre se puede crear y los administradores pueden agregar personas. Al habilitarlo con `true`, cualquiera que acceda a la aplicación puede crear una cuenta no administradora, sin acceso automático a los tableros. Reinicia la aplicación para aplicar el cambio.

Las cuentas agregadas por un administrador requieren cambiar la contraseña temporal al ingresar por primera vez. El administrador debe compartirla con la persona por un canal apropiado; la aplicación no envía invitaciones. Las cuentas anteriores a esta función se conservan sin exigir un cambio, ya que no se puede identificar cuáles usan una contraseña temporal.

Una tarea con proyectos es accesible a quien tenga acceso a alguno de ellos. Una tarea sin proyecto está disponible en «Sin proyecto» para los administradores y la persona responsable, que puede editarla. Las subtareas heredan dinámicamente los proyectos de su tarea principal y respetan sus permisos; se consultan desde el detalle de su tarea principal y no aparecen en «Sin proyecto». También pueden agregarse explícitamente a un tablero como tarjetas independientes.

El tipo base de cada proyecto tiene un estado en curso y otro finalizado. Los tipos personalizados permiten agregar estados, modificar su nombre y color, y ordenarlos. Debe existir al menos un estado en curso y uno finalizado. Se pueden eliminar tipos y estados personalizados cuando ninguna tarea los utiliza, incluidas tareas de otros proyectos o sin proyecto. El tipo base conserva sus dos estados. Las tareas con el tipo base general adoptan el tipo base equivalente al incorporarse a un proyecto, manteniendo su categoría de finalización.

## Estadísticas

Los eventos de creación, finalización y reapertura se agrupan por mes, tipo y proyectos de la tarea al ocurrir el evento. Una tarea finalizada varias veces cuenta en cada finalización. La duración se mide desde la creación hasta cada finalización, incluidos los períodos de reapertura, y se expresa en días decimales.

El desglose por persona atribuye las creaciones a su autor y las finalizaciones al responsable asignado en ese momento. Los eventos anteriores al registro de identidades aparecen como «Sin datos históricos»; las finalizaciones sin responsable aparecen como «Sin responsable». La aplicación indica desde cuándo se registran los eventos y las identidades; los anteriores no se reconstruyen.

## Acceso y despliegue

El acceso mediante usuario y contraseña es provisional. Las contraseñas se almacenan con PBKDF2 y sal; las cookies de sesión usan HttpOnly y SameSite, y se marcan Secure cuando la conexión es HTTPS. Antes de exponer la aplicación en una red o en producción se necesita HTTPS, endurecimiento de sesiones y una estrategia de autenticación adecuada. Los enlaces generados con `localhost` solo funcionan en el mismo computador; para compartirlos entre dispositivos se necesita una dirección accesible para el equipo.

Para una instancia Ubuntu EC2, consulta la [guía de despliegue con Supervisor y Nginx](deploy/README.md). Incluye un script para generar e instalar las configuraciones a partir de la ruta de la aplicación.
