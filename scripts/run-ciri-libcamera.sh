#!/bin/bash
# Ciri-only launcher. Path/library isolation, not a security sandbox.
set -euo pipefail
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run with sudo on Ciri.'
[[ $(uname -m) == aarch64 ]] || fail 'Run on the ARM64 Ciri, not the build host.'
[[ $# -gt 0 ]] || set -- --list
prefix=/opt/ciri-libcamera
rootfs=$prefix/rootfs
for path in /opt "$prefix" "$rootfs" "$rootfs/usr" "$rootfs/usr/bin" \
    "$rootfs/usr/lib" "$rootfs/usr/lib/ciri-libcamera" "$rootfs/dev" \
    "$rootfs/proc" "$rootfs/sys" "$rootfs/run" "$rootfs/tmp" "$rootfs/captures"; do
    [[ -d $path && ! -L $path ]] || fail "Missing or symlinked directory: $path"
done
for path in "$rootfs/usr/bin/cam" "$rootfs/usr/bin/ciri-libcamera-host" \
    "$rootfs/usr/lib/ciri-libcamera/libcamera.so.0.1.0" \
    "$rootfs/usr/lib64/mtkisp7_ipa_proxy.so" "$rootfs/lib64/ld-linux-aarch64.so.1"; do
    [[ -f $path && ! -L $path ]] || fail "Missing installed runtime file: $path"
done
case "$1" in
    --gst-launch|--gst-inspect)
        [[ -d $rootfs/usr/lib/ciri-libcamera/gstreamer-1.0 && ! -L $rootfs/usr/lib/ciri-libcamera/gstreamer-1.0 ]] || fail 'Missing or symlinked streaming plugin directory.'
        for path in "$rootfs/usr/bin/gst-launch-1.0" "$rootfs/usr/bin/gst-inspect-1.0" \
            "$rootfs/usr/lib/ciri-libcamera/gstreamer-1.0/libgstlibcamera.so"; do
            [[ -f $path && ! -L $path ]] || fail "Streaming runtime missing: $path"
        done ;;
esac
for command in unshare mount chroot env modprobe mktemp; do command -v "$command" >/dev/null; done
[[ -d /run/udev/data && ! -L /run/udev && ! -L /run/udev/data ]] || fail 'Host udev database missing.'
if [[ ! -e /dev/dma_heap/system ]]; then modprobe system_heap; fi
if [[ ! -e /dev/dma_heap/scp-isp-cma-region ]]; then modprobe cma_heap; fi
for heap in /dev/dma_heap/system /dev/dma_heap/scp-isp-cma-region; do
    [[ -c $heap ]] || fail "DMA heap missing: $heap"
done
export LIBCAMERA_LOG_LEVELS="${LIBCAMERA_LOG_LEVELS:-*:INFO}"
export GST_DEBUG="${GST_DEBUG:-2}"
# Opt-in export-only diagnostic. The RAM wrapper passes a freshly copied config;
# normal launches do not create tuning flags or change camera processing.
export CIRI_P1_DUMP_CONFIG="${CIRI_P1_DUMP_CONFIG:-}"
if [[ -n $CIRI_P1_DUMP_CONFIG ]]; then
    [[ -f $CIRI_P1_DUMP_CONFIG && ! -L $CIRI_P1_DUMP_CONFIG ]] || fail 'P1 dump config missing or symlinked.'
fi
exec unshare --mount --propagation private /bin/bash -c '
    set -euo pipefail
    prefix=$1; rootfs=$2; shift 2
    for name in dev proc sys; do mount --rbind "/$name" "$rootfs/$name"; done
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$rootfs/run"
    mkdir -p "$rootfs/run/camera" "$rootfs/run/chromeos-config/v1" "$rootfs/run/udev/data"
    printf "ciri\n" > "$rootfs/run/chromeos-config/v1/name"
    mount --bind /run/udev/data "$rootfs/run/udev/data"
    mount -o remount,bind,ro,nosuid,nodev,noexec "$rootfs/run/udev/data"
    mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs "$rootfs/tmp"
    [[ ! -L $prefix/captures ]]
    mkdir -p "$prefix/captures"
    output=$(mktemp -d "$prefix/captures/run.XXXXXXXX")
    mount --bind "$output" "$rootfs/captures"
    if [[ -n $CIRI_P1_DUMP_CONFIG ]]; then
        mkdir "$rootfs/captures/p1-dump"
        cp "$CIRI_P1_DUMP_CONFIG" "$rootfs/captures/p1-dump/dump.cfg"
        : > "$rootfs/run/camera/enable_tuning"
        printf "2\n" > "$rootfs/run/camera/export_dump"
        printf "P1 export: two early requests; directory %s/p1-dump\n" "$output"
    fi
    printf "Output directory: %s\n" "$output"
    cd "$rootfs/captures"
    exec env -i PATH=/usr/bin:/bin HOME=/tmp \
        LIBCAMERA_LOG_LEVELS="$LIBCAMERA_LOG_LEVELS" \
        LIBCAMERA_IPA_MODULE_PATH=/usr/lib64/libcamera \
        LIBCAMERA_IPA_PROXY_PATH=/usr/lib64 \
        LIBCAMERA_IPA_CONFIG_PATH=/etc/camera/libcamera/ipa:/usr/share/libcamera/ipa \
        GST_PLUGIN_PATH_1_0=/usr/lib/ciri-libcamera/gstreamer-1.0 \
        GST_PLUGIN_SYSTEM_PATH_1_0= GST_REGISTRY_1_0=/tmp/ciri-gstreamer-registry.bin \
        GST_REGISTRY_FORK=no GST_DEBUG="$GST_DEBUG" \
        /usr/bin/chroot "$rootfs" /lib64/ld-linux-aarch64.so.1 \
        --library-path /usr/lib/ciri-libcamera:/usr/lib64:/lib64:/usr/share/cros-camera/libfs \
        /usr/bin/ciri-libcamera-host "$@"
' ciri-libcamera "$prefix" "$rootfs" "$@"
