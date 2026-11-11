# Guía para agentes

## Mapa

- `app.py`: rutas Flask, permisos, conexión SQLite y `init()` (esquema y migraciones).
- `auth.py`: contraseñas y sesiones; `analytics.py`: eventos y estadísticas; `descriptions.py`: saneamiento de HTML; `settings.py`: configuración compartida.
- `templates/index.html` y `static/`: interfaz; `tests/`: pruebas `unittest`; `deploy/`: Gunicorn, Supervisor, Nginx y despliegue.

## Cambios y verificación

- Desde la raíz, con dependencias instaladas: `python -m unittest discover -s tests -v`. Las pruebas usan bases temporales. Para pruebas manuales, desactiva `FLUJO_CONFIG_ONLY`, configura `TASKS_DB` con una ruta temporal y comprueba la configuración efectiva antes de arrancar.
- `db()` activa claves foráneas; conserva en `init()` las migraciones compatibles e idempotentes para bases existentes y comprueba tanto bases nuevas como antiguas. No supongas que toda `init()` es una transacción única: usa `executescript()`.
- Conserva las comprobaciones del servidor `role()` y `can_task()`, incluidos proyectos heredados, tareas sin proyecto y permisos de escritura. Mantén juntas las escrituras relacionadas y el rollback ante errores; respeta los `BEGIN IMMEDIATE` que serializan operaciones concurrentes.
- El arranque local llama `init()` en `app.py`; en producción `deploy/gunicorn.conf.py` lo ejecuta en `on_starting` del master antes de los workers. Supervisor arranca `app:app` con esa configuración: conserva este orden al cambiar el arranque. Consulta `deploy/README.md` para despliegue.
- Protege `tasks.db`, otras bases, `config.ini` y secretos: no los borres, sobrescribas ni uses como datos de prueba. Revisa y conserva los cambios locales preexistentes; no los reviertas ni sobrescribas.
