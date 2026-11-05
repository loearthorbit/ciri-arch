# Contributing

## Development checks

Use Linux, Bash and Python 3.12+. Run `make check` before submitting changes. It checks shell/Python syntax, consistency of pinned source revisions, and unit tests for Docker endpoint/input validation, remote checksum handling, password prompting/hash cleanup and signed-rootfs-cache behaviour. It does not download code, run a container or verify every real local artifact set.

For changes to assembly, run `make test` with verified local kernel, firmware, cgpt and Submarine inputs. It verifies the selected inputs and creates a temporary whole-disk image with a nonbootable rootfs fixture. Do not describe a passing construction test as a successful hardware boot. The default fixture uses SSH-key login.

Exercise the explicit login/rotation paths when changing them:

```sh
python3 -I scripts/test-bundle-ciri-arch.py \
  --kernel-dir artifacts/kernel --firmware-dir artifacts/firmware \
  --cgpt-dir artifacts/cgpt --submarine-kernel artifacts/submarine/Image.gz \
  --password-login

python3 -I scripts/test-bundle-ciri-arch.py \
  --kernel-dir artifacts/kernel --firmware-dir artifacts/firmware \
  --cgpt-dir artifacts/cgpt --submarine-kernel artifacts/submarine/Image.gz \
  --blank-root-password --console-rotation 3
```

These are construction tests, not production builds. `--password-login` and `--blank-root-password` are mutually exclusive. **The test's `--console-rotation` option edits a copied kernel-config fixture, not its kernel binary.** It verifies configuration guards, GRUB argument plumbing and metadata, not actual framebuffer rendering. Use a genuinely rotation-enabled kernel and real booted-device evidence for hardware validation.

For documentation updates, preserve the distinction between generated settings, directly inspected image contents, written-media contents and observed runtime behaviour. Record dates and exact image/kernel identifiers in [bring-up status](docs/BRINGUP-STATUS.md), and maintain the [troubleshooting guide](docs/TROUBLESHOOTING.md). Do not turn unresolved reports into claims of a completed fix.

## Safety boundaries

- Keep image construction unprivileged and mount-free. Never add automatic writes to a physical drive.
- Continue refusing existing image outputs and requiring the actual Ciri SKU and intentional login credentials. Blank-root mode must remain explicit, mutually exclusive with keys/hashes, and must not enable root/empty-password SSH access.
- Preserve read-only-file handling for staged `machine-id`; fakeroot does not grant real root privileges. Do not introduce privileged assembly as a workaround.
- Keep the signed-rootfs cache persistent across failed builds, verify warm snapshots offline, serialize cache updates and publish refreshed snapshots atomically without discarding the previous valid snapshot. Do not silently redownload or bypass verification on corruption.
- Apply console-rotation arguments only to the distro entry, require supported kernel configuration and do not claim desktop/touchscreen rotation support from fbcon settings.
- Inspect ARM64 ELF tools on the host; do not run them on the host or use host `ldd` on guest files.
- Preserve checksum, static-link and architecture checks for native cgpt.
- Keep downloaded/extracted data in fresh directories, separate from executable scripts. Run Python with `-I` when handling that data.
- Remote containers may use their own named build volumes; do not mount Docker-host directories, sockets or block devices into them.
- Do not commit images, recovery archives, firmware, build artifacts, private keys or device-unique VPD data.

## Source updates

Update both `sources.json` and the corresponding build script default revision. Record the source and compiler/build provenance, rerun relevant builds/tests, and clearly distinguish build success, QEMU checks and real hardware results.

Source commits and the remote base image are pinned. Package repositories and default rootfs/recovery downloads remain moving inputs; do not promise byte-for-byte reproducibility.

CI is not configured. Local `make check` and `make test` are the available validation entry points.
