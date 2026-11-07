#!/bin/bash
# Operator-run helper-only update; no recovered files copied and no backups made.
set -euo pipefail
[[ $EUID == 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
[[ $# -le 2 ]] || { printf 'Usage: %s [UPDATE_DIRECTORY [RUNTIME_PREFIX]]\n' "$0" >&2; exit 2; }
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
prefix=${2:-/opt/ciri-libcamera}
python3 -I - "$source" "$prefix" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

OLD = '1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45'
HOST = 'rootfs/usr/bin/ciri-libcamera-host'
KIT = {'ciri-libcamera-host', 'build-info.txt', 'install-host-update.sh'}
source = Path(sys.argv[1]).resolve(strict=True)
requested = Path(sys.argv[2]).absolute()
prefix = requested.resolve(strict=True)
if prefix != requested:
    raise SystemExit('Runtime prefix must not contain symlink components.')


def checked_file(root, name):
    path = root
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink component refused: ' + str(path))
    if not path.is_file():
        raise ValueError('Missing regular file: ' + str(path))
    return path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(root):
    rows = {}
    for line in checked_file(root, 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('Malformed checksum row')
        sha, name = match.groups()
        if name.startswith('./'):
            name = name[2:]
        if (not name or name.startswith('/') or '\\' in name or
                any(part in ('', '.', '..') for part in name.split('/')) or
                any(c in name for c in ('\r', '\n', '\0')) or name in rows):
            raise ValueError('Unsafe or duplicate checksum path: ' + name)
        checked_file(root, name)
        rows[name] = sha
    if not rows:
        raise ValueError('Empty checksum manifest')
    return rows


staged = []
try:
    kit = manifest(source)
    if set(kit) != KIT:
        raise ValueError('Unexpected update-kit inventory')
    for name, sha in kit.items():
        if digest(checked_file(source, name)) != sha:
            raise ValueError('Update-kit checksum mismatch: ' + name)
    new = kit['ciri-libcamera-host']
    replacement = checked_file(source, 'ciri-libcamera-host')
    with replacement.open('rb') as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or
            int.from_bytes(header[18:20], 'little') != 183):
        raise ValueError('Replacement must be little-endian ELF64 AArch64')
    rows = manifest(prefix)
    required = {HOST, 'elf-report.json', 'run-libcamera', 'rootfs/usr/bin/cam',
                'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0'}
    if not required.issubset(rows):
        raise ValueError('Not a complete checksummed Ciri libcamera runtime')
    target = checked_file(prefix, HOST)
    current = digest(target)
    if current not in (OLD, new) or rows[HOST] not in (OLD, new):
        raise ValueError('Unexpected installed helper; refusing overwrite')
    if current == OLD and rows[HOST] != OLD:
        raise ValueError('Installed helper and manifest disagree')
    for name, sha in rows.items():
        # Also permits repair after helper replacement succeeded but manifest
        # replacement or final sync failed. All OTHER files must still match.
        if name != HOST and digest(checked_file(prefix, name)) != sha:
            raise ValueError('Installed runtime checksum mismatch: ' + name)
    report = json.loads(checked_file(prefix, 'elf-report.json').read_text())
    if report.get('architecture') != 'AArch64' or report.get('missing_strong_imports') != {}:
        raise ValueError('Installed runtime has no passing static ELF report')
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    rows[HOST] = new
    fd, temp = tempfile.mkstemp(prefix='.ipa-host-manifest.', dir=prefix)
    staged.append(Path(temp))
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), stat.S_IMODE(checked_file(prefix, 'SHA256SUMS').stat().st_mode))
        stream.write(''.join(f'{sha}  {name}\n' for name, sha in rows.items()))
        stream.flush()
        os.fsync(stream.fileno())
    if current != new:
        fd, binary_temp = tempfile.mkstemp(prefix='.ipa-host-new.', dir=target.parent)
        staged.append(Path(binary_temp))
        with os.fdopen(fd, 'wb') as stream, replacement.open('rb') as input_stream:
            shutil.copyfileobj(input_stream, stream)
            os.fchmod(stream.fileno(), 0o755)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(Path(binary_temp)) != new or digest(target) != current:
            raise ValueError('Helper changed during staging; refusing replacement')
        os.replace(binary_temp, target)
    os.replace(temp, prefix / 'SHA256SUMS')
    for name, sha in rows.items():
        if digest(checked_file(prefix, name)) != sha:
            raise ValueError('Final installed checksum mismatch: ' + name)
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    print('Updated only:', target)
    print('SHA256:', new)
    print('No backups made; no libraries, kernel, firmware, audio or services changed.')
    print('Hardware capture/colour verification remains pending.')
except (OSError, ValueError, subprocess.CalledProcessError) as error:
    raise SystemExit('Update failed (inspect state; rerun may repair the manifest): ' + str(error))
finally:
    # Only remove our own temporary staging files, never an old client/backup.
    for path in staged:
        if path.exists():
            path.unlink()
PY
