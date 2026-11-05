#!/bin/bash
# Install the verified 2026-11-04 crypto/Landlock kernel on the inspected Ciri USB.
# Run from this checkout: sudo bash scripts/install-new-ciri-kernel-usb.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stage="$repo/build/usb-crypto-kernel.QBzvmKys"
boot=/run/media/jono/ARCHBOOT
root=/run/media/jono/ARCHCIRI
release=7.3.0-rc4-ciri

check_mounts() {
    [[ $(findmnt -n -o TARGET --target "$boot") == "$boot" ]]
    [[ $(findmnt -n -o TARGET --target "$root") == "$root" ]]
    [[ $(findmnt -n -o UUID --target "$boot" | tr -d '[:space:]') == 1C9E-7182 ]]
    [[ $(findmnt -n -o UUID --target "$root" | tr -d '[:space:]') == 9d00d9e7-22f1-4f32-8006-19dae6343c92 ]]
}
check_mounts
python3 -I - "$stage" "$boot" "$root" <<'PY'
from pathlib import Path
import hashlib
import json
import shutil
import sys
s, boot, root = map(Path, sys.argv[1:])
def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()
record=json.loads((s/'verified-files.json').read_text())
for name, expected in record['files'].items():
    p=s/name
    assert p.resolve().is_relative_to(s.resolve()) and not p.is_symlink(), name
    assert digest(p)==expected, f'Staged file changed: {name}'
assert digest(s/'Image-ciri')=='5bd221e55df39a53c907bc357af58fb59aaafd4e9d4dd1b19705a4904f02e520'
assert digest(s/'initramfs-ciri.img')=='cc0e4e85137ee010b51a5095a88c2cf68344e344d6597287848e8c60f8ff3a43'
for p, key in ((boot/'Image-ciri','old_kernel'),(boot/'initramfs-ciri.img','old_initramfs'),(boot/'grub/grub.cfg','old_grub_boot'),(root/'boot/grub/grub.cfg','old_grub_root')):
    assert p.is_file() and not p.is_symlink(), str(p)
    assert digest(p)==record[key], f'USB file changed since preparation: {p}'
config=(s/'kernel.config').read_text().splitlines()
for name in ('FRAMEBUFFER_CONSOLE_ROTATION','SECURITY_LANDLOCK','CRYPTO_USER_API','CRYPTO_USER_API_HASH','CRYPTO_USER_API_SKCIPHER','CRYPTO_AES','CRYPTO_CBC','CRYPTO_CMAC','CRYPTO_DES','CRYPTO_DH','CRYPTO_HMAC','CRYPTO_MD5','CRYPTO_SHA1','CRYPTO_SHA256','CRYPTO_SHA512'):
    assert f'CONFIG_{name}=y' in config, name
text=(s/'grub.cfg').read_text()
assert 'root=UUID=9d00d9e7-22f1-4f32-8006-19dae6343c92' in text
assert '1C9E-7182' in text and 'fbcon=rotate:3' in text
for p in (root/'usr',root/'usr/lib',root/'usr/lib/modules',root/'usr/lib/modules/7.3.0-rc4-ciri',root/'boot',root/'boot/grub',boot/'grub'):
    assert p.is_dir() and not p.is_symlink(), f'Unexpected directory: {p}'
boot_bytes=(s/'Image-ciri').stat().st_size+(s/'initramfs-ciri.img').stat().st_size+1024*1024
module_bytes=sum(p.stat().st_size for p in (s/'modules').rglob('*') if p.is_file())
assert shutil.disk_usage(boot).free>boot_bytes, 'Not enough boot space for replacement files'
assert shutil.disk_usage(root).free>module_bytes+16*1024*1024, 'Not enough root space for replacement modules'
print('USB identity, staged checksums, kernel options and space verified', flush=True)
PY

newmodules="$root/usr/lib/modules/.${release}.crypto-new"
oldmodules="$root/usr/lib/modules/.${release}.crypto-old"
for path in "$newmodules" "$oldmodules" "$boot/Image-ciri.crypto-new" "$boot/initramfs-ciri.img.crypto-new" "$boot/grub/grub.cfg.crypto-new" "$root/boot/grub/grub.cfg.crypto-new"; do
    [[ ! -e "$path" && ! -L "$path" ]] || { printf 'Temporary path already exists: %s\n' "$path" >&2; exit 1; }
done
# Prepare complete replacements before changing any active boot path.
cp -a "$stage/modules/lib/modules/$release" "$newmodules"
chown -R root:root "$newmodules"
install -m 0644 "$stage/Image-ciri" "$boot/Image-ciri.crypto-new"
install -m 0644 "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.crypto-new"
install -m 0644 "$stage/grub.cfg" "$boot/grub/grub.cfg.crypto-new"
install -m 0644 "$stage/grub.cfg" "$root/boot/grub/grub.cfg.crypto-new"
diff -qr "$stage/modules/lib/modules/$release" "$newmodules"
cmp "$stage/Image-ciri" "$boot/Image-ciri.crypto-new"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img.crypto-new"
sync
check_mounts
mv "$root/usr/lib/modules/$release" "$oldmodules"
mv "$newmodules" "$root/usr/lib/modules/$release"
mv -f "$boot/initramfs-ciri.img.crypto-new" "$boot/initramfs-ciri.img"
mv -f "$boot/Image-ciri.crypto-new" "$boot/Image-ciri"
mv -f "$root/boot/grub/grub.cfg.crypto-new" "$root/boot/grub/grub.cfg"
mv -f "$boot/grub/grub.cfg.crypto-new" "$boot/grub/grub.cfg"
sync
cmp "$stage/Image-ciri" "$boot/Image-ciri"
cmp "$stage/initramfs-ciri.img" "$boot/initramfs-ciri.img"
cmp "$stage/grub.cfg" "$boot/grub/grub.cfg"
cmp "$stage/grub.cfg" "$root/boot/grub/grub.cfg"
diff -qr "$stage/modules/lib/modules/$release" "$root/usr/lib/modules/$release"
# The old module directory is temporary, not a retained backup.
rm -rf -- "$oldmodules"
sync
printf '\nUSB update applied and verified. No old kernel/module/GRUB backups retained.\n'
printf 'Installed: new kernel, matching modules/initramfs; GRUB retains fbcon=rotate:3.\n'
printf 'Submarine, DTB, UUIDs, login settings and installed packages were not changed.\n'
printf 'Safely eject both USB filesystems before unplugging the drive.\n'
printf 'After boot: zgrep -E "CONFIG_(CRYPTO_USER_API_HASH|CRYPTO_USER_API_SKCIPHER|SECURITY_LANDLOCK)=" /proc/config.gz\n'
