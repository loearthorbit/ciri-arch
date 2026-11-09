#!/bin/bash
# Temporary standard-cam diagnostic; neither installed cam nor manifest is changed.
set -euo pipefail
trace_formats=0
if [[ $# == 1 && $1 == --trace-formats ]]; then trace_formats=1; shift; fi
[[ $# == 0 && $EUID == 0 && $(uname -m) == aarch64 ]] || {
    printf 'Run with sudo on Ciri: %s [--trace-formats]. Stop the camera producer first.\n' "$0" >&2
    exit 2
}
if (( trace_formats )); then
    command -v strace >/dev/null || { printf 'strace is required for --trace-formats.\n' >&2; exit 1; }
fi
cam=/opt/ciri-libcamera/rootfs/usr/bin/cam
captures=/opt/ciri-libcamera/captures
replacement=/home/jono/cam-script.mbvaPEio/cam
test_script=/home/jono/test-ciri-camera-colour-bars.sh
[[ -f $cam && ! -L $cam && -d $captures && ! -L $captures &&
   -f $replacement && ! -L $replacement && -f $test_script && ! -L $test_script ]]
ulimit -c 0
umask 077
work=$(mktemp -d /dev/shm/ciri-colour-ram.XXXXXXXX)
printf 'Retained RAM directory: %s\n' "$work"
mkdir "$work/captures"
cp "$replacement" "$work/cam"
printf '%s  %s\n' \
    da173236ea70e9e7c25f17c2f10e3b89fe6a14632778dfb0fa4fe705fa9e8cf8 \
    "$work/cam" | sha256sum --check -
trace=()
if (( trace_formats )); then
    trace=(strace -f -qq -v -yy -s 256 -e trace=ioctl -o "$work/ioctl.trace")
    # Name-to-node mapping is captured on this boot, not assumed from old logs.
    for node in /sys/class/video4linux/*; do
        [[ -f $node/name ]] || continue
        IFS= read -r name < "$node/name"
        printf '%s: %s\n' "${node##*/}" "$name"
    done > "$work/video-names.txt"
    retain_trace() {
        local status=$? file
        for file in "$work" "$work/ioctl.trace" "$work/video-names.txt"; do
            [[ ! -e $file ]] || chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$file" || true
        done
        printf '\nTrace files (also retained after a failed capture): %s/{ioctl.trace,video-names.txt}\n' "$work"
        return "$status"
    }
    trap retain_trace EXIT
fi
"${trace[@]}" unshare --mount --propagation private /bin/bash -s -- "$work" "$cam" "$captures" "$test_script" <<'NAMESPACE'
set -euo pipefail
mount --bind "$1/cam" "$2"
mount -o remount,bind,ro "$2"
mount --bind "$1/captures" "$3"
exec /bin/bash "$4"
NAMESPACE
# The private mounts have exited. Captures remain under the shared RAM directory.
frames=("$work"/captures/run.*/colour-bars-cam0-stream0-000063.nv12)
[[ ${#frames[@]} == 1 && -f ${frames[0]} ]] || {
    printf 'Expected final frame missing; no image decoded.\n' >&2
    exit 1
}
ffmpeg -hide_banner -loglevel error -n -f rawvideo \
    -pixel_format nv12 -video_size 640x480 -i "${frames[0]}" \
    -frames:v 1 "$work/colour-bars.png"
chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$work" "$work/colour-bars.png"
printf '\nPNG: %s/colour-bars.png\nInstalled cam and manifest were not replaced. RAM files disappear at reboot.\n' "$work"
