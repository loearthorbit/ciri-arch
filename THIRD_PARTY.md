# Third-party components

The tooling retains the GNU GPL version 3 license supplied with the Submarine workspace it was extracted from; see `LICENSE`. That file does not relicense any independently built kernel, utility, distribution package or extracted firmware.

| Component | Source | Boundary |
| --- | --- | --- |
| Submarine | https://github.com/FyraLabs/submarine | External GPL-3.0 project; supplies the ARM64 boot carrier. Its source/build environment is not vendored here. |
| u-root | https://github.com/loearthorbit/u-root/tree/fix/grub-separate-boot-partition (fork of https://github.com/u-root/u-root) | External BSD-3-Clause project embedded in the supplied carrier; fixed commit `943ea126e1cb657bde11eca30d5a12b890eb35fe`, upstream PR 3817. |
| Ciri Linux kernel | https://github.com/mkopec/linux/tree/ciri-full | Linux kernel licensing, generally GPL-2.0-only with file-specific exceptions. Source is cloned only inside the build container. |
| cgpt / vboot_reference | https://chromium.googlesource.com/chromiumos/platform/vboot_reference | Upstream BSD-style license is preserved with the cgpt artifacts. Static libc/library dependencies have their own terms. |
| Ciri firmware updater | https://github.com/mkopec/ciri-mainline | The reviewed updater has an MIT SPDX header; callers must review the revision they supply. |
| Ciri audio configuration | https://github.com/mkopec/ciri-mainline | MIT UCM, calibration and WirePlumber configuration adapted from revision `542342f9d1bd30201b42913796840e8fa471049a` in `scripts/install-ciri-audio.sh`. The installer includes the upstream copyright/license and installs it under `/usr/share/doc/ciri-audio/`. Experimental DisplayPort audio is omitted. |
| ChromeOS recovery firmware | Google's recovery service | Firmware licensing/redistribution rights are not established by the updater's MIT license. Extract locally and do not commit blobs. |
| Arch Linux ARM | https://archlinuxarm.org | Distribution packages retain their respective licenses. Rootfs archives are downloaded or supplied separately, not vendored. |

`sources.json` records the inputs used for the local verified builds. Build artifact folders carry checksums and source metadata. Redistributing binary images or static utilities may require supplying additional notices, corresponding source or relinking materials for their components. Review those requirements before publishing binaries; this repository excludes them from Git by default.
