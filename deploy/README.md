# Despliegue en Ubuntu EC2

Flujo usa Flask en desarrollo y Gunicorn detrás de Nginx en producción. Supervisor mantiene Gunicorn en ejecución; la comunicación con Nginx usa un socket Unix, sin reservar un puerto TCP para la aplicación. Nginx entrega CSS, JavaScript y favicon directamente desde la carpeta `static/` del proyecto; Flask entrega el HTML y la API.

La aplicación debe estar en su ruta definitiva. Se requiere Python 3.9 o superior, `python3-venv`, `tzdata`, Nginx, Supervisor, `systemd-tmpfiles` e `iproute2`. Nginx y Supervisor deben estar en ejecución. El usuario de la aplicación necesita acceso de lectura al proyecto y de escritura a la base de datos y su directorio.

## Configuración

Desde la raíz del proyecto:

```sh
cp config.example.ini config.ini
nano config.ini
```

```ini
[app]
database = tasks.db
timezone = America/Santiago
port = 8000
socket = /run/flujo/flujo.sock
allow_self_registration = false
```

`database` puede ser absoluta o relativa a la raíz. `port` se usa solo al ejecutar `python app.py` en desarrollo; en producción Gunicorn utiliza `socket`. Configura el socket con una ruta absoluta de hasta 100 bytes, sin espacios. El directorio inmediato del socket debe ser privado, pertenecer al usuario de la aplicación y al grupo de Nginx, y no permitir acceso a otros usuarios. El grupo de Nginx también debe poder atravesar los directorios superiores. Conserva `config.ini`, `.venv` y la base de datos al copiar actualizaciones.

Con `allow_self_registration = false` solo se puede crear la primera cuenta o agregar usuarios desde una cuenta administradora. Si se cambia a `true`, cualquiera que pueda acceder a la aplicación podrá registrarse; reinicia Flujo para aplicar el cambio.

## Primera instalación

```sh
sudo python3 deploy/deploy.py install --server-name tareas.ejemplo.com --user ubuntu
```

El script crea `.venv`, instala las dependencias, valida el socket, configura Supervisor y Nginx y arranca la aplicación. Si el directorio del socket no existe, el script lo crea con modo `0750` y configura `systemd-tmpfiles` para recrearlo al reiniciar; su directorio superior debe existir. Si ya existe y no fue creado por el script, conserva sus permisos y debes asegurar que cumpla esos requisitos y se mantenga disponible tras reiniciar. El acceso al socket se controla mediante ese directorio; el socket se crea con modo `0666`, accesible solo a quienes puedan atravesarlo. El grupo de Nginx es `www-data` por defecto; si usa otro grupo, indícalo con `--nginx-group GRUPO`. El script no cambia la membresía de los usuarios en los grupos.

Supervisor también ejecuta `flujo-slack` con el mismo usuario, directorio, `APP_CONFIG` y `FLUJO_CONFIG_ONLY=1`. Gunicorn tiene prioridad de arranque 100 y el worker 200; el master de Gunicorn ejecuta `init()` antes de sus workers. La prioridad no garantiza que haya terminado la inicialización: si faltan tablas, el worker sale y Supervisor lo reinicia hasta que la base esté lista. Si la aplicación no arranca, corrige su error y revisa ambos procesos; los reinicios del worker pueden repetirse rápidamente.

Se comparte el Nginx existente; no se reemplazan otros sitios ni se actualizan otros procesos de Supervisor. Agrega `--dry-run` para generar los archivos sin instalarlos. Para conservar una configuración Nginx existente, usa `--skip-nginx` y configura manualmente su destino:

```nginx
proxy_pass http://unix:/run/flujo/flujo.sock;
```

Para que ese Nginx sirva también los estáticos, agrega dentro del bloque `server` que atiende la aplicación el `location` de assets que aparece en `deploy/nginx.conf.template`:

```nginx
location ^~ /static/ {
    alias /home/ubuntu/flujo-app/static/;
    add_header Cache-Control "no-cache";
}
```

Reemplaza la ruta del ejemplo por la ubicación real. El usuario de Nginx debe poder atravesar todos los directorios superiores y leer los archivos de `static/`. Después ejecuta `sudo nginx -t && sudo systemctl reload nginx`. Mientras no se incluya, Flask seguirá entregando los archivos como alternativa.

### Límite de intentos de acceso

La plantilla de instalaciones nuevas limita `/api/login` a 5 peticiones por minuto por IP, con una ráfaga de 3; las solicitudes excedentes reciben HTTP 429. Cuenta tanto accesos correctos como fallidos, y usuarios de una misma IP comparten el límite.

Para una instalación existente o con `--skip-nginx`, agrega una sola vez en el contexto `http` (fuera de `server`, por ejemplo en el archivo del sitio incluido por Nginx):

```nginx
limit_req_zone $binary_remote_addr zone=flujo_login:10m rate=5r/m;
```

En cada bloque `server` que atienda la aplicación, agrega `location = /api/login` de `deploy/nginx.conf.template`, reemplazando `@@SOCKET_PATH@@` por la ruta real del socket y conservando los encabezados y ajustes de proxy que necesite el sitio. No dupliques la zona si ya está definida. Comprueba con `sudo nginx -t` y luego recarga con `sudo systemctl reload nginx`.

Si hay otro proxy delante, configura la IP real solo para proxies de confianza; de lo contrario todos los usuarios podrían compartir el mismo límite. El acceso directo a Flask o al socket no pasa por este control.

La configuración inicial de Nginx usa HTTP. Restringe el acceso en el security group, crea la primera cuenta administradora y configura HTTPS antes de usar contraseñas desde la red. Con Certbot instalado:

```sh
sudo certbot --nginx -d tareas.ejemplo.com
```

Para crear la primera cuenta sin abrir HTTP al público puedes usar un túnel SSH desde tu computador, con el usuario que tiene acceso al socket:

```sh
ssh -L 18080:/run/flujo/flujo.sock ubuntu@IP_PUBLICA
```

Abre `http://localhost:18080` en tu computador con el túnel activo.

## Actualizar

### Slack

Configura las credenciales de tu aplicación Slack en `config.ini`:

```ini
[slack]
client_id = ID_DE_CLIENTE
client_secret = SECRETO_DE_CLIENTE
base_url = https://tareas.ejemplo.com
```

`base_url` debe ser el origen público HTTPS de Flujo: esquema, dominio y puerto opcional, sin ruta (salvo `/`), credenciales, consulta ni fragmento. Por ejemplo, `https://tareas.ejemplo.com`; no se admite instalar Flujo bajo una subruta. En la configuración OAuth de Slack registra exactamente estas dos Redirect URLs, sustituyendo el origen por el configurado:

```text
https://tareas.ejemplo.com/slack/callback/install
https://tareas.ejemplo.com/slack/callback/link
```

Configura el permiso bot `chat:write` y Sign in with Slack con `openid`. La rotación de tokens de la aplicación Slack debe permanecer desactivada: el worker no renueva tokens. Un administrador de Flujo inicia la instalación del workspace; cada usuario vincula su propia cuenta del mismo workspace. La desconexión individual elimina el vínculo; la desconexión administrativa elimina la instalación y vínculos locales. Se intenta revocar los tokens en Slack: si falla, la desconexión local permanece y el worker reintenta la revocación. Mientras haya revocaciones pendientes no se inician nuevas conexiones. Para retirar la aplicación del workspace, desinstálala también desde Slack. Solo se admite un workspace a la vez.

Protege `config.ini` (por ejemplo `chmod 600 config.ini`, propiedad del usuario de la aplicación), la base SQLite, sus respaldos y el directorio: contienen credenciales o tokens. No publiques esos archivos ni sus secretos. Reinicia ambos procesos con `sudo python3 deploy/deploy.py update` tras cambiar la configuración.

La verificación de identidad Slack requiere `PyJWT[crypto]`. `install` y `update` instalan las dependencias de `requirements.txt` en `.venv`; en una instalación manual ejecuta `.venv/bin/python -m pip install -r requirements.txt` como el usuario de la aplicación.

El worker procesa hasta 100 elementos por pasada y espera 30 segundos entre pasadas; agrupa comentarios durante cinco minutos. Respeta límites de Slack con hasta ocho intentos; los pendientes vencen a los siete días y el historial finalizado se elimina después de 30. Un envío interrumpido o con resultado de red incierto queda `uncertain` y no se reintenta automáticamente para evitar duplicados. El bloqueo mediante archivo coordina workers solo en un mismo host; no ejecutes workers de la misma base en varios hosts. Revisa los logs y los estados de `slack_queue` ante fallos; una URL base inválida mantiene los mensajes pendientes hasta corregir la configuración.

### Migración manual de IDs AUTOINCREMENT

Para actualizar una instalación anterior que aún no tenga esta migración, después de `git pull --rebase` y antes del despliegue normal, detén Flujo y respalda la base. Las instalaciones nuevas ya usan `AUTOINCREMENT`; no es necesario repetir este procedimiento en cada despliegue. La pausa evita que los procesos anteriores escriban mientras se reconstruyen las tablas y hasta que se inicie el código nuevo. Ejecuta la migración como el usuario de la aplicación, con permisos de escritura en la base y su directorio:

```sh
sudo supervisorctl stop flujo-slack
sudo supervisorctl stop flujo-app
.venv/bin/python -c 'import os, sqlite3; from pathlib import Path; from settings import load_settings; p=Path(load_settings(environ={"FLUJO_CONFIG_ONLY":"1"})["database"]); b=p.with_name(p.name+".antes-autoincrement.bak"); fd=os.open(b,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd); src=sqlite3.connect(p.as_uri()+"?mode=rw",uri=True); dst=sqlite3.connect(b); src.backup(dst); dst.close(); src.close(); print("Respaldo:",b)'
.venv/bin/python deploy/migrate_autoincrement.py
sudo python3 deploy/deploy.py update
```

El respaldo no sobrescribe uno existente. Si usas un `config.ini` distinto al de la raíz, proporciona su ruta al script con `--config /ruta/config.ini` y usa esa misma configuración para el respaldo. Verifica la ruta de base impresa antes de continuar; el script usa la configuración de producción e ignora `TASKS_DB`. No crea una base inexistente. Si la migración falla, revierte todos sus cambios; mantén Flujo detenido hasta resolver el error o restaurar el respaldo. La migración es idempotente y conserva los IDs, índices, relaciones y los identificadores históricos aún presentes en reglas o métricas. No puede recuperar IDs antiguos que ya no estén registrados en ningún lado.

Si aún no existe `flujo-slack` en una instalación anterior, omite su comando `stop`. Mantén ambos procesos detenidos durante la migración; `update` los arranca después.

### Despliegue normal

Después de copiar el código nuevo, conservando `config.ini`, `.venv` y la base de datos:

```sh
sudo python3 deploy/deploy.py update
```

Actualiza las dependencias y reinicia Flujo. Las URLs de los estáticos incluyen un identificador basado en su contenido y el navegador revalida sus copias en caché. El script migra los bloques anteriores de estáticos que reconoce y conserva las rutas de `/static/` configuradas manualmente. Si cambias el socket, actualiza el `proxy_pass`. Conserva el resto de la configuración y los certificados; si el sitio tiene una configuración personalizada que no puede actualizar, pedirá ajustarla manualmente. El cambio de socket puede provocar una interrupción breve.

Si falta la configuración temporal del socket, `update` intenta reconstruirla. Usa el grupo `www-data` por defecto; si Nginx usa otro, indica `sudo python3 deploy/deploy.py update --nginx-group GRUPO`. Si el directorio ya existía en `/run`, asegúrate de que se recree al reiniciar: el script no toma control de directorios existentes que podrían ser compartidos.

## Migrar una instalación anterior por TCP

Respalda la base de datos y la configuración de Nginx antes de actualizar el código. En el sitio existente `/etc/nginx/sites-available/flujo-app`, cambia cada `proxy_pass http://127.0.0.1:PUERTO;` por el destino Unix mostrado arriba. Verifica la sintaxis con `sudo nginx -t`, pero **aún no recargues Nginx**. Conserva los bloques y certificados HTTPS existentes.

Luego ejecuta:

```sh
sudo python3 deploy/deploy.py install --user ubuntu --skip-nginx
sudo nginx -t && sudo systemctl reload nginx
```

Si Supervisor es de una versión anterior que guardaba la base y zona horaria en su configuración, copia primero esos valores a `config.ini`. La transición puede provocar una breve interrupción. Para volver al servidor anterior, restaura el código y las configuraciones respaldadas y reinicia Supervisor y Nginx.

## Estado

```sh
sudo supervisorctl status flujo-app
sudo supervisorctl status flujo-slack
sudo tail -f /var/log/supervisor/flujo-app.err.log
sudo tail -f /var/log/supervisor/flujo-slack.err.log
sudo nginx -t
```
