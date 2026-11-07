#!/bin/bash
# Standard libcamerasrc -> videoconvert -> unbuffered filesink; no custom capture loop.
set -euo pipefail
camera=${1:-back}
device=${2:-/dev/video42}
fps=${3:-18}
case "$camera" in
    back) camera_id=/base/soc/i2c@11ec1000/camera-main-sensor@31 ;;
    front) camera_id=/base/soc/i2c@11ec0000/camera-sub-sensor@37 ;;
    *) printf 'Usage: %s [back|front [LOOPBACK_DEVICE [FPS_15_TO_30]]]\n' "$0" >&2; exit 2 ;;
esac
[[ $# -le 3 && $fps =~ ^(1[5-9]|2[0-9]|30)$ ]] || { printf 'FPS must be an integer from 15 to 30.\n' >&2; exit 2; }
[[ $EUID == 0 && $(uname -m) == aarch64 ]] || { printf 'Run with sudo on Ciri.\n' >&2; exit 1; }
[[ $device =~ ^/dev/video[0-9]+$ && -c $device && ! -L $device ]] || { printf 'An existing loopback video device is required.\n' >&2; exit 1; }
# Query the actual driver, not an enumeration number or card label. Never write
# frames into a sensor/capture node accidentally. Do not create/load loopback here.
python3 -I - "$device" <<'PY'
import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
try:
    capability = bytearray(104)
    fcntl.ioctl(fd, 0x80685600, capability, True)  # VIDIOC_QUERYCAP
    driver = bytes(capability[:16]).split(b'\0', 1)[0]
    caps, device_caps = struct.unpack_from('=II', capability, 84)
    caps = device_caps if caps & 0x80000000 else caps
    if driver != b'v4l2 loopback' or not caps & 0x2:
        raise SystemExit('Target is not an available v4l2loopback OUTPUT device; stop any existing producer.')
finally:
    os.close(fd)
PY
command -v v4l2-ctl >/dev/null || { printf 'Install v4l-utils on Ciri first.\n' >&2; exit 1; }
# filesink does not negotiate V4L2 formats. Set the device explicitly with the
# standard host utility, then write each packed YUY2 frame without buffering.
# GStreamer 1.22 v4l2sink's RW output branch only warns and drops the frame.
v4l2-ctl -d "$device" \
    --set-fmt-video-out=width=640,height=480,pixelformat=YUYV \
    --set-output-parm="$fps"
printf 'Streaming %s camera at requested %s fps to %s (colour remains uncorrected).\n' "$camera" "$fps" "$device"
exec /opt/ciri-libcamera/run-libcamera --gst-launch -e \
    libcamerasrc "camera-name=$camera_id" ! \
    "video/x-raw,format=NV12,width=640,height=480,framerate=$fps/1" ! \
    queue max-size-buffers=4 max-size-bytes=0 max-size-time=0 leaky=downstream ! \
    videoconvert ! video/x-raw,format=YUY2 ! \
    filesink "location=$device" buffer-mode=unbuffered sync=false
