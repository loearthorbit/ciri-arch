#!/bin/bash
# Build only our IPA supervisor against the retained ARM64 SDK. No vendor execution.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export CAMERA_SDK=${CAMERA_SDK:-$repo/build/camera/sdk/compiler}
export CAMERA_RECOVERY=${CAMERA_RECOVERY:-$repo/build/camera/recovery/chromeos-16836.19.0}
export LIBCAMERA_STACK_SDK=${LIBCAMERA_STACK_SDK:-$repo/build/camera/sdk/libcamera}
out=${LIBCAMERA_HOST_OUT:-$repo/build/camera/updates/ipa-scope}
baseline=${LIBCAMERA_HOST_BASELINE:-$repo/build/camera/stack/rootfs/usr/bin/ciri-libcamera-host}
[[ ! -e $out && ! -L $out ]] || { printf 'Choose a fresh LIBCAMERA_HOST_OUT; nothing overwritten.\n' >&2; exit 1; }
mkdir -p -- "$out"
unset CC CXX
"$repo/scripts/ciri-libcamera-cross-cc.sh" cpp -std=c++17 -O2 -Wall -Wextra -Werror \
    -pthread -fPIE -pie -Wl,--dynamic-linker=/lib64/ld-linux-aarch64.so.1 \
    -Wl,--eh-frame-hdr,-z,relro,-z,now "$repo/scripts/ciri-libcamera-host.cpp" \
    -o "$out/ciri-libcamera-host"
python3 -I - "$repo/scripts/verify-ciri-libcamera-elf.py" "$baseline" "$out/ciri-libcamera-host" "$CAMERA_RECOVERY/files/lib64/libc.so.6" <<'PY'
import hashlib
from pathlib import Path
import runpy
import sys
old, new, libc = map(Path, sys.argv[2:])
with old.open('rb') as stream:
    if hashlib.file_digest(stream, 'sha256').hexdigest() != '1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45':
        raise SystemExit('Unexpected baseline helper; static comparison refused')
inspect = runpy.run_path(sys.argv[1])['inspect']
a, b = inspect(old), inspect(new)
extra = b['imports'] - a['imports']
if set(a['needed']) != set(b['needed']) or not extra.issubset(inspect(libc)['exports']):
    raise SystemExit('Helper introduces different dependencies/non-libc imports; full runtime audit required')
print('ELF64 AArch64 verified; same DT_NEEDED; any new libc imports have matching versioned providers.')
print('New libc imports:', ', '.join(sorted(extra)) or 'none')
PY
printf 'architecture=AArch64\nchange=global-IPA-RTTI-and-allowlisted-standard-cam-GStreamer-clients\nold-host-sha256=1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45\nhardware-tested=false\n' > "$out/build-info.txt"
clang++ --version >> "$out/build-info.txt"
sha256sum "$repo/scripts/ciri-libcamera-host.cpp" "$repo/scripts/ciri-camera-ipa-worker.h" \
    "$repo/scripts/ciri-libcamera-cross-cc.sh" "$repo/scripts/build-ciri-libcamera-host.sh" >> "$out/build-info.txt"
install -m 0755 "$repo/scripts/update-ciri-libcamera-host.sh" "$out/install-host-update.sh"
(cd "$out"; sha256sum ciri-libcamera-host build-info.txt install-host-update.sh > SHA256SUMS)
printf '\nHelper-only update prepared: %s\nNo device installed or ARM64 code executed.\n' "$out"
