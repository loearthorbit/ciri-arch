#!/bin/bash
# Compiler driver for the retained split ARM64 SDK and isolated recovery ABI.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:?Expected c or cpp}; shift
sdk=${CAMERA_SDK:-$repo/build/camera/sdk/compiler}
recovery=${CAMERA_RECOVERY:-$repo/build/camera/recovery/chromeos-16836.19.0}
stack_sdk=${LIBCAMERA_STACK_SDK:?Set LIBCAMERA_STACK_SDK}
croot="$sdk/libc6-dev-arm64-cross/sysroot/usr/aarch64-linux-gnu"
gcc="$sdk/libgcc-dev/sysroot/usr/lib/gcc-cross/aarch64-linux-gnu/12"
libs="$recovery/files/usr/lib64"
clibs="$recovery/files/lib64"
compiler=${CC:-clang}
common=(--target=aarch64-linux-gnu --sysroot="$croot")
if [[ $mode == cpp ]]; then
    compiler=${CXX:-clang++}
    common+=(-nostdinc++ -isystem "$sdk/libcxx/sysroot/usr/lib/llvm-22/include/c++/v1"
        -Wno-error=vla-cxx-extension)
fi
common+=(-isystem "$croot/include"
    -isystem "$sdk/linux-libc-dev-arm64-cross/sysroot/usr/aarch64-linux-gnu/include")
# Meson detects libc++; its flag is redundant with our explicit header/library
# selection and clang diagnoses it as unused alongside -nostdinc++.
filtered=()
for arg in "$@"; do
    case "$arg" in
        -stdlib=libc++) continue ;;
        -stdlib=*) printf 'Unsupported C++ ABI flag: %s\n' "$arg" >&2; exit 1 ;;
    esac
    filtered+=("$arg")
done
set -- "${filtered[@]}"
link=true
shared=false
for arg in "$@"; do
    case "$arg" in
        -c|-E|-S|--version|-v|-dump*|-print*) link=false ;;
        -shared) shared=true ;;
    esac
done
if ! $link; then
    exec "$compiler" "${common[@]}" "$@"
fi
start=()
if ! $shared; then
    start+=("$croot/lib/Scrt1.o")
fi
# Never select host CRTs, host libstdc++, or the SDK's absolute libc linker script.
exec "$compiler" "${common[@]}" -fuse-ld=lld -nostdlib \
    "${start[@]}" "$croot/lib/crti.o" "$gcc/crtbeginS.o" "$@" \
    -L "$stack_sdk/lib" -L "$libs" -L "$clibs" -L "$gcc" \
    -Wl,-rpath-link,"$stack_sdk/lib:${GSTREAMER_SDK:+$GSTREAMER_SDK/lib:}$libs:$clibs:$recovery/files/usr/share/cros-camera/libfs" \
    -l:libc++.so.1.0 -l:libc++abi.so.1.0 -l:libm.so.6 \
    -l:libgcc_s.so.1 -l:libc.so.6 -l:ld-linux-aarch64.so.1 \
    "$gcc/crtendS.o" "$croot/lib/crtn.o"
