#!/bin/bash
# Operator-run add-on installation into a verified existing isolated runtime.
set -euo pipefail
[[ $EUID == 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
[[ $# -le 2 ]] || { printf 'Usage: %s [ADDON_DIRECTORY [RUNTIME_PREFIX]]\n' "$0" >&2; exit 2; }
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
prefix=${2:-/opt/ciri-libcamera}
python3 -I - "$source" "$prefix" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HOST = 'rootfs/usr/bin/ciri-libcamera-host'
MUTABLE_BASE = {HOST, 'run-libcamera'}
source = Path(sys.argv[1]).resolve(strict=True)
requested = Path(sys.argv[2]).absolute()
prefix = requested.resolve(strict=True)
if prefix != requested or source == prefix:
    raise SystemExit('Use a separate add-on directory and a real, nonsymlinked runtime prefix.')


def safe_name(name):
    if (not name or name.startswith('/') or '\\' in name or
            any(part in ('', '.', '..') for part in name.split('/')) or
            any(c in name for c in ('\r', '\n', '\0'))):
        raise ValueError('Unsafe relative path: ' + name)
    return name


def checked(root, name, allow_missing=False):
    path = root
    for part in Path(safe_name(name)).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink component refused: ' + str(path))
    if path.exists() and not path.is_file():
        raise ValueError('Not a regular file: ' + str(path))
    if not allow_missing and not path.is_file():
        raise ValueError('Missing regular file: ' + str(path))
    return path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(root):
    rows = {}
    for line in checked(root, 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('Malformed manifest row')
        sha, name = match.groups()
        if name.startswith('./'):
            name = name[2:]
        safe_name(name)
        if name in rows:
            raise ValueError('Duplicate checksum path')
        checked(root, name)
        rows[name] = sha
    if not rows:
        raise ValueError('Empty manifest')
    return rows


staged = []
try:
    kit = manifest(source)
    actual = set()
    for path in source.rglob('*'):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError('Unexpected add-on filesystem entry: ' + str(path))
        if path.is_file() and path != source / 'SHA256SUMS':
            actual.add(str(path.relative_to(source)))
    if actual != set(kit):
        raise ValueError('Add-on manifest does not exactly cover files')
    for name, sha in kit.items():
        if digest(checked(source, name)) != sha:
            raise ValueError('Add-on checksum mismatch: ' + name)
    required = {HOST, 'run-libcamera', 'stream-camera', 'expected-base.json',
                'streaming-elf-report.json', 'rootfs/usr/bin/gst-launch-1.0',
                'rootfs/usr/bin/gst-inspect-1.0',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstcoreelements.so',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstvideoconvertscale.so',
                'rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstvideo4linux2.so'}
    if not required.issubset(kit):
        raise ValueError('Incomplete streaming add-on')
    report = json.loads(checked(source, 'streaming-elf-report.json').read_text())
    if report.get('architecture') != 'AArch64' or report.get('missing_strong_imports') != {}:
        raise ValueError('No passing combined streaming ELF report')
    expected = json.loads(checked(source, 'expected-base.json').read_text())
    base = expected['files']
    if not base or HOST not in base or 'run-libcamera' not in base:
        raise ValueError('Incomplete expected base inventory')
    for name, sha in base.items():
        safe_name(name)
        if not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError('Malformed expected base digest')
    known_hosts = set(expected['allowed_host_sha256']) | {base[HOST], kit[HOST]}
    if any(not re.fullmatch(r'[0-9a-f]{64}', sha) for sha in known_hosts):
        raise ValueError('Malformed accepted helper digest')
    rows = manifest(prefix)
    if not set(base).issubset(rows):
        raise ValueError('Installed runtime differs from expected base inventory')
    for name, sha in rows.items():
        current = digest(checked(prefix, name))
        if current != sha and current != kit.get(name):
            raise ValueError('Installed checksum mismatch: ' + name)
    for name, sha in base.items():
        current = digest(checked(prefix, name))
        allowed = {sha}
        if name in MUTABLE_BASE:
            allowed.add(kit[name])
        if name == HOST:
            allowed |= known_hosts
        if current not in allowed:
            raise ValueError('Unrecognized base file; refusing overwrite: ' + name)
    # Never replace a baseline library/algorithm. Existing stream-only files may
    # be upgraded if they are tracked and verified; unexpected collisions fail.
    install_rows = {name: sha for name, sha in kit.items()
                    if name not in ('expected-base.json', 'install-streaming.sh')}
    for name, sha in install_rows.items():
        target = checked(prefix, name, allow_missing=True)
        if target.exists() and digest(target) != sha:
            if name in base and name not in MUTABLE_BASE:
                raise ValueError('Refusing to replace a baseline library/data file: ' + name)
            if name not in rows:
                raise ValueError('Unexpected untracked destination collision: ' + name)
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    for name, sha in install_rows.items():
        target = checked(prefix, name, allow_missing=True)
        if target.exists() and digest(target) == sha:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        checked(prefix, name, allow_missing=True)
        fd, temp = tempfile.mkstemp(prefix='.ciri-gst-new.', dir=target.parent)
        staged.append(Path(temp))
        with os.fdopen(fd, 'wb') as stream, checked(source, name).open('rb') as input_stream:
            shutil.copyfileobj(input_stream, stream)
            mode = 0o755 if name in (HOST, 'run-libcamera', 'stream-camera') or name.startswith('rootfs/usr/bin/') else 0o644
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(Path(temp)) != sha:
            raise ValueError('Staged checksum mismatch: ' + name)
        os.replace(temp, target)
    rows.update(install_rows)
    fd, temp = tempfile.mkstemp(prefix='.ciri-gst-manifest.', dir=prefix)
    staged.append(Path(temp))
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(''.join(f'{sha}  {name}\n' for name, sha in sorted(rows.items())))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, prefix / 'SHA256SUMS')
    for name, sha in rows.items():
        if digest(checked(prefix, name)) != sha:
            raise ValueError('Final checksum mismatch: ' + name)
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    print('Streaming add-on installed:', prefix)
    print('Base libcamera/algorithms, kernel, firmware, audio and Arch libraries unchanged. No backups made.')
    print('No module loaded or stream started. Hardware and desktop-app tests remain pending.')
except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
    raise SystemExit('Streaming installation failed; partial changes may remain. Inspect/rerun this same kit: ' + str(error))
finally:
    for path in staged:
        if path.exists():
            path.unlink()
PY
