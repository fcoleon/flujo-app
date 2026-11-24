# Guía para agentes

## Mapa

- `app.py`: arranque Flask, configuración, conexión SQLite e `init()` (esquema y migraciones).
- `api_router.py`: rutas HTTP explícitas, autenticación y transacciones comunes; recibe servicios acotados al registrarse. `workspace_routes.py`, `project_routes.py`, `task_routes.py`, `comment_routes.py` y `automation_routes.py`: operaciones por área.
- `permissions.py`: permisos compartidos; `task_domain.py`: lógica compartida de tareas, historial y automatizaciones; `web_helpers.py`: respuestas HTTP.
- `mcp_server.py`: servidor MCP de solo lectura; expone `/mcp` con uvicorn sobre un socket Unix junto al de la aplicación y aplica los permisos del usuario dueño de cada token. `mcp_tokens.py`: consulta y validación de tokens.
- `auth.py`: contraseñas y sesiones; `analytics.py`: eventos y estadísticas; `descriptions.py`: saneamiento de HTML; `validation.py`: validación compartida de campos; `settings.py`: configuración compartida.
- `templates/index.html` y `static/`: interfaz. Los archivos JS se cargan en el orden indicado por la plantilla y comparten estado global; conserva los manejadores inline y el versionado de cada asset. `tests/`: pruebas Python y JavaScript; `deploy/`: Gunicorn, Supervisor, Nginx y despliegue.

## Cambios y verificación

- Usa el entorno virtual del proyecto en `.venv` para ejecutar Python, no otro entorno ni el Python global. Desde la raíz, con dependencias instaladas: `.venv/bin/python -m unittest discover -s tests -v`. Las pruebas usan bases temporales. Para pruebas manuales, desactiva `FLUJO_CONFIG_ONLY`, configura `TASKS_DB` con una ruta temporal y comprueba la configuración efectiva antes de arrancar.
- Para ejecutar juntas las pruebas Python y JavaScript, instala Node.js y ejecuta `.venv/bin/python tests/run_all.py`.
- `db()` activa claves foráneas; conserva en `init()` las migraciones compatibles e idempotentes para bases existentes y comprueba tanto bases nuevas como antiguas. No supongas que toda `init()` es una transacción única: usa `executescript()`.
- Conserva las comprobaciones del servidor `role()` y `can_task()`, incluidos proyectos heredados, tareas sin proyecto y permisos de escritura. Mantén juntas las escrituras relacionadas y el rollback ante errores; respeta los `BEGIN IMMEDIATE` que serializan operaciones concurrentes.
- El arranque local llama `init()` en `app.py`; en producción `deploy/gunicorn.conf.py` lo ejecuta en `on_starting` del master antes de los workers. Supervisor arranca `app:app` con esa configuración: conserva este orden al cambiar el arranque. Consulta `deploy/README.md` para despliegue.
- Protege `tasks.db`, otras bases, `config.ini` y secretos: no los borres, sobrescribas ni uses como datos de prueba. Revisa y conserva los cambios locales preexistentes; no los reviertas ni sobrescribas.
