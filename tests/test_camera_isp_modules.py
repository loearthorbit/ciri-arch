"""Pinned ISP restoration tests: synthetic archives/files and mocked modinfo only."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def elf(tag):
    return b'\x7fELF\x02\x01' + bytes(10) + b'\x01\x00\xb7\x00' + tag.encode()


def manifest(root):
    (root / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
        for path in sorted(root.iterdir()) if path.is_file() and path.name != 'SHA256SUMS'))


class ISPModuleTests(unittest.TestCase):
    def archive(self, stage, change=None):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as tar:
            for path, name in stage.MEMBERS.items():
                if change == 'missing' and name == 'mtk-aie.ko':
                    continue
                blob = elf(name)
                info = tarfile.TarInfo(path)
                info.size = len(blob)
                if change == 'link' and name == 'mtk-aie.ko':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/etc/passwd'
                    info.size = 0
                    tar.addfile(info)
                else:
                    tar.addfile(info, io.BytesIO(blob if change != 'bytes' else b'x' * len(blob)))
                    if change == 'duplicate' and name == 'mtk-aie.ko':
                        tar.addfile(info, io.BytesIO(blob))
            other = tarfile.TarInfo('../../must-not-extract')
            other.size = 4
            tar.addfile(other, io.BytesIO(b'nope'))
        data.seek(0)
        return data

    def test_extracts_only_two_pinned_members(self):
        stage = load('stage-ciri-isp-modules.py')
        stage.installer.MODULES = {name: hashlib.sha256(elf(name)).hexdigest()
                                   for name in stage.installer.MODULES}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage.extract_selected(self.archive(stage), root)
            self.assertEqual({p.name for p in root.iterdir()}, set(stage.installer.MODULES))
            for name in stage.installer.MODULES:
                self.assertEqual((root / name).read_bytes(), elf(name))

    def test_selected_links_duplicates_tamper_and_missing_rejected(self):
        stage = load('stage-ciri-isp-modules.py')
        stage.installer.MODULES = {name: hashlib.sha256(elf(name)).hexdigest()
                                   for name in stage.installer.MODULES}
        for change in ('link', 'duplicate', 'bytes', 'missing'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    stage.extract_selected(self.archive(stage, change), Path(directory))

    def fixture(self, work):
        module = load('install-ciri-isp-modules.py')
        module.MODULES = {name: hashlib.sha256(elf(name)).hexdigest() for name in module.MODULES}
        source, root = work / 'kit', work / 'root'
        source.mkdir()
        target = root / 'usr/lib/modules' / module.RELEASE / 'updates/ciri-camera'
        target.mkdir(parents=True)
        (target / 'v4l2loopback.ko').write_bytes(b'loopback preserved')
        for name in module.MODULES:
            (source / name).write_bytes(elf(name))
        (source / 'install-modules.py').write_bytes(b'authored installer fixture')
        (source / 'module-build-info.txt').write_text(
            'kernel-release=' + module.RELEASE + '\narchitecture=AArch64\nmodule-only=true\n'
            'source-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993\n'
            'source-archive-sha256=' + module.ARCHIVE_SHA + '\n')
        manifest(source)
        return module, source, root, target

    def info(self, module, field, path):
        return {'vermagic': module.RELEASE + ' SMP preempt mod_unload aarch64',
                'name': Path(path).stem.replace('-', '_'),
                'depends': 'videobuf2-common,mtk-cmdq-helper'}[field]

    def test_install_preserves_loopback_and_is_idempotent_no_module_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            module, source, root, target = self.fixture(Path(directory))
            with mock.patch.object(module, 'modinfo', side_effect=lambda f, p: self.info(module, f, p)), \
                 mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='/fixture/dependency.ko')) as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                module.install(source, root)
                module.install(source, root)
            self.assertEqual((target / 'v4l2loopback.ko').read_bytes(), b'loopback preserved')
            for name in module.MODULES:
                self.assertEqual((target / name).read_bytes(), elf(name))
                self.assertEqual((target / name).stat().st_mode & 0o777, 0o644)
            self.assertTrue(all(call.args[0][0] in ('modinfo', 'sync', 'depmod') for call in run.call_args_list))
            self.assertFalse(list(target.glob('.ciri-isp-*')))

    def test_refuses_collisions_symlinks_tamper_and_missing_dependencies_without_writes(self):
        for change in ('collision', 'link', 'other-candidate', 'kit', 'dependency', 'vermagic'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                module, source, root, target = self.fixture(Path(directory))
                if change == 'collision':
                    (target / 'mtk_imgsys.ko').write_bytes(b'unknown')
                elif change == 'link':
                    (target / 'mtk_imgsys.ko').symlink_to(source / 'mtk_imgsys.ko')
                elif change == 'other-candidate':
                    (target.parent / 'mtk_imgsys.ko.zst').write_bytes(b'existing compressed module')
                elif change == 'kit':
                    (source / 'mtk_imgsys.ko').write_bytes(b'tampered')
                before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                def info(field, path):
                    return 'wrong-kernel' if change == 'vermagic' and field == 'vermagic' else self.info(module, field, path)
                with mock.patch.object(module, 'modinfo', side_effect=info), \
                     mock.patch.object(subprocess, 'run', return_value=mock.Mock(
                         returncode=1 if change == 'dependency' else 0, stdout='/fixture/dependency.ko')) as run, \
                     self.assertRaises(ValueError):
                    module.install(source, root)
                self.assertFalse(any(call.args[0][0] in ('sync', 'depmod') for call in run.call_args_list))
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_partial_link_and_sync_failures_repair_without_overwrite(self):
        for failure in ('link', 'first-sync', 'final-sync', 'depmod'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                module, source, root, target = self.fixture(Path(directory))
                real_link = os.link
                calls = 0
                def link(a, b):
                    if failure == 'link' and Path(b).name == 'mtk-aie.ko':
                        raise OSError('fixture publication failed')
                    real_link(a, b)
                def run(command, **kwargs):
                    nonlocal calls
                    if command[0] == 'sync':
                        calls += 1
                        if (failure == 'first-sync' and calls == 1) or (failure == 'final-sync' and calls == 2):
                            raise subprocess.CalledProcessError(1, command)
                    if command[0] == 'depmod' and failure == 'depmod':
                        raise subprocess.CalledProcessError(1, command)
                    return mock.Mock(returncode=0, stdout='/fixture/dependency.ko')
                with mock.patch.object(module, 'modinfo', side_effect=lambda f, p: self.info(module, f, p)), \
                     mock.patch.object(subprocess, 'run', side_effect=run), mock.patch.object(os, 'link', side_effect=link), \
                     self.assertRaises((OSError, subprocess.CalledProcessError)):
                    module.install(source, root)
                with mock.patch.object(module, 'modinfo', side_effect=lambda f, p: self.info(module, f, p)), \
                     mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='/fixture/dependency.ko')), \
                     contextlib.redirect_stdout(io.StringIO()):
                    module.install(source, root)
                self.assertFalse(list(target.glob('.ciri-isp-*')))
                self.assertEqual((target / 'v4l2loopback.ko').read_bytes(), b'loopback preserved')


if __name__ == '__main__':
    unittest.main()
