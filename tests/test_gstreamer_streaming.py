"""Test standard streaming tooling with synthetic files; no vendor/device execution."""
import contextlib
import hashlib
import importlib.util
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


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def put(root, name, content=b'ELF fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def manifest(root):
    (root / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(root)) + '\n'
        for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))


class StreamingTests(unittest.TestCase):
    def fixture(self, work):
        module = load('package-ciri-gstreamer.py')
        repo, built, baseline, sdk, recovery, host = (work / name for name in
                                                    ('repo', 'built', 'base', 'sdk', 'recovery', 'host'))
        module.REPO = repo
        for name in ('run-ciri-libcamera.sh', 'stream-ciri-camera.sh', 'install-ciri-gstreamer.sh'):
            put(repo, 'scripts/' + name, b'mock authored launcher')
        put(repo, 'config/libcamera/ciri.patch', b'mock patch')
        for name in module.elf.ROOTS:
            put(baseline, 'rootfs/' + name, ('base ' + name).encode())
        for name in ('usr/lib/ciri-libcamera/libcamera.so.0.1',
                     'usr/lib/ciri-libcamera/libcamera.so.0.1.0',
                     'usr/lib64/libglib-2.0.so.0', 'lib64/libc.so.6'):
            put(baseline, 'rootfs/' + name)
        put(baseline, 'run-libcamera', b'old launcher')
        put(baseline, 'elf-report.json', b'{"architecture":"AArch64","missing_strong_imports":{}}')
        put(baseline, 'build-info.txt', b'base source build')
        put(built, 'rootfs/' + module.PLUGIN_DIR + 'libgstlibcamera.so', b'new plugin')
        put(built, 'build-info.txt', b'mock source build\n')
        put(host, 'ciri-libcamera-host', b'new supervisor')
        put(host, 'build-info.txt', b'mock supervisor build\n')
        selected = {
            'gstreamer1.0-tools': ('usr/bin/gst-launch-1.0', 'usr/bin/gst-inspect-1.0'),
            'libgstreamer1.0-0': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstcoreelements.so',
                                  'usr/lib/aarch64-linux-gnu/libgstmini-1.0.so.0'),
            'gstreamer1.0-plugins-base': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstvideoconvertscale.so',),
            'gstreamer1.0-plugins-good': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstvideo4linux2.so',),
        }
        inputs = {'packages': []}
        for package, names in selected.items():
            archive = put(sdk, package + '/package.deb', b'fake archive ' + package.encode())
            inputs['packages'].append({'name': package, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
            for name in names:
                put(sdk, package + '/sysroot/' + name)
            put(sdk, package + '/sysroot/usr/share/doc/' + package + '/copyright', b'synthetic licence fixture')
        put(repo, 'config/ciri-gstreamer-inputs.json', json.dumps(inputs).encode())
        put(recovery, 'files/usr/lib64/libgobject-2.0.so.0')
        for path in (built, baseline, sdk, host):
            manifest(path)
        manifest(recovery / 'files')
        (recovery / 'SHA256SUMS').write_bytes((recovery / 'files/SHA256SUMS').read_bytes())
        return module, (built, baseline, sdk, recovery, host)

    def inspect(self, path):
        needed, exports, imports = [], set(), set()
        if path.name.startswith('libgst') or path.name.startswith('gst-'):
            if path.name == 'libgstmini-1.0.so.0':
                exports = {'gst_required'}
            else:
                needed, imports = ['libgstmini-1.0.so.0'], {'gst_required'}
        if path.name == 'libgstlibcamera.so':
            needed += ['libcamera.so.0.1', 'libgobject-2.0.so.0']
            imports |= {'camera_required', 'gobject_required'}
        elif path.name.startswith('libcamera.so'):
            exports = {'camera_required'}
        elif path.name == 'libgobject-2.0.so.0':
            needed, exports = ['libglib-2.0.so.0'], {'gobject_required'}
        elif path.name == 'libglib-2.0.so.0':
            exports = {'glib_required'}
        return {'needed': needed, 'soname': path.name if '.so' in path.name else None,
                'exports': exports, 'imports': imports}

    def planned(self, work):
        module, args = self.fixture(work)
        with mock.patch.object(module.elf, 'inspect', side_effect=self.inspect):
            payload, generated, report = module.plan(*args)
        return module, args, payload, generated, report

    def apply(self, kit, prefix, **patches):
        script = (ROOT / 'scripts/install-ciri-gstreamer.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['-', str(kit), str(prefix)]))
            stack.enter_context(mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0)))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(os, name, side_effect=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exec(compile(code, 'streaming-installer-inline.py', 'exec'), {})

    def test_plan_preserves_base_and_recopies_no_proprietary_components(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            module, args, payload, generated, report = self.planned(work)
            baseline = args[1]
            before = {p: p.read_bytes() for p in baseline.rglob('*') if p.is_file()}
            self.assertEqual(report['missing_strong_imports'], {})
            self.assertIn('output=640x480-NV12-to-YUY2-unbuffered-filesink\n',
                          generated['streaming-build-info.txt'])
            self.assertIn('loopback-configuration=host-v4l2-ctl\n',
                          generated['streaming-build-info.txt'])
            self.assertIn('rootfs/usr/lib64/libgobject-2.0.so.0', payload)
            self.assertFalse(any('ipa_mtkisp7' in name or 'tuning' in name or
                                 Path(name).name.startswith('libcamera.so') for name in payload))
            self.assertEqual({p: p.read_bytes() for p in baseline.rglob('*') if p.is_file()}, before)
            output = work / 'kit'
            module.assemble(payload, generated, output)
            self.assertFalse(any(p.is_symlink() for p in output.rglob('*')))
            self.assertTrue((output / 'SHA256SUMS').is_file())
            with self.assertRaisesRegex(ValueError, 'fresh output'):
                module.assemble(payload, generated, output)

    def test_missing_strong_provider_stops_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            module, args = self.fixture(Path(directory))
            def inspect(path):
                result = self.inspect(path)
                if path.name == 'libgstmini-1.0.so.0':
                    result['exports'] = set()
                return result
            with mock.patch.object(module.elf, 'inspect', side_effect=inspect):
                with self.assertRaisesRegex(ValueError, 'Missing strong streaming imports'):
                    module.plan(*args)

    def test_install_preserves_core_algorithms_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            module, args, payload, generated, report = self.planned(work)
            prefix = args[1]
            before = {p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
            kit = work / 'kit'
            module.assemble(payload, generated, kit)
            self.apply(kit, prefix)
            self.apply(kit, prefix)
            for path, content in before.items():
                if path.relative_to(prefix).as_posix() not in (HOST, 'run-libcamera', 'SHA256SUMS'):
                    self.assertEqual(path.read_bytes(), content)
            self.assertEqual((prefix / HOST).read_bytes(), b'new supervisor')
            self.assertFalse(list(prefix.rglob('.ciri-gst-*')))
            self.assertFalse((prefix / 'expected-base.json').exists())

    def test_partial_replace_failure_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            module, args, payload, generated, report = self.planned(work)
            kit, prefix = work / 'kit', args[1]
            module.assemble(payload, generated, kit)
            real_replace = os.replace
            def replace(a, b):
                if Path(b).name == 'SHA256SUMS':
                    raise OSError('mock manifest failure')
                return real_replace(a, b)
            with self.assertRaisesRegex(SystemExit, 'mock manifest failure'):
                self.apply(kit, prefix, replace=replace)
            self.assertFalse(list(prefix.rglob('.ciri-gst-*')))
            self.apply(kit, prefix)
            self.assertEqual((prefix / HOST).read_bytes(), b'new supervisor')

    def test_installer_refuses_tampering_collisions_and_symlinks_before_writes(self):
        for change in ('kit', 'base', 'unknown-host', 'collision', 'symlink', 'traversal',
                       'missing-plugin', 'extra-manifest', 'duplicate-row'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                module, args, payload, generated, report = self.planned(work)
                kit, prefix = work / 'kit', args[1]
                module.assemble(payload, generated, kit)
                if change == 'kit':
                    (kit / HOST).write_bytes(b'tampered')
                elif change == 'base':
                    (prefix / 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1').write_bytes(b'tampered')
                elif change == 'unknown-host':
                    (prefix / HOST).write_bytes(b'unknown supervisor')
                    manifest(prefix)
                elif change == 'collision':
                    put(prefix, 'stream-camera', b'untracked existing script')
                elif change == 'symlink':
                    (prefix / 'rootfs/usr/bin').rename(prefix / 'rootfs/usr/saved-bin')
                    (prefix / 'rootfs/usr/bin').symlink_to(prefix / 'rootfs/usr/saved-bin')
                elif change == 'missing-plugin':
                    (kit / 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstvideo4linux2.so').unlink()
                    manifest(kit)
                elif change == 'extra-manifest':
                    put(kit, 'nested/SHA256SUMS', b'untracked nested file')
                elif change == 'duplicate-row':
                    first = (kit / 'SHA256SUMS').read_text().splitlines()[0]
                    with (kit / 'SHA256SUMS').open('a') as stream:
                        stream.write(first + '\n')
                else:
                    with (kit / 'SHA256SUMS').open('a') as stream:
                        stream.write('0' * 64 + '  ../escaped\n')
                before = {p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}
                with self.assertRaises(SystemExit):
                    self.apply(kit, prefix)
                self.assertEqual({p: p.read_bytes() for p in prefix.rglob('*') if p.is_file()}, before)

    def test_launcher_and_supervisor_use_standard_elements_and_private_registry(self):
        stream = (ROOT / 'scripts/stream-ciri-camera.sh').read_text()
        self.assertIn('libcamerasrc', stream)
        self.assertIn('videoconvert ! video/x-raw,format=YUY2', stream)
        self.assertIn('filesink "location=$device" buffer-mode=unbuffered sync=false', stream)
        self.assertNotIn('io-mode=rw', stream)
        self.assertNotIn('io-mode=mmap', stream)
        self.assertIn('--set-fmt-video-out=width=640,height=480,pixelformat=YUYV', stream)
        self.assertIn('--set-output-parm="$fps"', stream)
        self.assertLess(stream.index('v4l2-ctl -d "$device"'),
                        stream.index('exec /opt/ciri-libcamera/run-libcamera'))
        self.assertIn("driver != b'v4l2 loopback'", stream)
        self.assertNotIn('modprobe ', stream)
        launcher = (ROOT / 'scripts/run-ciri-libcamera.sh').read_text()
        self.assertIn('GST_PLUGIN_SYSTEM_PATH_1_0=', launcher)
        self.assertIn('GST_REGISTRY_FORK=no', launcher)
        self.assertIn('GST_REGISTRY_1_0=/tmp/', launcher)
        host = (ROOT / 'scripts/ciri-libcamera-host.cpp').read_text()
        self.assertIn('"--gst-launch"', host)
        self.assertIn('"/usr/bin/gst-launch-1.0"', host)
        self.assertLess(host.index('worker.start()'), host.index('execv('))
        self.assertNotIn('CameraManager', host.replace("client's CameraManager", "client manager"))

    def test_sdk_scan_ignores_gdb_scripts_and_reuses_managed_aliases(self):
        module = load('prepare-ciri-gstreamer-sdk.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = {'name': 'libgstreamer1.0-0', 'version': '1.22.0',
                       'path': 'pool/main/g/gstreamer1.0/libgstreamer1.0-0_1.22.0_arm64.deb',
                       'sha256': '0' * 64}
            library = put(root, package['name'] + '/sysroot/usr/lib/libgstreamer-1.0.so.0', b'\x7fELFfixture')
            gdb = put(root, package['name'] + '/sysroot/usr/share/gdb/libgstreamer-1.0.so.0-gdb.py', b'must not execute')
            (root / 'lib').mkdir()
            (root / 'lib/libgstreamer-1.0.so').symlink_to(gdb)
            with mock.patch.object(module.base, 'prepare_package'):
                for _ in range(2):
                    module.prepare(root, root, {'packages': [package]}, False)
            self.assertEqual((root / 'lib/libgstreamer-1.0.so').resolve(), library)
            self.assertFalse(any(p.name.endswith('-gdb.py') for p in (root / 'lib').iterdir()))

    def test_streamer_checks_actual_driver_and_device_capabilities(self):
        import fcntl
        import struct
        script = (ROOT / 'scripts/stream-ciri-camera.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
        for driver, caps, device_caps, accepted in (
            (b'v4l2 loopback', 2, 0, True),
            (b'v4l2 loopback', 0x80000001, 2, True),
            (b'mtk-cam', 2, 0, False),
            (b'v4l2 loopback', 0x80000002, 1, False),
            (b'v4l2 loopback', 1, 0, False),
        ):
            with self.subTest(driver=driver, caps=caps, device_caps=device_caps):
                def query(fd, request, buffer, mutate):
                    self.assertEqual((fd, request, mutate), (999, 0x80685600, True))
                    buffer[:len(driver)] = driver
                    struct.pack_into('=II', buffer, 84, caps, device_caps)
                with mock.patch.object(sys, 'argv', ['-', '/dev/video42']), \
                     mock.patch.object(os, 'open', return_value=999) as opened, \
                     mock.patch.object(os, 'close') as closed, \
                     mock.patch.object(fcntl, 'ioctl', side_effect=query):
                    if accepted:
                        exec(compile(code, 'streamer-query-fixture.py', 'exec'), {})
                    else:
                        with self.assertRaisesRegex(SystemExit, 'OUTPUT device'):
                            exec(compile(code, 'streamer-query-fixture.py', 'exec'), {})
                    self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
                    closed.assert_called_once_with(999)

    def test_sdk_staging_symlinks_and_conflicting_aliases_are_refused(self):
        module = load('prepare-ciri-gstreamer-sdk.py')
        for name in ('lib', 'pkgconfig', 'SHA256SUMS', 'conflicting-alias'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                sdk = work / 'sdk'
                sdk.mkdir()
                outside = put(work, 'outside', b'outside sentinel')
                package = {'name': 'libgstreamer1.0-0', 'version': '1.22.0',
                           'path': 'pool/main/g/libgstreamer1.0-0_1.22.0_arm64.deb', 'sha256': '0' * 64}
                if name == 'conflicting-alias':
                    put(sdk, package['name'] + '/sysroot/usr/lib/libgsttest.so.0', b'\x7fELFfixture')
                    (sdk / 'lib').mkdir()
                    (sdk / 'lib/libgsttest.so').symlink_to(outside)
                else:
                    (sdk / name).symlink_to(outside)
                with mock.patch.object(module.base, 'prepare_package'), self.assertRaises(ValueError):
                    module.prepare(sdk, sdk, {'packages': [package]}, False)
                self.assertEqual(outside.read_bytes(), b'outside sentinel')

    def test_module_kit_preflight_uses_only_fixture_files(self):
        script = (ROOT / 'scripts/install-ciri-v4l2loopback.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
        pins = (
            'source=https://github.com/v4l2loopback/v4l2loopback\n'
            'revision=0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4\n'
            'kernel-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993\n'
            'kernel-release=7.3.0-rc4-ciri\narchitecture=AArch64\nmodule-only=true\n')
        for change in ('none', 'elf', 'provenance', 'checksum', 'extra', 'symlink', 'manifest-link'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                header = b'\x7fELF\x02\x01' + bytes(10) + b'\x01\x00\xb7\x00'
                put(root, 'v4l2loopback.ko', header)
                put(root, 'module-build-info.txt', pins.encode())
                put(root, 'install-loopback.sh', b'mock module installer')
                manifest(root)
                if change == 'elf':
                    (root / 'v4l2loopback.ko').write_bytes(b'wrong architecture')
                    manifest(root)
                elif change == 'provenance':
                    (root / 'module-build-info.txt').write_text('wrong kernel')
                    manifest(root)
                elif change == 'checksum':
                    (root / 'v4l2loopback.ko').write_bytes(b'tampered')
                elif change == 'extra':
                    put(root, 'unexpected', b'extra')
                elif change == 'symlink':
                    (root / 'install-loopback.sh').unlink()
                    (root / 'install-loopback.sh').symlink_to(root / 'v4l2loopback.ko')
                elif change == 'manifest-link':
                    (root / 'SHA256SUMS').unlink()
                    (root / 'SHA256SUMS').symlink_to(root / 'module-build-info.txt')
                with mock.patch.object(sys, 'argv', ['-', str(root)]):
                    if change == 'none':
                        exec(compile(code, 'module-preflight-fixture.py', 'exec'), {})
                    else:
                        with self.assertRaises(SystemExit):
                            exec(compile(code, 'module-preflight-fixture.py', 'exec'), {})

    def test_final_sync_failure_reports_failure_and_same_kit_can_be_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            module, args, payload, generated, report = self.planned(work)
            kit, prefix = work / 'kit', args[1]
            module.assemble(payload, generated, kit)
            calls = 0
            def sync(command, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise subprocess.CalledProcessError(1, command, stderr='mock sync failure')
            with mock.patch.object(subprocess, 'run', side_effect=sync), \
                 mock.patch.object(sys, 'argv', ['-', str(kit), str(prefix)]):
                script = (ROOT / 'scripts/install-ciri-gstreamer.sh').read_text()
                code = script.split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
                with self.assertRaisesRegex(SystemExit, 'Streaming installation failed'):
                    exec(compile(code, 'streaming-sync-fixture.py', 'exec'), {})
            self.apply(kit, prefix)

    def test_plugin_allocation_error_fix_is_saved_and_module_build_is_module_only(self):
        patch = (ROOT / 'config/libcamera/ciri.patch').read_text()
        self.assertIn('if (ret <= 0)', patch)
        self.assertIn('g_object_unref(self)', patch)
        module = (ROOT / 'scripts/build-ciri-v4l2loopback.sh').read_text()
        self.assertIn('Module.symvers', module)
        self.assertIn('include/generated/autoconf.h', module)
        self.assertIn('ARCH=arm64 "${toolchain[@]}" modules', module)
        self.assertNotIn('modules_install', module)
        self.assertNotIn('modprobe ', module)
        self.assertIn('0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4', module)
        self.assertIn('export LOCALVERSION=-ciri', module)
        self.assertIn('${KERNEL_MAKE_ARGS:-CROSS_COMPILE=aarch64-linux-gnu-}', module)
        remote = (ROOT / 'scripts/build-ciri-v4l2loopback-remote.py').read_text()
        self.assertIn("'Target': '/work', 'ReadOnly': True", remote)
        self.assertIn("'NetworkMode': 'none'", remote)
        self.assertIn('core.hooksPath=/dev/null', remote)
        self.assertIn("records = output.parent / (output.name + '-build-records')", remote)


if __name__ == '__main__':
    unittest.main()
