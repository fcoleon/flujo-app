# Despliegue en Ubuntu EC2

Flujo usa Flask en desarrollo y Gunicorn detrás de Nginx en producción. Supervisor mantiene Gunicorn en ejecución; la comunicación con Nginx usa un socket Unix, sin reservar un puerto TCP para la aplicación.

La aplicación debe estar en su ruta definitiva. Se requiere Python 3.9 o superior, `python3-venv`, `tzdata`, Nginx, Supervisor, `systemd-tmpfiles`, `iproute2` y el paquete `acl` (`sudo apt install acl`). Nginx y Supervisor deben estar en ejecución. El usuario de la aplicación necesita acceso de lectura al proyecto y de escritura a la base de datos y su directorio.

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

`database` puede ser absoluta o relativa a la raíz. `port` se usa solo al ejecutar `python app.py` en desarrollo; en producción Gunicorn utiliza `socket`. Configura el socket con una ruta absoluta de hasta 100 bytes, sin espacios. El usuario de la aplicación debe poder escribir en su directorio y el grupo de Nginx debe poder acceder a él y a sus directorios superiores. Conserva `config.ini`, `.venv` y la base de datos al copiar actualizaciones.

Con `allow_self_registration = false` solo se puede crear la primera cuenta o agregar usuarios desde una cuenta administradora. Si se cambia a `true`, cualquiera que pueda acceder a la aplicación podrá registrarse; reinicia Flujo para aplicar el cambio.

## Primera instalación

```sh
sudo python3 deploy/deploy.py install --server-name tareas.ejemplo.com --user ubuntu
```

El script crea `.venv`, instala las dependencias, valida el socket, configura Supervisor y Nginx y arranca la aplicación. Si el directorio del socket no existe, el script lo crea y configura `systemd-tmpfiles` para recrearlo al reiniciar; su directorio superior debe existir. Si ya existe, conserva sus permisos y debes asegurar que se mantenga disponible tras reiniciar. El socket permite acceso al usuario de la app y al grupo de Nginx (`www-data` por defecto) mediante ACL; si Nginx usa otro grupo, indícalo con `--nginx-group GRUPO`. El script no cambia la membresía de los usuarios en los grupos.

Se comparte el Nginx existente; no se reemplazan otros sitios ni se actualizan otros procesos de Supervisor. Agrega `--dry-run` para generar los archivos sin instalarlos. Para conservar una configuración Nginx existente, usa `--skip-nginx` y configura manualmente su destino:

```nginx
proxy_pass http://unix:/run/flujo/flujo.sock;
```

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

Después de copiar el código nuevo, conservando `config.ini`, `.venv` y la base de datos:

```sh
sudo python3 deploy/deploy.py update
```

Actualiza las dependencias y reinicia solo Flujo. Si cambias el socket, también actualiza el `proxy_pass` del sitio de Flujo, valida Nginx y lo recarga, conservando el resto de la configuración y los certificados. Si el sitio tiene varios destinos o una configuración personalizada, el script pedirá actualizar el destino manualmente. El cambio de socket puede provocar una interrupción breve.

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
sudo tail -f /var/log/supervisor/flujo-app.err.log
sudo nginx -t
```
