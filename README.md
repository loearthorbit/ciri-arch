# Ciri Arch

Build tooling for a minimal Arch Linux ARM disk image for the **Lenovo Chromebook Duet with MediaTek Kompanio 838 / MT8188**, board **Ciri** in the Geralt family.

This is not for the original MT8183 Duet or the Snapdragon Duet 3. It creates a new whole-disk image file; it does not install onto an existing ChromeOS disk layout or flash a drive.

## Status

- Ciri `7.3.0-rc4-ciri` kernel, modules and SKU0–SKU7 DTBs built and checksummed.
- Ciri-only firmware extracted from Google recovery `16836.19.0` and checksummed.
- Static ARM64 `cgpt` built; ChromeOS priority/tries/successful operations passed under QEMU.
- Image construction tested with the real kernel, firmware, cgpt and Submarine carrier, but a **deliberately nonbootable rootfs fixture**.
- A real Arch rootfs download/signature-verification build and a Ciri hardware boot have **not** been tested.

The initial local checkout has verified inputs under `artifacts/`. Those files are ignored by Git and will not appear in a fresh clone.

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

Each invocation creates a fresh remote container and volume and a new local `build/` directory. The runner prints `LOCAL ARTIFACTS` and records that path in `artifact-directory.txt`. Containers, volumes and logs remain available for inspection. No automatic cleanup or overwrite occurs.

The scripts pin the source revisions in `sources.json`. For an intentionally different revision, call `scripts/remote-build.py` directly with `--revision FULL_COMMIT_HASH` and review/test that source first. Kernel builds use 4 jobs and a 6 GiB memory limit; cgpt uses 4 jobs and 3 GiB.

### 3. Extract device firmware

Obtain and review `package/ciri-firmware-update` from [mkopec/ciri-mainline](https://github.com/mkopec/ciri-mainline). Keep that executable code separate from downloaded recovery data. The hash of the updater used for the existing extraction is recorded in `sources.json`; review changes before using a different updater.

```sh
mkdir -p build
python3 -I scripts/extract-ciri-firmware.py \
  --updater /path/to/reviewed/ciri-firmware-update \
  --output build/firmware-run-1
```

The output directory must not exist. By default, the wrapper selects the stable recovery and uses the updater's published size/SHA-1 verification. It retains the archive, extraction work, provenance and four firmware files under this new directory; the artifact path is `build/firmware-run-1/artifacts`.

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

The default generic Arch Linux ARM rootfs is downloaded over HTTPS and its detached signature checked against pinned official signing keys in an isolated GPG keyring. Alternatively provide `--rootfs FILE.tar.gz --rootfs-sha256 HASH` for a local archive.

An SSH public key and/or `--password-hash-file FILE` is required. Hash files accept SHA-512 crypt or yescrypt, not plaintext. Known default root/alarm passwords are removed. SSH password authentication is disabled when using a public key.

The default image is 8 GiB (`--size-gib 2..128`). Output images and sidecars must not already exist. The result includes an image, a `.sha256` file and a `.json` manifest recording source provenance, filesystem UUIDs and `hardware_boot_tested: false`.

## Boot layout

| Partition | Size | Purpose |
| --- | --- | --- |
| 1 | 32 MiB | ChromeOS kernel partition containing repacked Submarine |
| 2 | 256 MiB | FAT32 boot files and GRUB configuration |
| 3 | Remaining space | ext4 Arch root filesystem |

Host cgpt sets partition 1 to **priority 15, tries 1, successful 1** in the image's GPT. The successful bit makes it eligible without a ChromeOS boot-success service; it is not evidence of a successful hardware test. Writing an entire image carries those attributes; copying only files or a `.kpart` does not modify an existing disk's GPT.

Static ARM64 cgpt is installed at `/usr/local/bin/cgpt` for later on-device administration. It is separate from the host cgpt used during assembly. No script here changes an existing physical disk's flags.

Submarine's default ARM64 `kexec_file_load` path does not pass a separate GRUB `devicetree` to the next kernel. This builder repacks the selected Ciri DTB into the Submarine carrier so the next kernel inherits it. That handoff strategy still needs a hardware boot test.

## Tests

```sh
make check  # syntax, source-pin consistency and unit tests; no network or Docker
make test   # needs local artifacts and packer; constructs a temporary 2 GiB image
```

Override `KERNEL_DIR`, `FIRMWARE_DIR`, `CGPT_DIR` and `SUBMARINE_KERNEL` for other artifact locations. Construction tests inspect GPT CRCs, filesystems, ownership, boot configuration, initramfs dependencies/firmware, native cgpt placement, checksums and rejection of unsafe inputs. The rootfs fixture is intentionally not bootable and temporary images are removed.

## Limits and provenance

- Minimal command-line Arch only. Wired DHCP is configured; Wi-Fi, audio profiles, device-specific calibration and a desktop remain setup tasks.
- No device-unique VPD calibration was backed up or included.
- No automated flashing or existing-layout installer is implemented. Back up ChromeOS and identify a target before considering any physical installation.
- Git source commits and the remote base image are pinned, but apt packages and the default Arch/recovery downloads can change. Builds are **not promised bit-for-bit reproducible**.
- `sources.json` records tested inputs, not a guarantee that a fresh Submarine checkout recreates the supplied carrier. Submarine and the u-root GRUB fix remain external projects.
- Generated binaries, archives, firmware, images and private keys stay out of Git. See [THIRD_PARTY.md](THIRD_PARTY.md) for licensing boundaries.
