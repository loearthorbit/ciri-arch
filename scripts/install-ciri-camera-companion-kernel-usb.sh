#!/bin/bash
# Install the cached IMGSYS/AIE rebuild on the inspected Ciri USB. No flashing.
set -euo pipefail
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run with sudo.'
[[ $# -eq 0 ]] || fail 'This targeted installer takes no arguments.'
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stage="$repo/build/usb-camera-companion.sddmr37t"
boot=/run/media/jono/ARCHBOOT
root=/run/media/jono/ARCHCIRI
release=7.3.0-rc4-ciri
check_mounts() {
    [[ $(findmnt -n -o TARGET --target "$boot") == "$boot" &&
       $(findmnt -n -o UUID --target "$boot") == 1C9E-7182 &&
       $(findmnt -n -o FSTYPE --target "$boot") == vfat ]] ||
        fail 'Mount the expected ARCHBOOT filesystem at /run/media/jono/ARCHBOOT.'
    [[ $(findmnt -n -o TARGET --target "$root") == "$root" &&
       $(findmnt -n -o UUID --target "$root") == 9d00d9e7-22f1-4f32-8006-19dae6343c92 &&
       $(findmnt -n -o FSTYPE --target "$root") == ext4 ]] ||
        fail 'Mount the expected ARCHCIRI filesystem at /run/media/jono/ARCHCIRI.'
    [[ ! -L $boot && ! -L $root ]] || fail 'Mount directories must not be symlinks.'
}
check_mounts
(cd "$stage"; sha256sum --check --status SHA256SUMS)
for option in VIDEO_MTK_ISP_71_IMGSYS VIDEO_MTK_AIE; do
    grep -qx "CONFIG_${option}=m" "$stage/kernel.config" || fail "Missing staged CONFIG_${option}=m."
done
for filepath in "$stage/Image-ciri" "$stage/initramfs-ciri.img" "$boot/Image-ciri" "$boot/initramfs-ciri.img"; do
    [[ -f $filepath && ! -L $filepath ]] || fail "Expected a regular file: $filepath"
done
printf '%s  %s\n' \
    c61bdeaea2e0436e9a9ca944345d9106b5b62db3c5a5a01be61a78c98133ff8f "$stage/Image-ciri" \
    c8fb6b4ab1fde6d7163be16e87973e982025851b03129fc23da1eca23b3ff8c0 "$stage/initramfs-ciri.img" \
    ab4b2130de3ba7be9ac11921a60566c8a079538937d57e137c9b3c95d750fadb "$boot/Image-ciri" \
    2c47103a8a8d932e73b99a80c1c40a21949a4e2ce31f296c74bcfa65d1b71ead "$boot/initramfs-ciri.img" | sha256sum --check --status
for dirpath in "$root/usr" "$root/usr/lib" "$root/usr/lib/modules" "$root/usr/lib/modules/$release" "$stage/modules" "$stage/modules/lib" "$stage/modules/lib/modules" "$stage/modules/lib/modules/$release"; do
    [[ -d $dirpath && ! -L $dirpath ]] || fail "Expected a real directory: $dirpath"
done
for module in drivers/media/platform/mediatek/aie/mtk-aie.ko drivers/media/platform/mediatek/isp/isp_7x/imgsys/mtk_imgsys.ko; do
    [[ -f $stage/modules/lib/modules/$release/kernel/$module ]] || fail "Missing staged module: $module"
done
# The donor initramfs contains scp-dual.img, but not scp_c0.img. Preserve its
# firmware layout and require the multimedia SCP firmware on the root filesystem.
firmware_found=0
for firmware in "$root/usr/lib/firmware/mediatek/mt8188/scp_c0.img"{,.zst,.xz}; do
    [[ ! -f $firmware ]] || firmware_found=1
done
[[ $firmware_found -eq 1 ]] || fail 'Required multimedia SCP firmware missing from ARCHCIRI: mediatek/mt8188/scp_c0.img (or compressed equivalent). No files changed.'
newmodules="$root/usr/lib/modules/.${release}.camera-companion-new"
backupmodules="$root/usr/lib/modules/.${release}.pre-camera-companion"
for filepath in "$newmodules" "$backupmodules" "$boot/Image-ciri.camera-companion-new" "$boot/initramfs-ciri.img.camera-companion-new" "$boot/Image-ciri.pre-camera-companion" "$boot/initramfs-ciri.img.pre-camera-companion"; do
    [[ ! -e $filepath && ! -L $filepath ]] || fail "Path already exists: $filepath"
done
available_bytes() {
    df -B1 --output=avail -- "$1" | { read -r header; read -r available; printf '%s\n' "$available"; }
}
boot_required=$(( $(stat -c %s "$stage/Image-ciri") + $(stat -c %s "$stage/initramfs-ciri.img") + 16 * 1024 * 1024 ))
read -r module_bytes _ < <(du -sb -- "$stage/modules/lib/modules/$release")
[[ $(available_bytes "$boot") -ge $boot_required ]] || fail 'Not enough free space on ARCHBOOT to stage both replacements.'
[[ $(available_bytes "$root") -ge $((module_bytes + 64 * 1024 * 1024)) ]] || fail 'Not enough free space on ARCHCIRI to stage the matching modules.'
# Prepare and verify replacements before switching any active paths. Existing
# pre-camera backups stay untouched; this upgrade uses pre-camera-companion.
cp -a "$stage/modules/lib/modules/$release" "$newmodules"
chown -R root:root "$newmodules"
install -m 0644 "$stage/Image-ciri" "$boot/Image-ciri.camera-companion-new"
install -m 0644 "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.camera-companion-new"
diff -qr "$stage/modules/lib/modules/$release" "$newmodules"
cmp "$stage/Image-ciri" "$boot/Image-ciri.camera-companion-new"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.camera-companion-new"
sync -f "$root"
sync -f "$boot"
check_mounts
mv "$root/usr/lib/modules/$release" "$backupmodules"
mv "$newmodules" "$root/usr/lib/modules/$release"
mv "$boot/initramfs-ciri.img" "$boot/initramfs-ciri.img.pre-camera-companion"
mv "$boot/initramfs-ciri.img.camera-companion-new" "$boot/initramfs-ciri.img"
mv "$boot/Image-ciri" "$boot/Image-ciri.pre-camera-companion"
mv "$boot/Image-ciri.camera-companion-new" "$boot/Image-ciri"
sync -f "$root"
sync -f "$boot"
cmp "$stage/Image-ciri" "$boot/Image-ciri"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img"
diff -qr "$stage/modules/lib/modules/$release" "$root/usr/lib/modules/$release"
printf '\nIMGSYS/AIE camera kernel, matching modules and initramfs installed and verified.\n'
printf 'Previous kernel/initramfs/modules retained with pre-camera-companion names.\n'
printf 'GRUB, Submarine, DTBs, rootfs firmware, camera userspace and audio configuration unchanged.\n'
printf 'Safely eject both USB filesystems, then boot Ciri with the updated kernel.\n'
printf 'On Ciri: sudo modprobe mtk_imgsys mtk-aie\n'
printf '         sudo /opt/ciri-camera/run-camera --list\n'
printf '         sudo /opt/ciri-camera/run-camera --capture 0\n'
