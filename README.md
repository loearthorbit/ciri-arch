# Ciri Arch

Build tooling for a minimal Arch Linux ARM disk image for the **Lenovo Chromebook Duet with MediaTek Kompanio 838 / MT8188**, board **Ciri** in the Geralt family.

This is not for the original MT8183 Duet or the Snapdragon Duet 3. It creates a new whole-disk image file; it does not install onto an existing ChromeOS disk layout or flash a drive.

## Status

Documentation updated **2026-11-05**. The image inspection and boot/login reports below are from **2026-11-03**; the latest audio report is dated separately.

- Ciri `7.3.0-rc4-ciri` kernel, modules and SKU0–SKU7 DTBs built and checksummed. A second remote build has `CONFIG_FRAMEBUFFER_CONSOLE_ROTATION=y`; the original artifacts do not.
- Ciri-only firmware extracted from Google recovery `16836.19.0` and checksummed.
- Static ARM64 `cgpt` built; ChromeOS priority/tries/successful operations passed under QEMU.
- Password-only and explicit blank-root-console modes, persistent signed-rootfs caching, and distro console-rotation options implemented. Construction tests use real boot artifacts but a **deliberately nonbootable rootfs fixture**.
- Real Arch-rootfs images have been built. The device owner reports reaching the Ciri Linux console, but also reports root remaining locked and the console remaining clockwise by 90 degrees after writing another image. Earlier login required a manual shadow-file edit.
- **Direct inspection of `images/arch-ciri-upright.img` found an empty root password field, the new kernel binary, PAM `nullok`, and `fbcon=rotate:3` in both GRUB copies.** Its written USB and runtime state have not been compared successfully. The discrepancy and hardware rotation result remain unresolved; another rebuild alone is not an established fix.
- **2026-11-05:** the device owner reports sound working. A camera-enabled kernel was incrementally rebuilt from the retained remote source and objects, downloaded and checksum-verified. Only four config values changed: `VIDEO_MTK_ISP_71_CAMSYS`, `VIDEO_GC05A2`, `VIDEO_GC08A3` and `VIDEO_DW9768`, from disabled to modules. The full build config is saved in [config/ciri.config](config/ciri.config). No installation or camera capture test has yet been completed.

The local checkout has inputs under `artifacts/` and newer kernel output under `build/`. These generated files are ignored by Git and will not appear in a fresh clone. Both kernel builds report the same release, so `uname -r` cannot distinguish them.

## Documentation map

- [Build workflow](#build-workflow): remote Docker builds, firmware extraction, signed-rootfs cache and image assembly.
- [Boot layout and removable media](#boot-layout): what each partition contains and what writing a whole image does.
- [Troubleshooting](docs/TROUBLESHOOTING.md): root lock/password failures, console rotation, actual-image inspection and written/booted-image comparison.
- [Bring-up status](docs/BRINGUP-STATUS.md): dated evidence, local artifact locations, image identifiers and outstanding hardware checks.
- [Contributing](CONTRIBUTING.md): development checks and test limitations.
- [Third-party components](THIRD_PARTY.md): source and redistribution boundaries.

Snapdragon Duet 3 camera research belongs in the separate [Wormdingler folder](../wormdingler/README.md), not this MT8188 project. That sibling link is for the local workspace and is not part of a standalone Ciri checkout. The x86_64 ThinkPad C13 ChromeOS-EC fingerprint, SSH sudo and GDM work is not Ciri or Wormdingler hardware guidance.

## Requirements

For host-side image construction:

- Linux, Bash, Python **3.12+**, GNU coreutils and `make`.
- `fakeroot` for unprivileged construction.
- `bsdtar`/libarchive, `gzip`, `cpio`, e2fsprogs (`mkfs.ext4`, `debugfs`), dosfstools (`mkfs.vfat`), mtools and GPT fdisk (`sgdisk`).
- Host-native `cgpt`, GNU binutils (`readelf`), OpenSSH (`ssh-keygen`).
- `curl` and GnuPG for the default rootfs download.
- Either locally installed `depthchargectl` and `futility`, or an **already cached** local Podman packer image containing them. The default cached image name is `localhost/submarine-build:latest`; override with `--build-image`.
- An ARM64 Submarine kernel with its embedded patched u-root initramfs, supplied separately as `artifacts/submarine/Image.gz` or via `--submarine-kernel`. A signed `.kpart` is not that input. The tested carrier uses [our fixed u-root fork](https://github.com/loearthorbit/u-root/tree/fix/grub-separate-boot-partition), pinned to commit `943ea126e1cb657bde11eca30d5a12b890eb35fe` (upstream [PR 3817](https://github.com/u-root/u-root/pull/3817)). When rebuilding the carrier, use that fixed revision rather than an unpatched upstream checkout.

Firmware extraction needs `unzip`, `debugfs`, several GiB of temporary space, and a separately reviewed upstream updater. No root privileges, loop devices or mounts are needed.

Remote builds use the Docker HTTP API directly, so local Podman/Docker CLI differences do not matter. The pinned Debian image in `sources.json` must already be available on that daemon; the runner does not pull it implicitly. Allow at least 12 GiB free build space for the kernel.

## Build workflow

### 1. Connect to the remote Docker daemon

Replace `builder@build-host.example` with your SSH account and build host, and adjust the remote Docker socket path if needed:

```sh
ssh -N -T -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:23755:/var/run/docker.sock builder@build-host.example
```

Keep the tunnel open. The runner accepts only loopback HTTP endpoints, defaults to port 23755, disables HTTP proxies, and mounts only a fresh named build volume—not host directories or disks. Docker API access grants control of the remote daemon; do not expose this port publicly.

### 2. Build cgpt and the Ciri distro kernel

```sh
make cgpt
make kernel
```

Each invocation creates a fresh remote container and volume and a new local `build/` directory. The runner prints `LOCAL ARTIFACTS` and records that path in `artifact-directory.txt`. Containers, volumes and logs remain available for inspection. No automatic cleanup or overwrite occurs. It downloads the remote artifacts to this host; a successful build does **not** replace the original `artifacts/kernel` directory automatically.

To locate completed runs from the project root:

```sh
find build -name artifact-directory.txt -print
# Read the record belonging to the run you intend to use:
# cat build/ciri-kernel-<run-id>/artifact-directory.txt
```

The current local rotation-enabled run is recorded in [bring-up status](docs/BRINGUP-STATUS.md). Its artifact directory contains `Image`, `Image.gz`, `modules.tar.zst`, eight `dtbs/mt8188-geralt-ciri-sku*.dtb` files, `kernel.config`, release/source/build metadata, `System.map` and `SHA256SUMS`. Use the entire matching artifact set, not a kernel copied from one run and modules or a config copied from another.

The scripts pin the source revisions in `sources.json`. For an intentionally different revision, call `scripts/remote-build.py` directly with `--revision FULL_COMMIT_HASH` and review/test that source first. Kernel builds use 4 jobs and a 6 GiB memory limit; cgpt uses 4 jobs and 3 GiB.

### 3. Extract device firmware

Obtain and review `package/ciri-firmware-update` from [mkopec/ciri-mainline](https://github.com/mkopec/ciri-mainline). Keep that executable code separate from downloaded recovery data. The hash of the updater used for the existing extraction is recorded in `sources.json`; review changes before using a different updater.

```sh
mkdir -p build
python3 -I scripts/extract-ciri-firmware.py \
  --updater /path/to/reviewed/ciri-firmware-update \
  --output build/firmware-run-1
```

The output directory must not exist. By default, the wrapper selects the stable recovery and uses the updater's published size/SHA-1 verification. It retains the archive, extraction work, provenance and four firmware files under this new directory; the artifact path is `build/firmware-run-1/artifacts`:

- `himax_i2chid_1002.bin`
- `himax_i2chid_1003.bin`
- `himax_i2chid_1004.bin`
- `mediatek/mt8188/scp-dual.img`

The image builder copies these into the distro rootfs **and** its initramfs. These are touchscreen/SCP firmware inputs, not an extraction of every ChromeOS driver, camera userspace stack or device-unique calibration. No VPD calibration backup has been made by this workflow.

An optional `--zip /path/to/recovery.zip` uses a local archive instead. This mode **does not verify it against Google's recovery index**; provenance records that limitation. Extraction paths cannot contain whitespace, quotes or backslashes because of upstream `debugfs` path parsing.

### 4. Assemble an image

Identify the actual Ciri SKU, 0–7, from the device's original ChromeOS board/device-tree information. Do not guess. Use artifact directories printed by the build steps, or the verified local inputs already under `artifacts/`.

```sh
mkdir -p images
# Set CIRI_SKU to this device's actual SKU before running.
scripts/bundle-ciri-arch.sh \
  --sku "$CIRI_SKU" \
  --kernel-dir artifacts/kernel \
  --firmware-dir artifacts/firmware \
  --cgpt-dir artifacts/cgpt \
  --submarine-kernel artifacts/submarine/Image.gz \
  --ssh-key "$HOME/.ssh/id_ed25519.pub" \
  --output images/arch-ciri.img
```

The default generic Arch Linux ARM rootfs is downloaded **once** over HTTPS and retained under `build/cache/arch-rootfs/`, together with its detached signature, public signing keys and SHA-256 provenance. Every later build reuses that archive and verifies its hashes and pinned-key signature **offline**; it does not redownload the archive, signature or keys. A lock prevents concurrent builds from duplicating downloads. Failed image builds do not remove the cache.

Add `--refresh-rootfs` only when you intentionally want a fresh rootfs download. Refreshing publishes a new verified snapshot atomically and preserves previous snapshots; a failed refresh leaves the old cache available. Corrupt or incomplete cache entries fail rather than silently downloading replacements. Use `--rootfs-cache DIR` to choose a different cache location. The cache is ignored by Git. Alternatively provide `--rootfs FILE.tar.gz --rootfs-sha256 HASH` for a local archive, bypassing the cache.

The old builder deleted its downloaded rootfs during cleanup, so its previous builds cannot populate this cache. The first build with caching enabled needs one download; subsequent builds reuse it.

Choose an intentional login mode: an SSH public key, `--password-hash-file FILE`, or explicit `--blank-root-password`. Hash files accept SHA-512 crypt or yescrypt, not plaintext. Known default root/alarm passwords are removed; there is no universal image password.

| Mode | Root password field | Alarm password field | SSH setup |
| --- | --- | --- | --- |
| SSH key only | Locked (`!`) | Locked (`!`) | Installs both users' keys, enables sshd, disables password/keyboard-interactive SSH auth |
| Console password/hash only | Supplied hash | Supplied hash | Does not enable sshd or install keys |
| Blank root console | Empty | Locked (`!`) | Explicitly denies root SSH and empty SSH passwords; does not enable sshd |

The core builder can combine a key with a password hash; SSH still uses the key-only policy while the hash is available for console login. The interactive wrapper intentionally accepts neither supplied keys nor supplied hashes.

For console password login **without an SSH key**, use the interactive rebuild script:

```sh
mkdir -p images
scripts/rebuild-ciri-with-password.sh \
  --sku "$CIRI_SKU" \
  --output images/arch-ciri-password.img
```

It prompts twice without echoing the password, sets that password for both `root` and `alarm`, and uses the existing local artifacts. Only a temporary mode-0600 SHA-512 crypt hash file is passed to the builder; it is removed on exit. No plaintext password is written to a file or supplied as a command-line argument. The wrapper does not configure SSH access. Existing outputs are refused; choose a new filename rather than overwriting an earlier image. Other builder options, such as `--size-gib` or a checksummed local `--rootfs`, may also be supplied.

For a private bring-up device, an explicit blank **root console password** is also supported:

```sh
scripts/rebuild-ciri-with-password.sh \
  --sku "$CIRI_SKU" \
  --blank-root-password \
  --output images/arch-ciri-blank.img
```

This skips password prompts and generates an empty password field for `root`; `alarm` remains locked. The intended local login is `root`, pressing Enter if prompted for a password. The builder enables PAM's empty-password handling and explicitly disables root SSH login and empty SSH passwords. Anyone with console access can become root, so use this only for private hardware bring-up and set a password with `passwd` afterwards. The image manifest records `blank_root_console_login: true`. This does not change an already-built image or the booted USB device.

**An empty field and `!` are different:** `root::...` is passwordless; `root:!:...` is locked. `/etc/passwd` normally contains `x` because passwords are stored in `/etc/shadow`. The latest local image's empty root field was verified directly, but successful blank-password login on that image's actual written/booted device is not established. See [root login troubleshooting](docs/TROUBLESHOOTING.md#root-login-password-or-account-lock).

The default image is 8 GiB (`--size-gib 2..128`). Output images and sidecars must not already exist. The result includes an image, a `.sha256` file and a `.json` manifest recording source provenance, filesystem UUIDs and `hardware_boot_tested: false`.

## Boot layout

| Partition | Size | Purpose |
| --- | --- | --- |
| 1 | 32 MiB | ChromeOS kernel partition containing repacked Submarine |
| 2 | 1 GiB | FAT32 boot files and GRUB configuration |
| 3 | Remaining space | ext4 Arch root filesystem |

New images reserve 1 GiB for boot files to leave room for kernel and initramfs updates. Older images used 256 MiB; this change does not resize existing images or written media. The total image size is unchanged, so the root partition receives correspondingly less space.

Host cgpt sets partition 1 to **priority 15, tries 1, successful 1** in the image's GPT. The successful bit makes it eligible without a ChromeOS boot-success service; it is not evidence of a successful hardware test. Writing an entire image carries those attributes; copying only files or a `.kpart` does not modify an existing disk's GPT.

Static ARM64 cgpt is installed at `/usr/local/bin/cgpt` for later on-device administration. It is separate from the host cgpt used during assembly. No script here changes an existing physical disk's flags.

Submarine is the boot environment, not the board-specific distro kernel. The FAT partition contains `Image-ciri`, `initramfs-ciri.img`, the selected DTB under `dtbs/`, and `grub/grub.cfg`. A second GRUB copy lives in the root filesystem at `/boot/grub/grub.cfg`; both point at the FAT boot UUID for images and the ext4 root UUID for the distro root. The rootfs includes matching kernel modules, firmware and native cgpt. Do not treat either partition's GRUB copy as evidence of which entry the bootloader actually selected.

Submarine's default ARM64 `kexec_file_load` path does not pass a separate GRUB `devicetree` to the next kernel. This builder repacks the selected Ciri DTB into the Submarine carrier so the next kernel can inherit it. Generated GRUB deliberately has no `devicetree` directive. A Ciri console boot has been reported, but the selected SKU and DTB inheritance have not been independently verified on the booted device.

### Making removable boot media

Use a removable device large enough for the selected whole-disk image; **16 GB or larger is a practical recommendation** for the default 8 GiB image. Back up ChromeOS/recovery and the removable device first. External ChromeOS boot requires a device already configured to permit it; enabling developer mode can erase local data. This project does not change firmware/developer-mode settings.

1. Finish assembly and verify the checksum of the **exact output filename** you intend to write. For example, from `images/`, use `sha256sum -c arch-ciri-upright.img.sha256`.
2. Identify the removable device by model, size and transport with `lsblk -o NAME,SIZE,MODEL,TRAN,FSTYPE,UUID,MOUNTPOINTS`. Never reuse a device name from an earlier run as though it were a permanent identity.
3. Explicitly confirm the whole-disk target, unmount its partitions, then use your chosen image writer to write the entire image. This destroys the target's existing partition layout and data. Do not copy the image as a file onto a mounted filesystem, or write it into only one partition.
4. Allow the writer to finish and flush/eject the device. If possible, compare its UUIDs, boot configuration and kernel digest against the source image before booting; see [troubleshooting](docs/TROUBLESHOOTING.md).
5. Boot the external Submarine carrier, then select the intended Ciri Arch entry. Record the distro's actual command line and mounted root UUID after boot.

No project script performs step 3 or selects a physical target automatically. In particular, a new `arch-ciri-upright.img` does not update an older `arch-ciri.img`, `arch-ciri-password.img` or `arch-ciri-blank.img`, and rebuilding does not rewrite a previously flashed drive.

## Distro console orientation

The original `7.3.0-rc4-ciri` build has `CONFIG_FRAMEBUFFER_CONSOLE_ROTATION` disabled. Its console cannot be corrected just by adding a boot argument. The updated recipe enables the option and verifies that it survives `olddefconfig`; a new remote build with the option enabled has already been downloaded locally. This does not update existing kernel binaries or images.

If building another kernel, use `make kernel`, then use its **new artifact directory**, not the original `artifacts/kernel`, when assembling an image. For the reported clockwise-90-degree console, the proposed test is counter-clockwise rotation—not a hardware-confirmed fix:

```sh
scripts/rebuild-ciri-with-password.sh \
  --sku "$CIRI_SKU" \
  --kernel-dir /path/to/new/kernel/artifacts \
  --blank-root-password \
  --console-rotation 3 \
  --output images/arch-ciri-upright.img
```

This adds `fbcon=rotate:3` only to the distro's `linux /Image-ciri` line in GRUB. It leaves Submarine's boot command line unchanged. The builder refuses explicit rotation with an old kernel config that lacks rotation support. Values are 0 = upright, 1 = clockwise 90 degrees, 2 = 180 degrees and 3 = counter-clockwise 90 degrees; confirm the correct result on the tablet. This rotates console text, not the touchscreen mapping or a graphical desktop.

The existing `arch-ciri-upright.img` already contains the new kernel and that argument. Continued clockwise rotation is still reported, so first compare the written image and booted `/proc/cmdline`, `/proc/config.gz` and mounted root UUID. Do not infer a working rotation fix from a generated argument, a config file or a passing construction test. See [console rotation troubleshooting](docs/TROUBLESHOOTING.md#console-still-rotated-clockwise).

## Tablet audio setup

On a booted Ciri tablet with networking, install the audio userspace packages:

```sh
sudo pacman -Syu --needed python alsa-utils alsa-ucm-conf pipewire pipewire-audio pipewire-alsa pipewire-pulse wireplumber
```

Copy `scripts/install-ciri-audio.sh` to the tablet and run it there:

```sh
sudo bash install-ciri-audio.sh
```

The self-contained installer installs a Ciri-specific analog-only UCM `HiFi` profile, a system-wide WirePlumber rule disabling raw Pro Audio, and a MAX98390 speaker-calibration service. It checks the card, required mixer controls, audio firmware and conflicting destination files before installation. When run with sudo by the logged-in desktop user, it restarts that user's audio services and selects `HiFi`; it never starts root's PipeWire instance. It does not download firmware, change a kernel or enable experimental DisplayPort audio.

Without valid device-specific VPD calibration, the service deliberately uses reduced speaker gain (-11 dB). The next kernel recipe enables `GOOGLE_FIRMWARE`, `GOOGLE_COREBOOT_TABLE` and `GOOGLE_VPD` to support VPD access, but the actual firmware/kexec handoff must still expose the tablet's VPD. Do not borrow calibration from another device. `wpctl status` should be checked in the GNOME session after installation; no installer playback test or successful hardware audio result is implied by script validation.

For a nonprivileged preview without modifying audio hardware or system files:

```sh
bash scripts/install-ciri-audio.sh --stage /path/to/new/preview-directory
```

## Built-in camera bring-up

Ciri's built-in cameras are MIPI sensors behind the MediaTek ISP7.1 camera pipeline, not USB webcams. The saved SKU0 device tree contains a front GalaxyCore GC05A2, rear GC08A3 and rear Giantec GT9769 focus actuator. This describes the saved tree, not independent identification of the tablet's SKU or runtime device tree.

The newest saved build under `build/ciri-kernel-_tv4z7dr/download/artifacts` has `VIDEO_MTK_ISP_71_CAMSYS`, `VIDEO_GC05A2`, `VIDEO_GC08A3` and `VIDEO_DW9768` disabled. The recipe now requests these modules plus `MTK_SCP` and `RPMSG_MTK_SCP`, and refuses to compile if any required option is disabled after `olddefconfig`. The DW9768 driver in the pinned fork also matches `giantec,gt9769`. The imported camsys driver includes its own CSI PHY 2.0 implementation; enabling the unrelated `PHY_MTK_MIPI_CSI_0_5` is not the fix for this path.

The camera's SCP core requires `mediatek/mt8188/scp-dual.img`, already among this project's extracted firmware inputs. Check that the actual booted rootfs contains it; artifact presence on the build host is not enough. This is not interchangeable with `scp_c0.img` used by other MediaTek multimedia paths. The [pinned camera device tree](https://github.com/mkopec/linux/blob/b8f841230b1615aa58b5d4389e11bfe66abbd993/arch/arm64/boot/dts/mediatek/mt8188-geralt-ciri-camera.dtsi) specifies the dual-core firmware and connects the front/rear sensors.

On **Ciri after booting Arch**, first collect read-only runtime evidence:

```sh
uname -r
zgrep -E 'CONFIG_(VIDEO_MTK_ISP_71_CAMSYS|VIDEO_GC05A2|VIDEO_GC08A3|VIDEO_DW9768|MTK_SCP|RPMSG_MTK_SCP)' /proc/config.gz
ls -l /usr/lib/firmware/mediatek/mt8188/scp-dual.img
ls /dev/media* /dev/video* /dev/v4l-subdev* 2>/dev/null
sudo dmesg | grep -Ei 'gc05a2|gc08a3|dw9768|gt9769|seninf|camsys|mtk-cam|scp|firmware'
# If v4l-utils is already installed:
v4l2-ctl --list-devices
for m in /dev/media*; do
  [ -e "$m" ] || continue
  media-ctl -d "$m" -p
done
```

If the kernel config is unavailable, report that instead of treating it as proof that a driver is disabled. `/dev/video*` may represent video codecs rather than a camera; device-node existence alone is not a capture test. Stock webcam software cannot compensate for missing capture/sensor drivers.

The camera-enabled build is under `build/ciri-camera-incremental-p43mh2bk/download/ciri-camera-incremental-p43mh2bk`: Image, Image.gz, matching modules, eight SKU DTBs, config and checksums. It reused the previous remote source and compiled objects without source/dependency downloads. Existing images are not updated by editing the script or building a kernel. Keep the matching artifact set together. Any USB/kernel installation is a separate, explicitly chosen operation; do not reuse the dated crypto-kernel installer to install a different artifact set.

### Rebuild locally from the saved config

The configuration changes are stored in [config/ciri.config](config/ciri.config), with the flag-setting recipe in [scripts/build-ciri-kernel.sh](scripts/build-ciri-kernel.sh). Both can be committed to this repository; generated binaries stay under ignored `build/` directories. No kernel C source was changed. Use an existing `mkopec/linux` checkout at the revision pinned in [sources.json](sources.json), with the ARM64 cross-toolchain and kernel build dependencies installed:

```sh
# Run from this repository; set this to the existing pinned kernel source tree.
KERNEL_SOURCE=/path/to/mkopec-linux
OUT="$PWD/build/ciri-local-kernel"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-ciri
mkdir -p "$OUT"
cp config/ciri.config "$OUT/.config"
make -C "$KERNEL_SOURCE" O="$OUT" olddefconfig
make -C "$KERNEL_SOURCE" O="$OUT" -j4 Image Image.gz modules dtbs
```

The kernel source is not vendored in this repository. The existing local Submarine `7.2.11` source is not the pinned Ciri distro kernel. This local command sequence has not been run on the build host; the successful build was on remote Docker.

Userspace remains a separate blocker. The [upstream support status](https://github.com/mkopec/ciri-mainline#status) labels camera support partial, and its [inspected userspace notes](https://github.com/mkopec/ciri-mainline/blob/542342f9d1bd30201b42913796840e8fa471049a/userspace.md) describe an `mtk-isp7` libcamera pipeline handler and IPA as local work, without a repository link or installation recipe. A public, reproducible setup for that handler has not been established here. Do not assume stock Arch libcamera supports this imported driver, or claim GNOME/browser webcam functionality from a passing configuration check. First establish sensor probing and the media topology, then verify actual frame capture with compatible userspace.

## Tests

```sh
make check  # syntax, source-pin consistency and unit tests; no network or Docker
make test   # needs local artifacts and packer; constructs a temporary 2 GiB image
```

Override `KERNEL_DIR`, `FIRMWARE_DIR`, `CGPT_DIR` and `SUBMARINE_KERNEL` for other artifact locations. Construction tests inspect GPT CRCs, filesystems, ownership, boot configuration, initramfs dependencies/firmware, native cgpt placement, checksums and rejection of unsafe inputs. The rootfs fixture is intentionally not bootable and temporary images are removed.

`make test` uses the SSH-key fixture mode. For explicit password/blank-root and rotation-plumbing coverage, the construction script also accepts `--password-login`, `--blank-root-password` and `--console-rotation`; see [Contributing](CONTRIBUTING.md). Its rotation-plumbing option changes a **copied config fixture**, not the kernel binary, and does not test actual console rendering. Unit tests cover password prompting/hash cleanup and cache reuse, refresh, corruption and concurrent access; they do not authenticate a real console or validate the public rootfs server's current content.

## Limits and provenance

- Minimal command-line Arch only. Wired DHCP is configured; Wi-Fi, audio profiles, device-specific calibration and a desktop remain setup tasks.
- No device-unique VPD calibration was backed up or included.
- No automated flashing or existing-layout installer is implemented. Back up ChromeOS and identify a target before considering any physical installation.
- No Windows/UEFI boot path is implemented; the supported output is a ChromeOS-format Submarine carrier followed by an ARM64 Linux kexec handoff.
- Git source commits and the remote base image are pinned, but apt packages and the default Arch/recovery downloads can change. Builds are **not promised bit-for-bit reproducible**.
- `sources.json` records tested inputs, not a guarantee that a fresh Submarine checkout recreates the supplied carrier. Submarine and the u-root GRUB fix remain external projects.
- Generated binaries, archives, firmware, images and private keys stay out of Git. See [THIRD_PARTY.md](THIRD_PARTY.md) for licensing boundaries.
