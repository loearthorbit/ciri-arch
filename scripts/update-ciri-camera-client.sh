#!/bin/bash
# Install the warm-up/3A-state diagnostic; never execute vendor code here.
set -euo pipefail
umask 077
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 && $(uname -m) == aarch64 ]] || fail 'Run with sudo on Ciri.'
[[ $# -le 1 ]] || fail 'Usage: update-ciri-camera-client.sh [client-build-directory]'
source=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/ciri-camera-client-warmup}
prefix=/opt/ciri-camera
binary="$prefix/rootfs/usr/bin/ciri-camera-probe"
old_sha=9ad14fe84cec68c90da3b6b0cb3dcda743ef1716198e6b7fb0bc571dd3b08f13
new_sha=76210014d72fa17a1ae73dfd3923b03ddba6f75f86bf1b129c1a174296331144
info_sha=99d597f0475987c19aa74e69cc452381987f305312bea72a1b094af2fda170bc
sdk_sha=d1a1e3118b1e25672c1887b00fcdb534c0d5b9b238771ad44d9bb40980daa604
launcher_sha=61688a49fae6819710eff2631ee4323c4313335dbb11cb94d68fbb5f9f3717d4
for dirpath in /opt "$prefix" "$prefix/rootfs" "$prefix/rootfs/usr" \
    "$prefix/rootfs/usr/bin" "$source"; do
    [[ -d $dirpath && ! -L $dirpath ]] || fail "Expected a real directory: $dirpath"
done
for filepath in "$binary" "$prefix/run-camera" "$prefix/client-build-info.txt" \
    "$prefix/client-sdk-downloads.json" "$prefix/client-SHA256SUMS" \
    "$source/ciri-camera-probe" "$source/build-info.txt" "$source/sdk-downloads.json" \
    "$source/SHA256SUMS"; do
    [[ -f $filepath && ! -L $filepath ]] || fail "Expected a regular file: $filepath"
done
(cd "$source"; sha256sum --check --status SHA256SUMS)
printf '%s  %s\n' "$new_sha" "$source/ciri-camera-probe" \
    "$info_sha" "$source/build-info.txt" "$sdk_sha" "$source/sdk-downloads.json" \
    "$sdk_sha" "$prefix/client-sdk-downloads.json" \
    "$launcher_sha" "$prefix/run-camera" | sha256sum --check --status
(cd "$prefix"; sha256sum --check --status client-SHA256SUMS)
read -r current_sha _ < <(sha256sum "$binary")
if [[ $current_sha == "$new_sha" ]]; then
    printf '%s  %s\n' "$info_sha" "$prefix/client-build-info.txt" | sha256sum --check --status
    sync -f "$prefix" || fail 'Installed client checksums match, but filesystem sync failed. Check kernel/storage errors before another capture or update.'
    printf 'Warm-up client already installed; checksums and filesystem sync verified. Nothing changed.\n'
    exit 0
fi
[[ $current_sha == "$old_sha" ]] || fail 'Installed client differs from the inspected colour-gain version; refusing to overwrite it.'
for filepath in "$binary.warmup-new" "$prefix/client-build-info.txt.warmup-new" \
    "$prefix/client-SHA256SUMS.warmup-new"; do
    [[ ! -e $filepath && ! -L $filepath ]] || fail "Path already exists: $filepath"
done
sync -f "$prefix" || fail 'Filesystem sync failed before update; no client files changed. Check kernel/storage errors.'
trap 'printf "Client update failed; no old-client backup was made. Do not run the probe until the update is completed or reinstalled.\n" >&2' ERR
install -m 0755 "$source/ciri-camera-probe" "$binary.warmup-new"
install -m 0644 "$source/build-info.txt" "$prefix/client-build-info.txt.warmup-new"
printf '%s  %s\n' "$new_sha" "$binary.warmup-new" \
    "$info_sha" "$prefix/client-build-info.txt.warmup-new" | sha256sum --check --status
(cd "$prefix"; {
    printf '%s  rootfs/usr/bin/ciri-camera-probe\n' "$new_sha"
    sha256sum run-camera
    printf '%s  client-build-info.txt\n' "$info_sha"
    sha256sum client-sdk-downloads.json
} > client-SHA256SUMS.warmup-new)
mv -T "$binary.warmup-new" "$binary"
mv -T "$prefix/client-build-info.txt.warmup-new" "$prefix/client-build-info.txt"
mv -T "$prefix/client-SHA256SUMS.warmup-new" "$prefix/client-SHA256SUMS"
(cd "$prefix"; sha256sum --check --status client-SHA256SUMS)
sync -f "$prefix"
trap - ERR
printf 'Warm-up client installed; checksums and filesystem sync verified. No old-client backup made.\n'
printf 'Recovered libraries, launcher, kernel, audio and services unchanged.\n'
printf 'Retry: sudo /opt/ciri-camera/run-camera --capture 0\n'
