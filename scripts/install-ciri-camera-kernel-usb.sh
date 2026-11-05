#!/bin/bash
# Install the prepared camera kernel on the inspected Ciri USB. No flashing.
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stage="$repo/build/usb-camera-kernel.DnSezVCO"
boot=/run/media/jono/ARCHBOOT
root=/run/media/jono/ARCHCIRI
release=7.3.0-rc4-ciri
check_mounts() {
    [[ $(findmnt -n -o TARGET --target "$boot") == "$boot" ]]
    [[ $(findmnt -n -o TARGET --target "$root") == "$root" ]]
    [[ $(findmnt -n -o UUID --target "$boot") == 1C9E-7182 ]]
    [[ $(findmnt -n -o UUID --target "$root") == 9d00d9e7-22f1-4f32-8006-19dae6343c92 ]]
}
check_mounts
(cd "$stage"; sha256sum --check --status SHA256SUMS)
printf '%s  %s\n' \
    ab4b2130de3ba7be9ac11921a60566c8a079538937d57e137c9b3c95d750fadb "$stage/Image-ciri" \
    2c47103a8a8d932e73b99a80c1c40a21949a4e2ce31f296c74bcfa65d1b71ead "$stage/initramfs-ciri.img" \
    5bd221e55df39a53c907bc357af58fb59aaafd4e9d4dd1b19705a4904f02e520 "$boot/Image-ciri" \
    cc0e4e85137ee010b51a5095a88c2cf68344e344d6597287848e8c60f8ff3a43 "$boot/initramfs-ciri.img" | sha256sum --check --status
for path in "$root/usr" "$root/usr/lib" "$root/usr/lib/modules" "$root/usr/lib/modules/$release"; do
    [[ -d $path && ! -L $path ]]
done
newmodules="$root/usr/lib/modules/.${release}.camera-new"
backupmodules="$root/usr/lib/modules/.${release}.pre-camera"
for path in "$newmodules" "$backupmodules" "$boot/Image-ciri.camera-new" "$boot/initramfs-ciri.img.camera-new" "$boot/Image-ciri.pre-camera" "$boot/initramfs-ciri.img.pre-camera"; do
    [[ ! -e $path && ! -L $path ]] || { printf 'Path already exists: %s\n' "$path" >&2; exit 1; }
done
# Prepare replacements completely before switching active files.
cp -a "$stage/modules/lib/modules/$release" "$newmodules"
chown -R root:root "$newmodules"
install -m 0644 "$stage/Image-ciri" "$boot/Image-ciri.camera-new"
install -m 0644 "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.camera-new"
diff -qr "$stage/modules/lib/modules/$release" "$newmodules"
cmp "$stage/Image-ciri" "$boot/Image-ciri.camera-new"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.camera-new"
sync
check_mounts
mv "$root/usr/lib/modules/$release" "$backupmodules"
mv "$newmodules" "$root/usr/lib/modules/$release"
mv "$boot/initramfs-ciri.img" "$boot/initramfs-ciri.img.pre-camera"
mv "$boot/initramfs-ciri.img.camera-new" "$boot/initramfs-ciri.img"
mv "$boot/Image-ciri" "$boot/Image-ciri.pre-camera"
mv "$boot/Image-ciri.camera-new" "$boot/Image-ciri"
sync
cmp "$stage/Image-ciri" "$boot/Image-ciri"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img"
diff -qr "$stage/modules/lib/modules/$release" "$root/usr/lib/modules/$release"
printf '\nCamera kernel, matching modules and initramfs installed and verified.\n'
printf 'Previous kernel/initramfs/modules retained with pre-camera names.\n'
printf 'GRUB, Submarine, DTB, firmware, audio settings and user files unchanged.\n'
printf 'Safely eject both USB filesystems before unplugging.\n'
