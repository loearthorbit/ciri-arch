#!/usr/bin/python3
"""Ciri loopback lifecycle only: standard GStreamer owns all camera capture.

The pinned v4l2loopback 0.15.4 private event reports capture STREAMON/OFF,
not descriptor opens. A held OUTPUT descriptor keeps exclusive CAPTURE
advertisement and format negotiation working while the physical ISP is off.
"""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import platform
import select
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import time

CAMERAS = {
    'back': ('Ciri rear camera', '/base/soc/i2c@11ec1000/camera-main-sensor@31'),
    'front': ('Ciri front camera', '/base/soc/i2c@11ec0000/camera-sub-sensor@37'),
}
LAUNCHER = '/opt/ciri-libcamera/run-libcamera'
RUN = Path('/run/ciri-camera')
FRAME = bytes((16, 128, 16, 128)) * (640 * 480 // 2)
YUYV = int.from_bytes(b'YUYV', 'little')
USAGE_EVENT = 0x08000000 + 0x08E00000 + 1


def ioctl_number(direction, kind, number, size):
    # Linux asm-generic ioctl ABI, shared by native ARM64 and x86-64 fixtures.
    return direction << 30 | size << 16 | ord(kind) << 8 | number


QUERYCAP = ioctl_number(2, 'V', 0, 104)
S_FMT = ioctl_number(3, 'V', 5, 208)
S_PARM = ioctl_number(3, 'V', 22, 204)
S_CTRL = ioctl_number(3, 'V', 28, 8)
DQEVENT = ioctl_number(2, 'V', 89, 136)
SUBSCRIBE = ioctl_number(1, 'V', 90, 32)
CTL_VERSION = ioctl_number(2, '~', 0, 4)
CTL_ADD = ioctl_number(1, '~', 1, 72)
CTL_QUERY = ioctl_number(3, '~', 3, 72)


def log(message):
    print(message, flush=True)


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
                channel.sendto(message.encode(), '\0' + address[1:] if address.startswith('@') else address)
        except OSError as error:
            # Notification failure must never bypass owned-child cleanup.
            log('Systemd notification failed: ' + str(error))


def open_character(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    if not stat.S_ISCHR(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Not a character device: ' + str(path))
    return fd


def loopback_config(number=-1, label=''):
    return bytearray(struct.pack('=ii32sIIIIiiii', number, -1, label.encode(),
                                 640, 640, 480, 480, 4, 10, 0, 0))


def capability(fd, label):
    data = bytearray(104)
    fcntl.ioctl(fd, QUERYCAP, data)
    driver = data[:16].split(b'\0', 1)[0]
    card = data[16:48].split(b'\0', 1)[0]
    if driver != b'v4l2 loopback' or card != label.encode():
        raise ValueError('Refusing a non-loopback or incorrectly labelled video node')


def configure_output(fd, fps):
    fmt = bytearray(208)
    struct.pack_into('=I', fmt, 0, 2)  # V4L2_BUF_TYPE_VIDEO_OUTPUT
    struct.pack_into('=8I', fmt, 8, 640, 480, YUYV, 1, 1280, len(FRAME), 1, 0)
    fcntl.ioctl(fd, S_FMT, fmt)
    if struct.unpack_from('=6I', fmt, 8) != (640, 480, YUYV, 1, 1280, len(FRAME)):
        raise ValueError('Loopback refused the fixed 640x480 YUYV layout')
    parm = bytearray(204)
    struct.pack_into('=5I', parm, 0, 2, 0, 0, 1, fps)
    fcntl.ioctl(fd, S_PARM, parm)
    if struct.unpack_from('=II', parm, 12) != (1, fps):
        raise ValueError('Loopback refused the requested frame interval')
    # Pin format; do not run an idle frame-duplication or stale-image timer.
    for control, value in ((0x0098F900, 1), (0x0098F901, 0), (0x0098F902, 0)):
        fcntl.ioctl(fd, S_CTRL, bytearray(struct.pack('=Ii', control, value)))


def black(fd, count=1):
    # Placeholder bytes only. Never read, allocate or process physical-camera frames.
    for _ in range(count):
        if os.write(fd, FRAME) != len(FRAME):
            raise OSError('Short loopback placeholder write')


def subscribe(fd):
    data = bytearray(32)
    struct.pack_into('=III', data, 0, USAGE_EVENT, 0, 1)  # SEND_INITIAL
    fcntl.ioctl(fd, SUBSCRIBE, data)


def drain_usage(fd):
    """Drain merged state notifications; the final Boolean wins."""
    wanted = None
    while True:
        data = bytearray(136)
        try:
            fcntl.ioctl(fd, DQEVENT, data)
        except OSError as error:
            # Linux __v4l2_event_dequeue returns ENOENT for an empty queue
            # on the nonblocking path. Some implementations use EAGAIN.
            # Handle this ONLY for DQEVENT, not for missing files elsewhere.
            if error.errno in (errno.ENOENT, errno.EAGAIN):
                return wanted
            raise
        if struct.unpack_from('=I', data)[0] == USAGE_EVENT:
            count = struct.unpack_from('=I', data, 8)[0]
            if count not in (0, 1):
                raise ValueError('Unexpected loopback client-usage ABI')
            wanted = bool(count)


def pipeline(camera, fd, fps):
    # fdsink is a standard coreelements sink. The parent holds the SAME open
    # file description throughout handover; reopening /dev/videoN would lose it.
    return [LAUNCHER, '--gst-launch', '-e', 'libcamerasrc',
            'camera-name=' + CAMERAS[camera][1], '!',
            f'video/x-raw,format=NV12,width=640,height=480,framerate={fps}/1', '!',
            'queue', 'max-size-buffers=4', 'max-size-bytes=0', 'max-size-time=0',
            'leaky=downstream', '!', 'videoconvert', '!',
            'video/x-raw,format=YUY2', '!', 'fdsink', f'fd={fd}', 'sync=false']


def environment():
    env = os.environ.copy()
    env.pop('CIRI_P1_DUMP_CONFIG', None)
    env.pop('NOTIFY_SOCKET', None)
    env['LIBCAMERA_LOG_LEVELS'] = '*:WARN'
    return env


def stop_process(process, timeout=10):
    # Only the supervisor receives INT first; it forwards to gst-launch and
    # reaps the IPA afterwards. INT to the entire group would kill the IPA early.
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log('Camera shutdown timed out; killing only its owned process group')
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    # Also reap any descendants left by an abnormal supervisor exit.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def inspect_runtime(stopping, timeout=30):
    # Enumerate once, BEFORE producers. Never poll CameraManager while another
    # camera is active: pipeline matching configures shared media links.
    for arguments in (['--list'], ['--gst-inspect', 'fdsink']):
        with tempfile.TemporaryFile(dir=RUN) as output:
            child = subprocess.Popen([LAUNCHER, *arguments], stdout=output,
                                     stderr=subprocess.STDOUT, env=environment(),
                                     start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    if stopping() or time.monotonic() >= deadline:
                        raise RuntimeError('Interrupted/timed-out runtime preflight')
                    time.sleep(0.05)
                output.seek(0)
                text = output.read().decode(errors='replace')
                if child.returncode:
                    raise RuntimeError('Runtime preflight failed:\n' + text[-8000:])
                if arguments == ['--list']:
                    available = {name for name, (_, identity) in CAMERAS.items() if identity in text}
                    if not available:
                        return available
                elif 'fdsink' not in text:
                    raise RuntimeError('The isolated GStreamer runtime has no fdsink')
            finally:
                stop_process(child)
    return available


class Endpoint:
    def __init__(self, control, camera, fps):
        self.camera = camera
        self.fd = None
        self.configured = False
        self.wanted = False
        self.failed = False
        self.requested_at = 0.0
        label = CAMERAS[camera][0]
        matches = [path.parent.name for path in Path('/sys/class/video4linux').glob('video*/name')
                   if path.read_text().strip() == label]
        if len(matches) > 1:
            raise ValueError('Duplicate managed loopback label: ' + label)
        if matches:
            number = int(matches[0].removeprefix('video'))
            conf = loopback_config(number)
            fcntl.ioctl(control, CTL_QUERY, conf)
            if (struct.unpack_from('=4I', conf, 40) != (640, 640, 480, 480) or
                    struct.unpack_from('=ii', conf, 56) != (4, 10) or
                    struct.unpack_from('=i', conf, 68)[0] != 0):
                raise ValueError('Managed node has unexpected dimensions/buffers/exclusivity')
        else:
            number = fcntl.ioctl(control, CTL_ADD, loopback_config(label=label))
        self.device = '/dev/video' + str(number)
        deadline = time.monotonic() + 5
        while not Path(self.device).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('New loopback device node did not appear')
            time.sleep(0.05)
        try:
            self.fd = open_character(self.device)
            capability(self.fd, label)
            configure_output(self.fd, fps)
            self.configured = True
            black(self.fd, 4)  # Own OUTPUT STREAMON before applications negotiate.
            subscribe(self.fd)
            self.update(time.monotonic())
            # Device-add can precede priming. Publish a change after CAPTURE is
            # usable, so browser/PipeWire discovery isn't stranded at OUTPUT.
            subprocess.run(['/usr/bin/udevadm', 'trigger', '--action=change',
                            '/sys/class/video4linux/video' + str(number)], check=True)
        except BaseException:
            self.close()
            raise

    def update(self, now):
        wanted = drain_usage(self.fd)
        if wanted is not None:
            if wanted and not self.wanted:
                self.requested_at = now
            if not wanted:
                self.failed = False
            self.wanted = wanted

    def close(self):
        if self.fd is not None:
            try:
                if self.configured:
                    # Release retention only after claiming our own OUTPUT;
                    # never change another producer's controls on preflight failure.
                    fcntl.ioctl(self.fd, S_CTRL, bytearray(struct.pack('=Ii', 0x0098F900, 0)))
            except OSError as error:
                log('Cannot release managed loopback retention: ' + str(error))
            finally:
                os.close(self.fd)
                self.fd = None
        # Do not remove nodes or unload modules: external applications may still
        # own capture FDs. Managed labels are safely reused on the next start.


class Demand:
    def __init__(self, endpoints, fps=18, idle_seconds=2, spawn=subprocess.Popen):
        self.endpoints = endpoints
        self.fps = fps
        self.idle_seconds = idle_seconds
        self.spawn = spawn
        self.active = None
        self.child = None
        self.idle_since = None

    def stop(self):
        if self.child is not None:
            stop_process(self.child)
            self.child = None
            black(self.endpoints[self.active].fd, 4)  # Clear every output-ring slot.
            log('Physical camera stopped: ' + self.active)
        self.active = None
        self.idle_since = None

    def tick(self, now):
        was_running = self.child is not None
        if self.child is not None:
            current = self.endpoints[self.active]
            if self.child.poll() is not None:
                log(f'Capture failed for {self.active}: exit {self.child.returncode}; reopen client to retry')
                current.failed = True
                self.stop()
            elif current.wanted:
                self.idle_since = None
            else:
                if self.idle_since is None:
                    self.idle_since = now
                if now - self.idle_since >= self.idle_seconds:
                    self.stop()
        if self.child is None:
            if was_running:
                # Shutdown may take seconds. Refresh queued demand before
                # handing the ISP to a client that might already have closed.
                for endpoint in self.endpoints.values():
                    endpoint.update(time.monotonic())
            pending = [endpoint for endpoint in self.endpoints.values()
                       if endpoint.wanted and not endpoint.failed]
            if pending:
                selected = min(pending, key=lambda endpoint: endpoint.requested_at)
                self.active = selected.camera
                try:
                    self.child = self.spawn(pipeline(self.active, selected.fd, self.fps),
                                            pass_fds=(selected.fd,), env=environment(),
                                            start_new_session=True)
                    log('Physical camera starting: ' + self.active)
                except OSError as error:
                    log(f'Cannot start {self.active}: {error}')
                    selected.failed = True
                    self.active = None
        # At most one writer per endpoint. No parent placeholder writes race
        # the real pipeline; inactive endpoints get one cached black frame/sec.
        for name, endpoint in self.endpoints.items():
            if name != self.active:
                black(endpoint.fd)

    def status(self):
        return {'pid': os.getpid(), 'active': self.active,
                'backend_pid': self.child.pid if self.child else None,
                'requested_fps': self.fps, 'idle_seconds': self.idle_seconds,
                'cameras': {name: {'device': endpoint.device, 'wanted': endpoint.wanted,
                                  'state': 'failed' if endpoint.failed else
                                  'capturing' if name == self.active else
                                  'waiting-for-isp' if endpoint.wanted else 'idle'}
                            for name, endpoint in self.endpoints.items()}}


def write_status(data):
    fd, temp = tempfile.mkstemp(prefix='.status-', dir=RUN)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), 0o644)
            json.dump(data, output, indent=2)
            output.write('\n')
        os.replace(temp, RUN / 'status.json')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def serve(cameras, fps, idle_seconds):
    if os.geteuid() != 0 or platform.machine() != 'aarch64' or struct.calcsize('P') != 8:
        raise ValueError('Run the system service as root on ARM64 Ciri')
    RUN.mkdir(mode=0o755, exist_ok=True)
    if RUN.is_symlink() or RUN.stat().st_uid != 0 or RUN.stat().st_mode & 0o022:
        raise ValueError('Unsafe service runtime directory')
    lock = os.open(RUN / 'producer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    endpoints = {}
    demand = None
    control = None
    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        control = open_character('/dev/v4l2loopback')
        version = bytearray(4)
        fcntl.ioctl(control, CTL_VERSION, version)
        if struct.unpack('=I', version)[0] != (15 << 8 | 4):
            raise ValueError('This event/control ABI requires v4l2loopback 0.15.4')
        deadline = time.monotonic() + 45
        while True:
            available = inspect_runtime(lambda: stopping)
            missing = set(cameras) - available
            if not missing:
                break
            if stopping:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Requested physical cameras not registered: ' + ','.join(sorted(missing)))
            notify('STATUS=Waiting for physical camera registration')
            time.sleep(1)
        for camera in cameras:
            if stopping:
                return
            endpoints[camera] = Endpoint(control, camera, fps)
        demand = Demand(endpoints, fps, idle_seconds)
        poller = select.poll()
        for endpoint in endpoints.values():
            poller.register(endpoint.fd, select.POLLPRI | select.POLLERR | select.POLLHUP)
        write_status(demand.status())
        notify('READY=1\nSTATUS=Idle; physical cameras off')
        next_tick = time.monotonic()
        while not stopping:
            events = poller.poll(max(0, int((next_tick - time.monotonic()) * 1000)))
            now = time.monotonic()
            for fd, flags in events:
                if flags & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    raise RuntimeError('Loopback device disconnected')
            # Drain all nodes before scheduling; merged OFF/ON events should
            # not leave stale demand or start a camera for a discovery probe.
            for endpoint in endpoints.values():
                endpoint.update(now)
            if events or now >= next_tick:
                demand.tick(now)
                write_status(demand.status())
                notify('STATUS=' + ('Capturing ' + demand.active if demand.active else 'Idle; physical cameras off'))
                next_tick = now + 1
    finally:
        notify('STOPPING=1')
        try:
            if demand:
                demand.stop()
                write_status(demand.status())
        finally:
            for endpoint in endpoints.values():
                endpoint.close()
            if control is not None:
                os.close(control)
            os.close(lock)
            # RuntimeDirectory is systemd-owned. Leave status readable for an
            # operator while stopping, rather than touching retained captures.


def cpu_snapshot(service_pid, backend_pid):
    """Read-only /proc accounting; never includes the viewer or unrelated tasks."""
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / 'stat').read_text()
            fields = text[text.rfind(')') + 2:].split()
            pid = int(entry.name)
            if pid == service_pid or (backend_pid and int(fields[3]) == backend_pid):
                result[(pid, int(fields[19]))] = int(fields[11]) + int(fields[12])
        except (OSError, ValueError, IndexError):
            continue
    return result


def profile(seconds):
    status = json.loads((RUN / 'status.json').read_text())
    service_pid, backend_pid = status['pid'], status['backend_pid']
    before = cpu_snapshot(service_pid, backend_pid)
    start = time.monotonic()
    time.sleep(seconds)
    after = cpu_snapshot(service_pid, backend_pid)
    elapsed = time.monotonic() - start
    end_status = json.loads((RUN / 'status.json').read_text())
    if (end_status['pid'], end_status['backend_pid'], end_status['active']) != (
            service_pid, backend_pid, status['active']):
        raise ValueError('Camera lifecycle changed during profile; rerun in steady state')
    if before.keys() != after.keys() or not before:
        raise ValueError('Processes changed/disappeared during profile; rerun in steady state')
    ticks = sum(after[key] - before[key] for key in before)
    print(json.dumps({'camera': status['active'], 'sample_seconds': elapsed,
                      'cpu_percent_one_core': 100 * ticks / os.sysconf('SC_CLK_TCK') / elapsed,
                      'linux_logical_cpus': os.cpu_count(), 'processes': len(after),
                      'includes': 'service + capture supervisor/GStreamer/IPA process session',
                      'excludes': 'viewer, kernel threads, ISP/SCP power and other applications'}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cameras', default='back,front', choices=('back', 'front', 'back,front'))
    parser.add_argument('--fps', type=int, choices=range(15, 31), default=18)
    parser.add_argument('--idle-seconds', type=float, default=2)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--profile', type=int, choices=range(1, 61), metavar='SECONDS')
    args = parser.parse_args()
    if args.profile:
        profile(args.profile)
        return
    if args.status:
        print((RUN / 'status.json').read_text(), end='')
        return
    if not 0 <= args.idle_seconds <= 30:
        parser.error('idle-seconds must be between 0 and 30')
    serve(args.cameras.split(','), args.fps, args.idle_seconds)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        # Preserve the failing call in the journal, not just a bare errno.
        import traceback
        traceback.print_exc()
        raise SystemExit('Camera service failed: ' + str(error))
