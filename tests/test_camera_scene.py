"""Normal-scene script tests with inert capture/mount/decoder fixtures only."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / 'scripts/test-ciri-camera-colour-bars.sh'
WRAPPER = ROOT / 'scripts/test-ciri-camera-p1-dump-ram.sh'


class CameraSceneTests(unittest.TestCase):
    def test_capture_modes_use_explicit_pattern_and_bounded_standard_cam(self):
        for mode, name, pattern, count, timeout in (
                ([], 'colour-bars', 2, 64, '20s'),
                (['--scene'], 'scene', 0, 180, '30s')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='ciri-scene-script-test.') as directory:
                work = Path(directory)
                launcher = work / 'launcher'
                launcher.write_text('#!/bin/bash\nexit 99\n')  # Must never execute.
                launcher.chmod(0o755)
                stub = work / 'timeout'
                stub.write_text('#!/bin/bash\n'
                                'printf "%s\\n" "$@" > "$CAPTURE_ARGS"\n'
                                'printf "%s\\n" "${CIRI_P1_DUMP_CONFIG-UNSET}" > "$CAPTURE_ENV"\n')
                stub.chmod(0o755)
                # Redirect only native/root guards and the installed launcher
                # path. Run the real mode selection, YAML and cam arguments.
                body = CAPTURE.read_text().replace('$EUID == 0', '1 == 1').replace(
                    'launcher=/opt/ciri-libcamera/run-libcamera',
                    'launcher=' + shlex.quote(str(launcher)))
                script = ('uname() { printf "aarch64\\n"; }\n'
                          'mktemp() { printf "%s\\n" "$CAPTURE_WORK"; }\n' + body)
                result = subprocess.run(['bash', '-c', script, 'capture-fixture', *mode],
                                        env={**os.environ, 'PATH': f'{work}:' + os.environ['PATH'],
                                             'CAPTURE_WORK': str(work),
                                             'CAPTURE_ARGS': str(work / 'args'),
                                             'CAPTURE_ENV': str(work / 'env'),
                                             'CIRI_P1_DUMP_CONFIG': '/inherited/dump.cfg'},
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((work / f'{name}.yaml').read_text(),
                                 'properties:\n  - loop: 1\nframes:\n  - 0:\n'
                                 f'      TestPatternMode: {pattern}\n')
                self.assertEqual((work / 'args').read_text().splitlines(), [
                    '--signal=INT', '--kill-after=5s', timeout, str(launcher),
                    '--camera', '/base/soc/i2c@11ec1000/camera-main-sensor@31',
                    '--stream', 'role=viewfinder,width=640,height=480,pixelformat=NV12',
                    '--strict-formats', f'--script={work}/{name}.yaml',
                    f'--capture={count}', '--metadata', f'--file={name}-#.nv12'])
                self.assertEqual((work / 'env').read_text().strip(),
                                 'UNSET' if mode else '/inherited/dump.cfg')

    def test_scene_namespace_keeps_alias_binds_and_disables_odt(self):
        body = WRAPPER.read_text().split("<<'NAMESPACE'\n", 1)[1].split('\nNAMESPACE', 1)[0]
        with tempfile.TemporaryDirectory(prefix='ciri-scene-mount-test.') as directory:
            work = Path(directory)
            (work / 'test-colour-bars.sh').write_text(
                '#!/bin/bash\nset -eu\n'
                '[[ $# == 1 && $1 == --scene && ! -v CIRI_P1_DUMP_CONFIG ]]\n')
            script = 'mount() { printf "%s\\n" "$*" >> "$MOUNT_LOG"; }\n' + body
            core_dir = work / 'installed-core'
            result = subprocess.run(
                ['bash', '-c', script, 'namespace-fixture', str(work),
                 str(work / 'installed-cam'), str(core_dir),
                 str(work / 'installed-launcher'), str(work / 'installed-captures'), '--scene'],
                env={**os.environ, 'MOUNT_LOG': str(work / 'mount.log'),
                     'CIRI_P1_DUMP_CONFIG': '/inherited/dump.cfg'},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (work / 'mount.log').read_text().splitlines()
            for name in ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0'):
                self.assertIn(f'--bind {work}/libcamera.so.0.1.0 {core_dir}/{name}', calls)
                self.assertIn(f'-o remount,bind,ro {core_dir}/{name}', calls)
            self.assertIn(f'--bind {work}/captures {work}/installed-captures', calls)
            for name in ('cam', 'launcher'):
                self.assertIn(f'-o remount,bind,ro {work}/installed-{name}', calls)

    def test_scene_decodes_late_frame_without_requiring_pipeline_dumps(self):
        body = WRAPPER.read_text().split('\nNAMESPACE\n', 1)[1]
        with tempfile.TemporaryDirectory(prefix='ciri-scene-decode-test.') as directory:
            work = Path(directory)
            run = work / 'captures/run.fixture'
            run.mkdir(parents=True)
            # A size fixture, not claimed to contain real scene pixels.
            (run / 'scene-cam0-stream0-000179.nv12').write_bytes(bytes(460800))
            stub = work / 'ffmpeg'
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$DECODE_ARGS"\n')
            stub.chmod(0o755)
            script = ('set -euo pipefail\nwork=$1\nmode=--scene\n'
                      'capture_name=scene\nlast_frame=000179\n' + body)
            result = subprocess.run(['bash', '-c', script, 'decode-fixture', str(work)],
                                    env={**os.environ, 'PATH': f'{work}:' + os.environ['PATH'],
                                         'DECODE_ARGS': str(work / 'args')},
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = (work / 'args').read_text().splitlines()
            self.assertIn('-n', args)
            self.assertEqual(args[args.index('-i') + 1], str(run / 'scene-cam0-stream0-000179.nv12'))
            self.assertEqual(args[-1], str(work / 'scene.png'))
            self.assertIn('Colour remains unverified', result.stdout)
            self.assertNotIn('Pipeline dump files exist', result.stdout)

    def test_scene_missing_final_frame_does_not_decode(self):
        body = WRAPPER.read_text().split('\nNAMESPACE\n', 1)[1]
        with tempfile.TemporaryDirectory(prefix='ciri-scene-missing-test.') as directory:
            script = ('set -euo pipefail\nwork=$1\nmode=--scene\n'
                      'capture_name=scene\nlast_frame=000179\n' + body)
            result = subprocess.run(['bash', '-c', script, 'decode-fixture', directory],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('Expected final frame missing; no image decoded.', result.stderr)

    def test_shell_syntax_and_scene_argument_guards(self):
        for script in (CAPTURE, WRAPPER):
            result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for script, args in ((CAPTURE, ['--invalid']), (CAPTURE, ['--scene', 'extra']),
                             (WRAPPER, ['/kit', '--invalid']),
                             (WRAPPER, ['/kit', '--scene', 'extra'])):
            with self.subTest(script=script.name, args=args):
                result = subprocess.run(['bash', str(script), *args],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
