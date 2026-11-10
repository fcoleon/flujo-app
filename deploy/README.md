# Despliegue en Ubuntu EC2

La aplicación debe estar copiada en su ruta definitiva. Se requiere Python 3.9 o superior, `python3-venv`, `tzdata`, Nginx, Supervisor e `iproute2` instalados y los servicios Nginx y Supervisor en ejecución. El usuario de la aplicación debe tener acceso de lectura al proyecto y de escritura a la base de datos y su directorio.

## Configuración

Desde la raíz del proyecto, copia y edita la configuración local:

```sh
cp config.example.ini config.ini
nano config.ini
```

```ini
[app]
database = tasks.db
timezone = America/Santiago
port = 8100
```

`database` puede ser una ruta absoluta o relativa a la raíz del proyecto. Elige un puerto local libre para la aplicación; puedes revisarlos con `sudo ss -ltnp`. Mantén `config.ini`, `.venv` y la base de datos al copiar actualizaciones. La configuración local y la base están excluidas de Git.

## Primera instalación

Con un dominio distinto al de las demás aplicaciones y usando el usuario Linux que ejecutará Flujo:

```sh
sudo python3 deploy/deploy.py install --server-name tareas.ejemplo.com --user ubuntu
```

El script crea `.venv` si falta, comprueba el puerto, configura Supervisor y Nginx y arranca la aplicación. Comparte el Nginx existente; no reemplaza otros sitios ni actualiza otros procesos de Supervisor. Para revisar los archivos sin instalarlos, agrega `--dry-run`.

Para conservar una configuración Nginx existente, instala con `--skip-nginx` y asegúrate de que el proxy apunte a `127.0.0.1` y al puerto de `config.ini`:

```sh
sudo python3 deploy/deploy.py install --user ubuntu --skip-nginx
```

La configuración inicial de Nginx usa HTTP. Restringe el acceso en el security group, crea la primera cuenta administradora y configura HTTPS antes de usar contraseñas desde la red. Con Certbot instalado y el sitio de Nginx configurado:

```sh
sudo certbot --nginx -d tareas.ejemplo.com
```

## Actualizar

Después de copiar el código nuevo, conservando `config.ini`, `.venv` y la base de datos:

```sh
sudo python3 deploy/deploy.py update
```

El comando valida la configuración y el puerto y reinicia solo Flujo; no modifica ni recarga Nginx ni su certificado. Si cambias el puerto en `config.ini`, actualiza también el `proxy_pass` de Nginx y valida su configuración antes de reiniciar. El script detecta discrepancias en el sitio administrado por Flujo cuando puede identificar su puerto.

Si vienes de la versión anterior del script, copia el puerto, la base y la zona horaria desde `/etc/supervisor/conf.d/flujo-app.conf` a `config.ini` y ejecuta una vez `install --user ubuntu --skip-nginx`. Después podrás usar `update`.

Para consultar el estado o los errores:

```sh
sudo supervisorctl status flujo-app
sudo tail -f /var/log/supervisor/flujo-app.err.log
```
