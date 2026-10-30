#!/bin/bash
set -euo pipefail
[[ -f /.dockerenv ]] || { printf 'Run inside a dedicated Debian Docker container.\n' >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || { printf 'Container root is required.\n' >&2; exit 1; }
[[ ! -e /work/linux && ! -e /work/artifacts ]] || { printf 'Use an empty /work volume.\n' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=ciri-builder
export KBUILD_BUILD_HOST=ciri-docker
export LOCALVERSION=-ciri
jobs=${BUILD_JOBS:-4}

mkdir -p /work/artifacts
printf 'Installing build dependencies\n'
apt-get update > /work/dependencies.log 2>&1
apt-get install -y --no-install-recommends \
    build-essential gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    bc bison flex libssl-dev libelf-dev dwarves python3 rsync kmod \
    zstd gzip git ca-certificates xz-utils device-tree-compiler \
    >> /work/dependencies.log 2>&1
python3 -I -c 'import shutil; free = shutil.disk_usage("/work").free; print(f"Free build space: {free // 2**30} GiB"); assert free >= 12 * 2**30, "At least 12 GiB of free space is needed"'

requested_revision=${SOURCE_REVISION:-b8f841230b1615aa58b5d4389e11bfe66abbd993}
[[ "$requested_revision" =~ ^[a-fA-F0-9]{40}$ ]] || { printf 'SOURCE_REVISION must be a full commit hash.\n' >&2; exit 1; }
printf 'Cloning mkopec/linux ciri-full\n'
git clone --depth=1 --single-branch --branch=ciri-full \
    https://github.com/mkopec/linux.git /work/linux
if [[ $(git -C /work/linux rev-parse HEAD) != "$requested_revision" ]]; then
    git -C /work/linux fetch --depth=1 origin "$requested_revision"
    git -C /work/linux checkout --detach FETCH_HEAD
fi
cd /work/linux
revision=$(git rev-parse HEAD)
printf '%s\n' "$revision" > /work/artifacts/source-revision.txt
git show -s --format=fuller HEAD > /work/artifacts/source-commit.txt
export KBUILD_BUILD_TIMESTAMP="$(git show -s --format=%cD HEAD)"
printf 'Building source revision %s with %s jobs\n' "$revision" "$jobs"

make O=/work/out defconfig
# Keep the upstream ARM64 distro configuration, but only target MediaTek SoCs.
python3 -I -c 'import pathlib, re; p = pathlib.Path("arch/arm64/Kconfig.platforms"); names = re.findall(r"^config (ARCH_[A-Z0-9_]+)$", p.read_text(), re.M); pathlib.Path("/work/other-platforms.txt").write_text("\n".join(n for n in names if n != "ARCH_MEDIATEK") + "\n")'
while IFS= read -r platform; do
    [ -z "$platform" ] || scripts/config --file /work/out/.config --disable "$platform"
done < /work/other-platforms.txt
config=(scripts/config --file /work/out/.config)
"${config[@]}" --enable ARCH_MEDIATEK --enable MODULES --enable BLK_DEV_INITRD \
    --enable DEVTMPFS --enable DEVTMPFS_MOUNT --enable EXT4_FS \
    --enable MMC --enable MMC_MTK --enable PCI --enable PCIE_MEDIATEK_GEN3 \
    --enable USB --enable USB_XHCI_HCD --enable USB_XHCI_MTK \
    --enable CROS_EC --enable CROS_EC_I2C --enable CROS_EC_SPI \
    --enable CROS_EC_CHARDEV --enable MFD_CROS_EC_DEV \
    --enable THERMAL --enable THERMAL_GOV_POWER_ALLOCATOR \
    --disable THERMAL_DEFAULT_GOV_STEP_WISE --enable THERMAL_DEFAULT_GOV_POWER_ALLOCATOR \
    --enable CPU_FREQ --disable CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
    --enable CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
    --enable MEDIATEK_CPUFREQ --enable MTK_THERMAL --enable MTK_LVTS_THERMAL \
    --enable I2C --enable HID --enable HID_SUPPORT --enable INPUT_TOUCHSCREEN \
    --enable DRM --enable DRM_MEDIATEK --enable DRM_MEDIATEK_DP \
    --enable DRM_MIPI_DSI --enable BACKLIGHT_CLASS_DEVICE \
    --enable DRM_PANEL_HIMAX_HX83102 --enable DRM_FBDEV_EMULATION \
    --enable FRAMEBUFFER_CONSOLE --enable VT --enable VT_CONSOLE \
    --enable SND --enable SND_SOC --enable SND_SOC_SOF_TOPLEVEL \
    --enable SND_SOC_SOF_OF --enable SND_SOC_SOF_MTK_TOPLEVEL \
    --enable MEDIA_SUPPORT --enable MEDIA_PLATFORM_SUPPORT \
    --enable MEDIA_PLATFORM_DRIVERS --enable V4L_MEM2MEM_DRIVERS \
    --enable VIDEO_DEV --enable WLAN --enable CFG80211 --enable MAC80211 \
    --enable WLAN_VENDOR_MEDIATEK --enable BT --enable BT_HCIBTUSB \
    --enable KEXEC --enable KEXEC_FILE --enable KEXEC_CORE \
    --enable SERIAL_8250 --enable SERIAL_8250_CONSOLE \
    --enable SERIAL_8250_MT6577 --enable SMP \
    --disable DEBUG_INFO --disable DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
    --disable DEBUG_INFO_DWARF4 --disable DEBUG_INFO_DWARF5 \
    --enable DEBUG_INFO_NONE --disable LOCALVERSION_AUTO \
    --set-str INITRAMFS_SOURCE ''
"${config[@]}" --module HID_HIMAX --module I2C_HID_OF \
    --module DRM_PANFROST --module SND_SOC_MT8188_MT6359 \
    --module SND_SOC_SOF_MT8186 --module VIDEO_MEDIATEK_VCODEC \
    --module VIDEO_MEDIATEK_MDP3 --module MT7921E
make O=/work/out olddefconfig

python3 -I -c '
import pathlib
text = pathlib.Path("/work/out/.config").read_text()
required = ["ARCH_MEDIATEK", "MMC_MTK", "EXT4_FS", "HID_HIMAX", "I2C_HID_OF", "DRM_PANEL_HIMAX_HX83102", "DRM_MEDIATEK", "DRM_PANFROST", "SND_SOC_MT8188_MT6359", "SND_SOC_SOF_MT8186", "VIDEO_MEDIATEK_VCODEC", "VIDEO_MEDIATEK_MDP3", "MT7921E"]
missing = [name for name in required if not any(f"CONFIG_{name}={value}\n" in text for value in ("y", "m"))]
for name in required:
    print(next((line for line in text.splitlines() if line.startswith(f"CONFIG_{name}=")), f"MISSING: CONFIG_{name}"))
assert not missing, f"Required Ciri options disabled after olddefconfig: {missing}"
'
cp /work/out/.config /work/artifacts/kernel.config
printf 'Compiling ARM64 Image, Image.gz, modules, and device trees\n'
make O=/work/out -j"$jobs" Image Image.gz modules dtbs
release=$(make --no-print-directory -s O=/work/out kernelrelease)
printf '%s\n' "$release" > /work/artifacts/kernel-release.txt
printf 'Installing matching kernel modules\n'
make O=/work/out -j"$jobs" modules_install INSTALL_MOD_PATH=/work/rootfs
# Omit build-host symlinks from the distributable module tree.
rm -f "/work/rootfs/lib/modules/$release/build" "/work/rootfs/lib/modules/$release/source"
cp /work/out/arch/arm64/boot/Image /work/artifacts/Image
cp /work/out/arch/arm64/boot/Image.gz /work/artifacts/Image.gz
cp /work/out/System.map /work/artifacts/System.map
mkdir -p /work/artifacts/dtbs
ciri_dtbs=(/work/out/arch/arm64/boot/dts/mediatek/mt8188-geralt-ciri-sku*.dtb)
[ -f "${ciri_dtbs[0]}" ]
cp "${ciri_dtbs[@]}" /work/artifacts/dtbs/
tar --zstd -cf /work/artifacts/modules.tar.zst -C /work/rootfs lib/modules
printf 'source=https://github.com/mkopec/linux.git\nbranch=ciri-full\nrevision=%s\nkernel_release=%s\nbuild_jobs=%s\n' "$revision" "$release" "$jobs" > /work/artifacts/build-info.txt
aarch64-linux-gnu-gcc --version >> /work/artifacts/build-info.txt
printf 'Firmware and a distro initramfs are not included. The Ciri touchscreen firmware must be available in the initramfs; use the ciri-support project for firmware and userspace setup. This is not a signed ChromeOS partition or a full distro disk image.\n' > /work/artifacts/README.txt
cd /work/artifacts
find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
printf 'BUILD COMPLETE: %s (%s)\n' "$release" "$revision"
du -h Image Image.gz modules.tar.zst dtbs
