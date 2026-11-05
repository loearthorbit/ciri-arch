# Ciri bring-up status

Documentation updated **2026-11-04**. This records local build evidence and the latest **2026-11-03** device reports/image inspection; it is not a claim of newly completed hardware testing. Generated paths below belong to the original local workspace and are not shipped in a fresh Git clone.

See [README](../README.md) for reusable build instructions and [Troubleshooting](TROUBLESHOOTING.md) for read-only diagnosis.

## Scope and source inputs

- Device: Lenovo Chromebook Duet, Kompanio 838 / MediaTek MT8188, Ciri board in the Geralt family.
- Distro: a minimal Arch Linux ARM aarch64 rootfs with a Ciri-specific Linux kernel.
- Kernel: `mkopec/linux`, branch `ciri-full`, source `b8f841230b1615aa58b5d4389e11bfe66abbd993`, release `7.3.0-rc4-ciri`.
- Boot carrier: externally built ARM64 Submarine `7.2.11`, based on the Submarine revision recorded in [sources.json](../sources.json). Ciri Arch does not rebuild or vendor that environment.
- u-root: [fixed fork](https://github.com/loearthorbit/u-root/tree/fix/grub-separate-boot-partition), commit `943ea126e1cb657bde11eca30d5a12b890eb35fe`, [upstream PR 3817](https://github.com/u-root/u-root/pull/3817). The supplied carrier includes the cross-partition GRUB fixes; it is not an unpatched upstream carrier.
- cgpt: static ARM64 build from `vboot_reference` revision `72043688a9842a31a92e4829fe71ee94c4939fc8`.
- Firmware: local extraction from ChromeOS Geralt recovery `16836.19.0`; source/provenance and updater hash are recorded in `sources.json`.

The u-root work fixed FAT UUID case matching, `--no-floppy` flag parsing and hint normalization, with tests for configuration on one partition and boot files on another. It does not implement every GRUB device syntax or automatically strip `/boot` from arbitrary paths. The owner previously reported that cross-partition fix working; that is separate from validating the current Ciri distro image.

## Current local artifacts

All paths are relative to the **ciri-arch project root**.

| Input | Local path | Evidence / caveat |
| --- | --- | --- |
| Original Ciri kernel | `artifacts/kernel` | Image/modules/SKU0–SKU7 DTBs built and checksummed; console rotation disabled |
| New Ciri kernel | `build/ciri-kernel-erpnw_al/download/artifacts` | Downloaded from the remote Docker build; console rotation enabled |
| New-run path record | `build/ciri-kernel-erpnw_al/artifact-directory.txt` | Records the artifact directory printed by the runner |
| Ciri firmware | `artifacts/firmware` | Three Himax touchscreen blobs and `mediatek/mt8188/scp-dual.img`, checksummed |
| Native ARM64 cgpt | `artifacts/cgpt` | Static ELF; priority/tries/successful operations exercised under QEMU |
| Submarine input | `artifacts/submarine/Image.gz` | Prebuilt carrier with patched u-root; a `.kpart` is not this input |
| Signed Arch rootfs cache | `build/cache/arch-rootfs` | Archive, detached signature, public keys and provenance retained; offline verification on reuse |

Both distro kernels have **the same release string and source revision**. On 2026-11-03 their raw `Image` digests were compared:

| Build | SHA-256 of raw Image |
| --- | --- |
| Original, rotation disabled | `8d4c0f67cb4934aa9d813db9b203e9e924b448a1d569f5585f27ba6f1a956833` |
| New, rotation enabled | `93b5f5fba3058ce0420b1a1f5757d50f4d5e80899f40ab9d5d09e6d48677e280` |

`uname -r` is therefore insufficient to identify the build. Check the kernel digest and its matching config/modules. New remote runs create new local directories; they do not automatically replace `artifacts/kernel`.

## Latest inspected image

File: `images/arch-ciri-upright.img`, an 8 GiB whole-disk image built on 2026-11-03. The name “upright” expresses the requested test setting, **not a verified display result**.

The manifest records SKU0, blank-root-console mode and rotation `3`. SKU0 was reused from previous image metadata; the physical tablet's SKU has not been independently established by this investigation. Future builders must identify their own board SKU rather than copying it blindly.

Direct read-only inspection of the image's filesystems established:

- Root's `/etc/shadow` password field is **empty**; alarm's field is **locked**.
- PAM `system-auth` contains `nullok` for `pam_unix` authentication.
- The FAT `Image-ciri` digest matches the **new** kernel above.
- Both the FAT `grub/grub.cfg` and ext4 `/boot/grub/grub.cfg` contain the same entry:

```text
search --no-floppy --fs-uuid --set=root 3D63-2203
linux /Image-ciri root=UUID=654f94fb-9f48-4462-99c2-9cc4c8cbfda3 ro rootwait console=tty0 fbcon=rotate:3
initrd /initramfs-ciri.img
```

- The ext4 fstab agrees with root UUID `654f94fb-9f48-4462-99c2-9cc4c8cbfda3` and boot UUID `3D63-2203`.
- The manifest records rootfs archive SHA-256 `42a4eeaa038994ffd31fa173256ef2f0ef511358eeb41b9ea1f8626391b9b319` and `hardware_boot_tested: false`. The rootfs digest is provenance from the manifest; the direct inspection above is distinct from verifying the entire image's SHA-256 sidecar.

These identifiers are for this image only. A new assembly creates new UUIDs.

The older `images/arch-ciri.img`, `images/arch-ciri-password.img` and `images/arch-ciri-blank.img` were also checked for boot arguments: **none contains `fbcon=rotate:`**. Their filenames are distinct outputs and are not updated by building `arch-ciri-upright.img`. Their shadow contents were not inspected in this latest comparison.

## What the device owner reports

- Submarine/u-root reaches its correctly oriented console.
- The Ciri distro reaches a Linux console.
- Earlier login required manually removing `!` from root in shadow.
- After writing another image, root was again reported locked and the distro console still clockwise by 90 degrees.

The source-image inspection contradicts the reported shadow state, but **the reason is not known**. The USB was no longer connected when comparison was attempted. No successfully collected Ciri runtime command line, mounted root UUID, embedded kernel config or fbcon rotation has resolved the discrepancy.

It is not established whether the wrong file was written, a different entry/root was booted, files changed after assembly, or runtime behaviour is responsible. Do not select one of those possibilities as the diagnosis without evidence.

## Tooling fixes and test boundaries

- The read-only `etc/machine-id` extraction failure was reproduced. Assembly now replaces the staged file empty and restores mode `0444`; fakeroot cannot override real write permissions.
- The password rebuild wrapper prompts privately, hashes through stdin, passes only a temporary mode-0600 hash file and cleans it up. It also supports explicit blank-root mode without a prompt/hash.
- Rootfs caching retains signed inputs, verifies warm caches offline, serializes concurrent access, refuses corruption and preserves prior snapshots on refresh/failure.
- The kernel recipe enables framebuffer-console rotation and verifies configuration after `olddefconfig`. Image assembly validates the selected config and adds rotation only to the distro GRUB argument, not the Submarine command line.
- The preceding development session reported 22 unit tests passing, plus construction fixture checks for authentication/rotation settings and the machine-id fix. These are tests of authored code and generated files, **not successful hardware authentication or display rendering**.
- The optional construction rotation test modifies a copied config fixture while retaining its original kernel binary; it proves argument plumbing, not a rotation-enabled running kernel.
- During the 2026-11-04 documentation update, `make check` passed all 22 unit tests. Local Markdown targets/heading anchors and 17 shell examples across the Ciri and Wormdingler docs passed validation without executing the documented commands or fetching external sources. No image rebuild, construction-test rerun, flash or hardware test was performed for that documentation update.

## Next hardware checks

1. Identify the physical board SKU from original ChromeOS/device-tree information and record it.
2. Compare the exact source image with the written media: UUIDs, both GRUB copies, kernel digest, root account state and PAM settings. Do not rewrite or edit the device before collecting evidence.
3. On Ciri **after Arch boots**, capture `/proc/cmdline`, the mounted root source/UUID, `/proc/config.gz` rotation setting and fbcon rotation if present.
4. If identity/settings match, investigate actual login/account changes and framebuffer/DRM takeover on the tablet. Confirm a working password login and a usable orientation rather than relying on fixture assertions.
5. Verify DTB inheritance and matching modules/firmware, then test storage, keyboard, touchscreen, networking, audio, GPU/desktop, suspend/resume, charging and other required hardware. Boot progress is not proof those devices work.

## Still outside the completed work

- No independently verified full hardware boot matrix, rotation fix or blank-root-login success for the latest image.
- No automated physical flashing, existing-layout installation or firmware/developer-mode configuration.
- No device-unique VPD/calibration backup.
- Wi-Fi setup, audio profiles and a desktop are not configured by the minimal image builder.
- No Windows/UEFI handoff implemented.
- No Ciri camera bring-up has been demonstrated by the firmware extraction.

Wormdingler/Trogdor/SC7180 camera research and the owner's Alpine-based Nura/postmarketOS baseline are documented separately in the sibling `wormdingler/` folder. ThinkPad C13 fingerprint enrollment, GDM policy and SSH sudo behaviour are unrelated x86_64 host work, not evidence of either tablet's fingerprint support.
