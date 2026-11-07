#!/bin/bash
# One-time, on-device launcher update; do not replace the recovered libraries.
set -euo pipefail
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 && $(uname -m) == aarch64 ]] || fail 'Run with sudo on Ciri.'
[[ $# -le 1 ]] || fail 'Usage: update-ciri-camera-launcher.sh [run-ciri-camera-probe.sh]'
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run-ciri-camera-probe.sh}
prefix=/opt/ciri-camera
old_sha=f54ff644af1866780a3b2e428f25003a175d6c1d6623b6898b8d41850d6cd9aa
for dirpath in /opt "$prefix"; do
    [[ -d $dirpath && ! -L $dirpath ]] || fail "Expected a real directory: $dirpath"
done
for filepath in "$source" "$prefix/run-camera" "$prefix/client-SHA256SUMS"; do
    [[ -f $filepath && ! -L $filepath ]] || fail "Expected a regular file: $filepath"
done
bash -n "$source"
(cd "$prefix"; sha256sum --check --status client-SHA256SUMS)
read -r new_sha _ < <(sha256sum "$source")
read -r current_sha _ < <(sha256sum "$prefix/run-camera")
if [[ $current_sha == "$new_sha" ]]; then
    printf 'Launcher already matches this source; checksums verified. Nothing changed.\n'
    exit 0
fi
[[ $current_sha == "$old_sha" ]] || fail 'Installed launcher differs from the inspected version; refusing to overwrite it.'
for filepath in "$prefix/run-camera.pre-udev-data" "$prefix/client-SHA256SUMS.pre-udev-data" \
    "$prefix/run-camera.udev-data-new" "$prefix/client-SHA256SUMS.udev-data-new"; do
    [[ ! -e $filepath && ! -L $filepath ]] || fail "Path already exists: $filepath"
done
cp -a "$prefix/run-camera" "$prefix/run-camera.pre-udev-data"
cp -a "$prefix/client-SHA256SUMS" "$prefix/client-SHA256SUMS.pre-udev-data"
install -m 0755 "$source" "$prefix/run-camera.udev-data-new"
printf '%s  %s\n' "$new_sha" "$prefix/run-camera.udev-data-new" | sha256sum --check --status
(cd "$prefix"; {
    sha256sum rootfs/usr/bin/ciri-camera-probe
    printf '%s  run-camera\n' "$new_sha"
    sha256sum client-build-info.txt client-sdk-downloads.json
} > client-SHA256SUMS.udev-data-new)
mv -T "$prefix/run-camera.udev-data-new" "$prefix/run-camera"
mv -T "$prefix/client-SHA256SUMS.udev-data-new" "$prefix/client-SHA256SUMS"
(cd "$prefix"; sha256sum --check --status client-SHA256SUMS)
sync -f "$prefix"
printf 'Launcher updated and verified; previous launcher and checksum manifest retained.\n'
printf 'Private runtime keeps model ciri and now exposes the host udev database read-only.\n'
printf 'Libraries, executable, kernel, audio and services unchanged.\n'
printf 'Retry: sudo /opt/ciri-camera/run-camera --list\n'
