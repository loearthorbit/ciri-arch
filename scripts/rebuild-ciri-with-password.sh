#!/usr/bin/env bash
set +x
set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: ${0##*/} --sku 0..7 [--output FILE.img] [builder options]" \
        '' \
        'Prompt twice for a console password and build a new Ciri Arch image.' \
        'The entered password is set for both root and alarm; no SSH key is used.' \
        'Defaults to local artifacts and images/arch-ciri-password.img in this project.' \
        'Refuses existing images and sidecars. Never writes a physical drive.' \
        '' \
        'Builder options: --size-gib, --kernel-dir, --firmware-dir, --cgpt-dir,' \
        '--submarine-kernel, --rootfs, --rootfs-sha256, --rootfs-cache, --busybox, --build-image,' \
        '--console-rotation (0..3, requires a rotation-enabled distro kernel).' \
        'Value options take one value. --refresh-rootfs explicitly refreshes the cache.' \
        'SSH keys and supplied hashes are not accepted.' \
        '--blank-root-password skips the prompt: root console login needs no password,' \
        'alarm remains locked, and root SSH login is disabled.'
}

die() { printf '%s\n' "$*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"; }
repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sku= blank_root_password= output="$repo_root/images/arch-ciri-password.img"
builder_args=(
    --kernel-dir "$repo_root/artifacts/kernel"
    --firmware-dir "$repo_root/artifacts/firmware"
    --cgpt-dir "$repo_root/artifacts/cgpt"
    --submarine-kernel "$repo_root/artifacts/submarine/Image.gz"
)
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --sku) need_value "$@"; sku=$2; shift 2 ;;
        --output) need_value "$@"; output=$2; shift 2 ;;
        --blank-root-password) blank_root_password=1; shift ;;
        --refresh-rootfs) builder_args+=("$1"); shift ;;
        --size-gib|--kernel-dir|--firmware-dir|--cgpt-dir|--submarine-kernel|--rootfs|--rootfs-sha256|--rootfs-cache|--busybox|--build-image|--console-rotation)
            need_value "$@"; builder_args+=("$1" "$2"); shift 2 ;;
        --ssh-key|--password-hash-file)
            die 'This script prompts for a password; do not supply an SSH key or hash.' ;;
        *) die "Unknown option: $1" ;;
    esac
done
[[ "$sku" =~ ^[0-7]$ ]] || die '--sku must identify the actual Ciri SKU, from 0 to 7.'
[[ "$output" != /dev/* && "$output" != /proc/* && "$output" != /sys/* ]] || die 'Output must be a new image file, not a device or system path.'
for path in "$output" "$output.sha256" "$output.json"; do
    [[ ! -e "$path" && ! -L "$path" ]] || die "Refusing to replace $path"
done
[[ -d "$(dirname -- "$output")" ]] || die 'Create the output directory first.'
umask 077
if [[ -n "$blank_root_password" ]]; then
    printf 'WARNING: local root console login will have no password; root SSH login is disabled.\n'
    exec bash "$repo_root/scripts/bundle-ciri-arch.sh" \
        "${builder_args[@]}" --sku "$sku" --output "$output" --blank-root-password
fi
command -v openssl >/dev/null || die 'openssl is required to hash the password.'

hash_file=
unset password confirmation password_hash
cleanup() {
    unset password confirmation password_hash
    [[ -z "${hash_file:-}" ]] || rm -f -- "$hash_file"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
exec 3<>/dev/tty || die 'An interactive terminal is required for the password prompt.'
IFS= read -r -s -p 'Root/alarm console password: ' password <&3 || die 'Password input cancelled.'
printf '\n' >&3
[[ -n "$password" ]] || die 'The password must not be empty.'
IFS= read -r -s -p 'Confirm password: ' confirmation <&3 || die 'Password confirmation cancelled.'
printf '\n' >&3
exec 3>&-
[[ "$password" == "$confirmation" ]] || die 'Passwords do not match.'

# Send plaintext only through stdin, never command arguments or a plaintext file.
password_hash=$(printf '%s\n' "$password" | openssl passwd -6 -stdin)
unset password confirmation
[[ "$password_hash" == '$6$'* ]] || die 'Could not generate a SHA-512 crypt password hash.'
hash_file=$(mktemp "${TMPDIR:-/tmp}/ciri-password.XXXXXXXX")
printf '%s\n' "$password_hash" > "$hash_file"
unset password_hash
printf 'Building a new image with console password login for root and alarm.\n'
bash "$repo_root/scripts/bundle-ciri-arch.sh" \
    "${builder_args[@]}" --sku "$sku" --output "$output" \
    --password-hash-file "$hash_file"
