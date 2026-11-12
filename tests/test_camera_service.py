"""Service tests use synthetic ioctls/processes/files only, never cameras or recovered code."""
import contextlib
import errno
import fcntl
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sums(root):
    (root / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(root).as_posix() + '\n'
        for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))


class CameraServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = load('ciri-camera-service.py')

    def test_ioctl_abi_matches_native_linux_headers(self):
        code = r'''
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <stddef.h>
#include <stdio.h>
int main(void) {
 printf("%lu %lu %lu %lu %lu %lu %zu %zu\n",
 (unsigned long)VIDIOC_QUERYCAP, (unsigned long)VIDIOC_S_FMT,
 (unsigned long)VIDIOC_S_PARM, (unsigned long)VIDIOC_S_CTRL,
 (unsigned long)VIDIOC_DQEVENT, (unsigned long)VIDIOC_SUBSCRIBE_EVENT,
 offsetof(struct v4l2_format, fmt), offsetof(struct v4l2_event, u));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'abi-fixture'
            subprocess.run(['cc', '-x', 'c', '-', '-o', str(binary)], input=code, text=True, check=True)
            actual = subprocess.check_output([str(binary)], text=True)
        s = self.service
        self.assertEqual(list(map(int, actual.split())),
                         [s.QUERYCAP, s.S_FMT, s.S_PARM, s.S_CTRL, s.DQEVENT, s.SUBSCRIBE, 8, 8])
        self.assertEqual(len(s.loopback_config()), 72)
        self.assertEqual(struct.unpack_from('=ii', s.loopback_config()), (-1, -1))
        self.assertEqual(struct.unpack_from('=4I4i', s.loopback_config(), 40),
                         (640, 640, 480, 480, 4, 10, 0, 0))

    def test_format_and_rate_validation_precede_placeholder_output(self):
        s = self.service
        for bad in ('none', 'layout', 'rate'):
            calls = []
            def ioctl(fd, request, data):
                calls.append(request)
                if request == s.S_FMT and bad == 'layout':
                    struct.pack_into('=I', data, 8, 320)
                if request == s.S_PARM and bad == 'rate':
                    struct.pack_into('=I', data, 16, 30)
            with self.subTest(bad=bad), mock.patch.object(fcntl, 'ioctl', side_effect=ioctl):
                if bad == 'none':
                    s.configure_output(5, 18)
                    self.assertEqual(calls, [s.S_FMT, s.S_PARM, s.S_CTRL, s.S_CTRL, s.S_CTRL])
                else:
                    with self.assertRaises(ValueError):
                        s.configure_output(5, 18)
                    self.assertNotIn(s.S_CTRL, calls)
        with mock.patch.object(os, 'write', return_value=len(s.FRAME)) as write:
            s.black(5, 4)
            self.assertEqual(write.call_count, 4)
            self.assertEqual(len(write.call_args.args[1]), 614400)
        with mock.patch.object(os, 'write', return_value=1), self.assertRaisesRegex(OSError, 'Short'):
            s.black(5)

    def test_actual_driver_and_label_guard(self):
        for driver, card, accepted in ((b'v4l2 loopback', b'Ciri rear camera', True),
                                       (b'mtk-cam', b'Ciri rear camera', False),
                                       (b'v4l2 loopback', b'other', False)):
            def ioctl(fd, request, data):
                data[:len(driver)] = driver
                data[16:16 + len(card)] = card
            with self.subTest(driver=driver, card=card), mock.patch.object(fcntl, 'ioctl', side_effect=ioctl):
                if accepted:
                    self.service.capability(5, 'Ciri rear camera')
                else:
                    with self.assertRaises(ValueError):
                        self.service.capability(5, 'Ciri rear camera')
        with mock.patch.object(os, 'open', return_value=9) as opened, \
             mock.patch.object(os, 'fstat', return_value=mock.Mock(st_mode=0o100644)), \
             mock.patch.object(os, 'close') as close, self.assertRaises(ValueError):
            self.service.open_character('/dev/video115')
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        close.assert_called_once_with(9)

    def test_initial_and_merged_capture_events_not_open_count(self):
        s = self.service
        events = iter([(s.USAGE_EVENT, 1), (s.USAGE_EVENT, 0)])
        def ioctl(fd, request, data):
            try:
                event, count = next(events)
            except StopIteration:
                raise BlockingIOError(errno.EAGAIN, 'fixture empty queue')
            struct.pack_into('=I', data, 0, event)
            struct.pack_into('=I', data, 8, count)
        with mock.patch.object(fcntl, 'ioctl', side_effect=ioctl):
            self.assertIs(s.drain_usage(5), False)
        with mock.patch.object(fcntl, 'ioctl') as subscribed:
            s.subscribe(5)
            self.assertEqual(struct.unpack_from('=III', subscribed.call_args.args[2]),
                             (s.USAGE_EVENT, 0, 1))
        def invalid(fd, request, data):
            struct.pack_into('=I', data, 0, s.USAGE_EVENT)
            struct.pack_into('=I', data, 8, 20)
        with mock.patch.object(fcntl, 'ioctl', side_effect=invalid), self.assertRaises(ValueError):
            s.drain_usage(5)

    def test_native_empty_event_queue_enoent_and_other_errors(self):
        s = self.service
        for empty_errno in (errno.ENOENT, errno.EAGAIN):
            for states in ([], [1], [1, 0], [0, 1]):
                events = iter(states)
                def ioctl(fd, request, data):
                    self.assertEqual(request, s.DQEVENT)
                    try:
                        count = next(events)
                    except StopIteration:
                        raise OSError(empty_errno, 'fixture empty event queue')
                    struct.pack_into('=I', data, 0, s.USAGE_EVENT)
                    struct.pack_into('=I', data, 8, count)
                with self.subTest(errno=empty_errno, states=states), \
                     mock.patch.object(fcntl, 'ioctl', side_effect=ioctl):
                    self.assertIs(s.drain_usage(5), bool(states[-1]) if states else None)
        for failure in (errno.EIO, errno.EINVAL, errno.ENODEV):
            with self.subTest(errno=failure), \
                 mock.patch.object(fcntl, 'ioctl', side_effect=OSError(failure, 'fixture real failure')), \
                 self.assertRaises(OSError) as caught:
                s.drain_usage(5)
            self.assertEqual(caught.exception.errno, failure)

    def endpoints(self):
        return {name: mock.Mock(camera=name, fd=fd, wanted=False, failed=False,
                                requested_at=0, device=f'/dev/video{fd}')
                for fd, name in ((5, 'back'), (6, 'front'))}

    def test_idle_no_capture_single_isp_first_client_and_handover(self):
        s = self.service
        endpoints = self.endpoints()
        child = mock.Mock(pid=200, returncode=0)
        child.poll.return_value = None
        spawn = mock.Mock(return_value=child)
        demand = s.Demand(endpoints, spawn=spawn)
        with mock.patch.object(s, 'black') as black, mock.patch.object(s, 'stop_process') as stopped:
            demand.tick(0)
            spawn.assert_not_called()
            endpoints['front'].wanted = True
            endpoints['front'].requested_at = 1
            demand.tick(1)
            self.assertEqual(demand.active, 'front')
            self.assertEqual(spawn.call_args.kwargs['pass_fds'], (6,))
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertIn('fd=6', spawn.call_args.args[0])
            endpoints['back'].wanted = True
            endpoints['back'].requested_at = 2
            black.reset_mock()
            demand.tick(2)
            self.assertEqual(spawn.call_count, 1)
            self.assertEqual(demand.status()['cameras']['back']['state'], 'waiting-for-isp')
            black.assert_called_once_with(5)
            endpoints['front'].wanted = False
            demand.tick(3)
            demand.tick(4)
            stopped.assert_not_called()
            demand.tick(5)
            stopped.assert_called_once_with(child)
            self.assertEqual(demand.active, 'back')
            self.assertEqual(spawn.call_count, 2)
            self.assertEqual(spawn.call_args.kwargs['pass_fds'], (5,))
            self.assertIn(mock.call(6, 4), black.call_args_list)

    def test_reopen_debounce_failure_no_restart_spin(self):
        s = self.service
        endpoints = self.endpoints()
        child = mock.Mock(pid=200, returncode=1)
        child.poll.return_value = None
        spawn = mock.Mock(return_value=child)
        demand = s.Demand(endpoints, spawn=spawn)
        with mock.patch.object(s, 'black'), mock.patch.object(s, 'stop_process'):
            endpoints['back'].wanted = True
            demand.tick(0)
            endpoints['back'].wanted = False
            demand.tick(1)
            endpoints['back'].wanted = True
            demand.tick(2)
            self.assertEqual(spawn.call_count, 1)
            child.poll.return_value = 1
            demand.tick(3)
            demand.tick(4)
            self.assertIsNone(demand.active)
            self.assertTrue(endpoints['back'].failed)
            self.assertEqual(spawn.call_count, 1)
            endpoints['front'].wanted = True
            demand.tick(5)
            self.assertEqual(demand.active, 'front')
        broken = s.Demand(self.endpoints(), spawn=mock.Mock(side_effect=OSError('fixture spawn failed')))
        broken.endpoints['back'].wanted = True
        with mock.patch.object(s, 'black'):
            broken.tick(0)
            broken.tick(1)
            self.assertTrue(broken.endpoints['back'].failed)
            self.assertIsNone(broken.child)
            self.assertEqual(broken.spawn.call_count, 1)

    def test_stop_signals_supervisor_before_group_and_kills_owned_group_on_timeout(self):
        s = self.service
        child = mock.Mock(pid=123)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture', 10), 0]
        with mock.patch.object(os, 'killpg') as kill:
            s.stop_process(child)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(kill.call_args.args, (123, signal.SIGKILL))

    def test_standard_pipeline_private_environment_and_both_defaults(self):
        s = self.service
        for name, fd in (('back', 5), ('front', 6)):
            command = s.pipeline(name, fd, 18)
            self.assertIn('libcamerasrc', command)
            self.assertIn('fdsink', command)
            self.assertIn('camera-name=' + s.CAMERAS[name][1], command)
            self.assertNotIn('v4l2sink', command)
            self.assertNotIn('cam', command)
        with mock.patch.dict(os.environ, {'CIRI_P1_DUMP_CONFIG': 'must-not-leak', 'NOTIFY_SOCKET': '@fixture'}):
            self.assertNotIn('CIRI_P1_DUMP_CONFIG', s.environment())
            self.assertNotIn('NOTIFY_SOCKET', s.environment())
        config = (ROOT / 'config/camera/ciri-camera.conf').read_text()
        self.assertIn('CIRI_CAMERAS=back,front', config)
        unit = (ROOT / 'config/camera/ciri-camera.service').read_text()
        self.assertIn('KillMode=mixed', unit)
        for command in ('modprobe v4l2loopback devices=0', 'modprobe mtk_imgsys', 'modprobe mtk-aie'):
            self.assertIn('ExecStartPre=/usr/bin/' + command + '\n', unit)
            self.assertLess(unit.index(command), unit.index('ExecStart='))
        self.assertNotIn('PrivateDevices=', unit)
        subprocess.run(['systemd-analyze', 'verify', str(ROOT / 'config/camera/ciri-camera.service')], check=True)

    def test_endpoint_holds_output_fd_and_publishes_only_after_priming(self):
        s = self.service
        order = []
        def ioctl(fd, request, data):
            if request == s.S_CTRL:
                self.assertEqual((fd, struct.unpack('=Ii', data)), (9, (0x0098F900, 0)))
                return 0
            self.assertEqual((fd, request), (4, s.CTL_ADD))
            self.assertEqual(bytes(data[8:40]).split(b'\0', 1)[0], b'Ciri front camera')
            return 117  # Kernel-returned node, not a guessed video number.
        with mock.patch.object(Path, 'glob', return_value=[]), \
             mock.patch.object(Path, 'exists', return_value=True), \
             mock.patch.object(fcntl, 'ioctl', side_effect=ioctl), \
             mock.patch.object(s, 'open_character', return_value=9), \
             mock.patch.object(s, 'capability'), mock.patch.object(s, 'configure_output'), \
             mock.patch.object(s, 'black', side_effect=lambda *args: order.append('prime')), \
             mock.patch.object(s, 'subscribe', side_effect=lambda *args: order.append('subscribe')), \
             mock.patch.object(s, 'drain_usage', return_value=False), \
             mock.patch.object(subprocess, 'run', side_effect=lambda *args, **kwargs: order.append('publish')), \
             mock.patch.object(os, 'close') as close:
            endpoint = s.Endpoint(4, 'front', 18)
            self.assertEqual(endpoint.device, '/dev/video117')
            self.assertEqual(order, ['prime', 'subscribe', 'publish'])
            close.assert_not_called()
            endpoint.close()
            close.assert_called_once_with(9)

    def test_profile_one_core_units_and_rejects_lifecycle_change(self):
        s = self.service
        status = {'pid': 100, 'backend_pid': 200, 'active': 'front'}
        import json
        with mock.patch.object(Path, 'read_text', return_value=json.dumps(status)), \
             mock.patch.object(s, 'cpu_snapshot', side_effect=[{(100, 1): 20, (200, 2): 30},
                                                             {(100, 1): 21, (200, 2): 39}]), \
             mock.patch.object(time := s.time, 'monotonic', side_effect=[0, 2]), \
             mock.patch.object(time, 'sleep'), mock.patch.object(os, 'sysconf', return_value=100), \
             contextlib.redirect_stdout(output := io.StringIO()):
            s.profile(2)
        self.assertEqual(json.loads(output.getvalue())['cpu_percent_one_core'], 5)
        with mock.patch.object(Path, 'read_text', side_effect=[json.dumps(status),
                                                             json.dumps({**status, 'active': 'back'})]), \
             mock.patch.object(s, 'cpu_snapshot', return_value={(100, 1): 20}), \
             mock.patch.object(time, 'monotonic', side_effect=[0, 2]), mock.patch.object(time, 'sleep'), \
             self.assertRaisesRegex(ValueError, 'lifecycle changed'):
            s.profile(2)

    def install_fixture(self, root):
        installer = load('install-ciri-camera-service.py')
        kit, target = root / 'kit', root / 'target'
        target.mkdir()
        (target / 'etc').mkdir()
        (target / 'usr/local/lib').mkdir(parents=True)
        sources = {
            'usr/local/lib/ciri-camera/service.py': 'scripts/ciri-camera-service.py',
            'etc/systemd/system/ciri-camera.service': 'config/camera/ciri-camera.service',
            'etc/udev/rules.d/70-ciri-camera.rules': 'config/camera/70-ciri-camera.rules',
            'etc/ciri-camera.conf': 'config/camera/ciri-camera.conf',
            'install-service.py': 'scripts/install-ciri-camera-service.py',
        }
        for name, source in sources.items():
            path = kit / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / source).read_bytes())
        sums(kit)
        runtime = target / 'opt/ciri-libcamera'
        names = ('run-libcamera', 'rootfs/usr/bin/ciri-libcamera-host',
                 'rootfs/usr/bin/gst-launch-1.0', 'rootfs/usr/bin/gst-inspect-1.0',
                 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so',
                 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstcoreelements.so',
                 'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstvideoconvertscale.so',
                 'rootfs/usr/lib/ciri-libcamera/libcamera.so',
                 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1',
                 'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0')
        for name in names:
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture runtime untouched')
        sums(runtime)
        installer.CORE_HASH = hashlib.sha256(b'fixture runtime untouched').hexdigest()
        return installer, kit, target

    def test_service_install_preserves_runtime_and_edited_config_and_repairs_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, kit, root = self.install_fixture(Path(directory))
            runtime = root / 'opt/ciri-libcamera'
            before = {p: p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
            real_replace = os.replace
            def replace(source, target):
                if str(target).endswith(installer.RECEIPT):
                    raise OSError('fixture receipt failure')
                return real_replace(source, target)
            with mock.patch.object(subprocess, 'run'), mock.patch.object(os, 'replace', side_effect=replace):
                with self.assertRaisesRegex(OSError, 'receipt failure'):
                    installer.install(kit, root)
            with mock.patch.object(subprocess, 'run') as commands, contextlib.redirect_stdout(io.StringIO()):
                installer.install(kit, root)
                config = root / installer.CONFIG
                config.write_text('CIRI_CAMERAS=front\nCIRI_FPS=18\nCIRI_IDLE_SECONDS=2\n')
                installer.install(kit, root)
                self.assertIn('CIRI_CAMERAS=front\n', config.read_text())
                self.assertTrue(all(call.args[0][0] == 'sync' for call in commands.call_args_list))
            self.assertEqual(before, {p: p.read_bytes() for p in runtime.rglob('*') if p.is_file()})
            self.assertFalse(list(root.rglob('.ciri-service-*')))

    def test_install_refuses_unknown_files_tamper_and_symlinks_before_writes(self):
        for kind in ('kit', 'runtime', 'collision', 'symlink', 'inventory', 'manifest'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                installer, kit, root = self.install_fixture(Path(directory))
                if kind == 'kit':
                    (kit / 'install-service.py').write_text('tampered')
                elif kind == 'runtime':
                    (root / 'opt/ciri-libcamera/run-libcamera').write_text('tampered')
                elif kind == 'collision':
                    (root / 'etc/ciri-camera.conf').write_text('unknown existing file')
                elif kind == 'symlink':
                    (root / 'etc/ciri-camera.conf').symlink_to(kit / 'install-service.py')
                elif kind == 'inventory':
                    (kit / 'extra').write_text('unexpected')
                else:
                    (kit / 'SHA256SUMS').write_text('0' * 64 + '  ../escape\n')
                before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
                with mock.patch.object(subprocess, 'run') as run, self.assertRaises(ValueError):
                    installer.install(kit, root)
                run.assert_not_called()
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_sync_failure_is_not_bypassed(self):
        for failing_call in (1, 4):
            with self.subTest(call=failing_call), tempfile.TemporaryDirectory() as directory:
                installer, kit, root = self.install_fixture(Path(directory))
                calls = 0
                def sync(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failing_call:
                        raise subprocess.CalledProcessError(1, 'sync')
                with mock.patch.object(subprocess, 'run', side_effect=sync), \
                     self.assertRaises(subprocess.CalledProcessError):
                    installer.install(kit, root)
                with mock.patch.object(subprocess, 'run'), contextlib.redirect_stdout(io.StringIO()):
                    installer.install(kit, root)


if __name__ == '__main__':
    unittest.main()
