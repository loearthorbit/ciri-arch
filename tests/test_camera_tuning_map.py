"""Exercise the alias SQL with synthetic data; never mount or run camera code."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/test-ciri-camera-tuning-map.sh'


class CameraTuningMapTests(unittest.TestCase):
    def run_alias(self, source, destination):
        program = SCRIPT.read_text().split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        return subprocess.run(
            [sys.executable, '-I', '-c', program, str(source), str(destination)],
            capture_output=True, text=True, timeout=10,
        )

    def fixture(self, source, extra=(), module_id=2211):
        with closing(sqlite3.connect(source)) as con, con:
            con.execute('CREATE TABLE db_SensorDBMap (SensorName TEXT, ModuleID INTEGER, DBName TEXT, ChipID TEXT)')
            con.executemany('INSERT INTO db_SensorDBMap VALUES (?, ?, ?, ?)', [
                ('default_sensor', 0, 'default_sensor', 'MT8188'),
                ('gc08a3_mipi_raw', module_id, 'gc08a3_mipi_raw', 'MT8188'),
                ('gc05a2_mipi_raw', 1442, 'gc05a2_mipi_raw', 'MT8188'),
                *extra,
            ])
            con.execute('CREATE TABLE unrelated (payload BLOB)')
            con.execute('INSERT INTO unrelated VALUES (?)', (b'unchanged tuning fixture',))

    def test_alias_preserves_original_and_other_tuning_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / 'original.db', Path(directory) / 'alias.db'
            self.fixture(source)
            before = hashlib.sha256(source.read_bytes()).digest()
            result = self.run_alias(source, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            with closing(sqlite3.connect(source)) as con, con:
                original = con.execute('SELECT * FROM db_SensorDBMap ORDER BY SensorName, ModuleID').fetchall()
            with closing(sqlite3.connect(destination)) as con:
                rows = con.execute('SELECT * FROM db_SensorDBMap ORDER BY SensorName, ModuleID').fetchall()
                self.assertEqual(con.execute('SELECT payload FROM unrelated').fetchone()[0], b'unchanged tuning fixture')
            expected = sorted([*original, ('gc08a3_mipi_raw_0', 0, 'gc08a3_mipi_raw', 'MT8188')])
            self.assertEqual(rows, expected)
            with closing(sqlite3.connect(destination)) as con:
                # This is the SELECT reconstructed from the recovered vendor library.
                selected = con.execute(
                    'SELECT SensorName, DBName FROM db_SensorDBMap WHERE SensorName=? OR SensorName=? OR SensorName=?',
                    ('gc08a3_mipi_raw', 'default_sensor', 'gc08a3_mipi_raw_0'),
                ).fetchall()
            self.assertIn(('gc08a3_mipi_raw_0', 'gc08a3_mipi_raw'), selected)

    def test_existing_alias_and_missing_expected_entry_are_refused(self):
        cases = [
            {'extra': [('gc08a3_mipi_raw_0', 0, 'other_tuning', 'MT8188')]},
            {'module_id': 999},
            {'extra': [('gc08a3_mipi_raw', 2211, 'gc08a3_mipi_raw', 'MT8188')]},
        ]
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                source, destination = Path(directory) / 'original.db', Path(directory) / 'alias.db'
                self.fixture(source, **options)
                before = source.read_bytes()
                result = self.run_alias(source, destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(destination.exists())
                self.assertEqual(source.read_bytes(), before)

    def test_numeric_module_alias_is_not_the_vendor_lookup_key(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / 'original.db', Path(directory) / 'alias.db'
            self.fixture(source, extra=[('gc08a3_mipi_raw', 0, 'gc08a3_mipi_raw', 'MT8188')])
            with closing(sqlite3.connect(source)) as con:
                self.assertIsNone(con.execute(
                    'SELECT DBName FROM db_SensorDBMap WHERE SensorName=?',
                    ('gc08a3_mipi_raw_0',),
                ).fetchone())
            result = self.run_alias(source, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(destination)) as con:
                self.assertEqual(con.execute(
                    'SELECT DBName FROM db_SensorDBMap WHERE SensorName=?',
                    ('gc08a3_mipi_raw_0',),
                ).fetchall(), [('gc08a3_mipi_raw',)])

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / 'original.db', Path(directory) / 'alias.db'
            self.fixture(source)
            destination.write_bytes(b'retained test output')
            result = self.run_alias(source, destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(destination.read_bytes(), b'retained test output')

    def test_mount_is_private_readonly_and_checksum_checked_afterward(self):
        source = SCRIPT.read_text()
        self.assertIn('unshare --mount --propagation private', source)
        self.assertIn('mount -o remount,bind,ro "$db"', source)
        self.assertIn('--capture=64 --metadata --file=', source)
        self.assertNotIn('io-mode=rw', source)
        self.assertNotIn('rm ', source)
        self.assertLess(source.index('before=$(sha256sum'), source.index('unshare --mount'))
        self.assertGreater(source.index('[[ $(sha256sum'), source.index('\nNAMESPACE\n'))


if __name__ == '__main__':
    unittest.main()
