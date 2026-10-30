#!/usr/bin/env bash
set -euo pipefail

# Run in a dedicated Debian Docker container, not on the development host.
[[ -f /.dockerenv ]] || { printf 'Run this script inside a dedicated Debian Docker container.\n' >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || { printf 'Container root is required to install build dependencies.\n' >&2; exit 1; }
[[ ! -e /work/vboot_reference && ! -e /work/artifacts ]] || { printf 'Use an empty /work volume; existing sources/artifacts are not replaced.\n' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
mkdir -p /work/artifacts
printf 'Installing ARM64 cross-build and emulation dependencies\n'
dpkg --add-architecture arm64
apt-get update > /work/dependencies.log 2>&1
apt-get install -y --no-install-recommends \
    build-essential git ca-certificates pkg-config python3 file \
    gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    libssl-dev:arm64 uuid-dev:arm64 qemu-user \
    >> /work/dependencies.log 2>&1

source=https://chromium.googlesource.com/chromiumos/platform/vboot_reference
printf 'Cloning upstream ChromiumOS vboot_reference\n'
requested_revision=${SOURCE_REVISION:-72043688a9842a31a92e4829fe71ee94c4939fc8}
[[ "$requested_revision" =~ ^[a-fA-F0-9]{40}$ ]] || { printf 'SOURCE_REVISION must be a full commit hash.\n' >&2; exit 1; }
git clone --depth=1 --single-branch --branch=main "$source" /work/vboot_reference
if [[ $(git -C /work/vboot_reference rev-parse HEAD) != "$requested_revision" ]]; then
    git -C /work/vboot_reference fetch --depth=1 origin "$requested_revision"
    git -C /work/vboot_reference checkout --detach FETCH_HEAD
fi
revision=$(git -C /work/vboot_reference rev-parse HEAD)
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
printf 'Building static ARM64 cgpt at source revision %s\n' "$revision"
make -C /work/vboot_reference -j"${BUILD_JOBS:-4}" \
    BUILD=/work/out ARCH=arm64 FIRMWARE_ARCH= \
    CC='aarch64-linux-gnu-gcc -I/usr/include/aarch64-linux-gnu' \
    AR=aarch64-linux-gnu-ar STATIC=1 USE_FLASHROM=0 cgpt
install -m 755 /work/out/cgpt/cgpt /work/artifacts/cgpt
cp /work/vboot_reference/LICENSE /work/artifacts/LICENSE
printf '%s\n' "$revision" > /work/artifacts/source-revision.txt
printf 'source=%s\nrevision=%s\narchitecture=arm64\nstatic=yes\n' "$source" "$revision" > /work/artifacts/build-info.txt
file /work/artifacts/cgpt
python3 -I - /work/artifacts/cgpt <<'PY'
from pathlib import Path
import struct
import subprocess
import sys
with Path(sys.argv[1]).open('rb') as stream:
    header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', header, 16)[0] not in (2, 3)
            or struct.unpack_from('<H', header, 18)[0] != 183):
        raise SystemExit('cgpt is not an ARM64 ELF64 executable')
program = subprocess.check_output(['readelf', '--program-headers', sys.argv[1]], text=True)
dynamic = subprocess.check_output(['readelf', '--dynamic', sys.argv[1]], text=True)
if 'INTERP' in program or '(NEEDED)' in dynamic:
    raise SystemExit('cgpt must be statically linked, without shared-library dependencies')
PY

# Exercise the ARM64 binary under QEMU against a regular image file only.
truncate -s 64M /work/flags-test.img
qemu-aarch64 /work/artifacts/cgpt create /work/flags-test.img
qemu-aarch64 /work/artifacts/cgpt add -i 1 -t kernel -b 2048 -s 65536 \
    -P 15 -T 1 -S 1 /work/flags-test.img
for attribute in P T S; do
    value=$(qemu-aarch64 /work/artifacts/cgpt show -i 1 -"$attribute" /work/flags-test.img)
    case "$attribute" in P) expected=15;; T|S) expected=1;; esac
    [[ "$value" == "$expected" ]] || { printf 'ARM64 cgpt flag test failed for %s: %s\n' "$attribute" "$value" >&2; exit 1; }
done
printf 'qemu_arm64_gpt_flags_test=passed\n' >> /work/artifacts/build-info.txt
cd /work/artifacts
sha256sum cgpt LICENSE source-revision.txt build-info.txt > SHA256SUMS
printf 'ARM64 cgpt build and priority/tries/successful flag tests passed.\n'
