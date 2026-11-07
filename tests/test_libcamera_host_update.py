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

    def apply(self, source, prefix, **patches):
        script = (ROOT / 'scripts/update-ciri-libcamera-host.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        # Replace the pinned shipped-helper hash only in the TEST's code copy.
        pinned = "OLD = '1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45'"
        self.assertEqual(code.count(pinned), 1)
        code = code.replace(pinned, "OLD = '" + OLD_HASH + "'")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['-', str(source), str(prefix)]))
            stack.enter_context(mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0)))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(os, name, side_effect=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exec(compile(code, 'helper-update-inline.py', 'exec'), {})

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
