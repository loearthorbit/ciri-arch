#!/bin/bash
# Incremental rebuild in the retained Ciri Docker container; no downloads.
set -euo pipefail
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-ciri
export KBUILD_BUILD_USER=ciri-builder KBUILD_BUILD_HOST=ciri-docker
cd /work/linux
[[ $(git rev-parse HEAD) == b8f841230b1615aa58b5d4389e11bfe66abbd993 ]]
export KBUILD_BUILD_TIMESTAMP="$(git show -s --format=%cD HEAD)"
# The vendor pipeline needs both drivers AND enabled device-tree nodes.
camera_nodes=(/soc/imgsys-fw@15000000 /soc/aie@15310000 /soc/imgsys-l10
    /soc/imgsys-l11a /soc/imgsys-l11b /soc/imgsys-l11c /soc/imgsys-l15)
camera_dtsi=arch/arm64/boot/dts/mediatek/mt8188-geralt-ciri.dtsi
if ! grep -Fq '// Ciri camera userspace companion nodes' "$camera_dtsi"; then
    printf '\n// Ciri camera userspace companion nodes\n' >> "$camera_dtsi"
    for node in "${camera_nodes[@]}"; do
        printf '&{%s} { status = "okay"; };\n' "$node" >> "$camera_dtsi"
    done
fi
[[ -f /work/out/.config ]]
cp /work/out/.config "$CAMERA_ARTIFACTS/config-before"
scripts/config --file /work/out/.config \
    --module VIDEO_MTK_ISP_71_IMGSYS --module VIDEO_MTK_AIE \
    --module VIDEO_MTK_ISP_71_CAMSYS --module VIDEO_GC05A2 \
    --module VIDEO_GC08A3 --module VIDEO_DW9768 \
    --module MTK_SCP --module RPMSG_MTK_SCP
make O=/work/out olddefconfig
for name in VIDEO_MTK_ISP_71_IMGSYS VIDEO_MTK_AIE VIDEO_MTK_ISP_71_CAMSYS \
    VIDEO_GC05A2 VIDEO_GC08A3 VIDEO_DW9768 MTK_SCP RPMSG_MTK_SCP; do
    grep -E "^CONFIG_${name}=(m|y)$" /work/out/.config
done
# Refuse the rebuild if olddefconfig changed any of the working audio options.
diff -u <(grep -E '^(# )?CONFIG_(SOUND|SND)' "$CAMERA_ARTIFACTS/config-before") \
    <(grep -E '^(# )?CONFIG_(SOUND|SND)' /work/out/.config)
make O=/work/out -j"${BUILD_JOBS:-4}" Image Image.gz modules dtbs
for dtb in /work/out/arch/arm64/boot/dts/mediatek/mt8188-geralt-ciri-sku*.dtb; do
    for node in "${camera_nodes[@]}"; do
        [[ $(fdtget -t s "$dtb" "$node" status) == okay ]]
    done
done
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
