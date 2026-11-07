#!/bin/bash
# Copy the recovered runtime and compiled diagnostic into Ciri's isolated prefix.
# No vendor code is executed; Arch libraries, audio and services are not changed.
set -euo pipefail
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this script with sudo.'
[[ $# -eq 0 ]] || fail 'This script takes no arguments.'

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source="$repo/build/ciri-camera-recovery.sb0PBtT8"
client="$repo/build/ciri-camera-client"
root=/run/media/jono/ARCHCIRI
destination="$root/opt/ciri-camera"
check_mount() {
    [[ -d $root && ! -L $root ]] || fail 'ARCHCIRI mount directory is missing or symlinked.'
    [[ $(findmnt -n -o TARGET --target "$root") == "$root" ]] || fail 'ARCHCIRI is not mounted.'
    [[ $(findmnt -n -o UUID --target "$root") == 9d00d9e7-22f1-4f32-8006-19dae6343c92 ]] || fail 'Unexpected root filesystem UUID.'
    [[ $(findmnt -n -o FSTYPE --target "$root") == ext4 ]] || fail 'Expected an ext4 root filesystem.'
    [[ -d $root/opt && ! -L $root/opt ]] || fail 'Expected a real /opt directory on ARCHCIRI.'
    [[ $(findmnt -n -o TARGET --target "$root/opt") == "$root" ]] || fail '/opt is on a different mount.'
}
check_mount
[[ -d $source/files && ! -L $source/files ]] || fail 'Recovered camera files are missing.'
[[ -f $source/SHA256SUMS && -f $source/camera-dependencies.json ]] || fail 'Extraction inventory is missing.'
[[ -f $client/ciri-camera-probe && -f $client/SHA256SUMS ]] || fail 'Build the camera client first.'
for name in usr/lib64/libcamera.so.0.1.0 usr/lib64/libcamera/ipa_mtkisp7.so \
    usr/lib64/mtkisp7_ipa_proxy.so usr/lib64/libgc05a2_mipi_raw_tuning.so \
    usr/lib64/libgc08a3_mipi_raw_tuning.so etc/camera/libcamera/camera_hal.yaml \
    usr/share/cros-camera/libfs/libfacessd_cros.so; do
    [[ -f $source/files/$name ]] || fail "Required recovered file is missing: $name"
done
printf 'Verifying recovered files and compiled client...\n'
(cd -- "$source/files"; sha256sum --check --status "$source/SHA256SUMS")
(cd -- "$client"; sha256sum --check --status SHA256SUMS)

existing=false
required=$(du -sk -- "$source/files" | cut -f1)
if [[ -e $destination || -L $destination ]]; then
    for dirpath in "$destination" "$destination/rootfs" "$destination/rootfs/usr" \
        "$destination/rootfs/usr/bin" "$destination/rootfs/usr/lib64"; do
        [[ -d $dirpath && ! -L $dirpath ]] || fail "Missing or symlinked existing directory: $dirpath"
    done
    [[ $(findmnt -n -o TARGET --target "$destination/rootfs") == "$root" ]] || fail 'Existing runtime is on a different mount.'
    cmp -- "$source/SHA256SUMS" "$destination/SHA256SUMS" || fail 'Existing runtime has a different inventory.'
    (cd -- "$destination/rootfs"; sha256sum --check --status "$source/SHA256SUMS")
    existing=true
    required=0
fi
for filepath in "$destination/rootfs/usr/bin/ciri-camera-probe" "$destination/run-camera" \
    "$destination/client-build-info.txt" "$destination/client-sdk-downloads.json" \
    "$destination/client-SHA256SUMS"; do
    [[ ! -e $filepath && ! -L $filepath ]] || fail "Already exists; nothing overwritten: $filepath"
done
available=$(df --output=avail -k -- "$root" | tr -cd '0-9\n')
(( available > required + 65536 )) || fail 'Insufficient free space for the camera runtime.'

# Reuse a verified previous copy, or create a new private root. Never touch /usr/lib.
check_mount
trap 'printf "Copy failed; partial files retained at %s. No system libraries replaced.\n" "$destination" >&2' ERR
if [[ $existing == false ]]; then
    mkdir -- "$destination"
    cp -a --no-preserve=ownership -- "$source/files" "$destination/rootfs"
    install -m 0644 -- "$source/SHA256SUMS" "$source/camera-dependencies.json" "$destination/"
    mkdir -p -- "$destination/rootfs"/{dev,proc,sys,run/camera,tmp,usr/bin}
    chmod 1777 -- "$destination/rootfs/tmp"
fi
install -m 0755 -- "$client/ciri-camera-probe" "$destination/rootfs/usr/bin/ciri-camera-probe"
install -m 0755 -- "$repo/scripts/run-ciri-camera-probe.sh" "$destination/run-camera"
install -m 0644 -- "$client/build-info.txt" "$destination/client-build-info.txt"
install -m 0644 -- "$client/sdk-downloads.json" "$destination/client-sdk-downloads.json"
cmp -- "$client/ciri-camera-probe" "$destination/rootfs/usr/bin/ciri-camera-probe"
cmp -- "$repo/scripts/run-ciri-camera-probe.sh" "$destination/run-camera"
(cd -- "$destination"; sha256sum rootfs/usr/bin/ciri-camera-probe run-camera \
    client-build-info.txt client-sdk-downloads.json > client-SHA256SUMS)
(cd -- "$destination/rootfs"; sha256sum --check --status "$destination/SHA256SUMS")
(cd -- "$destination"; sha256sum --check --status client-SHA256SUMS)
check_mount
sync -f "$destination"
trap - ERR
printf '\nCamera runtime and ARM64 diagnostic installed and checksum-verified.\n'
printf 'After booting Ciri, list cameras:\n  sudo /opt/ciri-camera/run-camera --list\n'
printf 'Then capture raw frames from camera 0:\n  sudo /opt/ciri-camera/run-camera --capture 0\n'
printf 'Arch libraries, audio, kernel, boot files and services unchanged.\n'
printf 'Safely eject the USB filesystems before unplugging.\n'
