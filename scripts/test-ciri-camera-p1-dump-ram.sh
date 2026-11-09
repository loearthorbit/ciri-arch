#!/bin/bash
# Existing standard cam + optional pipeline dumps, all temporary RAM binds.
set -euo pipefail
[[ ( $# == 1 || ( $# == 2 && $2 == --scene ) ) && $1 == /* && $EUID == 0 && $(uname -m) == aarch64 ]] || {
    printf 'Run with sudo on Ciri: %s /absolute/path/to/dump-kit [--scene]\nStop the camera producer first.\n' "$0" >&2
    exit 2
}
kit=$1
mode=${2:-}
capture_name=colour-bars
last_frame=000063
if [[ $mode == --scene ]]; then
    capture_name=scene
    last_frame=000179
fi
prefix=/opt/ciri-libcamera
cam=$prefix/rootfs/usr/bin/cam
core_dir=$prefix/rootfs/usr/lib/ciri-libcamera
launcher=$prefix/run-libcamera
replacement=/home/jono/cam-script.mbvaPEio/cam
[[ -d $kit && ! -L $kit && -d $prefix/captures && ! -L $prefix/captures ]]
# Packaging materializes aliases as separate regular files, not symlinks.
# Cover the SONAME used by DT_NEEDED as well as the unversioned/full names.
for file in "$cam" "$core_dir/libcamera.so" "$core_dir/libcamera.so.0.1" \
    "$core_dir/libcamera.so.0.1.0" "$launcher" "$replacement"; do
    [[ -f $file && ! -L $file ]]
done
ulimit -c 0
umask 077
work=$(mktemp -d /dev/shm/ciri-p1-dump.XXXXXXXX)
printf 'Retained RAM directory: %s\n' "$work"
mkdir "$work/captures"
# Copy only the diagnostic kit, not any recovered runtime. Verify the RAM copy
# before executing its authored scripts or binding its core.
for file in libcamera.so.0.1.0 build-info.txt run-libcamera test-p1-dump-ram.sh test-colour-bars.sh dump.cfg SHA256SUMS; do
    [[ -f $kit/$file && ! -L $kit/$file ]]
    cp "$kit/$file" "$work/$file"
done
(cd "$work" && sha256sum --check --strict SHA256SUMS)
cp "$replacement" "$work/cam"
printf '%s  %s\n' \
    da173236ea70e9e7c25f17c2f10e3b89fe6a14632778dfb0fa4fe705fa9e8cf8 \
    "$work/cam" | sha256sum --check -
chmod 0755 "$work/run-libcamera"
retain_dumps() {
    local status=$?
    # This new root-private directory contains only this diagnostic run.
    chown -hR "${SUDO_UID:-0}:${SUDO_GID:-0}" "$work" || true
    printf '\nRetained captures/dumps (including after failure): %s/captures\n' "$work"
    printf 'Installed core, cam, launcher and manifest were not replaced. RAM files disappear at reboot.\n'
    return "$status"
}
trap retain_dumps EXIT
unshare --mount --propagation private /bin/bash -s -- "$work" "$cam" "$core_dir" "$launcher" "$prefix/captures" "$mode" <<'NAMESPACE'
set -euo pipefail
work=$1
for pair in "cam:$2" "run-libcamera:$4"; do
    mount --bind "$work/${pair%%:*}" "${pair#*:}"
    mount -o remount,bind,ro "${pair#*:}"
done
for name in libcamera.so libcamera.so.0.1 libcamera.so.0.1.0; do
    mount --bind "$work/libcamera.so.0.1.0" "$3/$name"
    mount -o remount,bind,ro "$3/$name"
done
mount --bind "$work/captures" "$5"
if [[ ${6:-} == --scene ]]; then
    unset CIRI_P1_DUMP_CONFIG
    exec /bin/bash "$work/test-colour-bars.sh" --scene
fi
export CIRI_P1_DUMP_CONFIG="$work/dump.cfg"
exec /bin/bash "$work/test-colour-bars.sh"
NAMESPACE
# Do not interpret packed pipeline dumps as NV12. Only final standard-cam output
# has the independently traced 640x480 linear NV12 layout.
frames=("$work"/captures/run.*/"$capture_name-cam0-stream0-$last_frame.nv12")
[[ ${#frames[@]} == 1 && -f ${frames[0]} ]] || {
    printf 'Expected final frame missing; no image decoded.\n' >&2
    exit 1
}
ffmpeg -hide_banner -loglevel error -n -f rawvideo \
    -pixel_format nv12 -video_size 640x480 -i "${frames[0]}" \
    -frames:v 1 "$work/$capture_name.png"
printf '\nPNG: %s/%s.png\n' "$work" "$capture_name"
if [[ $mode == --scene ]]; then
    printf 'Normal-scene frame 179 decoded; inspect scene.png and the reported 3A metadata. Colour remains unverified.\n'
    exit 0
fi
# Config presence is not evidence of export. Require at least one nonempty
# payload, then print the names/sizes so the host can inspect actual formats.
shopt -s nullglob
dumps=("$work"/captures/run.*/p1-dump/UKey*/*)
nonempty=0
for file in "${dumps[@]}"; do
    [[ -f $file && -s $file ]] || continue
    nonempty=$((nonempty + 1))
    stat --printf='%s bytes  %n\n' "$file"
done
(( nonempty > 0 )) || {
    printf 'No nonempty pipeline dump files found. Read the capture log; no upstream pixels established.\n' >&2
    exit 1
}
printf 'Pipeline dump files exist; requested stages, pixel correctness and colour remain unverified.\n'
