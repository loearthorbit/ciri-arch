#!/bin/bash
# Incremental rebuild in the retained Ciri Docker container; no downloads.
set -euo pipefail
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-ciri
export KBUILD_BUILD_USER=ciri-builder KBUILD_BUILD_HOST=ciri-docker
cd /work/linux
[[ $(git rev-parse HEAD) == b8f841230b1615aa58b5d4389e11bfe66abbd993 ]]
export KBUILD_BUILD_TIMESTAMP="$(git show -s --format=%cD HEAD)"
[[ -f /work/out/.config ]]
cp /work/out/.config "$CAMERA_ARTIFACTS/config-before"
scripts/config --file /work/out/.config \
    --module VIDEO_MTK_ISP_71_CAMSYS --module VIDEO_GC05A2 \
    --module VIDEO_GC08A3 --module VIDEO_DW9768 \
    --module MTK_SCP --module RPMSG_MTK_SCP
make O=/work/out olddefconfig
for name in VIDEO_MTK_ISP_71_CAMSYS VIDEO_GC05A2 VIDEO_GC08A3 VIDEO_DW9768 MTK_SCP RPMSG_MTK_SCP; do
    grep -E "^CONFIG_${name}=(m|y)$" /work/out/.config
done
make O=/work/out -j"${BUILD_JOBS:-4}" Image Image.gz modules dtbs
release=$(make --no-print-directory -s O=/work/out kernelrelease)
make O=/work/out -j"${BUILD_JOBS:-4}" modules_install INSTALL_MOD_PATH="${CAMERA_ARTIFACTS}-modules"
rm -f "${CAMERA_ARTIFACTS}-modules/lib/modules/$release/build" "${CAMERA_ARTIFACTS}-modules/lib/modules/$release/source"
cp /work/out/arch/arm64/boot/Image /work/out/arch/arm64/boot/Image.gz /work/out/System.map "$CAMERA_ARTIFACTS/"
cp /work/out/.config "$CAMERA_ARTIFACTS/kernel.config"
mkdir "$CAMERA_ARTIFACTS/dtbs"
cp /work/out/arch/arm64/boot/dts/mediatek/mt8188-geralt-ciri-sku*.dtb "$CAMERA_ARTIFACTS/dtbs/"
tar --zstd -cf "$CAMERA_ARTIFACTS/modules.tar.zst" -C "${CAMERA_ARTIFACTS}-modules" lib/modules
printf '%s\n' "$release" > "$CAMERA_ARTIFACTS/kernel-release.txt"
git rev-parse HEAD > "$CAMERA_ARTIFACTS/source-revision.txt"
printf 'source=https://github.com/mkopec/linux.git\nrevision=%s\nkernel_release=%s\nincremental_camera_rebuild=true\n' "$(git rev-parse HEAD)" "$release" > "$CAMERA_ARTIFACTS/build-info.txt"
cd "$CAMERA_ARTIFACTS"
find . -type f ! -path './module-root/*' ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
printf 'CAMERA BUILD COMPLETE: %s\n' "$release"
