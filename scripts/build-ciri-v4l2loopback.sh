#!/bin/bash
# Build only the loopback module against prepared, matching Ciri kernel objects.
# No downloads, full-kernel rebuild, module load or device installation.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source=${V4L2LOOPBACK_SOURCE:-$repo/build/camera/source/v4l2loopback}
kernel_source=${CIRI_KERNEL_SOURCE:?Set CIRI_KERNEL_SOURCE to the retained pinned mkopec/linux source}
kernel_build=${CIRI_KERNEL_BUILD:?Set CIRI_KERNEL_BUILD to its complete prepared kernel object directory}
out=${V4L2LOOPBACK_OUT:-$repo/build/camera/kernel/v4l2loopback}
revision=0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4
kernel_revision=b8f841230b1615aa58b5d4389e11bfe66abbd993
release=7.3.0-rc4-ciri
export LOCALVERSION=-ciri
[[ $(git -C "$source" rev-parse HEAD) == "$revision" ]] || { printf 'Wrong v4l2loopback source revision.\n' >&2; exit 1; }
[[ $(git -C "$kernel_source" rev-parse HEAD) == "$kernel_revision" ]] || { printf 'Wrong Ciri kernel source revision.\n' >&2; exit 1; }
[[ -f $kernel_build/Module.symvers && -f $kernel_build/include/generated/autoconf.h && -f $kernel_build/include/config/kernel.release ]] || {
    printf 'Need the matching COMPLETE kernel build objects/symbols, not stock Arch headers or modules_prepare alone.\n' >&2; exit 1;
}
[[ $(< "$kernel_build/include/config/kernel.release") == "$release" ]] || { printf 'Kernel build release mismatch.\n' >&2; exit 1; }
[[ ! -e $out && ! -L $out ]] || { printf 'Choose a fresh V4L2LOOPBACK_OUT; nothing overwritten.\n' >&2; exit 1; }
# Incremental external-module build. Override KERNEL_MAKE_ARGS for the exact
# toolchain used by the retained kernel (GCC 14 in the retained Docker build).
read -r -a toolchain <<< "${KERNEL_MAKE_ARGS:-CROSS_COMPILE=aarch64-linux-gnu-}"
make -C "$kernel_source" O="$kernel_build" M="$source" ARCH=arm64 "${toolchain[@]}" modules
[[ $(modinfo -F vermagic "$source/v4l2loopback.ko") == "$release "* ]] || { printf 'Built module vermagic mismatch.\n' >&2; exit 1; }
readelf -h "$source/v4l2loopback.ko" | grep -Eq 'Machine:[[:space:]]+AArch64'
mkdir -p -- "$out"
install -m 0644 "$source/v4l2loopback.ko" "$out/v4l2loopback.ko"
printf 'source=https://github.com/v4l2loopback/v4l2loopback\nrevision=%s\nversion=0.15.4\nkernel-revision=%s\nkernel-release=%s\narchitecture=AArch64\nmodule-only=true\n' "$revision" "$kernel_revision" "$release" > "$out/module-build-info.txt"
modinfo -F vermagic "$out/v4l2loopback.ko" >> "$out/module-build-info.txt"
printf 'kernel-make-args=%s\nlocalversion=%s\n' "${toolchain[*]}" "$LOCALVERSION" >> "$out/module-build-info.txt"
sha256sum "$kernel_build/.config" "$kernel_build/Module.symvers" >> "$out/module-build-info.txt"
install -m 0755 "$repo/scripts/install-ciri-v4l2loopback.sh" "$out/install-loopback.sh"
(cd "$out"; sha256sum v4l2loopback.ko module-build-info.txt install-loopback.sh > SHA256SUMS)
printf 'Loopback module prepared: %s\nNothing installed or loaded.\n' "$out"
