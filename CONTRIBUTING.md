# Contributing

## Development checks

Use Linux, Bash and Python 3.12+. Run `make check` before submitting changes. It checks shell/Python syntax, consistency of pinned source revisions, artifact checksums and Docker endpoint/input validation without downloading code or running a container.

For changes to assembly, run `make test` with verified local kernel, firmware, cgpt and Submarine inputs. It creates a temporary whole-disk image with a nonbootable rootfs fixture. Do not describe a passing construction test as a successful hardware boot.

## Safety boundaries

- Keep image construction unprivileged and mount-free. Never add automatic writes to a physical drive.
- Continue refusing existing image outputs and requiring the actual Ciri SKU and intentional login credentials.
- Inspect ARM64 ELF tools on the host; do not run them on the host or use host `ldd` on guest files.
- Preserve checksum, static-link and architecture checks for native cgpt.
- Keep downloaded/extracted data in fresh directories, separate from executable scripts. Run Python with `-I` when handling that data.
- Remote containers may use their own named build volumes; do not mount Docker-host directories, sockets or block devices into them.
- Do not commit images, recovery archives, firmware, build artifacts, private keys or device-unique VPD data.

## Source updates

Update both `sources.json` and the corresponding build script default revision. Record the source and compiler/build provenance, rerun relevant builds/tests, and clearly distinguish build success, QEMU checks and real hardware results.

Source commits and the remote base image are pinned. Package repositories and default rootfs/recovery downloads remain moving inputs; do not promise byte-for-byte reproducibility.

CI is not configured. Local `make check` and `make test` are the available validation entry points.
