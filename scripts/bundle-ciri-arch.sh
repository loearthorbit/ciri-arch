#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: ${0##*/} --sku 0..7 --kernel-dir DIR --firmware-dir DIR --cgpt-dir DIR --output FILE.img" \
        '       [--rootfs FILE.tar.gz --rootfs-sha256 SHA256]' \
        '       [--ssh-key FILE.pub] [--password-hash-file FILE]' \
        '       [--submarine-kernel FILE] [--size-gib N] [--busybox FILE]' \
        '       [--build-image localhost/submarine-build:latest]' \
        '' \
        'Build an Arch Linux ARM/Ciri GPT disk-image FILE. Never writes a physical drive.' \
        'Partition 1: 32 MiB ChromeOS Submarine kernel; 2: 256 MiB FAT boot; 3: ext4 root.' \
        'Default image size: 8 GiB. The selected Ciri DTB is packed into Submarine.' \
        '' \
        'Without --rootfs, download the generic AArch64 rootfs over HTTPS and verify its' \
        'detached signature against the official, pinned Arch Linux ARM signing keys.' \
        'A supplied local rootfs requires --rootfs-sha256.' \
        '--cgpt-dir must contain the static ARM64 cgpt build artifacts and SHA256SUMS.' \
        'The native cgpt is installed at /usr/local/bin/cgpt for on-device GPT changes.' \
        'Supply an SSH public key and/or a crypt password hash file for login.' \
        'The default root/root and alarm/alarm passwords are never retained.' \
        '' \
        'Runs without mounts or ARM emulation: uses fakeroot, mkfs.ext4 -d and mtools.' \
        'Requires the ARM64 BusyBox from mkinitcpio-busybox, plus mount, blkid and kmod.' \
        'Image construction is tested separately from a real Duet boot.'
}

die() { printf '%s\n' "$*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"; }
repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sku= kernel_dir= firmware_dir= cgpt_dir= output= rootfs= rootfs_sha256= ssh_key= password_hash_file= busybox=
submarine_kernel="$repo_root/artifacts/submarine/Image.gz"
size_gib=8
build_image=localhost/submarine-build:latest
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --sku) need_value "$@"; sku=$2; shift 2 ;;
        --kernel-dir) need_value "$@"; kernel_dir=$2; shift 2 ;;
        --firmware-dir) need_value "$@"; firmware_dir=$2; shift 2 ;;
        --cgpt-dir) need_value "$@"; cgpt_dir=$2; shift 2 ;;
        --output) need_value "$@"; output=$2; shift 2 ;;
        --rootfs) need_value "$@"; rootfs=$2; shift 2 ;;
        --rootfs-sha256) need_value "$@"; rootfs_sha256=$2; shift 2 ;;
        --ssh-key) need_value "$@"; ssh_key=$2; shift 2 ;;
        --password-hash-file) need_value "$@"; password_hash_file=$2; shift 2 ;;
        --submarine-kernel) need_value "$@"; submarine_kernel=$2; shift 2 ;;
        --size-gib) need_value "$@"; size_gib=$2; shift 2 ;;
        --busybox) need_value "$@"; busybox=$2; shift 2 ;;
        --build-image) need_value "$@"; build_image=$2; shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done
[[ "$sku" =~ ^[0-7]$ ]] || die '--sku must identify the actual Ciri SKU, from 0 to 7.'
[[ -n "$kernel_dir" && -n "$firmware_dir" && -n "$cgpt_dir" && -n "$output" ]] || die '--kernel-dir, --firmware-dir, --cgpt-dir and --output are required.'
[[ "$size_gib" =~ ^[0-9]+$ && "$size_gib" -ge 2 && "$size_gib" -le 128 ]] || die '--size-gib must be between 2 and 128.'
[[ -n "$ssh_key" || -n "$password_hash_file" ]] || die 'Provide --ssh-key and/or --password-hash-file; default passwords are not allowed.'
[[ -z "$rootfs" || "$rootfs_sha256" =~ ^[a-fA-F0-9]{64}$ ]] || die 'A local --rootfs requires a 64-digit --rootfs-sha256.'
[[ -n "$rootfs" || -z "$rootfs_sha256" ]] || die '--rootfs-sha256 requires --rootfs.'
[[ ! -e "$output" && ! -L "$output" ]] || die "Refusing to replace $output"
[[ ! -e "$output.sha256" && ! -L "$output.sha256" && ! -e "$output.json" && ! -L "$output.json" ]] || die 'Output sidecar files already exist.'
[[ -d "$(dirname -- "$output")" ]] || die 'Create the output directory first.'
output=$(realpath -m -- "$output")
[[ "$output" != /dev/* && "$output" != /proc/* && "$output" != /sys/* ]] || die 'Output must be a new image file, not a device or system path.'
for dependency in python3 bsdtar gzip cpio mkfs.ext4 mkfs.vfat mcopy mmd sgdisk cgpt dd truncate sha256sum realpath readelf; do
    command -v "$dependency" >/dev/null || die "$dependency is required."
done
if [[ "$EUID" -ne 0 && -z "${FAKEROOTKEY:-}" ]]; then
    command -v fakeroot >/dev/null || die 'fakeroot is required when not running as root.'
    exec fakeroot -- "$BASH" "$0" --sku "$sku" --kernel-dir "$kernel_dir" \
        --firmware-dir "$firmware_dir" --cgpt-dir "$cgpt_dir" --output "$output" --size-gib "$size_gib" \
        --submarine-kernel "$submarine_kernel" --build-image "$build_image" \
        ${rootfs:+--rootfs} ${rootfs:+"$rootfs"} \
        ${rootfs_sha256:+--rootfs-sha256} ${rootfs_sha256:+"$rootfs_sha256"} \
        ${ssh_key:+--ssh-key} ${ssh_key:+"$ssh_key"} \
        ${password_hash_file:+--password-hash-file} ${password_hash_file:+"$password_hash_file"} \
        ${busybox:+--busybox} ${busybox:+"$busybox"}
fi
kernel_dir=$(realpath -e -- "$kernel_dir")
firmware_dir=$(realpath -e -- "$firmware_dir")
cgpt_dir=$(realpath -e -- "$cgpt_dir")
submarine_kernel=$(realpath -e -- "$submarine_kernel")
for file in cgpt LICENSE source-revision.txt build-info.txt SHA256SUMS; do
    [[ -s "$cgpt_dir/$file" ]] || die "Missing ARM64 cgpt artifact: $file"
done
for file in Image modules.tar.zst kernel-release.txt kernel.config SHA256SUMS "dtbs/mt8188-geralt-ciri-sku$sku.dtb"; do
    [[ -f "$kernel_dir/$file" ]] || die "Missing kernel artifact: $file"
done
for file in himax_i2chid_1002.bin himax_i2chid_1003.bin himax_i2chid_1004.bin mediatek/mt8188/scp-dual.img SHA256SUMS; do
    [[ -s "$firmware_dir/$file" ]] || die "Missing extracted firmware: $file (wait for firmware extraction to finish)."
done
[[ -f "$submarine_kernel" ]] || die 'Submarine kernel must be a regular file.'
[[ -z "$rootfs" || -f "$rootfs" ]] || die 'Rootfs archive must be a regular file.'
[[ -z "$ssh_key" || -s "$ssh_key" ]] || die 'SSH public key file is empty or absent.'
[[ -z "$password_hash_file" || -s "$password_hash_file" ]] || die 'Password hash file is empty or absent.'
if [[ -n "$ssh_key" ]]; then
    command -v ssh-keygen >/dev/null || die 'ssh-keygen is required to validate the public key.'
    ssh-keygen -lf "$ssh_key" >/dev/null || die 'Invalid SSH public key.'
fi

packer=local
if ! command -v depthchargectl >/dev/null; then
    command -v podman >/dev/null || die 'Install depthcharge-tools, or provide the cached Podman Submarine build image.'
    env -u LD_PRELOAD -u FAKEROOTKEY podman image exists "$build_image" || die "Podman image $build_image is not cached; it is not pulled automatically."
    packer=podman
elif ! command -v futility >/dev/null; then
    die 'futility is required with the local depthchargectl packer.'
fi

work=$(mktemp -d "$(dirname -- "$output")/.arch-ciri-work.XXXXXXXX")
image=$(mktemp "$(dirname -- "$output")/.arch-ciri-image.XXXXXXXX")
cleanup() {
    [[ -z "${work:-}" ]] || rm -rf -- "$work"
    [[ -z "${image:-}" ]] || rm -f -- "$image"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$work/rootfs" "$work/boot" "$work/initrd" "$work/loader-dtbs" "$work/archive" "$work/metadata"

python3 -I - "$kernel_dir" "$firmware_dir" "$sku" "$cgpt_dir" <<'PY'
import hashlib
from pathlib import Path
import re
import struct
import subprocess
import sys
kernel, firmware = map(Path, sys.argv[1:3])
sku = sys.argv[3]
cgpt = Path(sys.argv[4])
for directory, required in (
    (kernel, {'Image', 'modules.tar.zst', 'kernel-release.txt', 'kernel.config', f'dtbs/mt8188-geralt-ciri-sku{sku}.dtb'}),
    (firmware, {'himax_i2chid_1002.bin', 'himax_i2chid_1003.bin', 'himax_i2chid_1004.bin', 'mediatek/mt8188/scp-dual.img'}),
    (cgpt, {'cgpt', 'LICENSE', 'source-revision.txt', 'build-info.txt'}),
):
    expected = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.removeprefix('*').removeprefix('./')
        if name in required:
            expected[name] = digest
    for name in required:
        with (directory / name).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected.get(name) != actual:
            raise SystemExit(f'Checksum mismatch or missing entry: {directory / name}')
release = (kernel / 'kernel-release.txt').read_text().strip()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', release):
    raise SystemExit('Invalid kernel release name')
with (kernel / 'Image').open('rb') as stream:
    if stream.read(64)[56:60] != b'ARM\x64':
        raise SystemExit('Expected a raw ARM64 kernel Image')
with (cgpt / 'cgpt').open('rb') as stream:
    header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', header, 16)[0] not in (2, 3)
            or struct.unpack_from('<H', header, 18)[0] != 183):
        raise SystemExit('Expected a static ARM64 ELF64 cgpt executable')
program = subprocess.check_output(['readelf', '--program-headers', str(cgpt / 'cgpt')], text=True)
dynamic = subprocess.check_output(['readelf', '--dynamic', str(cgpt / 'cgpt')], text=True)
if 'INTERP' in program or '(NEEDED)' in dynamic:
    raise SystemExit('ARM64 cgpt must be statically linked, without shared-library dependencies')
print(f'Verified kernel {release}, firmware, Ciri SKU{sku} DTB and static ARM64 cgpt')
PY
release=$(<"$kernel_dir/kernel-release.txt")

if [[ -z "$rootfs" ]]; then
    for dependency in curl gpg; do command -v "$dependency" >/dev/null || die "$dependency is required for rootfs download."; done
    base=https://de3.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz
    rootfs="$work/archive/ArchLinuxARM-aarch64-latest.tar.gz"
    printf 'Downloading official Arch Linux ARM rootfs and signature...\n'
    curl --fail --location --show-error --retry 3 --connect-timeout 30 --output "$work/metadata/rootfs.sig" "$base.sig"
    curl --fail --location --show-error --retry 3 --connect-timeout 30 --output "$rootfs" "$base"
    mkdir -m 700 "$work/gnupg"
    # Official fingerprints: https://archlinuxarm.org/about/package-signing
    keys=(02922214DE8981D14DC2ACABBC704E86B823CD25 9D22B7BB678DC056B1F7723CB55C5315DCD9EE1A 69DD6C8FD314223E14362848BF7EEF7A9C6B5765 68B3537F39A313B3E574D06777193F152BDBE6A6)
    gpg --homedir "$work/gnupg" --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys "${keys[@]}"
    gpg --homedir "$work/gnupg" --batch --status-fd 1 --verify "$work/metadata/rootfs.sig" "$rootfs" > "$work/metadata/signature-status"
    python3 -I - "$work/metadata/signature-status" "${keys[@]}" <<'PY'
from pathlib import Path
import sys
allowed = set(sys.argv[2:])
valid = False
for line in Path(sys.argv[1]).read_text().splitlines():
    fields = line.split()
    if len(fields) >= 3 and fields[:2] == ['[GNUPG:]', 'VALIDSIG']:
        valid |= fields[2] in allowed or fields[-1] in allowed
if not valid:
    raise SystemExit('Rootfs signature is not from an allowed Arch Linux ARM key')
PY
    rootfs_sha256=$(sha256sum "$rootfs")
    rootfs_sha256=${rootfs_sha256%% *}
else
    actual=$(sha256sum "$rootfs")
    [[ "${actual%% *}" == "${rootfs_sha256,,}" ]] || die 'Local rootfs SHA-256 mismatch.'
fi
printf 'Extracting rootfs without executing its ARM64 programs...\n'
bsdtar -xpf "$rootfs" -C "$work/rootfs"
root="$work/rootfs"
python3 -I - "$root" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
for name in ('etc', 'usr', 'usr/lib', 'boot', 'root'):
    path = root / name
    if not path.is_dir() or not path.resolve().is_relative_to(root):
        raise SystemExit(f'Unsafe or missing rootfs directory: {name}')
for name in ('etc/passwd', 'etc/shadow'):
    path = root / name
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f'Unsafe or missing rootfs file: {name}')
for name in ('usr/local', 'usr/local/bin', 'usr/share', 'usr/share/doc', 'usr/share/doc/ciri-cgpt'):
    path = root / name
    if not path.resolve().is_relative_to(root) or (path.exists() and not path.is_dir()):
        raise SystemExit(f'Unsafe cgpt installation directory: {name}')
path = root / 'usr/local/bin/cgpt'
if path.is_symlink() or path.exists():
    raise SystemExit('Refusing to replace an existing /usr/local/bin/cgpt in the rootfs')
PY

bsdtar -tf "$kernel_dir/modules.tar.zst" | python3 -I -c '
from pathlib import PurePosixPath
import sys
release = sys.argv[1]
for line in sys.stdin:
    p = PurePosixPath(line.strip())
    permitted = p in (PurePosixPath("lib"), PurePosixPath("lib/modules")) or p == PurePosixPath("lib/modules") / release or (len(p.parts) > 3 and p.parts[:3] == ("lib", "modules", release))
    if p.is_absolute() or ".." in p.parts or not permitted:
        raise SystemExit(f"Unsafe module archive member: {p}")
' "$release"
mkdir "$work/modules"
bsdtar -xpf "$kernel_dir/modules.tar.zst" -C "$work/modules"
cp -a "$work/modules/lib/modules/$release" "$root/usr/lib/modules/"
[[ -s "$root/usr/lib/modules/$release/modules.dep" ]] || die 'Matching modules.dep is absent.'
mkdir -p "$root/usr/lib/firmware/mediatek/mt8188"
for file in himax_i2chid_1002.bin himax_i2chid_1003.bin himax_i2chid_1004.bin mediatek/mt8188/scp-dual.img; do
    install -m 644 "$firmware_dir/$file" "$root/usr/lib/firmware/$file"
done

# The build host's cgpt sets image attributes; this executable runs on the Duet.
mkdir -p "$root/usr/local/bin" "$root/usr/share/doc/ciri-cgpt"
install -m 755 "$cgpt_dir/cgpt" "$root/usr/local/bin/cgpt"
for file in LICENSE source-revision.txt build-info.txt SHA256SUMS; do
    [[ ! -L "$root/usr/share/doc/ciri-cgpt/$file" ]] || die "Unsafe cgpt metadata path: $file"
    install -m 644 "$cgpt_dir/$file" "$root/usr/share/doc/ciri-cgpt/$file"
done

initrd="$work/initrd"
mkdir -p "$initrd"/{bin,sbin,proc,sys,dev,newroot,lib/modules,lib/firmware/mediatek/mt8188}
initrd_args=(--rootfs "$root" --initrd "$initrd")
[[ -z "$busybox" ]] || initrd_args+=(--busybox "$busybox")
python3 -I "$repo_root/scripts/ciri-initramfs-tools.py" "${initrd_args[@]}"
for applet in sh umount sleep mkdir; do ln -s busybox "$initrd/bin/$applet"; done
cp -a "$work/modules/lib/modules/$release" "$initrd/lib/modules/"
for file in himax_i2chid_1002.bin himax_i2chid_1003.bin himax_i2chid_1004.bin mediatek/mt8188/scp-dual.img; do
    install -m 644 "$firmware_dir/$file" "$initrd/lib/firmware/$file"
done
printf '%s\n' '#!/bin/sh' \
    'export PATH=/bin:/sbin' \
    'mount -t proc proc /proc' \
    'mount -t sysfs sysfs /sys' \
    'mount -t devtmpfs devtmpfs /dev' \
    'for module in mmc_block mmc_mtk xhci_mtk usb_storage uas nvme ext4 hid_himax i2c_hid_of usbhid hid_google_hammer cros_ec_keyb; do' \
    '    modprobe "$module" 2>/dev/null || true' \
    'done' \
    'root_uuid=' \
    'for arg in $(cat /proc/cmdline); do case "$arg" in root=UUID=*) root_uuid=${arg#root=UUID=};; esac; done' \
    'if [ -z "$root_uuid" ]; then printf "Missing root=UUID= argument\n"; exec sh; fi' \
    'root_device=' \
    'attempt=0' \
    'while [ "$attempt" -lt 90 ]; do' \
    '    for device in /dev/*; do' \
    '        [ -b "$device" ] || continue' \
    '        info=$(blkid "$device" 2>/dev/null)' \
    '        case "$info" in *"UUID=\"$root_uuid\""*) root_device=$device; break;; esac' \
    '    done' \
    '    [ -z "$root_device" ] || break' \
    '    sleep 1; attempt=$((attempt + 1))' \
    'done' \
    'if [ -z "$root_device" ]; then printf "Root filesystem UUID=%s not found\n" "$root_uuid"; exec sh; fi' \
    'mount -t ext4 -o ro "$root_device" /newroot || exec sh' \
    'mkdir -p /newroot/dev /newroot/proc /newroot/sys' \
    'mount --move /dev /newroot/dev' \
    'mount --move /proc /newroot/proc' \
    'mount --move /sys /newroot/sys' \
    'exec switch_root /newroot /sbin/init' > "$initrd/init"
# The init script needs cat, but never executes an ARM64 binary while building.
ln -s busybox "$initrd/bin/cat"
chmod 755 "$initrd/init"
(cd "$initrd"; find . -print0 | cpio --null --create --format=newc --quiet) | gzip -n -9 > "$work/boot/initramfs-ciri.img"

python3 -I - "$root" "$ssh_key" "$password_hash_file" <<'PY'
import os
from pathlib import Path
import re
import sys
root = Path(sys.argv[1])
key = Path(sys.argv[2]).read_text().strip() if sys.argv[2] else None
password = Path(sys.argv[3]).read_text().strip() if sys.argv[3] else None
if password and not re.fullmatch(r'\$(?:6|y)\$[^:\s]+', password):
    raise SystemExit('Password file must contain one SHA-512 crypt or yescrypt hash, not a plaintext password')
if key and (len(key.splitlines()) != 1 or not re.match(r'^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+)\s+\S+(?:\s.*)?$', key)):
    raise SystemExit('SSH key file must contain one OpenSSH public key, never a private key')
users = {}
for line in (root / 'etc/passwd').read_text().splitlines():
    fields = line.split(':')
    if len(fields) == 7:
        users[fields[0]] = fields
if not {'root', 'alarm'} <= users.keys():
    raise SystemExit('Expected the official Arch Linux ARM root and alarm accounts')
lines = []
for line in (root / 'etc/shadow').read_text().splitlines():
    fields = line.split(':')
    if fields[0] in ('root', 'alarm'):
        fields[1] = password or '!'
    lines.append(':'.join(fields))
(root / 'etc/shadow').write_text('\n'.join(lines) + '\n')
os.chmod(root / 'etc/shadow', 0o600)
if key:
    for name in ('root', 'alarm'):
        fields = users[name]
        home = root / fields[5].lstrip('/')
        if not home.resolve().is_relative_to(root.resolve()):
            raise SystemExit('Unsafe account home directory')
        ssh = home / '.ssh'
        ssh.mkdir(parents=True, exist_ok=True)
        os.chmod(ssh, 0o700)
        authorized = ssh / 'authorized_keys'
        authorized.write_text(key + '\n')
        os.chmod(authorized, 0o600)
        for path in (ssh, authorized):
            os.chown(path, int(fields[2]), int(fields[3]))
    settings = root / 'etc/ssh/sshd_config.d'
    settings.mkdir(parents=True, exist_ok=True)
    (settings / '10-ciri-image.conf').write_text('PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n')
    config = root / 'etc/ssh/sshd_config'
    original = config.read_text() if config.exists() else ''
    config.write_text('Include /etc/ssh/sshd_config.d/*.conf\n' + original)
    if not (root / 'usr/lib/systemd/system/sshd.service').is_file():
        raise SystemExit('Rootfs has no sshd.service')
    wants = root / 'etc/systemd/system/multi-user.target.wants'
    wants.mkdir(parents=True, exist_ok=True)
    target = wants / 'sshd.service'
    if not target.exists() and not target.is_symlink():
        target.symlink_to('/usr/lib/systemd/system/sshd.service')
(root / 'etc/hostname').write_text('arch-ciri\n')
# Regenerate machine identity and SSH host keys on the target, not on this builder.
(root / 'etc/machine-id').write_text('')
for path in (root / 'etc/ssh').glob('ssh_host_*'):
    if path.is_file() or path.is_symlink():
        path.unlink()
PY
mkdir -p "$root/etc/systemd/network" "$root/etc/systemd/system/multi-user.target.wants"
printf '%s\n' '[Match]' 'Name=en* eth* usb*' '' '[Network]' 'DHCP=yes' > "$root/etc/systemd/network/20-ciri-wired.network"
for service in systemd-networkd systemd-resolved; do
    target="$root/etc/systemd/system/multi-user.target.wants/$service.service"
    [[ -e "$target" || -L "$target" ]] || ln -s "/usr/lib/systemd/system/$service.service" "$target"
done

root_uuid=$(python3 -I -c 'import uuid; print(uuid.uuid4())')
boot_id=$(python3 -I -c 'import secrets; print(secrets.token_hex(4).upper())')
boot_uuid="${boot_id:0:4}-${boot_id:4:4}"
printf 'UUID=%s / ext4 defaults 0 1\nUUID=%s /boot vfat defaults,umask=0077 0 2\n' "$root_uuid" "$boot_uuid" > "$root/etc/fstab"
mkdir -p "$work/boot/grub" "$work/boot/dtbs" "$root/boot/grub"
cp "$kernel_dir/Image" "$work/boot/Image-ciri"
cp "$kernel_dir/dtbs/mt8188-geralt-ciri-sku$sku.dtb" "$work/boot/dtbs/"
printf "set default=0\nset timeout=5\nmenuentry 'Arch Linux ARM - Ciri SKU%s' {\n  search --no-floppy --fs-uuid --set=root %s\n  linux /Image-ciri root=UUID=%s ro rootwait console=tty0\n  initrd /initramfs-ciri.img\n}\n" "$sku" "$boot_uuid" "$root_uuid" > "$work/boot/grub/grub.cfg"
cp "$work/boot/grub/grub.cfg" "$root/boot/grub/grub.cfg"
# kexec_file_load reuses the running DTB. Pack the NEW kernel's tree into the
# Submarine carrier, rather than adding a devicetree directive it cannot honor.
cp "$kernel_dir/dtbs/mt8188-geralt-ciri-sku$sku.dtb" "$work/loader-dtbs/"
cp "$submarine_kernel" "$work/submarine-Image.gz"
if [[ "$packer" == local ]]; then
    depthchargectl build --board arm64-generic --kernel "$work/submarine-Image.gz" \
        --fdtdir "$work/loader-dtbs" --root none --kernel-cmdline 'console=tty0' \
        --output "$work/submarine.kpart"
    futility vbutil_kernel --verify "$work/submarine.kpart"
else
    env -u LD_PRELOAD -u FAKEROOTKEY podman run --rm --network=none -v "$work:/work" "$build_image" \
        depthchargectl build --board arm64-generic --kernel /work/submarine-Image.gz \
        --fdtdir /work/loader-dtbs --root none --kernel-cmdline 'console=tty0' \
        --output /work/submarine.kpart
    env -u LD_PRELOAD -u FAKEROOTKEY podman run --rm --network=none -v "$work:/work:ro" "$build_image" \
        futility vbutil_kernel --verify /work/submarine.kpart
fi
[[ $(stat -c %s "$work/submarine.kpart") -le $((32 * 1024 * 1024)) ]] || die 'Repacked Submarine payload exceeds the 32 MiB kernel partition.'

truncate -s "${size_gib}G" "$image"
sgdisk --clear \
    --new=1:2048:+32M --typecode=1:FE3A2A5D-4F32-41A7-B725-ACCC3285A309 --change-name=1:Submarine \
    --new=2:0:+256M --typecode=2:0700 --change-name=2:ArchBoot \
    --new=3:0:0 --typecode=3:8300 --change-name=3:ArchRoot "$image"
cgpt add -i 1 -P 15 -T 1 -S 1 "$image"
boot_sectors=$(cgpt show -i 2 -s "$image")
root_sectors=$(cgpt show -i 3 -s "$image")
truncate -s "$((boot_sectors * 512))" "$work/boot.part"
mkfs.vfat -F 32 -i "$boot_id" -n ARCHBOOT "$work/boot.part"
mcopy -s -i "$work/boot.part" "$work/boot/"* ::/
truncate -s "$((root_sectors * 512))" "$work/root.part"
mkfs.ext4 -F -U "$root_uuid" -L ARCHCIRI -E root_owner=0:0 -d "$root" "$work/root.part"
for number in 1 2 3; do
    case "$number" in 1) part="$work/submarine.kpart";; 2) part="$work/boot.part";; 3) part="$work/root.part";; esac
    start=$(cgpt show -i "$number" -b "$image")
    dd if="$part" of="$image" bs=4M seek="$((start * 512))" oflag=seek_bytes conv=sparse,notrunc status=progress
 done
sgdisk --verify "$image"
# Publish atomically without replacing an existing file, even if it appeared late.
ln -- "$image" "$output"
rm -f -- "$image"
image=
checksum=$(sha256sum "$output")
printf '%s  %s\n' "${checksum%% *}" "${output##*/}" > "$output.sha256"
python3 -I - "$output.json" "$kernel_dir" "$sku" "$rootfs_sha256" "$root_uuid" "$boot_uuid" "$size_gib" "$cgpt_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
manifest, kernel = map(Path, sys.argv[1:3])
cgpt = Path(sys.argv[8])
manifest.write_text(json.dumps({
    'kernel_release': (kernel / 'kernel-release.txt').read_text().strip(),
    'source_revision': (kernel / 'source-revision.txt').read_text().strip() if (kernel / 'source-revision.txt').is_file() else None,
    'ciri_sku': int(sys.argv[3]), 'arch_rootfs_sha256': sys.argv[4],
    'root_uuid': sys.argv[5], 'boot_uuid': sys.argv[6], 'image_size_gib': int(sys.argv[7]),
    'chromeos_kernel_partition_mib': 32,
    'cgpt_path': '/usr/local/bin/cgpt',
    'cgpt_sha256': hashlib.sha256((cgpt / 'cgpt').read_bytes()).hexdigest(),
    'cgpt_source_revision': (cgpt / 'source-revision.txt').read_text().strip(),
    'boot_method': 'Submarine kexec_file_load, matching Ciri DTB repacked into the carrier',
    'hardware_boot_tested': False,
}, indent=2) + '\n')
PY
printf '\nImage ready: %s\nChecksums: %s.sha256\nManifest: %s.json\n' "$output" "$output" "$output"
printf 'No physical disk was written. Flash only after identifying and approving the target drive.\n'
printf 'This is a minimal Arch install, not a desktop. Wired DHCP is configured; Wi-Fi and Ciri audio userspace still need setup.\n'
