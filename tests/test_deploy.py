import importlib.util
import errno
import os
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'deploy.py'
spec = importlib.util.spec_from_file_location('flujo_deploy', DEPLOY_SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentConfigTest(unittest.TestCase):
    def test_generated_templates_fill_all_values_and_preserve_nginx_variables(self):
        values = {'APP_DIR': '/srv/flujo', 'APP_USER': 'ubuntu', 'SOCKET_PATH': '/run/flujo/flujo.sock',
                  'CONFIG_PATH': '/srv/flujo/config.ini', 'NGINX_GROUP': 'www-data',
                  'SERVER_NAME': 'tareas.ejemplo.com'}
        supervisor = deploy.render('supervisor.conf.template', values)
        nginx = deploy.render('nginx.conf.template', values)
        self.assertIn('command=/srv/flujo/.venv/bin/gunicorn --config /srv/flujo/deploy/gunicorn.conf.py app:app', supervisor)
        self.assertIn('directory=/srv/flujo', supervisor)
        self.assertIn('user=ubuntu', supervisor)
        self.assertIn('APP_CONFIG="/srv/flujo/config.ini"', supervisor)
        self.assertIn('FLUJO_CONFIG_ONLY="1"', supervisor)
        self.assertNotIn('TASKS_DB=', supervisor)
        self.assertIn('server_name tareas.ejemplo.com;', nginx)
        self.assertIn('proxy_pass http://unix:/run/flujo/flujo.sock;', nginx)
        self.assertIn('proxy_set_header Host $host;', nginx)
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
                with self.assertRaisesRegex(ValueError, 'otra configuración'):
                    deploy.check_program_identity()
            config.write_text('[program:flujo-app]\ndirectory=/otra/app\ncommand=/otra/app/bin/python\n')
            with patch.object(deploy, 'SUPERVISOR_FILE', config):
                with self.assertRaisesRegex(ValueError, 'otra configuración o ruta'):
                    deploy.check_program_identity()

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
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy, 'APP_DIR', root), patch.object(deploy.os, 'geteuid', return_value=0), \
                 patch.object(deploy.shutil, 'which', return_value='/usr/bin/fake'), \
                 patch.object(deploy, 'check_program_identity', return_value=123), \
                 patch.object(deploy, 'installed_supervisor_settings', return_value=user), \
                 patch.object(deploy, 'check_socket') as socket_check, \
                 patch.object(deploy, 'plan_nginx_socket_update', return_value=None) as nginx_check, \
                 patch.object(deploy, 'installed_socket_group', return_value=deploy.grp.getgrgid(os.getgid()).gr_name), \
                 patch.object(deploy, 'ensure_socket_directory') as socket_dir, \
                 patch.object(deploy, 'prepare_venv') as prepare, patch.object(deploy, 'run') as run:
                self.assertEqual(deploy.main(['update']),0)
            socket_check.assert_called_once_with(Path('/run/flujo/flujo.sock'),user,123)
            nginx_check.assert_called_once_with(Path('/run/flujo/flujo.sock'))
            self.assertFalse(socket_dir.call_args.kwargs['install'])
            self.assertFalse(prepare.call_args.kwargs['create'])
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['supervisorctl','restart','flujo-app'], ['supervisorctl','status','flujo-app']])

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
            self.assertIn(f'APP_CONFIG="{root}/config.ini"',supervisor)
            self.assertIn('d /run/otra-app 0750 ',(root/'generated/tmpfiles.conf').read_text())

    def test_nginx_socket_update_preserves_https_and_rejects_multiple_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'flujo-app'
            original='server {\n ssl_certificate /etc/letsencrypt/cert.pem;\n proxy_pass http://unix:/run/flujo/old.sock:;\n}\n'
            site.write_text(original)
            with patch.object(deploy,'NGINX_FILE',site):
                before,after=deploy.plan_nginx_socket_update('/run/custom/new.sock')
                self.assertEqual(before,original)
                self.assertIn('ssl_certificate /etc/letsencrypt/cert.pem;',after)
                self.assertIn('proxy_pass http://unix:/run/custom/new.sock;',after)
                site.write_text(original + 'proxy_pass http://127.0.0.1:9000;\n')
                with self.assertRaisesRegex(ValueError,'varios destinos'):
                    deploy.plan_nginx_socket_update('/run/custom/new.sock')

    def test_shared_socket_directory_permissions_are_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'shared'; directory.mkdir(mode=0o755)
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
            base=Path(tmp); config=base/'tmpfiles.conf'; directory=base/'shared'; directory.mkdir()
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            group=deploy.grp.getgrgid(os.getgid()).gr_name
            original=f'd /run/flujo 0750 {user} {group} -\n'; config.write_text(original)
            with patch.object(deploy,'TMPFILES_FILE',config), patch.object(deploy,'as_user',side_effect=subprocess.CalledProcessError(1,'test')):
                with self.assertRaisesRegex(ValueError,'permisos'):
                    deploy.ensure_socket_directory(user,group,directory/'custom.sock',install=False)
            self.assertEqual(config.read_text(),original)

    def test_update_changes_nginx_and_reloads_when_socket_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); site=root/'nginx.conf'
            (root/'config.ini').write_text('[app]\ndatabase=tasks.db\ntimezone=UTC\nsocket=/run/custom/new.sock\n')
            site.write_text('server {\n proxy_pass http://unix:/run/flujo/old.sock;\n}\n')
            user=deploy.pwd.getpwuid(os.getuid()).pw_name
            with patch.object(deploy,'APP_DIR',root), patch.object(deploy,'NGINX_FILE',site), \
                 patch.object(deploy.os,'geteuid',return_value=0), patch.object(deploy.shutil,'which',return_value='/bin/fake'), \
                 patch.object(deploy,'check_program_identity',return_value=123), \
                 patch.object(deploy,'installed_supervisor_settings',return_value=user), \
                 patch.object(deploy,'installed_socket_group',return_value=deploy.grp.getgrgid(os.getgid()).gr_name), \
                 patch.object(deploy,'check_socket'), patch.object(deploy,'ensure_socket_directory'), \
                 patch.object(deploy,'prepare_venv'), patch.object(deploy,'run') as run:
                self.assertEqual(deploy.main(['update']),0)
            self.assertIn('proxy_pass http://unix:/run/custom/new.sock;',site.read_text())
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['nginx','-t'], ['supervisorctl','restart','flujo-app'],
                ['supervisorctl','status','flujo-app'], ['systemctl','reload','nginx']])


if __name__ == '__main__':
    unittest.main()
