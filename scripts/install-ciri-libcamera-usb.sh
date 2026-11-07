#!/bin/bash
# USER-RUN installer for a mounted Ciri root. Never run automatically.
# Fresh isolated prefix only: no kernel, firmware, audio, Arch libs or services.
set -euo pipefail
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this script with sudo.'
[[ $# -le 1 ]] || fail 'Usage: install-ciri-libcamera-usb.sh [PACKAGE_DIRECTORY]'
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source=${1:-$repo/build/camera/packages/libcamera}
root=/run/media/jono/ARCHCIRI
destination=$root/opt/ciri-libcamera
check_mount() {
    [[ -d $root && ! -L $root ]] || fail 'ARCHCIRI mount missing or symlinked.'
    [[ $(findmnt -n -o TARGET --target "$root") == "$root" ]] || fail 'ARCHCIRI is not mounted.'
    [[ $(findmnt -n -o UUID --target "$root") == 9d00d9e7-22f1-4f32-8006-19dae6343c92 ]] || fail 'Unexpected root UUID.'
    [[ $(findmnt -n -o FSTYPE --target "$root") == ext4 ]] || fail 'Expected ext4 root.'
    [[ -d $root/opt && ! -L $root/opt ]] || fail 'Expected a real /opt directory.'
    [[ $(findmnt -n -o TARGET --target "$root/opt") == "$root" ]] || fail '/opt is on another mount.'
}
check_mount
[[ ! -e $destination && ! -L $destination ]] || fail 'Destination already exists; nothing overwritten or backed up.'
[[ -d $source && ! -L $source ]] || fail 'Missing or symlinked package directory.'
# Packages authored by package-ciri-libcamera-stack.py contain regular files,
# no symlinks/devices. Validate manifest names and complete coverage before sudo
# copies any data. This does not execute recovered or compiled ARM64 code.
python3 -I - "$source" <<'PY'
from pathlib import Path
import hashlib, json, re, sys
root = Path(sys.argv[1]).resolve()
manifest = root/'SHA256SUMS'
if not manifest.is_file() or manifest.is_symlink(): raise SystemExit('Missing real package manifest')
listed = set()
for line in manifest.read_text().splitlines():
    match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
    if not match: raise SystemExit('Malformed package checksum row')
    expected, name = match.groups()
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or name in listed:
        raise SystemExit('Unsafe or duplicate package path')
    path = root/relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise SystemExit('Missing, symlinked or escaping package file: '+name)
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected: raise SystemExit('Package checksum mismatch: '+name)
    listed.add(name)
actual_files = set()
for path in root.rglob('*'):
    if path.is_symlink() or not (path.is_dir() or path.is_file()):
        raise SystemExit('Unsupported package object: '+str(path))
    if path.is_file() and path != manifest: actual_files.add(str(path.relative_to(root)))
if actual_files != listed: raise SystemExit('Package manifest does not cover exactly its files')
required = {'run-libcamera','build-info.txt','elf-report.json',
    'rootfs/usr/bin/cam','rootfs/usr/bin/ciri-libcamera-host',
    'rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0',
    'rootfs/usr/lib/ciri-libcamera/libcamera-base.so.0.1.0',
    'rootfs/usr/lib/ciri-libcamera/libcamera-mtkisp7-platform.so',
    'rootfs/usr/lib64/mtkisp7_ipa_proxy.so',
    'rootfs/usr/lib64/libcamera/ipa_mtkisp7.so',
    'rootfs/lib64/ld-linux-aarch64.so.1'}
if not required <= listed: raise SystemExit('Incomplete camera stack package')
report = json.loads((root/'elf-report.json').read_text())
if report.get('architecture') != 'AArch64' or report.get('missing_strong_imports') != {}:
    raise SystemExit('Package has not passed static ELF checks')
for directory in ('rootfs/dev','rootfs/proc','rootfs/sys','rootfs/run','rootfs/tmp','rootfs/captures'):
    if not (root/directory).is_dir(): raise SystemExit('Missing runtime mount point: '+directory)
PY
required=$(du -sk -- "$source" | cut -f1)
available=$(df --output=avail -k -- "$root" | tr -cd '0-9\n')
(( available > required + 65536 )) || fail 'Insufficient free space.'
# Surface pending device write errors BEFORE copying. No false no-op success.
sync -f "$root/opt"
check_mount
[[ ! -e $destination && ! -L $destination ]] || fail 'Destination appeared during preflight.'
trap 'printf "Install failed; partial prefix retained at %s. No system files replaced.\n" "$destination" >&2' ERR
mkdir -- "$destination"
cp -a --no-preserve=ownership -- "$source/." "$destination/"
check_mount
(cd -- "$destination"; sha256sum --check --status SHA256SUMS)
sync -f "$destination"
trap - ERR
printf '\nIsolated libcamera package copied and checksum-verified. No ARM64 code executed.\n'
printf 'After booting Ciri: sudo /opt/ciri-libcamera/run-libcamera --list\n'
printf 'Standard cam indices start at 1, unlike the earlier diagnostic.\n'
printf 'Kernel, boot files, audio, Arch libraries, services and /opt/ciri-camera unchanged.\n'
printf 'Safely eject the USB filesystems before unplugging.\n'
