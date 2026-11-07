"""Exercise authored tooling with fake files; never execute recovered ARM code."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
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


class ToolingTests(unittest.TestCase):
    def test_organize_preserves_inputs_and_is_idempotent(self):
        module = load('organize-ciri-camera-workspace.py')
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            old = build / 'ciri-camera-client-sdk.4Jej3wox'
            old.mkdir()
            (old / 'retained').write_bytes(b'keep compiled SDK')
            objects = build / 'ciri-libcamera-meson'
            objects.mkdir()
            (objects / 'build.ninja').write_text('retained rules')
            module.organize(build, False)
            self.assertFalse((build / 'camera').exists())
            module.organize(build, True)
            module.organize(build, True)
            self.assertTrue(old.is_symlink())
            self.assertEqual((old / 'retained').read_bytes(), b'keep compiled SDK')
            self.assertEqual((build / 'camera/objects/libcamera').resolve(), objects)
            self.assertEqual((objects / 'build.ninja').read_text(), 'retained rules')

    def test_organize_refuses_existing_destination_before_moves(self):
        module = load('organize-ciri-camera-workspace.py')
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / 'ciri-camera-client-sdk.4Jej3wox').mkdir()
            target = build / 'camera/sdk/compiler'
            target.mkdir(parents=True)
            with self.assertRaises(ValueError):
                module.organize(build, True)
            self.assertFalse((build / 'ciri-camera-client-sdk.4Jej3wox').is_symlink())

    def test_elf_parser_accepts_weak_providers_and_checks_versions(self):
        module = load('verify-ciri-libcamera-elf.py')
        text = '''Class: ELF64
Machine: AArch64
0x01 (NEEDED) Shared library: [libsample.so.1]
0x02 (SONAME) Library soname: [libplugin.so]
1: 0000 0 FUNC GLOBAL DEFAULT UND required@V1 (2)
2: 1234 0 FUNC WEAK DEFAULT 10 required@@V1
3: 1234 0 FUNC GLOBAL HIDDEN 10 hidden
4: 0000 0 FUNC WEAK DEFAULT UND optional
'''
        with mock.patch.object(module.subprocess, 'run', return_value=mock.Mock(stdout=text)):
            item = module.inspect(Path('mock.so'))
        self.assertEqual(item['needed'], ['libsample.so.1'])
        self.assertEqual(item['imports'], {'required@V1'})
        self.assertEqual(item['exports'], {'required', 'required@V1'})
        with mock.patch.object(module.subprocess, 'run', return_value=mock.Mock(stdout=text.replace('AArch64', 'x86-64'))):
            with self.assertRaises(ValueError):
                module.inspect(Path('mock.so'))

    def test_elf_closure_uses_new_core_and_reports_missing_imports(self):
        module = load('verify-ciri-libcamera-elf.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*module.ROOTS,
                         'usr/lib/ciri-libcamera/libcamera.so.0.1.0',
                         'usr/lib/ciri-libcamera/libcamera-base.so.0.1.0',
                         'usr/lib/ciri-libcamera/libcamera-mtkisp7-platform.so',
                         'usr/lib/ciri-libcamera/libcamera.so.0.1',
                         'usr/lib64/libcamera.so.0.1'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'mock ELF')
            seen = []
            def inspect(path):
                seen.append(str(path.relative_to(root)))
                return {'needed': ['libcamera.so.0.1'] if path.name == 'cam' else [],
                        'soname': None, 'exports': {'available@V1'},
                        'imports': {'available@V1', 'missing@V2'} if path.name == 'cam' else set()}
            with mock.patch.object(module, 'inspect', side_effect=inspect):
                report = module.verify(root)
            self.assertIn('usr/lib/ciri-libcamera/libcamera.so.0.1', seen)
            self.assertNotIn('usr/lib64/libcamera.so.0.1', seen)
            self.assertEqual(report['missing_strong_imports'], {'usr/bin/cam': ['missing@V2']})
            (root / 'usr/lib/ciri-libcamera/libcamera.so.0.1').unlink()
            (root / 'usr/lib64/libcamera.so.0.1').unlink()
            with mock.patch.object(module, 'inspect', side_effect=inspect):
                with self.assertRaisesRegex(ValueError, 'Missing libcamera'):
                    module.verify(root)

    def test_libevent_linker_aliases_replace_reduced_recovery_providers(self):
        script = (ROOT / 'scripts/build-ciri-libcamera-stack.sh').read_text()
        code = script.split("for package in ('libevent-2.1-7'", 1)[1].split('for name, version, libs, requires in (', 1)[0]
        code = "for package in ('libevent-2.1-7'" + code
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lib').mkdir()
            reduced = root / 'reduced.so'
            reduced.write_bytes(b'reduced library fixture')
            for package, name in (('libevent-2.1-7', 'libevent'),
                                  ('libevent-core-2.1-7', 'libevent_core'),
                                  ('libevent-pthreads-2.1-7', 'libevent_pthreads')):
                runtime = root / package / 'sysroot/usr/lib/aarch64-linux-gnu'
                runtime.mkdir(parents=True)
                binary = runtime / (name + '-2.1.so.7.0.1')
                binary.write_bytes(b'approved library fixture')
                (runtime / (name + '-2.1.so.7')).symlink_to(binary.name)
                dev = root / 'libevent-dev/sysroot/usr/lib/aarch64-linux-gnu'
                dev.mkdir(parents=True, exist_ok=True)
                (dev / (name + '.so')).symlink_to(binary.name)
                (root / 'lib' / (name + '.so')).symlink_to(reduced)
            for _ in range(2):
                exec(code, {'root': root})
                for name in ('libevent', 'libevent_core', 'libevent_pthreads'):
                    self.assertEqual((root / 'lib' / (name + '.so')).read_bytes(), b'approved library fixture')
                    self.assertFalse((root / 'lib' / (name + '.so')).readlink().is_absolute())
            target = root / 'lib/libevent.so'
            target.unlink()
            target.write_bytes(b'not our staging alias')
            with self.assertRaises(SystemExit):
                exec(code, {'root': root})
            self.assertEqual(target.read_bytes(), b'not our staging alias')

    def test_package_refuses_existing_output_without_modification(self):
        module = load('package-ciri-libcamera-stack.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'evidence').write_text('retain')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                module.package(root, root, root, root)
            self.assertEqual((root / 'evidence').read_text(), 'retain')

    def test_package_assembly_and_static_failure_with_fixture_components(self):
        module = load('package-ciri-libcamera-stack.py')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            built, recovery, sdk, repo = (work / name for name in ('built', 'recovery', 'sdk', 'repo'))
            def write(root, name, data=b'fixture component, never executed'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path
            def manifest(root, target, prefix=''):
                target.write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' +
                    prefix + str(path.relative_to(root)) + '\n'
                    for path in sorted(root.rglob('*')) if path.is_file() and path != target))
            libraries = {'libcamera.so.0.1.0': 'libcamera.so.0.1',
                         'libcamera-base.so.0.1.0': 'libcamera-base.so.0.1',
                         'libcamera-mtkisp7-platform.so': 'libcamera-mtkisp7-platform.so'}
            for name, soname in libraries.items():
                path = write(built / 'rootfs', 'usr/lib/ciri-libcamera/' + name, b'new core fixture')
                if soname != name:
                    (path.parent / soname).symlink_to(name)
            for name in ('usr/bin/cam', 'usr/bin/ciri-libcamera-host'):
                write(built / 'rootfs', name)
            write(built, 'build-info.txt')
            write(built, 'build-inputs.json', b'{}')
            manifest(built, built / 'SHA256SUMS')
            recovered = ['usr/lib64/libvendor.so', 'lib64/ld-linux-aarch64.so.1',
                         'usr/lib64/libcamera.so.0.1.0', *module.elf.ROOTS[2:]]
            for name in recovered:
                write(recovery / 'files', name)
            write(recovery / 'files', 'etc/camera/config.yaml', b'fixture calibration')
            write(recovery / 'files', 'usr/lib64/libcamera/ipa_mtkisp7.so.sign', b'fixture signature')
            manifest(recovery / 'files', recovery / 'SHA256SUMS', './')
            write(recovery, 'camera-dependencies.json', json.dumps({'dependency_files': recovered}).encode())
            packages = []
            for package, library in (('libevent-2.1-7', 'libevent'),
                                     ('libevent-core-2.1-7', 'libevent_core'),
                                     ('libevent-pthreads-2.1-7', 'libevent_pthreads')):
                archive = write(sdk, package + '/package.deb', package.encode())
                packages.append({'name': package, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
                write(sdk, package + '/sysroot/usr/lib/aarch64-linux-gnu/' + library + '-2.1.so.7.0.1')
            write(repo, 'config/ciri-libcamera-build-inputs.json', json.dumps({'libevent_packages': packages}).encode())
            write(repo, 'scripts/run-ciri-libcamera.sh', b'fixture launcher, never executed')
            write(repo, 'config/libcamera/ciri.patch')
            def inspect(path):
                name = path.name
                soname = libraries.get(name)
                if '-2.1.so.7.0.1' in name:
                    soname = name.removesuffix('.0.1')
                is_core = name.startswith('libcamera.so')
                needed = ['libvendor.so'] if is_core else []
                if name in ('cam', 'mtkisp7_ipa_proxy.so', 'ipa_mtkisp7.so'):
                    needed = ['libcamera.so.0.1']
                imports = {'core_symbol'} if name == 'libvendor.so' else set()
                return {'needed': needed, 'soname': soname,
                        'exports': {'core_symbol'} if is_core else set(), 'imports': imports}
            output = work / 'package'
            with mock.patch.object(module, 'REPO', repo), mock.patch.object(module.elf, 'inspect', side_effect=inspect):
                module.package(built, recovery, sdk, output)
                module.check_manifest(output, output / 'SHA256SUMS')
            self.assertEqual((output / 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1').read_bytes(), b'new core fixture')
            self.assertFalse((output / 'rootfs/usr/lib64/libcamera.so.0.1.0').exists())
            self.assertEqual((output / 'rootfs/usr/lib64/libcamera/ipa_mtkisp7.so.sign').read_bytes(), b'fixture signature')
            self.assertFalse(any(path.is_symlink() for path in output.rglob('*')))
            self.assertEqual(json.loads((output / 'elf-report.json').read_text())['missing_strong_imports'], {})
            def broken(path):
                item = inspect(path)
                item['imports'].add('unprovided_symbol')
                return item
            failed = work / 'failed-package'
            with mock.patch.object(module, 'REPO', repo), mock.patch.object(module.elf, 'inspect', side_effect=broken):
                with self.assertRaisesRegex(ValueError, 'Strong imports'):
                    module.package(built, recovery, sdk, failed)
            self.assertTrue((failed / 'elf-report.json').exists())
            self.assertFalse((failed / 'SHA256SUMS').exists())

    def test_manifest_and_runtime_paths_reject_escape_and_duplicates(self):
        module = load('package-ciri-libcamera-stack.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'input'
            file.write_bytes(b'fixture')
            sha = hashlib.sha256(file.read_bytes()).hexdigest()
            manifest = root / 'SHA256SUMS'
            manifest.write_text(f'{sha}  ./input\n')
            self.assertEqual(module.check_manifest(root, manifest), {file})
            for name in ('../input', str(file), 'a/../input'):
                with self.assertRaises(ValueError):
                    module.elf.safe_file(root, name)
            manifest.write_text(f'{sha}  ./input\n{sha}  input\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                module.check_manifest(root, manifest)
            manifest.write_text('not a checksum\n')
            with self.assertRaisesRegex(ValueError, 'Malformed'):
                module.check_manifest(root, manifest)

    def test_archive_name_checks_reject_escape_and_link_parents(self):
        module = load('prepare-ciri-libcamera-sdk.py')
        for name in ('../escape', '/absolute', 'a/../escape', 'a\\escape'):
            with self.assertRaises(ValueError):
                module.member_path(name)
        for link in ('../escape', '/absolute', 'a/../escape'):
            with self.assertRaises(ValueError):
                module.safe_link(Path('lib.so'), link)

    def test_deb_extraction_writes_only_regular_data_and_safe_links(self):
        module = load('prepare-ciri-libcamera-sdk.py')
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as tar:
            file = tarfile.TarInfo('usr/lib/libmock.so.1')
            file.size = 4
            file.mode = 0o4755
            tar.addfile(file, io.BytesIO(b'data'))
            link = tarfile.TarInfo('usr/lib/libmock.so')
            link.type = tarfile.SYMTYPE
            link.linkname = 'libmock.so.1'
            tar.addfile(link)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'sysroot'
            with mock.patch.object(module, 'deb_payload', return_value=payload.getvalue()):
                module.extract_deb(Path('mock.deb'), target)
            self.assertEqual((target / 'usr/lib/libmock.so').read_bytes(), b'data')
            self.assertEqual((target / 'usr/lib/libmock.so.1').stat().st_mode & 0o7777, 0o755)
            with mock.patch.object(module, 'deb_payload', return_value=payload.getvalue()):
                with self.assertRaises(FileExistsError):
                    module.extract_deb(Path('mock.deb'), target)

    def test_installer_preflight_requires_complete_manifest_no_links(self):
        script = (ROOT / 'scripts/install-ciri-libcamera-usb.sh').read_text()
        code = re.search(r"python3 -I - \"\$source\" <<'PY'\n(.*?)\nPY", script, re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = {'run-libcamera','build-info.txt','elf-report.json',
                'rootfs/usr/bin/cam','rootfs/usr/bin/ciri-libcamera-host',
                'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0',
                'rootfs/usr/lib/ciri-libcamera/libcamera-base.so.0.1.0',
                'rootfs/usr/lib/ciri-libcamera/libcamera-mtkisp7-platform.so',
                'rootfs/usr/lib64/mtkisp7_ipa_proxy.so',
                'rootfs/usr/lib64/libcamera/ipa_mtkisp7.so',
                'rootfs/lib64/ld-linux-aarch64.so.1'}
            for name in required:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'fixture only, never executed')
            (root / 'elf-report.json').write_text(json.dumps({'architecture': 'AArch64', 'missing_strong_imports': {}}))
            for name in ('dev', 'proc', 'sys', 'run', 'tmp', 'captures'):
                (root / 'rootfs' / name).mkdir()
            def manifest():
                (root / 'SHA256SUMS').write_text(''.join(
                    hashlib.sha256((root / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                    for name in sorted(required)))
            def run():
                return subprocess.run([sys.executable, '-I', '-', str(root)], input=code,
                                      text=True, capture_output=True, timeout=10)
            manifest()
            self.assertEqual(run().returncode, 0)
            (root / 'unlisted').write_text('extra')
            self.assertNotEqual(run().returncode, 0)
            (root / 'unlisted').unlink()
            (root / 'run-libcamera').write_text('tampered')
            self.assertNotEqual(run().returncode, 0)
            manifest()
            (root / 'link').symlink_to('run-libcamera')
            self.assertNotEqual(run().returncode, 0)

    def test_launcher_and_host_do_not_implement_capture_or_global_paths(self):
        launcher = (ROOT / 'scripts/run-ciri-libcamera.sh').read_text()
        host = (ROOT / 'scripts/ciri-libcamera-host.cpp').read_text()
        self.assertIn('unshare --mount --propagation private', launcher)
        self.assertIn('remount,bind,ro,nosuid,nodev,noexec', launcher)
        self.assertNotIn('/run/udev/control', launcher)
        self.assertNotIn('LD_LIBRARY_PATH', launcher)
        self.assertIn('StandaloneIPAWorker worker', host)
        self.assertIn('WEXITSTATUS(status)', host)
        self.assertIn('chdir("/captures")', host)
        self.assertNotIn('queueRequest', host)
        self.assertNotIn('CameraManager', host.replace("client's CameraManager threads", ''))


if __name__ == '__main__':
    unittest.main()
