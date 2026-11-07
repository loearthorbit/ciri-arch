#!/bin/bash
# Cross-build only the diagnostic client; link to the recovered vendor libraries.
# Dependencies are unpacked under build/, never installed into the host system.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source=${LIBCAMERA_SOURCE:-$repo/build/ciri-camera-client-source.F6dRON1e/libcamera}
sdk=${CAMERA_SDK:-$repo/build/ciri-camera-client-sdk.4Jej3wox}
recovery=${CAMERA_RECOVERY:-$repo/build/ciri-camera-recovery.sb0PBtT8}
out=${CAMERA_CLIENT_OUT:-$repo/build/ciri-camera-client}
revision=a5a75eeabdce349d8f794caacb3177c0e71287d5
gyro_symbol=_ZN9libcamera10GyroSensor4initENS0_8LocationE
face_symbols=(_ZN9libcamera12FaceDetector5startEv
    _ZN9libcamera12FaceDetector4stopEv _ZN9libcamera14FaceDetectTask3runEv)
face_exports=()
for symbol in "${face_symbols[@]}"; do
    face_exports+=("-Wl,--export-dynamic-symbol=$symbol")
done
[[ $(git -C "$source" rev-parse HEAD) == "$revision" ]] || {
    printf 'Expected vendor header revision %s\n' "$revision" >&2; exit 1;
}
headers="$sdk/libcxx/sysroot/usr/lib/llvm-22/include/c++/v1"
croot="$sdk/libc6-dev-arm64-cross/sysroot/usr/aarch64-linux-gnu"
linuxheaders="$sdk/linux-libc-dev-arm64-cross/sysroot/usr/aarch64-linux-gnu/include"
gcc="$sdk/libgcc-dev/sysroot/usr/lib/gcc-cross/aarch64-linux-gnu/12"
libs="$recovery/files/usr/lib64"
clibs="$recovery/files/lib64"
[[ -f $headers/__config_site && -f $croot/lib/Scrt1.o && -f $gcc/crtbeginS.o ]]
python3 -I - "$sdk" "$repo/config/ciri-camera-client-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
sdk = Path(sys.argv[1])
inputs = json.loads(Path(sys.argv[2]).read_text())
for package in inputs['sdk_packages']:
    filename = package['url'].rsplit('/', 1)[-1]
    with (sdk / package['directory'] / filename).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != package['sha256']:
        raise SystemExit('Compiler input checksum mismatch: ' + filename)
PY
(cd -- "$recovery/files"; sha256sum --check --status "$recovery/SHA256SUMS")
mkdir -p -- "$out"
"${CXX:-clang++}" --target=aarch64-linux-gnu --sysroot="$croot" \
    -std=c++17 -O2 -g -Wall -Wextra -Werror -pthread -fPIE \
    -nostdinc++ -isystem "$headers" -isystem "$croot/include" \
    -isystem "$linuxheaders" -I "$source/include" \
    -fuse-ld=lld -nostdlib -pie \
    "$croot/lib/Scrt1.o" "$croot/lib/crti.o" "$gcc/crtbeginS.o" \
    "$repo/scripts/ciri-camera-probe.cpp" "$repo/scripts/ciri-camera-no-face-detection.cpp" \
    -L "$libs" -L "$clibs" \
    -Wl,--dynamic-linker=/lib64/ld-linux-aarch64.so.1 \
    -Wl,--eh-frame-hdr,-z,relro,-z,now \
    -Wl,--export-dynamic-symbol="$gyro_symbol" "${face_exports[@]}" \
    -Wl,-rpath-link,"$libs:$clibs:$recovery/files/usr/share/cros-camera/libfs" \
    -l:libcamera.so.0.1.0 -l:libcamera-base.so.0.1.0 \
    -l:libc++.so.1.0 -l:libc++abi.so.1.0 -l:libm.so.6 \
    -l:libgcc_s.so.1 -l:libc.so.6 -l:ld-linux-aarch64.so.1 \
    "$gcc/crtendS.o" "$croot/lib/crtn.o" -o "$out/ciri-camera-probe"
readelf -h "$out/ciri-camera-probe" | grep -E 'Class:|Machine:'
readelf -d "$out/ciri-camera-probe" | grep NEEDED
# The override must be defined in .dynsym, not just the static symbol table.
readelf --dyn-syms --wide "$out/ciri-camera-probe" |
    grep -E "FUNC +GLOBAL +DEFAULT +[0-9]+ +${gyro_symbol}$"
for symbol in "${face_symbols[@]}"; do
    readelf --dyn-syms --wide "$out/ciri-camera-probe" |
        grep -E "FUNC +GLOBAL +DEFAULT +[0-9]+ +${symbol}$"
done
printf 'source=https://chromium.googlesource.com/chromiumos/third_party/libcamera\nbranch=release-R156-16836.B-mtkisp7\nrevision=%s\narchitecture=AArch64\ngyro=disabled-with-ENXIO-no-chromeos-mojo\nipa=private-unix-socket-worker-ipaRun-int-fd\nface-detection=disabled-start-stop-task-completes-without-AIE\ncapture=separate-Y-UV-DMA-allocations-zero-offset-fixed-NV12-length-dma-pattern-check-drain-requests\ncolour-controls=explicit-four-unity-gains-avoid-vendor-zero-blue-fallback\nwarmup=120-completions-reuse-eight-requests-reprime-before-requeue-save-final-eight\nmetadata=log-AE-AWB-states-missing-minus-one\n' "$revision" > "$out/build-info.txt"
cp -- "$repo/config/ciri-camera-client-inputs.json" "$out/sdk-downloads.json"
(cd -- "$out"; sha256sum ciri-camera-probe build-info.txt sdk-downloads.json > SHA256SUMS)
printf '\nBuilt camera diagnostic: %s/ciri-camera-probe\n' "$out"
