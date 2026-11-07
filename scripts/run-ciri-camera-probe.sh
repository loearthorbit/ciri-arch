#!/bin/bash
# Run on the booted Ciri, not the build host. Private mounts preserve vendor paths.
# This is path/library isolation for bring-up, not a security sandbox.
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { printf 'Run this on the ARM64 Ciri.\n' >&2; exit 1; }
if [[ $# -eq 0 ]]; then set -- --list; fi
[[ $# -eq 1 && $1 == --list ]] || \
    [[ $# -eq 2 && $1 == --capture && $2 =~ ^[0-9]+$ ]] || {
        printf 'Usage: run-ciri-camera-probe.sh [--list | --capture CAMERA_INDEX]\n' >&2
        exit 2
    }
rootfs=/opt/ciri-camera/rootfs
for dirpath in /opt /opt/ciri-camera "$rootfs" "$rootfs/usr" "$rootfs/usr/bin" \
    "$rootfs/dev" "$rootfs/proc" "$rootfs/sys" "$rootfs/run" "$rootfs/tmp"; do
    [[ -d $dirpath && ! -L $dirpath ]] || { printf 'Missing or symlinked directory: %s\n' "$dirpath" >&2; exit 1; }
done
[[ -f $rootfs/usr/bin/ciri-camera-probe && ! -L $rootfs/usr/bin/ciri-camera-probe ]] || {
    printf 'The compiled camera client is not installed.\n' >&2; exit 1;
}
for command in unshare mount chroot env modprobe; do command -v "$command" >/dev/null; done
[[ -d /run/udev/data && ! -L /run/udev && ! -L /run/udev/data ]] || {
    printf 'Host udev database missing: /run/udev/data\n' >&2; exit 1;
}
# Heap drivers are modules in the camera kernel and do not autoload on open().
if [[ ! -e /dev/dma_heap/system ]]; then modprobe system_heap; fi
if [[ ! -e /dev/dma_heap/scp-isp-cma-region ]]; then modprobe cma_heap; fi
for heap in /dev/dma_heap/system /dev/dma_heap/scp-isp-cma-region; do
    [[ -c $heap ]] || { printf 'Required vendor DMA heap missing: %s\n' "$heap" >&2; exit 1; }
done
export LIBCAMERA_LOG_LEVELS="${LIBCAMERA_LOG_LEVELS:-*:DEBUG}"
exec unshare --mount --propagation private /bin/bash -c '
    set -euo pipefail
    rootfs=$1; shift
    for name in dev proc sys; do
        mount --rbind "/$name" "$rootfs/$name"
    done
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$rootfs/run"
    mkdir -p "$rootfs/run/camera" "$rootfs/run/chromeos-config/v1"
    # The vendor pipeline reads this before discovering cameras. The recovered
    # ChromeOS configuration identifies this model as ciri. Keep it private.
    printf "ciri\n" > "$rootfs/run/chromeos-config/v1/name"
    # The libcamera udev enumerator requires initialized-device records. A fresh
    # /run hides them even though /dev and /sys are visible. Expose only the
    # database read-only; do not expose the host udev control socket.
    mkdir -p "$rootfs/run/udev/data"
    mount --bind /run/udev/data "$rootfs/run/udev/data"
    mount -o remount,bind,ro,nosuid,nodev,noexec "$rootfs/run/udev/data"
    mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs "$rootfs/tmp"
    if [[ $1 == --capture ]]; then
        [[ ! -L /opt/ciri-camera/captures ]]
        mkdir -p /opt/ciri-camera/captures
        output=$(mktemp -d /opt/ciri-camera/captures/run.XXXXXXXX)
        [[ ! -L $rootfs/captures ]]
        mkdir -p "$rootfs/captures"
        mount --bind "$output" "$rootfs/captures"
        printf "Capture output: %s\n" "$output"
    fi
    exec env -i PATH=/usr/bin:/bin HOME=/tmp \
        LIBCAMERA_LOG_LEVELS="$LIBCAMERA_LOG_LEVELS" \
        LIBCAMERA_IPA_MODULE_PATH=/usr/lib64/libcamera \
        LIBCAMERA_IPA_PROXY_PATH=/usr/lib64 \
        LIBCAMERA_IPA_CONFIG_PATH=/etc/camera/libcamera/ipa:/usr/share/libcamera/ipa \
        /usr/bin/chroot "$rootfs" /lib64/ld-linux-aarch64.so.1 \
        --library-path /usr/lib64:/lib64:/usr/share/cros-camera/libfs \
        /usr/bin/ciri-camera-probe "$@"
' ciri-camera-probe "$rootfs" "$@"
