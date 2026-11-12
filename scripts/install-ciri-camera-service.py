#!/usr/bin/python3
"""Operator-run service-only installation. Never loads a module or starts a camera."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile

CORE_HASH = 'db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d'
FILES = {
    'usr/local/lib/ciri-camera/service.py': 0o644,
    'etc/systemd/system/ciri-camera.service': 0o644,
    'etc/udev/rules.d/70-ciri-camera.rules': 0o644,
    'etc/ciri-camera.conf': 0o644,
}
CONFIG = 'etc/ciri-camera.conf'
RECEIPT = 'usr/local/lib/ciri-camera/SHA256SUMS'


def checked(root, name, missing=False):
    if (not name or name.startswith('/') or
            any(part in ('', '.', '..') for part in name.split('/')) or
            any(c in name for c in ('\\', '\r', '\n', '\0'))):
        raise ValueError('Unsafe inventory path: ' + name)
    path = root
    for component in name.split('/'):
        path /= component
        if path.is_symlink():
            raise ValueError('Symlink component refused: ' + str(path))
        if root == Path('/') and path.exists():
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Root-owned, non-writable installation paths required: ' + str(path))
    if path.exists() and not path.is_file():
        raise ValueError('Not a regular file: ' + str(path))
    if not missing and not path.is_file():
        raise ValueError('Missing file: ' + str(path))
    return path


def digest(path):
    with path.open('rb') as data:
        return hashlib.file_digest(data, 'sha256').hexdigest()


def manifest(root, name='SHA256SUMS'):
    rows = {}
    for line in checked(root, name).read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in rows:
            raise ValueError('Malformed/duplicate checksum row')
        checked(root, match[2], missing=True)
        rows[match[2]] = match[1]
    if not rows:
        raise ValueError('Empty manifest')
    return rows


def validate_runtime(root):
    prefix = root / 'opt/ciri-libcamera'
    if prefix.resolve() != prefix:
        raise ValueError('Symlinked runtime prefix refused')
    rows = manifest(prefix)
    required = {'run-libcamera', 'rootfs/usr/bin/ciri-libcamera-host',
                'rootfs/usr/bin/gst-launch-1.0', 'rootfs/usr/bin/gst-inspect-1.0',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstcoreelements.so',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstvideoconvertscale.so'}
    cores = {'rootfs/usr/lib/ciri-libcamera/' + name
             for name in ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0')}
    if not (required | cores).issubset(rows):
        raise ValueError('Incomplete installed streaming runtime')
    for name, sha in rows.items():
        if digest(checked(prefix, name)) != sha:
            raise ValueError('Installed runtime checksum mismatch: ' + name)
    if any(rows[name] != CORE_HASH for name in cores):
        raise ValueError('All core aliases must be the native-tested separate-plane core')


def install(source, root=Path('/')):
    source, root = Path(source).absolute(), Path(root).absolute()
    if source.resolve() != source or root.resolve() != root:
        raise ValueError('Use real, nonsymlinked source/root paths')
    kit = manifest(source)
    expected = set(FILES) | {'install-service.py'}
    actual = set()
    for path in source.rglob('*'):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError('Unexpected kit filesystem entry: ' + str(path))
        if path.is_file() and path != source / 'SHA256SUMS':
            actual.add(path.relative_to(source).as_posix())
    if set(kit) != expected or actual != expected:
        raise ValueError('Unexpected service-kit inventory')
    for name, sha in kit.items():
        if digest(checked(source, name)) != sha:
            raise ValueError('Kit checksum mismatch: ' + name)
    validate_runtime(root)
    if root == Path('/'):
        result = subprocess.run(['/usr/bin/systemctl', 'show', '--property=ActiveState',
                                 '--value', 'ciri-camera.service'], capture_output=True, text=True, check=True)
        if result.stdout.strip() not in ('inactive', 'failed'):
            raise ValueError('Stop ciri-camera.service successfully before installing/updating')
    receipt = checked(root, RECEIPT, missing=True)
    old = manifest(root, RECEIPT) if receipt.exists() else {}
    if old and set(old) != set(FILES):
        raise ValueError('Unknown installed service receipt')
    before, wanted, inputs = {}, {}, {}
    for name in FILES:
        path = checked(root, name, missing=True)
        current = digest(path) if path.exists() else None
        before[name] = current
        # Preserve an operator-edited configuration only when this installation
        # already owns it. Unknown initial collisions are never overwritten.
        if name == CONFIG and name in old and current is not None:
            wanted[name] = current
            continue
        if current is not None and current not in (old.get(name), kit[name]):
            raise ValueError('Unknown/tampered destination refused: ' + name)
        wanted[name] = kit[name]
        if current != kit[name]:
            inputs[name] = checked(source, name)
    before[RECEIPT] = digest(receipt) if receipt.exists() else None
    staged = []
    try:
        # Sync existing filesystems before creating any installation directory.
        for base in ('etc', 'usr/local/lib'):
            subprocess.run(['sync', '-f', str(root / base)], check=True)
        replacements = []
        for name, input_path in inputs.items():
            target = checked(root, name, missing=True)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            checked(root, name, missing=True)
            fd, temp = tempfile.mkstemp(prefix='.ciri-service-', dir=target.parent)
            staged.append(temp)
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), FILES[name])
                output.write(input_path.read_bytes())
                output.flush()
                os.fsync(output.fileno())
            if digest(Path(temp)) != wanted[name]:
                raise ValueError('Staged hash mismatch')
            replacements.append((name, temp))
        receipt.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.ciri-service-', dir=receipt.parent)
        staged.append(temp)
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(''.join(sha + '  ' + name + '\n' for name, sha in sorted(wanted.items())))
            output.flush()
            os.fsync(output.fileno())
        replacements.append((RECEIPT, temp))
        for name, temp in replacements:
            target = checked(root, name, missing=True)
            if (digest(target) if target.exists() else None) != before[name]:
                raise ValueError('Destination changed during staging: ' + name)
            os.replace(temp, target)
        for name, sha in wanted.items():
            if digest(checked(root, name)) != sha:
                raise ValueError('Final installed checksum mismatch: ' + name)
        for base in ('etc', 'usr/local/lib'):
            subprocess.run(['sync', '-f', str(root / base)], check=True)
    finally:
        for temp in staged:
            if os.path.exists(temp):
                os.unlink(temp)
    print('Installed service/config/udev rules only. Existing camera runtime and kernel untouched.')
    print('No module loading, camera start, service enable, backups or backup deletion performed.')
    print('Operator next: systemctl daemon-reload; udevadm control --reload-rules; systemctl enable --now ciri-camera.service')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.machine() != 'aarch64':
        parser.error('Run with sudo on Ciri, not the build host')
    install(args.kit)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit('Service installation failed; repair with this same kit before enabling: ' + str(error))
