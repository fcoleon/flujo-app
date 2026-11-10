import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=2)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


class GunicornSocketTest(unittest.TestCase):
    def test_unix_socket_serves_flask_and_rejects_another_master(self):
        with tempfile.TemporaryDirectory(prefix='flujo-test-', dir='/tmp') as temp:
            root = Path(temp); path = root/'app.sock'; config = root/'config.ini'; db = root/'tasks.db'
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.bind(str(root/'probe.sock'))
            except PermissionError:
                self.skipTest('El entorno impide crear sockets Unix')
            config.write_text(f'[app]\ndatabase={db}\ntimezone=UTC\nport=8000\nsocket={path}\n')
            env = dict(os.environ, APP_CONFIG=str(config), FLUJO_CONFIG_ONLY='1', FLUJO_BEHIND_PROXY='1')
            command = [sys.executable, '-m', 'gunicorn', '--config', 'deploy/gunicorn.conf.py', 'app:app']
            with tempfile.TemporaryFile(mode='w+') as errors:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=errors)
                try:
                    deadline = time.monotonic()+10
                    while time.monotonic()<deadline:
                        if process.poll() is not None:
                            errors.seek(0); self.fail('Gunicorn no pudo iniciar: '+errors.read())
                        try:
                            conn=UnixHTTPConnection(path);conn.request('GET','/api/session')
                            response=conn.getresponse();body=json.loads(response.read());conn.close()
                            if response.status==200: break
                        except (OSError,ValueError):
                            time.sleep(.05)
                    else: self.fail('Gunicorn no respondió por el socket')
                    self.assertTrue(body['needs_setup']);self.assertTrue(db.exists())
                    conn=UnixHTTPConnection(path)
                    conn.request('POST','/api/setup',body=json.dumps({'name':'Prueba','username':'prueba','password':'clave123'}),
                                 headers={'Content-Type':'application/json','X-Forwarded-Proto':'https'})
                    response=conn.getresponse();response.read();cookie=response.getheader('Set-Cookie');conn.close()
                    self.assertEqual(response.status,200);self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie)
                    competing=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
                    self.assertNotEqual(competing.returncode,0)
                    conn=UnixHTTPConnection(path);conn.request('GET','/api/data',headers={'Cookie':cookie.split(';')[0]})
                    response=conn.getresponse();data=json.loads(response.read());conn.close()
                    self.assertEqual(response.status,200);self.assertEqual(data['user']['username'],'prueba')
                finally:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill();process.wait()


if __name__ == '__main__':
    unittest.main()
