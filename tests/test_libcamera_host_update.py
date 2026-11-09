"""Exercise helper-only update logic with synthetic files, never vendor code."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOST = 'rootfs/usr/bin/ciri-libcamera-host'
OLD_BYTES = b'old helper fixture'
OLD_HASH = hashlib.sha256(OLD_BYTES).hexdigest()
NEW_BYTES = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\xb7\x00' + b'new helper fixture'


def write_manifest(root, names):
    (root / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256((root / name).read_bytes()).hexdigest() + '  ' + name + '\n'
        for name in names))


class HostUpdateTests(unittest.TestCase):
    def fixture(self, work):
        source, prefix = work / 'update', work / 'runtime'
        source.mkdir()
        source_files = {'ciri-libcamera-host': NEW_BYTES, 'build-info.txt': b'mock provenance',
                        'install-host-update.sh': b'mock installer'}
        runtime_files = {HOST: OLD_BYTES, 'run-libcamera': b'launcher',
                         'rootfs/usr/bin/cam': b'cam fixture',
                         'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0': b'core fixture',
                         'elf-report.json': json.dumps({'architecture': 'AArch64',
                                                       'missing_strong_imports': {}}).encode()}
        for root, files in ((source, source_files), (prefix, runtime_files)):
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            write_manifest(root, files)
        return source, prefix

    def cam_fixture(self, work):
        source, prefix = self.fixture(work)
        (source / 'ciri-libcamera-host').rename(source / 'cam')
        (source / 'install-host-update.sh').rename(source / 'install-cam-update.sh')
        write_manifest(source, ['cam', 'build-info.txt', 'install-cam-update.sh'])
        return source, prefix

    def core_fixture(self, work):
        source, prefix = self.fixture(work)
        (source / 'ciri-libcamera-host').rename(source / 'libcamera.so.0.1.0')
        (source / 'install-host-update.sh').rename(source / 'install-core-update.sh')
        info = {'architecture': 'AArch64', 'missing_strong_imports': {},
                'core_sha256': hashlib.sha256(NEW_BYTES).hexdigest(),
                'baseline_core_sha256': hashlib.sha256(b'core fixture').hexdigest(),
                'p2_separate_plane_buffers': True, 'hardware_tested': True}
        (source / 'build-info.txt').write_text(json.dumps(info))
        write_manifest(source, ['libcamera.so.0.1.0', 'build-info.txt', 'install-core-update.sh'])
        for name in ('libcamera.so', 'libcamera.so.0.1'):
            (prefix / 'rootfs/usr/lib/ciri-libcamera' / name).write_bytes(b'core fixture')
        for name in ('stream-camera', 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so',
                     'rootfs/usr/lib64/libcamera/ipa_mtkisp7.so', 'rootfs/etc/camera/tuning.db'):
            path = prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'preserve this streaming/algorithm/tuning fixture')
        write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                if p.is_file() and p.name != 'SHA256SUMS'])
        return source, prefix

    def stream_fixture(self, work):
        source, prefix = self.core_fixture(work)
        (source / 'libcamera.so.0.1.0').rename(source / 'stream-camera')
        (source / 'install-core-update.sh').rename(source / 'install-stream-update.sh')
        (source / 'stream-camera').write_bytes((ROOT / 'scripts/stream-ciri-camera.sh').read_bytes())
        (prefix / 'stream-camera').write_bytes(b'old MMAP stream fixture')
        info = {'stream_launcher_sha256': hashlib.sha256((source / 'stream-camera').read_bytes()).hexdigest(),
                'baseline_stream_launcher_sha256': hashlib.sha256(b'old MMAP stream fixture').hexdigest(),
                'filesink_bridge': True}
        (source / 'build-info.txt').write_text(json.dumps(info))
        write_manifest(source, ['stream-camera', 'build-info.txt', 'install-stream-update.sh'])
        write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                if p.is_file() and p.name != 'SHA256SUMS'])
        return source, prefix

    def apply(self, source, prefix, component='host', sync=None, **patches):
        script = (ROOT / 'scripts/update-ciri-libcamera-host.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        # Replace the pinned shipped-helper hash only in the TEST's code copy.
        pinned = "OLD = '1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45'"
        self.assertEqual(code.count(pinned), 1)
        code = code.replace(pinned, "OLD = '" + OLD_HASH + "'")
        cam_pinned = "CAM_OLD = 'a8e0393e09af9b63eddb3b9dbb3e064bff51e355095fddfa1cc47599452e9661'"
        self.assertEqual(code.count(cam_pinned), 1)
        code = code.replace(cam_pinned, "CAM_OLD = '" + hashlib.sha256(b'cam fixture').hexdigest() + "'")
        code = code.replace("CORE_OLD = 'af1014e2f73934ab79ccc1eddff5806194b83b9fce925e5e443b81443487a335'",
                            "CORE_OLD = '" + hashlib.sha256(b'core fixture').hexdigest() + "'")
        code = code.replace("CORE_VERIFIED = 'db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d'",
                            "CORE_VERIFIED = '" + hashlib.sha256(NEW_BYTES).hexdigest() + "'")
        code = code.replace("STREAM_OLD = '0489e6babd3c7ec7180579be6cbe12dc25e1c41f4f1b23845a205b1f875b4b83'",
                            "STREAM_OLD = '" + hashlib.sha256(b'old MMAP stream fixture').hexdigest() + "'")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['-', str(source), str(prefix), component]))
            stack.enter_context(mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0), side_effect=sync))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(os, name, side_effect=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exec(compile(code, 'helper-update-inline.py', 'exec'), {})

    def test_stream_update_installs_filesink_preserves_core_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.stream_fixture(Path(directory))
            before = {str(p.relative_to(prefix)): p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
            self.apply(source, prefix, component='stream')
            self.apply(source, prefix, component='stream')
            for name, content in before.items():
                if name not in ('stream-camera', 'SHA256SUMS'):
                    self.assertEqual((prefix / name).read_bytes(), content)
            script = (prefix / 'stream-camera').read_text()
            self.assertIn('\n    filesink ', script)
            self.assertIn('buffer-mode=unbuffered', script)
            self.assertNotIn('\n    v4l2sink ', script)
            self.assertEqual((prefix / 'stream-camera').stat().st_mode & 0o777, 0o755)
            self.assertEqual({str(p.relative_to(prefix)) for p in prefix.rglob('*') if p.is_file()}, set(before))

    def test_stream_update_repairs_manifest_and_refuses_unknown_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.stream_fixture(Path(directory))
            real_replace = os.replace
            def replace(a, b):
                if Path(b).name == 'SHA256SUMS':
                    raise OSError('mock streaming manifest failure')
                return real_replace(a, b)
            with self.assertRaisesRegex(SystemExit, 'mock streaming manifest failure'):
                self.apply(source, prefix, component='stream', replace=replace)
            self.assertEqual((prefix / 'stream-camera').read_bytes(), (source / 'stream-camera').read_bytes())
            self.apply(source, prefix, component='stream')
            (prefix / 'stream-camera').write_bytes(b'unknown launcher')
            write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                    if p.is_file() and p.name != 'SHA256SUMS'])
            before = {p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(SystemExit, 'Unexpected installed executable'):
                self.apply(source, prefix, component='stream')
            self.assertEqual({p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}, before)

    def test_stream_stager_copies_only_authored_scripts_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'scripts').mkdir()
            (repo / 'build/camera/updates').mkdir(parents=True)
            for name in ('stage-ciri-stream-launcher-update.sh', 'stream-ciri-camera.sh',
                         'update-ciri-libcamera-host.sh'):
                (repo / 'scripts' / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
            result = subprocess.run(['bash', str(repo / 'scripts/stage-ciri-stream-launcher-update.sh')],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged, = (repo / 'build/camera/updates').glob('stream-update.*')
            self.assertEqual({p.name for p in staged.iterdir()},
                             {'stream-camera', 'build-info.txt', 'install-stream-update.sh', 'SHA256SUMS'})
            self.assertEqual((staged / 'stream-camera').read_bytes(), (ROOT / 'scripts/stream-ciri-camera.sh').read_bytes())
            info = json.loads((staged / 'build-info.txt').read_text())
            self.assertTrue(info['filesink_bridge'])
            self.assertFalse(info['runtime_copied'])
            check = subprocess.run(['sha256sum', '--check', '--strict', 'SHA256SUMS'], cwd=staged,
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_core_update_covers_all_aliases_and_preserves_streaming_and_algorithms(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.core_fixture(Path(directory))
            before = {str(p.relative_to(prefix)): p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
            self.apply(source, prefix, component='core')
            self.apply(source, prefix, component='core')
            aliases = {'rootfs/usr/lib/ciri-libcamera/' + name for name in
                       ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0')}
            for name, content in before.items():
                if name != 'SHA256SUMS':
                    self.assertEqual((prefix / name).read_bytes(), NEW_BYTES if name in aliases else content)
            for name in aliases:
                self.assertFalse((prefix / name).is_symlink())
                self.assertEqual((prefix / name).stat().st_mode & 0o777, 0o644)
            self.assertEqual((prefix / 'core-update-info.json').read_bytes(), (source / 'build-info.txt').read_bytes())
            self.assertFalse(list(prefix.rglob('.ipa-host-*')))
            self.assertEqual({str(p.relative_to(prefix)) for p in prefix.rglob('*') if p.is_file()},
                             set(before) | {'core-update-info.json'})

    def test_core_partial_alias_receipt_or_manifest_failure_is_repairable(self):
        destinations = ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0',
                        'core-update-info.json', 'SHA256SUMS')
        for fail_at in destinations:
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as directory:
                source, prefix = self.core_fixture(Path(directory))
                real_replace = os.replace
                def replace(a, b):
                    if Path(b).name == fail_at:
                        raise OSError('mock core replacement failure')
                    return real_replace(a, b)
                with self.assertRaisesRegex(SystemExit, 'mock core replacement failure'):
                    self.apply(source, prefix, component='core', replace=replace)
                self.assertFalse(list(prefix.rglob('.ipa-host-*')))
                self.apply(source, prefix, component='core')
                for name in destinations[:3]:
                    self.assertEqual((prefix / 'rootfs/usr/lib/ciri-libcamera' / name).read_bytes(), NEW_BYTES)
                self.assertIn(hashlib.sha256(NEW_BYTES).hexdigest(), (prefix / 'SHA256SUMS').read_text())

    def test_core_final_sync_failure_is_reported_and_same_kit_can_be_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.core_fixture(Path(directory))
            calls = []
            def sync(command, **kwargs):
                calls.append(command)
                if len(calls) == 2:
                    raise subprocess.CalledProcessError(1, command)
                return mock.Mock(returncode=0)
            with self.assertRaisesRegex(SystemExit, 'Update failed'):
                self.apply(source, prefix, component='core', sync=sync)
            self.assertEqual(len(calls), 2)
            self.apply(source, prefix, component='core')
            self.assertEqual((prefix / 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1').read_bytes(), NEW_BYTES)

    def test_core_refuses_unverified_core_alias_changes_or_unrelated_changes_without_writes(self):
        for change in ('alias', 'alias-manifest', 'missing-alias', 'symlink-alias', 'plugin',
                       'receipt', 'receipt-symlink', 'core', 'baseline', 'proof'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                source, prefix = self.core_fixture(Path(directory))
                alias = prefix / 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1'
                if change in ('alias', 'alias-manifest'):
                    alias.write_bytes(b'unrecognized core')
                    if change == 'alias-manifest':
                        write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                                if p.is_file() and p.name != 'SHA256SUMS'])
                elif change == 'missing-alias':
                    alias.unlink()
                elif change == 'symlink-alias':
                    alias.unlink()
                    alias.symlink_to('libcamera.so.0.1.0')
                elif change == 'plugin':
                    (prefix / 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so').write_bytes(b'tampered')
                elif change == 'receipt':
                    (prefix / 'core-update-info.json').write_bytes(b'unrecognized receipt')
                elif change == 'receipt-symlink':
                    (prefix / 'core-update-info.json').symlink_to('elf-report.json')
                elif change == 'core':
                    (source / 'libcamera.so.0.1.0').write_bytes(NEW_BYTES + b'unverified')
                    write_manifest(source, ['libcamera.so.0.1.0', 'build-info.txt', 'install-core-update.sh'])
                else:
                    info = json.loads((source / 'build-info.txt').read_text())
                    info['baseline_core_sha256' if change == 'baseline' else 'hardware_tested'] = (
                        '0' * 64 if change == 'baseline' else False)
                    (source / 'build-info.txt').write_text(json.dumps(info))
                    write_manifest(source, ['libcamera.so.0.1.0', 'build-info.txt', 'install-core-update.sh'])
                before = {str(p.relative_to(prefix)): p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
                with self.assertRaises(SystemExit):
                    self.apply(source, prefix, component='core')
                self.assertEqual({str(p.relative_to(prefix)): p.read_bytes() for p in prefix.rglob('*') if p.is_file()}, before)

    def test_core_stager_reuses_verified_bytes_and_never_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source, prefix = self.core_fixture(work)
            repo = work / 'repo'
            (repo / 'scripts').mkdir(parents=True)
            (repo / 'build/camera/updates').mkdir(parents=True)
            script = (ROOT / 'scripts/stage-ciri-libcamera-core-update.sh').read_text()
            script = script.replace('db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d',
                                    hashlib.sha256(NEW_BYTES).hexdigest())
            script = script.replace('af1014e2f73934ab79ccc1eddff5806194b83b9fce925e5e443b81443487a335',
                                    hashlib.sha256(b'core fixture').hexdigest())
            stager = repo / 'scripts/stage-ciri-libcamera-core-update.sh'
            stager.write_text(script)
            installer = repo / 'scripts/update-ciri-libcamera-host.sh'
            installer.write_bytes((ROOT / 'scripts/update-ciri-libcamera-host.sh').read_bytes())
            before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
            result = subprocess.run(['bash', str(stager), str(source)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged, = (repo / 'build/camera/updates').glob('core-update.*')
            self.assertEqual((staged / 'libcamera.so.0.1.0').read_bytes(), NEW_BYTES)
            self.assertEqual((staged / 'install-core-update.sh').read_bytes(), installer.read_bytes())
            info = json.loads((staged / 'build-info.txt').read_text())
            self.assertTrue(info['hardware_tested'])
            self.assertFalse(info['normal_streaming_verified'])
            check = subprocess.run(['sha256sum', '--check', '--strict', 'SHA256SUMS'], cwd=staged,
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual({p: p.read_bytes() for p in source.rglob('*') if p.is_file()}, before)
            (source / 'libcamera.so.0.1.0').write_bytes(b'wrong core')
            write_manifest(source, ['libcamera.so.0.1.0', 'build-info.txt', 'install-core-update.sh'])
            result = subprocess.run(['bash', str(stager), str(source)], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(list((repo / 'build/camera/updates').glob('core-update.*'))), 1)
            self.assertEqual((prefix / 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0').read_bytes(), b'core fixture')

    def test_cam_update_preserves_helper_libraries_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.cam_fixture(Path(directory))
            before = {p.relative_to(prefix): p.read_bytes()
                      for p in prefix.rglob('*') if p.is_file()}
            self.apply(source, prefix, component='cam')
            self.apply(source, prefix, component='cam')
            for name, content in before.items():
                if str(name) not in ('rootfs/usr/bin/cam', 'SHA256SUMS'):
                    self.assertEqual((prefix / name).read_bytes(), content)
            self.assertEqual((prefix / 'rootfs/usr/bin/cam').read_bytes(), NEW_BYTES)
            self.assertEqual(set(p.relative_to(prefix) for p in prefix.rglob('*') if p.is_file()), set(before))

    def test_cam_update_rejects_unknown_installed_client(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.cam_fixture(Path(directory))
            (prefix / 'rootfs/usr/bin/cam').write_bytes(b'unknown cam')
            write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                    if p.is_file() and p.name != 'SHA256SUMS'])
            before = {p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(SystemExit, 'Unexpected installed executable'):
                self.apply(source, prefix, component='cam')
            self.assertEqual({p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}, before)

    def test_updates_only_helper_and_manifest_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.fixture(Path(directory))
            before = {path.relative_to(prefix): path.read_bytes()
                      for path in prefix.rglob('*') if path.is_file()}
            self.apply(source, prefix)
            self.apply(source, prefix)
            for name, content in before.items():
                if str(name) not in (HOST, 'SHA256SUMS'):
                    self.assertEqual((prefix / name).read_bytes(), content)
            self.assertEqual((prefix / HOST).read_bytes(), NEW_BYTES)
            self.assertEqual((prefix / HOST).stat().st_mode & 0o777, 0o755)
            self.assertIn(hashlib.sha256(NEW_BYTES).hexdigest() + '  ' + HOST,
                          (prefix / 'SHA256SUMS').read_text())
            self.assertFalse(list(prefix.rglob('.ipa-host-*')))
            self.assertEqual(set(path.relative_to(prefix) for path in prefix.rglob('*') if path.is_file()), set(before))

    def test_repairs_manifest_after_partial_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source, prefix = self.fixture(Path(directory))
            real_replace = os.replace
            def replace(a, b):
                if Path(b).name == 'SHA256SUMS':
                    raise OSError('mock manifest write failure')
                return real_replace(a, b)
            with self.assertRaisesRegex(SystemExit, 'mock manifest write failure'):
                self.apply(source, prefix, replace=replace)
            self.assertEqual((prefix / HOST).read_bytes(), NEW_BYTES)
            self.assertIn(OLD_HASH + '  ' + HOST, (prefix / 'SHA256SUMS').read_text())
            self.assertFalse(list(prefix.rglob('.ipa-host-*')))
            self.apply(source, prefix)
            self.assertIn(hashlib.sha256(NEW_BYTES).hexdigest(), (prefix / 'SHA256SUMS').read_text())

    def test_rejects_tampering_unknown_host_and_wrong_architecture_without_writes(self):
        for change in ('kit', 'runtime', 'host', 'arch', 'report'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                source, prefix = self.fixture(Path(directory))
                if change == 'kit':
                    (source / 'ciri-libcamera-host').write_bytes(b'tampered')
                elif change == 'runtime':
                    (prefix / 'rootfs/usr/bin/cam').write_bytes(b'tampered')
                elif change == 'host':
                    (prefix / HOST).write_bytes(b'unexpected helper')
                    write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                            if p.is_file() and p.name != 'SHA256SUMS'])
                elif change == 'arch':
                    (source / 'ciri-libcamera-host').write_bytes(b'not an AArch64 ELF')
                    write_manifest(source, ['ciri-libcamera-host', 'build-info.txt', 'install-host-update.sh'])
                else:
                    (prefix / 'elf-report.json').write_text('{"architecture":"AArch64","missing_strong_imports":{"cam":["missing"]}}')
                    write_manifest(prefix, [str(p.relative_to(prefix)) for p in prefix.rglob('*')
                                            if p.is_file() and p.name != 'SHA256SUMS'])
                before = {p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
                with self.assertRaises(SystemExit):
                    self.apply(source, prefix)
                self.assertEqual({p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}, before)

    def test_rejects_symlinks_and_unsafe_duplicate_manifest_paths(self):
        for change in ('symlink', 'traversal', 'duplicate', 'empty'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                source, prefix = self.fixture(Path(directory))
                if change == 'symlink':
                    target = prefix / HOST
                    saved = prefix / 'saved-helper'
                    target.rename(saved)
                    target.symlink_to(saved)
                else:
                    manifest = source / 'SHA256SUMS'
                    original = manifest.read_text()
                    if change == 'traversal':
                        manifest.write_text(original + OLD_HASH + '  ../escaped\n')
                    elif change == 'duplicate':
                        manifest.write_text(original + original.splitlines()[0] + '\n')
                    else:
                        manifest.write_text('')
                with self.assertRaises(SystemExit):
                    self.apply(source, prefix)
                self.assertEqual((prefix / HOST).read_bytes(), OLD_BYTES)


if __name__ == '__main__':
    unittest.main()
