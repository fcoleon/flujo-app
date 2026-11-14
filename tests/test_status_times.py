import sqlite3
import unittest
from datetime import datetime, timezone

from analytics import status_times


class StatusTimesTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
            CREATE TABLE task_types(id INTEGER, name TEXT, project_id INTEGER);
            CREATE TABLE task_statuses(id INTEGER, name TEXT, type_id INTEGER, is_done INTEGER);
            CREATE TABLE tasks(id INTEGER, type_id INTEGER);
            CREATE TABLE placements(task_id INTEGER, project_id INTEGER);
            CREATE TABLE history(id INTEGER PRIMARY KEY, task_id INTEGER, created_at TEXT,
                                 status_id INTEGER, status_name TEXT, type_id INTEGER, type_name TEXT,
                                 status_is_done INTEGER, message TEXT);
            INSERT INTO task_types VALUES(1,'Trabajo',1);
            INSERT INTO task_statuses VALUES(1,'Activo',1,0),(2,'Pausado',1,0),(3,'Finalizado',1,1);
            INSERT INTO tasks VALUES(1,1),(2,1);
            INSERT INTO placements VALUES(1,1),(2,2);
            INSERT INTO history VALUES
                (1,1,'2024-03-28T00:00:00+00:00',2,'Pausado',1,'Trabajo',0,'inicio del registro de tiempo por estado'),
                (2,1,'2024-04-03T00:00:00+00:00',1,'Activo',1,'Trabajo',0,'cambio'),
                (3,1,'2024-04-05T00:00:00+00:00',2,'Pausado',1,'Trabajo',0,'cambio'),
                (4,1,'2024-05-03T00:00:00+00:00',1,'Activo',1,'Trabajo',0,'cambio'),
                (5,2,'2024-04-01T00:00:00+00:00',2,'Pausado',1,'Trabajo',0,'cambio');
        ''')

    def test_clips_and_sums_repeated_states_for_current_project(self):
        start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        end = datetime(2024, 5, 1, tzinfo=timezone.utc)
        result = {row['id']: row['seconds'] for row in status_times(self.c, 1, start, end, None)}
        self.assertEqual(result, {1: 2 * 86400, 2: 28 * 86400})

    def test_deleted_status_remains_available(self):
        self.c.execute('DELETE FROM task_statuses WHERE id=2')
        start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        end = datetime(2024, 5, 1, tzinfo=timezone.utc)
        result = {row['id']: row for row in status_times(self.c, 1, start, end, 1)}
        self.assertEqual(result[2]['name'], 'Pausado')
        self.assertEqual(result[2]['seconds'], 28 * 86400)

    def test_completed_status_is_excluded_but_ends_previous_interval(self):
        self.c.execute("INSERT INTO history VALUES(6,1,'2024-04-06T00:00:00+00:00',3,'Finalizado',1,'Trabajo',1,'cambio')")
        start = datetime(2024, 4, 1, tzinfo=timezone.utc)
        end = datetime(2024, 5, 1, tzinfo=timezone.utc)
        result = {row['id']: row['seconds'] for row in status_times(self.c, 1, start, end, None)}
        self.assertEqual(result, {1: 2 * 86400, 2: 3 * 86400})

    def test_previous_status_time_sums_closed_visits_and_partial_history(self):
        from analytics import previous_status_time
        self.assertEqual(previous_status_time(self.c, 1, 2), (34 * 86400, True))
        self.assertEqual(previous_status_time(self.c, 1, 1), (2 * 86400, True))


if __name__ == '__main__':
    unittest.main()
