import tempfile
import unittest
import json
import os
import subprocess
import sys
from pathlib import Path

from settings import ROOT, load_settings


class SettingsTest(unittest.TestCase):
    def test_application_uses_same_ini_settings_as_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini';database=Path(tmp)/'tasks.db'
            config.write_text(f'[app]\ndatabase={database}\ntimezone=UTC\nport=8100\n')
            env=dict(os.environ, APP_CONFIG=str(config), FLUJO_CONFIG_ONLY='1', PORT='9999', TASKS_DB='/tmp/ignorada.db')
            result=subprocess.run([sys.executable,'-c','import app,json; print(json.dumps([app.DB,app.PORT,str(app.STATS_ZONE)]))'],
                                  cwd=ROOT,env=env,text=True,capture_output=True,check=True)
            self.assertEqual(json.loads(result.stdout),[str(database.resolve()),8100,'UTC'])

    def test_ini_values_and_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini'
            config.write_text('[app]\ndatabase=data/tasks.db\ntimezone=UTC\nport=8100\n')
            actual=load_settings(config, environ={})
            self.assertEqual(actual['database'],str((ROOT/'data/tasks.db').resolve()))
            self.assertEqual((actual['timezone'],actual['port']),('UTC',8100))
            overridden=load_settings(config, environ={'TASKS_DB':'/tmp/otra.db','TASKS_TIMEZONE':'UTC','PORT':'8200'})
            self.assertEqual((overridden['database'],overridden['port']),(str(Path('/tmp/otra.db').resolve()),8200))
            config_only=load_settings(config, environ={'FLUJO_CONFIG_ONLY':'1','PORT':'8200','TASKS_DB':'/tmp/otra.db'})
            self.assertEqual(config_only['port'],8100)
            self.assertEqual(config_only['database'],actual['database'])

    def test_invalid_or_missing_explicit_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini'
            with self.assertRaisesRegex(ValueError,'No existe'):
                load_settings(config,environ={})
            for contents in ('[app]\nport=99999\n','[app]\ntimezone=Invalid/Zone\n','[app]\ndatabase=\n','[other]\nx=1\n'):
                config.write_text(contents)
                with self.subTest(contents=contents), self.assertRaises(ValueError):
                    load_settings(config,environ={})


if __name__ == '__main__':
    unittest.main()
