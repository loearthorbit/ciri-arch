"""Config-only P2 staging tests; no vendor runtime, compilation or real mounts."""
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
NAMES = ('libcamera.so.0.1.0', 'build-info.txt', 'run-libcamera',
         'test-p1-dump-ram.sh', 'test-colour-bars.sh', 'dump.cfg')


class CameraP2DumpTests(unittest.TestCase):
    def test_config_labels_all_selected_images_without_import(self):
        rows = list(csv.reader((ROOT / 'config/libcamera/p2-dump.cfg').read_text().splitlines()))
        expected = {('P1', name) for name in ('YUVO_R1', 'YUVO_R2')}
        expected |= {('TR_Y2Y_F1', name) for name in ('YUVO_T2', 'YUVO_T3', 'YUVO_T4')}
        expected |= {('TR_Y2Y_F4', name) for name in ('YUVO_T2', 'YUVO_T3')}
        expected |= {(stage, 'IMG3O') for stage in
                     ('P2_MS_F_SMALL', 'P2_MS_F4', 'P2_MS_F3', 'P2_MS_F2', 'P2_MS_F1')}
        expected |= {('P2_MS_F1', name) for name in ('IMGI_D1', 'IMG4O')}
        expected |= {('WPE_P2_PQDIP_MS_F0', name) for name in
                     ('IMGI_D1', 'RECI_D1', 'IMG3O', 'IMG4O', 'WDMAO')}
        self.assertEqual(len(rows), 19)
        self.assertEqual({(row[1], row[4]) for row in rows}, expected)
        self.assertEqual(len({row[2] for row in rows}), len(rows))
        for row in rows:
            self.assertEqual(len(row), 7)
            self.assertEqual((row[0], row[3], row[5], row[6]), ('Preview', 'Streaming', '1', '0'))
            self.assertEqual(row[2], f'[Prefix]-{row[1]}-{row[4]}[Size][Bit][Padding].[Format]')
            self.assertEqual(set(re.findall(r'\[([^]]+)\]', row[2])),
                             {'Prefix', 'Size', 'Bit', 'Padding', 'Format'})

    def test_staging_reuses_core_and_keeps_parent_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='ciri-p2-stage-test.') as directory:
            root = Path(directory)
            repo, kit = root / 'repo', root / 'parent-kit'
            (repo / 'scripts').mkdir(parents=True)
            (repo / 'config/libcamera').mkdir(parents=True)
            (repo / 'build/camera/updates').mkdir(parents=True)
            kit.mkdir()
            for name in ('stage-ciri-camera-p2-dump.sh', 'run-ciri-libcamera.sh',
                         'test-ciri-camera-p1-dump-ram.sh', 'test-ciri-camera-colour-bars.sh'):
                shutil.copyfile(ROOT / 'scripts' / name, repo / 'scripts' / name)
            shutil.copyfile(ROOT / 'config/libcamera/p2-dump.cfg', repo / 'config/libcamera/p2-dump.cfg')
            # An inert byte fixture, never loaded or executed.
            core = b'not an ELF; staging only copies and hashes this fixture\n'
            for name in NAMES:
                (kit / name).write_bytes(core if name == NAMES[0] else b'parent fixture\n')
            core_hash = hashlib.sha256(core).hexdigest()
            (kit / 'build-info.txt').write_text(json.dumps({'core_sha256': core_hash}))
            manifest = ''.join(hashlib.sha256((kit / name).read_bytes()).hexdigest() +
                               '  ' + name + '\n' for name in NAMES)
            (kit / 'SHA256SUMS').write_text(manifest)
            before = {path.name: path.read_bytes() for path in kit.iterdir()}
            result = subprocess.run(['bash', str(repo / 'scripts/stage-ciri-camera-p2-dump.sh'), str(kit)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged, = (repo / 'build/camera/updates').glob('p2-dump.*')
            self.assertEqual((staged / NAMES[0]).read_bytes(), core)
            self.assertEqual((staged / 'dump.cfg').read_bytes(), (ROOT / 'config/libcamera/p2-dump.cfg').read_bytes())
            info = json.loads((staged / 'build-info.txt').read_text())
            self.assertTrue(info['config_only_restage'])
            self.assertFalse(info['hardware_tested'])
            self.assertEqual(info['core_sha256'], core_hash)
            check = subprocess.run(['sha256sum', '--check', '--strict', 'SHA256SUMS'],
                                   cwd=staged, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual({path.name: path.read_bytes() for path in kit.iterdir()}, before)

    def test_staging_syntax_and_argument_guard(self):
        script = ROOT / 'scripts/stage-ciri-camera-p2-dump.sh'
        result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for args in ([], ['relative-kit'], ['/kit', 'extra']):
            result = subprocess.run(['bash', str(script), *args], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
