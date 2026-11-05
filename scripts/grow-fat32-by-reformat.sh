#!/usr/bin/env bash
# Preserve a FAT32 partition's files and UUID while recreating it at full size.
# Usage: sudo bash scripts/grow-fat32-by-reformat.sh /dev/sda2
set -euo pipefail
export LC_ALL=C

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run with sudo.'
[[ $# -eq 1 ]] || fail 'Usage: sudo bash grow-fat32-by-reformat.sh /dev/PARTITION'
for cmd in realpath lsblk findmnt blkid blockdev mkfs.fat fsck.fat mount umount mountpoint mktemp cp diff sync df du grep tee tr cut python3; do
    command -v "$cmd" >/dev/null || fail "Missing command: $cmd"
done

device=$(realpath -e -- "$1")
[[ -b "$device" ]] || fail 'Target is not a block device.'
[[ $(lsblk -dn -o TYPE "$device") == part ]] || fail 'Target must be a partition, not a whole disk.'
[[ $(lsblk -dn -o RO "$device") == 0 ]] || fail 'Target is read-only.'
[[ -z $(lsblk -dn -o MOUNTPOINTS "$device" | tr -d '[:space:]') ]] || fail 'Unmount the target first.'
[[ $(blkid -s TYPE -o value "$device") == vfat ]] || fail 'Target is not a FAT filesystem.'
[[ -z $(lsblk -n -o TYPE "$device" | grep -v '^part$') ]] || fail 'Target has dependent block devices.'

uuid=$(blkid -s UUID -o value "$device")
label=$(blkid -s LABEL -o value "$device" || true)
[[ $uuid =~ ^[[:xdigit:]]{4}-[[:xdigit:]]{4}$ ]] || fail 'Unexpected FAT UUID; refusing to format.'
bytes=$(blockdev --getsize64 "$device")
serial=${uuid//-/}
identity=$(lsblk -dn -o MAJ:MIN "$device")

printf 'Target partition:\n'
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "$device"
printf '\nThis will copy all files to the host, FORMAT %s as FAT32,\n' "$device"
printf 'then restore the files and UUID %s. Other partitions are not changed.\n' "$uuid"
printf 'Type the full target path (%s) to proceed: ' "$device"
read -r answer
[[ $answer == "$device" ]] || fail 'Confirmation did not match; nothing changed.'

# Backup goes on the host, never inside the filesystem being formatted.
work=$(mktemp -d /var/tmp/ciri-fat32-grow.XXXXXXXX)
backup="$work/files"
mnt="$work/mount"
mkdir "$backup" "$mnt"
formatted=0
cleanup() {
    status=$?
    trap - EXIT
    if mountpoint -q "$mnt"; then
        if ! umount "$mnt"; then
            printf 'WARNING: could not unmount %s; unmount it manually.\n' "$mnt" >&2
            status=1
        fi
    fi
    printf '\nSaved files and logs retained at: %s\n' "$work"
    if [[ $status -ne 0 && $formatted -eq 1 ]]; then
        printf 'UPDATE INCOMPLETE: preserve that directory; it contains your boot files.\n' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Read-only checks: refuse corruption instead of repairing it implicitly.
fsck.fat -vn "$device" | tee "$work/fsck-before.txt"
grep -Eq '2 FATs, 32 bit entries' "$work/fsck-before.txt" || fail 'Target is not FAT32.'
mount -o ro "$device" "$mnt"
[[ $(findmnt -n -o MAJ:MIN --target "$backup") != "$identity" ]] || fail 'Backup is on the target partition.'
needed=$(du -sb "$mnt" | cut -f1)
available=$(python3 -I -c 'import shutil,sys; print(shutil.disk_usage(sys.argv[1]).free)' "$backup")
[[ $available -gt $((needed + 64 * 1024 * 1024)) ]] || fail 'Insufficient host space for a verified file copy.'
cp -a "$mnt/." "$backup/"
diff -qr "$mnt" "$backup"
sync
umount "$mnt"

# Recheck target identity immediately before the destructive operation.
[[ $(lsblk -dn -o MAJ:MIN "$device") == "$identity" ]] || fail 'Target device changed.'
[[ $(blockdev --getsize64 "$device") == "$bytes" ]] || fail 'Target size changed.'
[[ $(blkid -s UUID -o value "$device") == "$uuid" ]] || fail 'Target UUID changed.'
[[ -z $(lsblk -dn -o MOUNTPOINTS "$device" | tr -d '[:space:]') ]] || fail 'Target became mounted.'
mkfs_args=(-F 32 -i "$serial")
[[ -z $label ]] || mkfs_args+=(-n "$label")
formatted=1
mkfs.fat "${mkfs_args[@]}" "$device"
mount "$device" "$mnt"
cp -a "$backup/." "$mnt/"
sync
diff -qr "$backup" "$mnt"
printf '\nRestored filesystem usage:\n'
df -h "$mnt"
umount "$mnt"
fsck.fat -vn "$device" | tee "$work/fsck-after.txt"
[[ ${uuid^^} == "$(blkid -s UUID -o value "$device" | tr '[:lower:]' '[:upper:]')" ]] || fail 'Restored UUID does not match.'
printf '\nSuccess: FAT32 recreated at full partition size; files and UUID verified.\n'
printf 'The partition is unmounted. Keep the saved files until boot is confirmed.\n'
