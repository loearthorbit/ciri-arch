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
for command in unshare mount chroot env; do command -v "$command" >/dev/null; done
export LIBCAMERA_LOG_LEVELS="${LIBCAMERA_LOG_LEVELS:-*:DEBUG}"
exec unshare --mount --propagation private /bin/bash -c '
    set -euo pipefail
    rootfs=$1; shift
    for name in dev proc sys; do
        mount --rbind "/$name" "$rootfs/$name"
    done
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$rootfs/run"
    mkdir "$rootfs/run/camera"
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
        --library-path /usr/lib64:/lib64 /usr/bin/ciri-camera-probe "$@"
' ciri-camera-probe "$rootfs" "$@"
