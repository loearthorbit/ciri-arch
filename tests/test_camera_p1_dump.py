"""Diagnostic export/build guards; never execute the recovered camera runtime."""
import ast
import csv
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class CameraP1DumpTests(unittest.TestCase):
    def test_saved_patch_changes_only_dump_runtime_compatibility(self):
        patch = (ROOT / 'config/libcamera/ciri.patch').read_text()
        odt = patch.split('diff --git a/src/libcamera/pipeline/mtkisp7/odt/on_device_tuner.cpp', 1)[1].split('diff --git ', 1)[0]
        added = '\n'.join(line[1:] for line in odt.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        self.assertIn('#include <system_error>', added)
        self.assertIn('kWorkDir = "/captures/p1-dump";', added)
        self.assertIn('std::filesystem::create_directories(newPath, error);', added)
        self.assertIn('if (error)', added)
        self.assertIn('return -error.value();', added)
        self.assertNotIn('system(', added)
        self.assertNotIn('sensor', added.lower())

    def test_config_exports_only_three_p1_modules_without_import(self):
        rows = list(csv.reader((ROOT / 'config/libcamera/p1-dump.cfg').read_text().splitlines()))
        self.assertEqual(len(rows), 3)
        self.assertEqual({row[4] for row in rows}, {'IMGO', 'YUVO_R1', 'YUVO_R2'})
        for row in rows:
            self.assertEqual(len(row), 7)
            self.assertEqual(row[:2], ['Preview', 'P1'])
            self.assertEqual(row[3], 'Streaming')
            self.assertEqual(row[5:], ['1', '0'])
            keys = set(re.findall(r'\[([^]]+)\]', row[2]))
            self.assertEqual(keys, {'Prefix', 'Port', 'Size', 'Bit', 'BayerOrder', 'Padding', 'Format'})
            self.assertNotIn('/', row[2])

    def test_incremental_builder_scope_and_static_checks(self):
        builder = (ROOT / 'scripts/build-ciri-libcamera-dump-core.sh').read_text()
        python_body = builder.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        ast.parse(python_body)  # Parse, never execute the builder or external source.
        commands = [line for line in builder.splitlines() if line.startswith('ninja ')]
        self.assertEqual(commands, ['ninja -C "$objects" "${compiled_targets[@]}"'])
        self.assertIn('compiled_targets=(src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_odt_on_device_tuner.cpp.o', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/info_frame.cpp.o', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_imgsys_mcnr.cpp.o', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_odt_imagiq_adapter_imagiq_adapter.cpp.o)', builder)
        self.assertIn('args[args.index(\'-o\') + 1] = str(out / name)', builder)
        self.assertIn("new['soname'] != 'libcamera.so.0.1'", builder)
        self.assertIn("new['needed'] != old['needed']", builder)
        self.assertIn("old['exports'] - new['exports']", builder)
        self.assertIn("item['imports'] - providers", builder)
        self.assertIn("if item['soname'] != old['soname']", builder)
        self.assertIn("arg.startswith('-Wl,-rpath,')", builder)
        self.assertNotIn('shutil.copytree', builder)
        self.assertNotIn('install-cam-update.sh', builder)
        cam = (ROOT / 'scripts/build-ciri-libcamera-cam.sh').read_text()
        self.assertNotIn('on_device_tuner.cpp.o', cam)

    def test_launcher_sets_export_flags_only_inside_opt_in(self):
        launcher = (ROOT / 'scripts/run-ciri-libcamera.sh').read_text()
        opt_in = launcher.split('    if [[ -n $CIRI_P1_DUMP_CONFIG ]]; then', 1)[1].split('    fi', 1)[0]
        self.assertIn('"$rootfs/captures/p1-dump/dump.cfg"', opt_in)
        self.assertIn(': > "$rootfs/run/camera/enable_tuning"', opt_in)
        self.assertIn('printf "2\\n" > "$rootfs/run/camera/export_dump"', opt_in)
        self.assertNotIn('import_dump', launcher)
        self.assertNotIn('camsys_debug_frame', launcher)

    def test_ram_wrapper_uses_existing_capture_and_private_binds(self):
        wrapper = (ROOT / 'scripts/test-ciri-camera-p1-dump-ram.sh').read_text()
        self.assertIn('mktemp -d /dev/shm/ciri-p1-dump.XXXXXXXX', wrapper)
        self.assertIn('sha256sum --check --strict SHA256SUMS', wrapper)
        self.assertIn('unshare --mount --propagation private', wrapper)
        self.assertIn('mount --bind "$work/captures" "$5"', wrapper)
        self.assertIn('exec /bin/bash "$work/test-colour-bars.sh"', wrapper)
        self.assertIn('trap retain_dumps EXIT', wrapper)
        self.assertIn('(( nonempty > 0 ))', wrapper)
        self.assertNotIn('sync ', wrapper)
        self.assertNotIn('rm ', wrapper)

    def test_namespace_binds_all_materialized_core_aliases(self):
        wrapper = (ROOT / 'scripts/test-ciri-camera-p1-dump-ram.sh').read_text()
        body = wrapper.split("<<'NAMESPACE'\n", 1)[1].split('\nNAMESPACE', 1)[0]
        # Exercise the real namespace block with a recording mount function and
        # a trivial authored capture fixture. No mount or camera runtime executes.
        with tempfile.TemporaryDirectory(prefix='ciri-p1-mount-test.') as directory:
            work = Path(directory)
            log = work / 'mount.log'
            (work / 'test-colour-bars.sh').write_text(
                '#!/bin/bash\n[[ $CIRI_P1_DUMP_CONFIG == "$EXPECTED_CONFIG" ]]\n')
            script = 'mount() { printf "%s\\n" "$*" >> "$MOUNT_LOG"; }\n' + body
            core_dir = str(work / 'installed-core')
            result = subprocess.run(
                ['bash', '-c', script, 'namespace-fixture', str(work),
                 str(work / 'installed-cam'), core_dir,
                 str(work / 'installed-launcher'), str(work / 'installed-captures')],
                env={**os.environ, 'MOUNT_LOG': str(log),
                     'EXPECTED_CONFIG': str(work / 'dump.cfg')},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text().splitlines()
            for name in ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0'):
                self.assertIn(f'--bind {work}/libcamera.so.0.1.0 {core_dir}/{name}', calls)
                self.assertIn(f'-o remount,bind,ro {core_dir}/{name}', calls)

    def test_shell_syntax_and_argument_guards(self):
        for name in ('build-ciri-libcamera-dump-core.sh',
                     'test-ciri-camera-p1-dump-ram.sh', 'run-ciri-libcamera.sh'):
            result = subprocess.run(['bash', '-n', str(ROOT / 'scripts' / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        cases = (
            ('build-ciri-libcamera-dump-core.sh', ['--unknown']),
            ('test-ciri-camera-p1-dump-ram.sh', []),
            ('test-ciri-camera-p1-dump-ram.sh', ['relative-kit']),
            ('test-ciri-camera-p1-dump-ram.sh', ['/kit', 'extra']),
        )
        for name, args in cases:
            with self.subTest(name=name, args=args):
                result = subprocess.run(['bash', str(ROOT / 'scripts' / name), *args],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
