#!/bin/bash
# One external module against retained kernel objects; never rebuild the kernel.
set -euo pipefail
source=${CIRI_KERNEL_SOURCE:-/work/linux}
build=${CIRI_KERNEL_BUILD:-/work/out}
output=${CIRI_UINPUT_OUT:-/module/uinput}
release=7.3.0-rc4-ciri
[[ ! -e $output && ! -L $output ]] || { printf 'Use a fresh uinput output directory.\n' >&2; exit 1; }
[[ -f $build/Module.symvers && -f $build/include/generated/autoconf.h && -f $build/include/config/kernel.release ]] || {
    printf 'Complete matching kernel objects are required.\n' >&2; exit 1;
}
printf '%s  %s\n' \
    287c4281daf61395cc1736f49a906698c52b2e8e363222e683b9c1afb979d13b "$build/.config" \
    4847b6f27e825ac8f3f8c18687bea4ac700d948b811ba062e23f8d91acb4297f "$build/Module.symvers" \
    b256f2504c308d4944af6e7c562682a8ddbba82bebd6155827a41a0d96830368 "$source/drivers/input/misc/uinput.c" \
    3eb602f1c1f7db35d50af553041efa623c225f4439443e932db76e3872729dbc "$source/drivers/input/input-compat.h" |
    sha256sum --check --strict
read -r actual_release < "$build/include/config/kernel.release"
[[ $actual_release == "$release" ]] || { printf 'Wrong kernel release.\n' >&2; exit 1; }
mkdir -p "$output/source/input/misc" "$output/kit"
cp "$source/drivers/input/misc/uinput.c" "$output/source/input/misc/uinput.c"
cp "$source/drivers/input/input-compat.h" "$output/source/input/input-compat.h"
printf 'obj-m := uinput.o\n' > "$output/source/input/misc/Makefile"
# obj-m builds uinput externally even though the running kernel's config did
# not select it. The core input APIs and compatibility headers are unchanged.
make -C "$build" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    M="$output/source/input/misc" modules
[[ $(modinfo -F name "$output/source/input/misc/uinput.ko") == uinput ]]
[[ $(modinfo -F vermagic "$output/source/input/misc/uinput.ko") == "$release "* ]]
install -m 0644 "$output/source/input/misc/uinput.ko" "$output/kit/uinput.ko"
printf 'kernel-release=%s\narchitecture=AArch64\nmodule-only=true\nsource-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993\n' "$release" > "$output/kit/build-info.txt"
printf 'Built as an external module; retained kernel configuration/objects are unchanged.\n' >> "$output/kit/build-info.txt"
modinfo -F vermagic "$output/kit/uinput.ko" >> "$output/kit/build-info.txt"
sha256sum "$build/.config" "$build/Module.symvers" >> "$output/kit/build-info.txt"
(cd "$output/kit"; sha256sum uinput.ko build-info.txt > SHA256SUMS)
printf 'uinput module-only build complete: %s/kit\n' "$output"
