#!/bin/bash
# Operator-run host/cam/core/stream-launcher update. No recovered files or backups.
# Stop the existing camera producer before updating; restart it after success.
set -euo pipefail
[[ $EUID == 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
component=host
case "${1:-}" in
    --cam) component=cam; shift ;;
    --core) component=core; shift ;;
    --stream) component=stream; shift ;;
    --*) printf 'Unknown update component: %s\n' "$1" >&2; exit 2 ;;
esac
[[ $# -le 2 ]] || { printf 'Usage: %s [--cam|--core|--stream] [UPDATE_DIRECTORY [RUNTIME_PREFIX]]\n' "$0" >&2; exit 2; }
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
prefix=${2:-/opt/ciri-libcamera}
python3 -I - "$source" "$prefix" "$component" <<'PY'
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
CAM_OLD = 'a8e0393e09af9b63eddb3b9dbb3e064bff51e355095fddfa1cc47599452e9661'
CORE_OLD = 'af1014e2f73934ab79ccc1eddff5806194b83b9fce925e5e443b81443487a335'
CORE_VERIFIED = 'db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d'
STREAM_OLD = '0489e6babd3c7ec7180579be6cbe12dc25e1c41f4f1b23845a205b1f875b4b83'
CORE_DIR = 'rootfs/usr/lib/ciri-libcamera/'
CORE_NAMES = ('libcamera.so', 'libcamera.so.0.1', 'libcamera.so.0.1.0')
RECEIPT = 'core-update-info.json'
HOST = 'rootfs/usr/bin/ciri-libcamera-host'
BINARY = 'ciri-libcamera-host'
KIT = {BINARY, 'build-info.txt', 'install-host-update.sh'}
component = sys.argv[3] if len(sys.argv) > 3 else 'host'
if component == 'cam':
    OLD = CAM_OLD
    HOST = 'rootfs/usr/bin/cam'
    BINARY = 'cam'
    KIT = {BINARY, 'build-info.txt', 'install-cam-update.sh'}
elif component == 'core':
    OLD = CORE_OLD
    HOST = CORE_DIR + 'libcamera.so.0.1.0'
    BINARY = 'libcamera.so.0.1.0'
    KIT = {BINARY, 'build-info.txt', 'install-core-update.sh'}
elif component == 'stream':
    OLD = STREAM_OLD
    HOST = 'stream-camera'
    BINARY = 'stream-camera'
    KIT = {BINARY, 'build-info.txt', 'install-stream-update.sh'}
elif component != 'host':
    raise SystemExit('Unknown update component')
TARGETS = tuple(CORE_DIR + name for name in CORE_NAMES) if component == 'core' else (HOST,)
source = Path(sys.argv[1]).resolve(strict=True)
requested = Path(sys.argv[2]).absolute()
prefix = requested.resolve(strict=True)
if prefix != requested:
    raise SystemExit('Runtime prefix must not contain symlink components.')


def checked_file(root, name, allow_missing=False):
    path = root
    for part in Path(name).parts:
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
    new = kit[BINARY]
    replacement = checked_file(source, BINARY)
    with replacement.open('rb') as stream:
        header = stream.read(20)
    if component == 'stream':
        info = json.loads(checked_file(source, 'build-info.txt').read_text())
        if (not header.startswith(b'#!/bin/bash\n') or
                info.get('stream_launcher_sha256') != new or
                info.get('baseline_stream_launcher_sha256') != OLD or
                info.get('filesink_bridge') is not True):
            raise ValueError('Not the authored filesink streaming-launcher update')
    elif (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or
          int.from_bytes(header[18:20], 'little') != 183):
        raise ValueError('Replacement must be little-endian ELF64 AArch64')
    if component == 'core':
        info = json.loads(checked_file(source, 'build-info.txt').read_text())
        if (new != CORE_VERIFIED or info.get('core_sha256') != new or
                info.get('baseline_core_sha256') != OLD or
                info.get('architecture') != 'AArch64' or
                info.get('missing_strong_imports') != {} or
                info.get('p2_separate_plane_buffers') is not True or
                info.get('hardware_tested') is not True):
            raise ValueError('Not the native-pixel-verified separate-plane core update')
    rows = manifest(prefix)
    required = set(TARGETS) | {HOST, 'elf-report.json', 'run-libcamera', 'rootfs/usr/bin/cam',
                'rootfs/usr/bin/ciri-libcamera-host',
                'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0'}
    if not required.issubset(rows):
        raise ValueError('Not a complete checksummed Ciri libcamera runtime')
    updates = {name: checked_file(source, BINARY) for name in TARGETS}
    current = {}
    for name in TARGETS:
        current[name] = digest(checked_file(prefix, name))
        if current[name] not in (OLD, new) or rows[name] not in (OLD, new):
            raise ValueError('Unexpected installed executable/core; refusing overwrite: ' + name)
        if current[name] == OLD and rows[name] != OLD:
            raise ValueError('Installed executable/core and manifest disagree: ' + name)
    if component == 'core':
        receipt = checked_file(prefix, RECEIPT, allow_missing=True)
        current[RECEIPT] = digest(receipt) if receipt.exists() else None
        receipt_sha = kit['build-info.txt']
        if current[RECEIPT] not in (None, receipt_sha) or rows.get(RECEIPT) not in (None, receipt_sha):
            raise ValueError('Unrecognized existing core-update receipt; refusing overwrite')
        updates[RECEIPT] = checked_file(source, 'build-info.txt')
    for name, sha in rows.items():
        # Repair known partial alias/receipt updates without accepting changes to
        # any other library, client, plugin, launcher, algorithm or data file.
        if name not in updates and digest(checked_file(prefix, name)) != sha:
            raise ValueError('Installed runtime checksum mismatch: ' + name)
    report = json.loads(checked_file(prefix, 'elf-report.json').read_text())
    if report.get('architecture') != 'AArch64' or report.get('missing_strong_imports') != {}:
        raise ValueError('Installed runtime has no passing static ELF report')
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    replacements = []
    for name, input_path in updates.items():
        target = checked_file(prefix, name, allow_missing=name == RECEIPT)
        expected = new if name in TARGETS else kit['build-info.txt']
        rows[name] = expected
        if current[name] == expected:
            continue
        fd, binary_temp = tempfile.mkstemp(prefix='.ipa-host-new.', dir=target.parent)
        staged.append(Path(binary_temp))
        with os.fdopen(fd, 'wb') as stream, input_path.open('rb') as input_stream:
            shutil.copyfileobj(input_stream, stream)
            mode = (stat.S_IMODE(target.stat().st_mode) if component == 'core' and target.exists()
                    else 0o644 if name == RECEIPT else 0o755)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(Path(binary_temp)) != expected:
            raise ValueError('Staged update checksum mismatch: ' + name)
        replacements.append((binary_temp, target, current[name]))
    fd, temp = tempfile.mkstemp(prefix='.ipa-host-manifest.', dir=prefix)
    staged.append(Path(temp))
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), stat.S_IMODE(checked_file(prefix, 'SHA256SUMS').stat().st_mode))
        stream.write(''.join(f'{sha}  {name}\n' for name, sha in rows.items()))
        stream.flush()
        os.fsync(stream.fileno())
    # Each replacement is atomic, not the whole multi-file transaction. Stop the
    # producer first; a failed update must be repaired before restarting it.
    for binary_temp, target, before in replacements:
        now = digest(target) if target.exists() else None
        if target.is_symlink() or now != before:
            raise ValueError('Installed file changed during staging; refusing replacement')
        os.replace(binary_temp, target)
    os.replace(temp, prefix / 'SHA256SUMS')
    for name, sha in rows.items():
        if digest(checked_file(prefix, name)) != sha:
            raise ValueError('Final installed checksum mismatch: ' + name)
    subprocess.run(['sync', '-f', str(prefix)], check=True)
    print('Updated only:', ', '.join(str(prefix / name) for name in updates), 'and SHA256SUMS')
    print('SHA256:', new)
    if component == 'core':
        print('All three core aliases match the native-tested core. Provenance: ' + str(prefix / RECEIPT))
        print('No backups made; client, supervisor, launchers, plugins, other libraries, tuning, kernel, firmware, audio and services unchanged.')
        print('Existing ELF reports remain build-time records; the receipt holds the corrected core report.')
        print('Restart the existing standard streaming producer; post-install streaming verification remains pending.')
    elif component == 'stream':
        print('Streaming launcher now uses v4l2-ctl + unbuffered filesink, not the broken v4l2sink MMAP/RW paths.')
        print('No backups made; core, clients, plugins, algorithms, kernel, firmware, audio and services unchanged.')
        print('Restart stream-camera with the actual loopback node; no module reload needed.')
    else:
        print('No backups made; no libraries, kernel, firmware, audio or services changed.')
        print('Hardware capture/colour verification remains pending.')
except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
    raise SystemExit('Update failed (inspect state; rerun may repair the manifest): ' + str(error))
finally:
    # Only remove our own temporary staging files, never an old client/backup.
    for path in staged:
        if path.exists():
            path.unlink()
PY
