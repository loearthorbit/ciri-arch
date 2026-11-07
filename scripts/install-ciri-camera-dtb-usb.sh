#!/bin/bash
# Targeted Submarine DTB update. Default: read-only checks; --install writes KERN.
set -euo pipefail
umask 077
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run with sudo on the host with the Ciri boot media connected.'
[[ $# -le 1 ]] || fail 'Usage: install-ciri-camera-dtb-usb.sh [--check|--install]'
action=check
case "${1:-}" in
    ''|--check) ;;
    --install) action=install ;;
    *) fail 'Usage: install-ciri-camera-dtb-usb.sh [--check|--install]' ;;
esac
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stage="$repo/build/ciri-camera-carrier-ready"
payload="$stage/camera-carrier.kpart"
backup="$stage/installed-carrier.pre-camera-dtb.partition"
original_sha=39e376ffd9e9f431f9bd308141549d373bce59842b727f02c71dbed1c06df1ba
camera_sha=74454f8e1274a6c55b9aa69093c17265aad78b2579513e02dec89acc9295e336
[[ -d $stage && ! -L $stage && -f $payload && ! -L $payload ]] || fail 'Verified carrier stage missing.'
(cd "$stage"; sha256sum --check --status SHA256SUMS)
printf '%s  %s\n' "$camera_sha" "$payload" | sha256sum --check --status
[[ $(stat -c %s "$payload") -eq 33554432 ]] || fail 'Expected a padded 32 MiB carrier.'
futility vbutil_kernel --verify "$payload" >/dev/null

find_target() {
    local rootdev bootdev rootparent bootparent candidate number
    rootdev=$(readlink -e /dev/disk/by-uuid/9d00d9e7-22f1-4f32-8006-19dae6343c92) || fail 'Expected ARCHCIRI device not connected.'
    bootdev=$(readlink -e /dev/disk/by-uuid/1C9E-7182) || fail 'Expected ARCHBOOT device not connected.'
    [[ -b $rootdev && -b $bootdev ]] || fail 'Expected block devices for ARCHCIRI and ARCHBOOT.'
    rootparent=$(lsblk -ndno PKNAME "$rootdev")
    bootparent=$(lsblk -ndno PKNAME "$bootdev")
    [[ -n $rootparent && $rootparent == "$bootparent" ]] || fail 'ARCHBOOT and ARCHCIRI must be on the same disk.'
    read -r number < "/sys/class/block/${rootdev##*/}/partition"
    [[ $number == 3 ]] || fail 'Expected ARCHCIRI on partition 3.'
    read -r number < "/sys/class/block/${bootdev##*/}/partition"
    [[ $number == 2 ]] || fail 'Expected ARCHBOOT on partition 2.'
    disk="/dev/$rootparent"
    [[ -b $disk ]] || fail 'Expected the parent disk to be a block device.'
    carrier=
    while read -r candidate; do
        [[ -f /sys/class/block/${candidate##*/}/partition ]] || continue
        read -r number < "/sys/class/block/${candidate##*/}/partition"
        [[ $number != 1 ]] || carrier=$candidate
    done < <(lsblk -nrpo NAME "$disk")
    [[ -n $carrier && -b $carrier ]] || fail 'Carrier partition 1 not found.'
    [[ $(lsblk -ndno PARTTYPE "$carrier") == fe3a2a5d-4f32-41a7-b725-accc3285a309 ]] || fail 'Partition 1 is not a ChromeOS kernel partition.'
    [[ $(cgpt show -i 1 -b "$disk") == 2048 && $(cgpt show -i 1 -s "$disk") == 65536 ]] || fail 'Unexpected carrier partition location or size.'
    [[ $(blockdev --getsize64 "$carrier") -eq 33554432 ]] || fail 'Carrier partition must be 32 MiB.'
}
find_target
read -r current_sha _ < <(sha256sum "$carrier")
if [[ $current_sha == "$camera_sha" ]]; then
    printf 'Camera-enabled carrier already installed on %s. Nothing changed.\n' "$carrier"
    exit 0
fi
[[ $current_sha == "$original_sha" ]] || fail "Installed carrier differs from the retained original ($current_sha). Refusing to overwrite it."
printf 'Verified target: %s on %s; ARCHBOOT/ARCHCIRI UUIDs and original carrier match.\n' "$carrier" "$disk"
if [[ $action == check ]]; then
    printf 'Read-only checks passed. No files or partitions changed.\n'
    printf 'To back up and install: sudo bash %s --install\n' "$0"
    exit 0
fi
[[ ! -e $backup && ! -L $backup ]] || fail "Backup already exists: $backup"
available=$(df -B1 --output=avail -- "$stage" | { read -r header; read -r value; printf '%s\n' "$value"; })
[[ $available -ge 67108864 ]] || fail 'At least 64 MiB free host space is required for the backup.'
# Back up the actual installed partition, not just the retained source image.
# Both filesystem partitions and the GPT are never written by this installer.
dd if="$carrier" of="$backup" bs=4M conv=fsync status=none
printf '%s  %s\n' "$original_sha" "$backup" | sha256sum --check --status
futility vbutil_kernel --verify "$backup" >/dev/null
previous_carrier=$carrier
find_target
[[ $carrier == "$previous_carrier" ]] || fail 'Target changed after backup; refusing to write.'
printf '%s  %s\n' "$original_sha" "$carrier" | sha256sum --check --status
printf 'Writing only %s. Verified original backup: %s\n' "$carrier" "$backup"
writing=1
trap 'rc=$?; if [[ ${writing:-0} == 1 ]]; then printf "Carrier update failed. Do not boot this media until restored or verified. Backup: %s\n" "$backup" >&2; fi; exit "$rc"' ERR
dd if="$payload" of="$carrier" bs=4M conv=fsync,notrunc status=progress
printf '%s  %s\n' "$camera_sha" "$carrier" | sha256sum --check --status
writing=0
trap - ERR
printf '\nCamera-enabled Submarine carrier installed and byte-verified.\n'
printf 'Only partition 1 changed. GPT, ARCHBOOT, ARCHCIRI, kernel modules and audio settings untouched.\n'
printf 'Safely eject the boot media, then boot Ciri and run:\n'
printf '  sudo modprobe mtk_imgsys mtk-aie\n'
printf '  sudo /opt/ciri-camera/run-camera --list\n'
printf '  sudo /opt/ciri-camera/run-camera --capture 0\n'
