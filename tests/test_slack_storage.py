import os
import tempfile
import unittest
from contextlib import closing

import app


class SlackStorageTest(unittest.TestCase):
    def test_migration_preserves_data_and_removes_links_with_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = app.DB
            app.DB = os.path.join(directory, 'tasks.db')
            try:
                app.init()
                with closing(app.db()) as c, c:
                    c.execute("INSERT INTO users(id,name,username,password,salt) VALUES(1,'Ana','ana','','')")
                    c.execute("INSERT INTO slack_installations VALUES(1,'T123','Equipo','secret-token')")
                    c.execute("INSERT INTO slack_accounts VALUES(1,1,'U123')")
                    c.execute('INSERT INTO slack_preferences(installation_id,user_id,project_id,comment) VALUES(1,1,NULL,1)')
                app.init()
                app.init()
                with closing(app.db()) as c, c:
                    self.assertEqual(c.execute('SELECT comment FROM slack_preferences').fetchone()[0], 1)
                    c.execute('DELETE FROM slack_installations WHERE id=1')
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_accounts').fetchone()[0], 0)
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_preferences').fetchone()[0], 0)
            finally:
                app.DB = old_db


if __name__ == '__main__':
    unittest.main()
