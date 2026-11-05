#!/bin/bash
# Copy recovered ChromeOS camera userspace into an isolated root on Ciri's USB.
# This does not install a camera client, execute vendor code, or enable services.
set -euo pipefail
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this script with sudo.'
[[ $# -eq 0 ]] || fail 'This script takes no arguments.'

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source="$repo/build/ciri-camera-recovery.sb0PBtT8"
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
[[ ! -e $destination && ! -L $destination ]] || fail "$destination already exists; nothing overwritten."
[[ -d $source/files && ! -L $source/files ]] || fail 'Recovered camera files are missing.'
[[ -f $source/SHA256SUMS && -f $source/camera-dependencies.json ]] || fail 'Extraction inventory is missing.'
for name in usr/lib64/libcamera.so.0.1.0 usr/lib64/libcamera/ipa_mtkisp7.so \
    usr/lib64/mtkisp7_ipa_proxy.so usr/lib64/libgc05a2_mipi_raw_tuning.so \
    usr/lib64/libgc08a3_mipi_raw_tuning.so etc/camera/libcamera/camera_hal.yaml \
    usr/share/cros-camera/libfs/libfacessd_cros.so; do
    [[ -f $source/files/$name ]] || fail "Required recovered file is missing: $name"
done
printf 'Verifying recovered files...\n'
(cd -- "$source/files"; sha256sum --check --status "$source/SHA256SUMS")
required=$(du -sk -- "$source/files" | cut -f1)
available=$(df --output=avail -k -- "$root" | tr -cd '0-9\n')
(( available > required + 65536 )) || fail 'Insufficient free space for the camera runtime.'

# A new private destination only: never copy into Arch's /usr/lib or /etc.
check_mount
mkdir -- "$destination"
trap 'printf "Copy failed; partial files retained at %s. No system libraries replaced.\n" "$destination" >&2' ERR
cp -a --no-preserve=ownership -- "$source/files" "$destination/rootfs"
install -m 0644 -- "$source/SHA256SUMS" "$source/camera-dependencies.json" "$destination/"
mkdir -p -- "$destination/rootfs"/{dev,proc,sys,run/camera,tmp,usr/bin}
chmod 1777 -- "$destination/rootfs/tmp"
(cd -- "$destination/rootfs"; sha256sum --check --status "$destination/SHA256SUMS")
check_mount
sync -f "$destination"
trap - ERR
printf '\nRecovered camera libraries/configuration copied and checksum-verified.\n'
printf 'On Ciri: /opt/ciri-camera/rootfs\n'
printf 'This is staged userspace, not a working camera application; no client has been built.\n'
printf 'Arch libraries, audio, kernel, boot files and services unchanged.\n'
printf 'Safely eject the USB filesystems before unplugging.\n'
