#!/bin/bash
# User-run module installation on booted Ciri; does not replace the kernel.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]] || { printf 'Run with sudo on booted Ciri.\n' >&2; exit 1; }
[[ $# -le 1 ]] || { printf 'Usage: %s [MODULE_KIT]\n' "$0" >&2; exit 2; }
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
release=$(uname -r)
[[ $release == 7.3.0-rc4-ciri ]] || { printf 'This kit is for 7.3.0-rc4-ciri, not %s.\n' "$release" >&2; exit 1; }
python3 -I - "$source" <<'PY'
import hashlib
from pathlib import Path
import re, sys
requested = Path(sys.argv[1]).absolute()
root = requested.resolve(strict=True)
if root != requested or not root.is_dir():
    raise SystemExit('Use a real, nonsymlinked module-kit directory')
expected = {'v4l2loopback.ko', 'module-build-info.txt', 'install-loopback.sh'}
if {path.name for path in root.iterdir()} != expected | {'SHA256SUMS'}:
    raise SystemExit('Module-kit inventory mismatch')
if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
    raise SystemExit('Module kit must contain only regular, nonsymlinked files')
rows = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
    if not match or match[2] not in expected or match[2] in rows:
        raise SystemExit('Invalid module-kit manifest')
    sha, name = match.groups()
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise SystemExit('Invalid module-kit file')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != sha:
            raise SystemExit('Module-kit checksum mismatch')
    rows[name] = sha
if set(rows) != expected:
    raise SystemExit('Incomplete module kit')
info = (root / 'module-build-info.txt').read_text().splitlines()
pins = {
    'source=https://github.com/v4l2loopback/v4l2loopback',
    'revision=0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4',
    'kernel-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993',
    'kernel-release=7.3.0-rc4-ciri', 'architecture=AArch64', 'module-only=true',
}
if not pins.issubset(info):
    raise SystemExit('Unexpected module build provenance')
with (root / 'v4l2loopback.ko').open('rb') as stream:
    header = stream.read(20)
if (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or
        int.from_bytes(header[16:18], 'little') != 1 or
        int.from_bytes(header[18:20], 'little') != 183):
    raise SystemExit('Expected an ELF64 AArch64 relocatable module')
PY
[[ $(modinfo -F vermagic "$source/v4l2loopback.ko") == "$release "* ]] || { printf 'Module vermagic mismatch.\n' >&2; exit 1; }
[[ $(modinfo -F name "$source/v4l2loopback.ko") == v4l2loopback ]] || { printf 'Wrong module name.\n' >&2; exit 1; }
[[ ! -d /sys/module/v4l2loopback ]] || { printf 'Loopback already loaded; nothing changed. Stop it yourself before installing.\n' >&2; exit 1; }
for path in /usr /usr/lib /usr/lib/modules "/usr/lib/modules/$release"; do
    [[ -d $path && ! -L $path ]] || { printf 'Missing or symlinked kernel module directory: %s\n' "$path" >&2; exit 1; }
done
directory=/usr/lib/modules/$release/updates/ciri-camera
[[ ! -L /usr/lib/modules/$release/updates && ! -L $directory ]] || { printf 'Module directory symlink refused.\n' >&2; exit 1; }
[[ ! -e $directory/v4l2loopback.ko && ! -L $directory/v4l2loopback.ko ]] || { printf 'Module destination exists; nothing overwritten.\n' >&2; exit 1; }
sync -f /usr/lib/modules/$release
mkdir -p -- "$directory"
temporary=$(mktemp "$directory/.ciri-loopback.XXXXXXXX")
# Only remove the temporary file created above, never an installed module.
trap 'rm -f -- "$temporary"' EXIT
trap 'printf "Module installation failed; inspect the destination before retrying. A module file may remain, but no module was loaded.\n" >&2' ERR
install -m 0644 "$source/v4l2loopback.ko" "$temporary"
cmp "$source/v4l2loopback.ko" "$temporary"
sync -f "$temporary"
# Atomic, same-filesystem publication, with no overwrite if another file appears.
ln -- "$temporary" "$directory/v4l2loopback.ko"
depmod -a "$release"
sync -f "$directory"
printf 'Installed loopback module only. No kernel replaced or module loaded.\n'
printf 'Create a free loopback device yourself, then start the streaming launcher.\n'
