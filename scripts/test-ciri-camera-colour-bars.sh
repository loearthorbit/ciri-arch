#!/bin/bash
# Native Ciri diagnostic using the installed standard cam client, not a new capture loop.
set -euo pipefail
[[ ( $# == 0 || ( $# == 1 && $1 == --scene ) ) && $EUID == 0 && $(uname -m) == aarch64 ]] || {
    printf 'Run with sudo on Ciri: %s [--scene]. Stop the camera producer first.\n' "$0" >&2
    exit 2
}
launcher=/opt/ciri-libcamera/run-libcamera
[[ -f $launcher && ! -L $launcher && -x $launcher && -d /dev/shm ]] || {
    printf 'Installed camera launcher or shared-memory directory missing.\n' >&2
    exit 1
}
capture_name=colour-bars
pattern=2
capture_count=64
capture_timeout=20s
if [[ $# == 1 ]]; then
    capture_name=scene
    pattern=0
    capture_count=180
    capture_timeout=30s
    # No ODT export/import or tuning flags for the normal-scene test.
    unset CIRI_P1_DUMP_CONFIG
fi
umask 077
# The launcher recursively binds host /dev into its private root. Host /tmp is
# hidden by a fresh tmpfs, so an ordinary /tmp capture-script path would not work.
work=$(mktemp -d "/dev/shm/ciri-$capture_name.XXXXXXXX")
cat > "$work/$capture_name.yaml" <<YAML
properties:
  - loop: 1
frames:
  - 0:
      TestPatternMode: $pattern
YAML
printf 'Capture script (retained until reboot): %s/%s.yaml\n' "$work" "$capture_name"
if [[ $capture_name == scene ]]; then
    printf 'Rear-camera scene: test pattern OFF on every request; AE/AWB left automatic; 180 frames.\n'
else
    printf 'Requesting rear-sensor colour bars on every frame; inspect the saved pixels, not just metadata.\n'
fi
export LIBCAMERA_LOG_LEVELS='*:INFO,CameraSensor:DEBUG'
# A worker failure must not leave this diagnostic waiting indefinitely.
exec timeout --signal=INT --kill-after=5s "$capture_timeout" "$launcher" --camera /base/soc/i2c@11ec1000/camera-main-sensor@31 \
    --stream role=viewfinder,width=640,height=480,pixelformat=NV12 \
    --strict-formats --script="$work/$capture_name.yaml" \
    --capture="$capture_count" --metadata --file="$capture_name-#.nv12"
