import importlib.util
import runpy
import errno
import os
import re
from pathlib import Path
import subprocess
import stat
import sys
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'deploy.py'
spec = importlib.util.spec_from_file_location('flujo_deploy', DEPLOY_SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentConfigTest(unittest.TestCase):
    def setUp(self):
        self.pid_patch = patch.object(deploy, 'supervisor_process_pid', return_value=None)
        self.pid_patch.start()
        self.addCleanup(self.pid_patch.stop)

    def test_gunicorn_uses_directory_permissions_without_acl(self):
        config = DEPLOY_SCRIPT.parent / 'gunicorn.conf.py'
        with patch('settings.load_settings', return_value={'socket': '/run/custom/app.sock'}):
            gunicorn = runpy.run_path(str(config))
            self.assertEqual(0o777 & ~gunicorn['umask'], 0o666)
            self.assertNotIn('when_ready', gunicorn)

    def test_prepare_venv_checks_required_modules_exist_and_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'templates').mkdir()
            (root / 'templates' / 'index.html').touch()
            modules = ('app.py', 'settings.py', 'analytics.py', 'auth.py', 'descriptions.py',
                       'slack_notify.py', 'slack_auth.py', 'automations.py', 'mcp_server.py')
            for name in modules:
                (root / name).touch()
            with patch.object(deploy, 'APP_DIR', root):
                for name in ('slack_auth.py', 'automations.py'):
                    path = root / name
                    with self.subTest(name=name, failure='missing'):
                        path.unlink()
                        with patch.object(deploy, 'as_user') as run, self.assertRaisesRegex(ValueError, 'aplicación completa'):
                            deploy.prepare_venv('ubuntu', root / 'tasks.db', 'UTC')
                        run.assert_not_called()
                        path.touch()
                    with self.subTest(name=name, failure='unreadable'):
                        def check(user, *command):
                            if command == ('test', '-r', path):
                                raise subprocess.CalledProcessError(1, command)
                        with patch.object(deploy, 'as_user', side_effect=check), self.assertRaisesRegex(ValueError, re.escape(name)):
                            deploy.prepare_venv('ubuntu', root / 'tasks.db', 'UTC')

    def test_slack_supervisor_migration_is_idempotent_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); config = root / 'flujo.conf'
            config.write_text('[program:flujo-app]\nuser=ubuntu\n')
            with patch.object(deploy, 'SUPERVISOR_FILE', config):
                deploy.prepare_worker_supervisor('ubuntu')
                migrated = config.read_text()
                deploy.prepare_worker_supervisor('ubuntu')
                self.assertEqual(config.read_text(), migrated)
                self.assertIn('[program:flujo-app]\nuser=ubuntu', migrated)
                self.assertIn('[program:flujo-mcp]', migrated)
                config.write_text(migrated.replace('user=ubuntu\nenvironment=', 'user=other\nenvironment='))
                with self.assertRaisesRegex(ValueError, 'otra configuración'):
                    deploy.prepare_worker_supervisor('ubuntu')

    def test_worker_recovers_after_application_initializes_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root / 'config.ini'; db = root / 'tasks.db'
            config.write_text(f'[app]\ndatabase={db}\ntimezone=UTC\n')
            env = {**os.environ, 'APP_CONFIG': str(config), 'FLUJO_CONFIG_ONLY': '1'}
            worker = [sys.executable, str(deploy.APP_DIR / 'slack_notify.py')]
            failed = subprocess.run(worker, env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('slack_preferences', failed.stderr)
            subprocess.run([sys.executable, '-c', 'import app; app.init()'], env=env, check=True, timeout=10)
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run(worker, env=env, capture_output=True, timeout=2)
            with sqlite3.connect(db) as connection:
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='slack_queue'").fetchone())

    def test_generated_templates_fill_all_values_and_preserve_nginx_variables(self):
        values = {'APP_DIR': '/srv/flujo', 'APP_USER': 'ubuntu', 'SOCKET_PATH': '/run/flujo/flujo.sock',
                  'MCP_SOCKET_PATH': '/run/flujo/mcp.sock',
                  'CONFIG_PATH': '/srv/flujo/config.ini', 'NGINX_GROUP': 'www-data',
                  'SERVER_NAME': 'tareas.ejemplo.com'}
        supervisor = deploy.render('supervisor.conf.template', values)
        nginx = deploy.render('nginx.conf.template', values)
        self.assertIn('command=/srv/flujo/.venv/bin/gunicorn --config /srv/flujo/deploy/gunicorn.conf.py app:app', supervisor)
        self.assertIn('directory=/srv/flujo', supervisor)
        self.assertIn('user=ubuntu', supervisor)
        self.assertIn('APP_CONFIG="/srv/flujo/config.ini"', supervisor)
        self.assertIn('FLUJO_CONFIG_ONLY="1"', supervisor)
        self.assertIn('[program:flujo-slack]', supervisor)
        self.assertIn('command=/srv/flujo/.venv/bin/python /srv/flujo/slack_notify.py', supervisor)
        self.assertIn('priority=200\nstartsecs=0', supervisor)
        self.assertIn('[program:flujo-mcp]', supervisor)
        self.assertIn('command=/srv/flujo/.venv/bin/python /srv/flujo/mcp_server.py', supervisor)
        self.assertIn('priority=150\nstartsecs=3', supervisor)
        self.assertNotIn('TASKS_DB=', supervisor)
        self.assertIn('server_name tareas.ejemplo.com;', nginx)
        self.assertIn('proxy_pass http://unix:/run/flujo/flujo.sock;', nginx)
        self.assertIn('location = /mcp {', nginx)
        self.assertIn('proxy_pass http://unix:/run/flujo/mcp.sock;', nginx)
        self.assertIn('proxy_buffering off;', nginx)
        self.assertIn('proxy_set_header Host $host;', nginx)
        self.assertIn('limit_req_zone $binary_remote_addr zone=flujo_login:10m rate=5r/m;', nginx)
        self.assertIn('location = /api/login {', nginx)
        self.assertIn('limit_req zone=flujo_login burst=3 nodelay;', nginx)
        self.assertIn('limit_req_status 429;', nginx)
        self.assertNotIn('@@', supervisor + nginx)

    def test_invalid_configuration_values_are_rejected(self):
        for name in ('example.com;return 200', 'example.com\nserver_name _', '*.example.com', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deploy.check_server_name(name)
        for path in ('/srv/app with spaces', '/srv/app;include', '/srv/100%'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                deploy.check_path(path, 'Ruta')
        self.assertEqual(deploy.check_server_name('192.0.2.1'), '192.0.2.1')

    def test_existing_nginx_changes_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            confdir = base / 'supervisor'; available = base / 'available'; enabled = base / 'enabled'
            for directory in (confdir, available, enabled):
                directory.mkdir()
            nginx = available / 'flujo-app'; nginx.write_text('HTTPS existente', encoding='utf-8')
            with patch.object(deploy, 'SUPERVISOR_FILE', confdir / 'flujo-app.conf'), \
                 patch.object(deploy, 'NGINX_FILE', nginx), \
                 patch.object(deploy, 'NGINX_LINK', enabled / 'flujo-app'), \
                 patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'):
                with self.assertRaisesRegex(ValueError, 'ya existe y es diferente'):
                    deploy.apply_config('supervisor nuevo', 'nginx nuevo', False)
            self.assertEqual(nginx.read_text(encoding='utf-8'), 'HTTPS existente')
            self.assertFalse((confdir / 'flujo-app.conf').exists())

    def test_nginx_validation_failure_removes_only_new_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            confdir = base / 'supervisor'; available = base / 'available'; enabled = base / 'enabled'
            for directory in (confdir, available, enabled):
                directory.mkdir()
            nginx = available / 'flujo-app'; link = enabled / 'flujo-app'
            with patch.object(deploy, 'SUPERVISOR_FILE', confdir / 'flujo-app.conf'), \
                 patch.object(deploy, 'NGINX_FILE', nginx), patch.object(deploy, 'NGINX_LINK', link), \
                 patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'), \
                 patch.object(deploy, 'run', side_effect=subprocess.CalledProcessError(1, ['nginx', '-t'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    deploy.apply_config('supervisor nuevo', 'nginx nuevo', False)
            self.assertFalse(nginx.exists())
            self.assertFalse(os.path.lexists(link))
            self.assertFalse((confdir / 'flujo-app.conf').exists())

    def test_atomic_config_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / 'original'; original.write_text('conservar', encoding='utf-8')
            link = base / 'config'; link.symlink_to(original)
            with self.assertRaises(ValueError):
                deploy.write_atomic(link, 'nuevo')
            self.assertEqual(original.read_text(encoding='utf-8'), 'conservar')

    def test_apply_enables_nginx_and_restarts_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            confdir = base / 'supervisor'; available = base / 'available'; enabled = base / 'enabled'
            for directory in (confdir, available, enabled):
                directory.mkdir()
            nginx = available / 'flujo-app'; link = enabled / 'flujo-app'; supervisor = confdir / 'flujo-app.conf'
            with patch.object(deploy, 'SUPERVISOR_FILE', supervisor), \
                 patch.object(deploy, 'NGINX_FILE', nginx), patch.object(deploy, 'NGINX_LINK', link), \
                 patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'), \
                 patch.object(deploy.Path, 'mkdir'), patch.object(deploy, 'run') as run:
                run.return_value = subprocess.CompletedProcess([], 0, '', '')
                deploy.apply_config('config supervisor', 'server_name tareas.test;', False)
            self.assertEqual(supervisor.read_text(), 'config supervisor')
            self.assertEqual(nginx.read_text(), 'server_name tareas.test;')
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), nginx.resolve())
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['nginx', '-t'], ['supervisorctl', 'reread'], ['supervisorctl', 'update', 'flujo-app'],
                ['supervisorctl', 'restart', 'flujo-app'], ['supervisorctl', 'status', 'flujo-app'],
                ['supervisorctl', 'update', 'flujo-slack'], ['supervisorctl', 'status', 'flujo-slack'],
                ['supervisorctl', 'update', 'flujo-mcp'], ['supervisorctl', 'status', 'flujo-mcp'],
                ['systemctl', 'reload', 'nginx'],
            ])

    def test_socket_path_and_conflicts(self):
        self.assertEqual(deploy.validate_socket_path('/run/flujo/app.sock'),Path('/run/flujo/app.sock'))
        self.assertEqual(deploy.validate_socket_path('/tmp/app.sock'),Path('/tmp/app.sock'))
        self.assertEqual(deploy.validate_socket_path('/run/otro/socket'),Path('/run/otro/socket'))
        for path in ('/run/flujo/../other.sock','relativo.sock','/tmp/a:b'):
            with self.subTest(path=path),self.assertRaises(ValueError): deploy.validate_socket_path(path)
        with tempfile.TemporaryDirectory() as tmp:
            existing=Path(tmp)/'app.sock';existing.write_text('no borrar')
            with self.assertRaisesRegex(ValueError,'otro archivo'):
                deploy.check_socket(existing,deploy.pwd.getpwuid(os.getuid()).pw_name,None)
            self.assertEqual(existing.read_text(),'no borrar')

    def test_active_socket_must_belong_to_own_supervisor_process(self):
        class ExistingSocket:
            def is_symlink(self): return False
            def exists(self): return True
            def stat(self): return SimpleNamespace(st_mode=stat.S_IFSOCK,st_uid=os.getuid())
            def __str__(self): return '/run/flujo/app.sock'
        path=ExistingSocket();user=deploy.pwd.getpwuid(os.getuid()).pw_name
        with patch.object(deploy.socket,'socket') as sock, patch.object(deploy.shutil,'which',return_value='/usr/bin/ss'), \
             patch.object(deploy.subprocess,'run') as run:
            probe=sock.return_value.__enter__.return_value;probe.connect_ex.return_value=0
            run.return_value=subprocess.CompletedProcess([],0,'u_str LISTEN 0 128 /run/flujo/app.sock 1 * 0 users:(("gunicorn",pid=123,fd=3))\n','')
            deploy.check_socket(path,user,123)
            with self.assertRaisesRegex(ValueError,'en uso'): deploy.check_socket(path,user,456)
            probe.connect_ex.return_value=errno.ECONNREFUSED
            deploy.check_socket(path,user,None)

    def test_socket_directory_configuration_never_overwrites_other_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);config=base/'tmpfiles.conf';directory=base/'flujo'
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            config.write_text('d /run/flujo 0750 otra otra -\n')
            with patch.object(deploy,'TMPFILES_FILE',config), \
                 patch.object(deploy,'run') as run:
                with self.assertRaises(ValueError):
                    deploy.ensure_socket_directory(user,group,directory/'socket',install=True)
                run.assert_not_called()
            self.assertEqual(config.read_text(),'d /run/flujo 0750 otra otra -\n')

    def test_existing_supervisor_program_from_another_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'flujo-app.conf'
            with patch.object(deploy, 'SUPERVISOR_FILE', config), patch.object(deploy, 'supervisor_process_pid', return_value=123):
                with self.assertRaisesRegex(ValueError, 'no se encontró'):
                    deploy.check_program_identity()
            config.write_text('[program:flujo-app]\ndirectory=/otra/app\ncommand=/otra/app/bin/python\n')
            with patch.object(deploy, 'SUPERVISOR_FILE', config):
                with self.assertRaisesRegex(ValueError, 'otra configuración o ruta'):
                    deploy.check_program_identity()

    def test_update_recognizes_supervisor_config_with_another_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();config=root/'flujotasks.conf';default=root/'flujo-app.conf'
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            config.write_text(f'[program:flujo-app]\nuser={user}\ndirectory={deploy.APP_DIR}\n'
                f'command={deploy.APP_DIR}/.venv/bin/gunicorn --config {deploy.APP_DIR}/deploy/gunicorn.conf.py app:app\n'
                f'environment=APP_CONFIG="{deploy.APP_DIR}/config.ini",FLUJO_CONFIG_ONLY="1"\n')
            with patch.object(deploy,'SUPERVISOR_FILE',default),patch.object(deploy,'supervisor_process_pid',return_value=123):
                self.assertEqual(deploy.check_program_identity(),123)
                self.assertEqual(deploy.installed_supervisor_settings(),user)
                default.write_text(config.read_text())
                with self.assertRaisesRegex(ValueError,'varias configuraciones'):
                    deploy.check_program_identity()

    def test_missing_socket_config_can_be_rebuilt_without_changing_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'tmpfiles.conf';directory=root/'socket';directory.mkdir(mode=0o750)
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            before=directory.stat().st_mode
            with patch.object(deploy,'TMPFILES_FILE',config),patch.object(deploy,'as_user'), \
                 patch.object(deploy.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())):
                self.assertEqual(deploy.installed_socket_group(user,directory/'app.sock',group),group)
                deploy.ensure_socket_directory(user,group,directory/'app.sock',install=True)
                self.assertEqual(deploy.installed_socket_group(user,directory/'app.sock'),group)
            self.assertEqual(directory.stat().st_mode,before)
            self.assertIn(f'# flujo-app user={user} group={group}',config.read_text())
            self.assertNotIn('d '+str(directory),config.read_text())

    def test_missing_socket_config_does_not_guess_different_nginx_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'socket';directory.mkdir()
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            other='different-group' if group!='different-group' else 'other-group'
            with patch.object(deploy,'TMPFILES_FILE',root/'tmpfiles.conf'):
                with self.assertRaisesRegex(ValueError,'grupo'):
                    deploy.installed_socket_group(user,directory/'app.sock',other)

    def test_update_recovers_missing_socket_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); directory=root/'socket';directory.mkdir(mode=0o750)
            (root/'config.ini').write_text(f'[app]\ndatabase=tasks.db\ntimezone=UTC\nsocket={directory}/app.sock\n')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            with patch.object(deploy,'APP_DIR',root),patch.object(deploy,'TMPFILES_FILE',root/'tmpfiles.conf'), \
                 patch.object(deploy.os,'geteuid',return_value=0),patch.object(deploy.shutil,'which',return_value='/bin/fake'), \
                 patch.object(deploy,'check_program_identity',return_value=123), \
                 patch.object(deploy,'installed_supervisor_settings',return_value=user), \
                 patch.object(deploy,'check_socket'),patch.object(deploy,'plan_nginx_socket_update',return_value=None), \
                 patch.object(deploy,'plan_nginx_static_update',return_value=None), \
                 patch.object(deploy,'prepare_venv'),patch.object(deploy,'ensure_socket_directory') as ensure, \
                 patch.object(deploy,'run'):
                self.assertEqual(deploy.main(['update','--nginx-group',group]),0)
                ensure.assert_called_once_with(user,group,directory/'app.sock',install=True)

    def test_nginx_conflicting_server_name_is_rejected_even_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); confdir = base / 'supervisor'; available = base / 'available'; enabled = base / 'enabled'
            for directory in (confdir, available, enabled): directory.mkdir()
            nginx = available / 'flujo-app'; link = enabled / 'flujo-app'
            output = 'nginx: [warn] conflicting server name "tareas.test" on 0.0.0.0:80, ignored\n'
            with patch.object(deploy, 'SUPERVISOR_FILE', confdir / 'flujo-app.conf'), \
                 patch.object(deploy, 'NGINX_FILE', nginx), patch.object(deploy, 'NGINX_LINK', link), \
                 patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'), \
                 patch.object(deploy, 'run', return_value=subprocess.CompletedProcess([], 0, '', output)):
                with self.assertRaisesRegex(ValueError, 'ya tiene un sitio'):
                    deploy.apply_config('supervisor', 'server_name tareas.test;', False)
            self.assertFalse(nginx.exists()); self.assertFalse(os.path.lexists(link))

    def test_update_only_restarts_flujo_without_touching_nginx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/'config.ini').write_text('[app]\ndatabase=tasks.db\nport=8100\ntimezone=UTC\n')
            runtime=root/'runtime.conf';runtime.write_text('existing')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy, 'APP_DIR', root), patch.object(deploy,'TMPFILES_FILE',runtime), patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'), \
                 patch.object(deploy, 'check_program_identity', return_value=123), \
                 patch.object(deploy, 'installed_supervisor_settings', return_value=user), \
                 patch.object(deploy, 'check_socket') as socket_check, \
                 patch.object(deploy, 'plan_nginx_socket_update', return_value=None) as nginx_check, \
                 patch.object(deploy, 'plan_nginx_static_update', return_value=None), \
                 patch.object(deploy, 'installed_socket_group', return_value=deploy.grp.getgrgid(os.getgid()).gr_name), \
                 patch.object(deploy, 'ensure_socket_directory') as socket_dir, \
                 patch.object(deploy, 'prepare_venv') as prepare, \
                 patch.object(deploy, 'run') as run:
                self.assertEqual(deploy.main(['update']),0)
            socket_check.assert_called_once_with(Path('/run/flujo/flujo.sock'),user,123)
            nginx_check.assert_called_once_with(Path('/run/flujo/flujo.sock'), Path('/run/flujo/mcp.sock'))
            self.assertFalse(socket_dir.call_args.kwargs['install'])
            self.assertFalse(prepare.call_args.kwargs['create'])
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['supervisorctl','reread'], ['supervisorctl','restart','flujo-app'], ['supervisorctl','status','flujo-app'],
                ['supervisorctl','update','flujo-slack'], ['supervisorctl','status','flujo-slack'],
                ['supervisorctl','update','flujo-mcp'], ['supervisorctl','status','flujo-mcp']])

    def test_update_starts_stopped_flujo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/'config.ini').write_text('[app]\ndatabase=tasks.db\nport=8100\ntimezone=UTC\n')
            runtime=root/'runtime.conf';runtime.write_text('existing')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy,'APP_DIR',root), patch.object(deploy,'TMPFILES_FILE',runtime), patch.object(deploy.os,'geteuid',return_value=0), \
                 patch.object(deploy.shutil,'which',return_value='/usr/bin/fake'), patch.object(deploy,'check_program_identity',return_value=0), \
                 patch.object(deploy,'installed_supervisor_settings',return_value=user), patch.object(deploy,'check_socket'), \
                 patch.object(deploy,'plan_nginx_socket_update',return_value=None), patch.object(deploy,'plan_nginx_static_update',return_value=None), \
                 patch.object(deploy,'installed_socket_group',return_value=deploy.grp.getgrgid(os.getgid()).gr_name), \
                 patch.object(deploy,'ensure_socket_directory'), patch.object(deploy,'prepare_venv'), patch.object(deploy,'run') as run:
                self.assertEqual(deploy.main(['update']),0)
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['supervisorctl','reread'], ['supervisorctl','start','flujo-app'], ['supervisorctl','status','flujo-app'],
                ['supervisorctl','update','flujo-slack'], ['supervisorctl','status','flujo-slack'],
                ['supervisorctl','update','flujo-mcp'], ['supervisorctl','status','flujo-mcp']])

    def test_update_refuses_mismatched_nginx_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'flujo-app';site.write_text('proxy_pass http://unix:/run/flujo/flujo.sock:;')
            with patch.object(deploy,'NGINX_FILE',site):
                deploy.check_nginx_socket('/run/flujo/flujo.sock')
                with self.assertRaisesRegex(ValueError,'otro destino'):
                    deploy.check_nginx_socket('/run/flujo/otro.sock')
                site.write_text('proxy_pass http://127.0.0.1:8000;')
                with self.assertRaisesRegex(ValueError,'otro destino'):
                    deploy.check_nginx_socket('/run/flujo/flujo.sock')
            site.write_text('proxy_pass http://unix:/run/flujo/flujo.sock;\nproxy_pass http://unix:/run/flujo/mcp.sock;\n')
            with patch.object(deploy,'NGINX_FILE',site), patch('builtins.print'):
                deploy.check_nginx_socket('/run/flujo/flujo.sock','/run/flujo/mcp.sock')
            site.write_text('proxy_pass http://unix:/run/flujo/mcp.sock;\n')
            with patch.object(deploy,'NGINX_FILE',site):
                with self.assertRaisesRegex(ValueError,'otro destino'):
                    deploy.check_nginx_socket('/run/flujo/flujo.sock','/run/flujo/mcp.sock')

    def test_nginx_socket_update_rewrites_only_the_application_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'flujo-app'
            original=('server {\n proxy_pass http://unix:/run/flujo/old.sock;\n'
                      ' location = /mcp {\n proxy_pass http://unix:/run/flujo/mcp.sock;\n }\n}\n')
            site.write_text(original)
            with patch.object(deploy,'NGINX_FILE',site):
                before,after=deploy.plan_nginx_socket_update('/run/custom/new.sock','/run/custom/mcp.sock')
                self.assertEqual(before,original)
                self.assertIn('proxy_pass http://unix:/run/custom/new.sock;',after)
                self.assertIn('proxy_pass http://unix:/run/custom/mcp.sock;',after)
                # Con el mismo directorio el socket MCP no cambia.
                site.write_text(original.replace('/run/flujo/old.sock','/run/custom/new.sock'))
                self.assertIsNone(deploy.plan_nginx_socket_update('/run/custom/new.sock','/run/flujo/mcp.sock'))

    def test_old_supervisor_environment_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'flujo.conf'
            config.write_text('[program:flujo-app]\nuser=ubuntu\nenvironment=PORT="8000",TASKS_DB="/srv/app.db"\n')
            with patch.object(deploy,'SUPERVISOR_FILE',config):
                with self.assertRaisesRegex(ValueError,'versión anterior'):
                    deploy.installed_supervisor_settings()
                config.write_text(f'[program:flujo-app]\nuser=ubuntu\ncommand={deploy.APP_DIR}/.venv/bin/gunicorn --config {deploy.APP_DIR}/deploy/gunicorn.conf.py app:app\nenvironment=APP_CONFIG="{deploy.APP_DIR}/config.ini",FLUJO_CONFIG_ONLY="1"\n')
                self.assertEqual(deploy.installed_supervisor_settings(),'ubuntu')

    def test_install_dry_run_uses_ini_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'config.ini').write_text('[app]\nport=8123\ntimezone=UTC\ndatabase=tasks.db\nsocket=/run/otra-app/custom.sock\n')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy,'APP_DIR',root):
                self.assertEqual(deploy.main(['install','--server-name','tareas.example.com','--user',user,'--dry-run','--output-dir',str(root/'generated')]),0)
            nginx=(root/'generated/nginx.conf').read_text()
            supervisor=(root/'generated/supervisor.conf').read_text()
            self.assertIn('proxy_pass http://unix:/run/otra-app/custom.sock;',nginx)
            self.assertIn('proxy_pass http://unix:/run/otra-app/mcp.sock;',nginx)
            self.assertIn(f'APP_CONFIG="{root}/config.ini"',supervisor)
            self.assertIn('d /run/otra-app 0750 ',(root/'generated/tmpfiles.conf').read_text())
            self.assertNotIn('flujo-app-static.conf',nginx)
            self.assertIn(f'alias {root}/static/;',nginx)
            self.assertIn('no-cache',nginx)
            self.assertFalse((root/'generated/nginx-static.conf').exists())

    def test_nginx_socket_update_preserves_https_and_rejects_multiple_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'flujo-app'
            original='server {\n ssl_certificate /etc/letsencrypt/cert.pem;\n proxy_pass http://unix:/run/flujo/old.sock:;\n}\n'
            site.write_text(original)
            with patch.object(deploy,'NGINX_FILE',site):
                before,after=deploy.plan_nginx_socket_update('/run/custom/new.sock','/run/custom/mcp.sock')
                self.assertEqual(before,original)
                self.assertIn('ssl_certificate /etc/letsencrypt/cert.pem;',after)
                self.assertIn('proxy_pass http://unix:/run/custom/new.sock;',after)
                site.write_text(original + 'proxy_pass http://127.0.0.1:9000;\n')
                with self.assertRaisesRegex(ValueError,'varios destinos'):
                    deploy.plan_nginx_socket_update('/run/custom/new.sock','/run/custom/mcp.sock')

    def test_shared_socket_directory_permissions_are_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'shared'; directory.mkdir(mode=0o750)
            before=directory.stat().st_mode
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            with patch.object(deploy,'TMPFILES_FILE',config), patch.object(deploy,'as_user'), \
                 patch.object(deploy.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())), \
                 patch.object(deploy,'run') as run:
                deploy.ensure_socket_directory(user,group,directory/'custom.sock',install=True)
            self.assertEqual(directory.stat().st_mode,before)
            self.assertNotIn('d '+str(directory),config.read_text())
            run.assert_not_called()

    def test_failed_directory_check_restores_previous_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'shared'; directory.mkdir(mode=0o750)
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            original=f'd /run/flujo 0750 {user} {group} -\n'; config.write_text(original)
            with patch.object(deploy,'TMPFILES_FILE',config), patch.object(deploy,'as_user',side_effect=subprocess.CalledProcessError(1,'test')):
                with self.assertRaisesRegex(ValueError,'permisos'):
                    deploy.ensure_socket_directory(user,group,directory/'custom.sock',install=False)
            self.assertEqual(config.read_text(),original)

    def test_legacy_managed_directory_keeps_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'managed'; directory.mkdir(mode=0o750)
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            config.write_text(f'd {directory} 0750 {user} {group} -\n')
            with patch.object(deploy,'TMPFILES_FILE',config), patch.object(deploy,'as_user'), \
                 patch.object(deploy.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())), \
                 patch.object(deploy,'run') as run:
                deploy.ensure_socket_directory(user,group,directory/'app.sock',install=False)
            self.assertIn(f'd {directory} 0750 {user} {group} -',config.read_text())
            run.assert_called_once_with(['systemd-tmpfiles','--create',str(config)])

    def test_public_socket_directory_is_rejected_without_changing_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'shared'; directory.mkdir(mode=0o755)
            before=directory.stat().st_mode
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            with patch.object(deploy,'TMPFILES_FILE',config):
                with self.assertRaisesRegex(ValueError,'directorio privado'):
                    deploy.ensure_socket_directory(user,group,directory/'custom.sock',install=True)
            self.assertEqual(directory.stat().st_mode,before)
            self.assertFalse(config.exists())

    def test_update_changes_nginx_and_reloads_when_socket_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); site=root/'nginx.conf'
            (root/'config.ini').write_text('[app]\ndatabase=tasks.db\ntimezone=UTC\nsocket=/run/custom/new.sock\n')
            site.write_text('server {\n location / {\n proxy_pass http://unix:/run/flujo/old.sock;\n }\n}\n')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy,'APP_DIR',root), patch.object(deploy,'NGINX_FILE',site), \
                 patch.object(deploy.os,'geteuid',return_value=0), patch.object(deploy.shutil,'which',return_value='/bin/fake'), \
                 patch.object(deploy,'check_program_identity',return_value=123), \
                 patch.object(deploy,'installed_supervisor_settings',return_value=user), \
                 patch.object(deploy,'installed_socket_group',return_value=deploy.grp.getgrgid(os.getgid()).gr_name), \
                 patch.object(deploy,'check_socket'), patch.object(deploy,'ensure_socket_directory'), \
                 patch.object(deploy,'prepare_venv'), \
                 patch.object(deploy,'run') as run:
                self.assertEqual(deploy.main(['update']),0)
            self.assertIn('proxy_pass http://unix:/run/custom/new.sock;',site.read_text())
            self.assertIn(f'alias {root}/static/;',site.read_text())
            self.assertNotIn('flujo-app-static.conf',site.read_text())
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['supervisorctl','reread'], ['nginx','-t'], ['supervisorctl','restart','flujo-app'],
                ['supervisorctl','status','flujo-app'], ['systemctl','reload','nginx'],
                ['supervisorctl','update','flujo-slack'], ['supervisorctl','status','flujo-slack'],
                ['supervisorctl','update','flujo-mcp'], ['supervisorctl','status','flujo-mcp']])

    def test_static_nginx_migration_keeps_ssl_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'site'
            original='server {\n ssl_certificate /etc/letsencrypt/live/test.pem;\n location / {\n  proxy_pass http://unix:/run/flujo/app.sock;\n }\n}\n'
            site.write_text(original)
            with patch.object(deploy,'NGINX_FILE',site):
                before,after=deploy.plan_nginx_static_update(None)
                self.assertEqual(before,original)
                self.assertIn('ssl_certificate /etc/letsencrypt/live/test.pem;',after)
                self.assertIn('location ^~ /static/',after)
                self.assertNotIn('flujo-app-static.conf',after)
                site.write_text(after)
                self.assertIsNone(deploy.plan_nginx_static_update(None))
                customized=after.replace(f'alias {deploy.APP_DIR}/static/;', 'alias /srv/flujo/static/;')
                site.write_text(customized)
                self.assertIsNone(deploy.plan_nginx_static_update(None))

    def test_migrates_existing_snippet_without_changing_custom_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); site=root/'site'; snippet=root/'old-static.conf'
            include=f'include {snippet};'
            original=f'server {{\n ssl_certificate /etc/letsencrypt/live/test.pem;\n {include}\n location / {{\n  proxy_pass http://unix:/run/flujo/app.sock;\n }}\n}}\n'
            site.write_text(original)
            snippet.write_text('\n'.join(deploy.PREVIOUS_STATIC_BLOCK.splitlines()[1:-1])+'\n')
            with patch.object(deploy,'NGINX_FILE',site),patch.object(deploy,'LEGACY_NGINX_STATIC_FILE',snippet):
                before,after=deploy.plan_nginx_static_update(None)
                self.assertEqual(before,original)
                self.assertIn('ssl_certificate /etc/letsencrypt/live/test.pem;',after)
                self.assertIn('location ^~ /static/',after)
                self.assertNotIn(include,after)
                self.assertTrue(snippet.exists())
                snippet.write_text('location /personalizado { return 200; }\n')
                with self.assertRaisesRegex(ValueError,'modificado'):
                    deploy.plan_nginx_static_update(None)

    def test_upgrade_of_old_static_block_and_reject_custom_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'site'
            old=deploy.PREVIOUS_STATIC_BLOCK
            original=f'server {{\n{old}\n location / {{\n  proxy_pass http://unix:/run/flujo/app.sock;\n }}\n}}\n'
            site.write_text(original)
            with patch.object(deploy,'NGINX_FILE',site):
                before,after=deploy.plan_nginx_static_update(None)
                self.assertEqual(before,original)
                self.assertIn('location ^~ /static/',after)
                self.assertIn('alias ',after)
                site.write_text(after)
                self.assertIsNone(deploy.plan_nginx_static_update(None))
                site.write_text(original.replace(old,deploy.PREVIOUS_STATIC_PREFIX_BLOCK))
                self.assertIn('location ^~ /static/',deploy.plan_nginx_static_update(None)[1])
                site.write_text(original.replace('access_log off;', 'access_log /custom.log;'))
                with self.assertRaisesRegex(ValueError,'modificado'):
                    deploy.plan_nginx_static_update(None)


if __name__ == '__main__':
    unittest.main()
