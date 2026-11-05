# Ciri boot and login troubleshooting

Updated 2026-11-04. These notes apply to Ciri/Geralt/MT8188 Arch Linux ARM images, not the Snapdragon Duet 3 or the ThinkPad C13 fingerprint setup.

Start with the [build workflow](../README.md#build-workflow) and [dated bring-up status](BRINGUP-STATUS.md). A passing build, generated manifest or fixture test does not establish which image was written or which root filesystem and kernel the tablet actually booted.

## First establish which system you are inspecting

There are three separate environments:

1. **Build host:** creates regular image files, downloads remote artifacts and can inspect image contents without mounting them.
2. **Submarine/u-root on Ciri:** the first boot environment. Its kernel and command line are not the distro kernel and command line.
3. **Arch Linux ARM on Ciri:** the system reached after kexec. Runtime login and distro-console rotation evidence must come from here.

Run these on **Ciri after booting Arch**, not on the host or in u-root:

```sh
cat /proc/cmdline
findmnt -n -o SOURCE,UUID /
uname -r
zgrep CONFIG_FRAMEBUFFER_CONSOLE_ROTATION /proc/config.gz
cat /sys/class/graphics/fbcon/rotate
```

If `/proc/config.gz` or the fbcon sysfs file is unavailable, record that rather than assuming the option is disabled or the rotation is zero. The original and rotation-enabled builds both report `7.3.0-rc4-ciri`; `uname -r` alone does not distinguish them.

Compare the mounted root UUID and `root=UUID=` argument with the selected image's manifest and its embedded GRUB configuration. Check the actual kernel on the boot filesystem by digest. Do not identify a device by a stale `/dev/sdX` name or assume a familiar image filename was updated by a later build.

## Root login: password or account lock

There is no universal root password. The requested build mode determines the shadow fields:

- Key-only mode locks root/alarm password authentication with `!`, installs SSH keys and enables sshd.
- Password-only mode places the supplied password hash in both root and alarm and does not enable sshd.
- `--blank-root-password` produces an **empty root field**, leaves alarm locked, adds PAM `nullok` and denies root SSH/empty SSH passwords. It is only for private hardware bring-up; set a real password afterwards.

`root::...` means passwordless; `root:!:...` means locked. An `x` in `/etc/passwd` is normal: consult `/etc/shadow`. An empty root password does not provide an SSH login, and alarm remains locked in blank-root mode.

If root still has `!` after requesting blank-root mode:

1. Check that the wrapper was invoked with `--blank-root-password` and that the intended image filename is the one written. Use this project's `scripts/` directory, not the older retained scripts under a sibling Submarine checkout.
2. Inspect the actual source image, not only `blank_root_console_login: true` in its JSON sidecar; the read-only procedure below reports account state without printing hashes.
3. Compare the written device's root UUID, GRUB copies and shadow state with that source image. Inspect only; do not edit a mounted drive as part of diagnosis without explicitly choosing and backing up the target.
4. Compare the booted root UUID and command line. If source-image, written-device and booted-root identity all match but the shadow state differs, investigate boot-time changes and relevant logs. A matching manifest alone is not enough to identify which stage changed the account.
5. If the field is correct but login still fails, examine the actual booted PAM login stack, account status and authentication logs. On an accessible Ciri system, `sudo passwd -S root` is a read-only status check; it does not set a password. Never share password hashes or private keys in troubleshooting output.

The owner previously removed `!` manually to log in. The 2026-11-03 source-image inspection found root empty in `arch-ciri-upright.img`, but the written device was disconnected before comparison. Neither successful blank-root authentication nor the cause of the reported lock is established. Do not present repeated image construction as a proven hardware fix.

## Console still rotated clockwise

Submarine's console is reported correctly oriented; the distro console is reported clockwise by 90 degrees. The settings must therefore be checked on the **distro** side.

Both prerequisites are necessary:

- The selected distro kernel has `CONFIG_FRAMEBUFFER_CONSOLE_ROTATION=y`.
- The selected distro GRUB `linux /Image-ciri` line contains the requested `fbcon=rotate:N`.

Values are 0 = upright, 1 = clockwise 90 degrees, 2 = 180 degrees, and 3 = counter-clockwise 90 degrees. The value `3` was proposed as a test for this report; its hardware result is not confirmed. fbcon rotation changes text consoles, not touchscreen coordinates or desktop display orientation.

The original `artifacts/kernel` has rotation disabled. A new `make kernel` run downloads into a fresh `build/ciri-kernel-<run-id>/download/artifacts` directory; it does not replace the old artifacts automatically. Pass the new directory with `--kernel-dir` and retain its matching modules, DTBs, config and checksums. The builder refuses explicit rotation with an unsupported kernel config.

The latest inspected image already has the new binary and `fbcon=rotate:3`. All three older local images lack that argument. If rotation is still wrong:

1. Verify the written boot filesystem's `Image-ciri` digest against the new artifact and inspect both GRUB copies.
2. Check the booted distro command line, embedded kernel config and mounted root UUID using the commands above.
3. If those match, record the actual fbcon rotation and framebuffer/DRM driver messages. Driver takeover or runtime rotation behaviour then needs investigation on Ciri; do not guess a new argument or declare the problem solved solely from a file on the build host.

The image's `dtbs/` directory is not a separate-DTB kexec mechanism. This builder embeds the chosen Ciri DTB in Submarine, deliberately omitting a GRUB `devicetree` directive. The inherited device tree and actual board SKU remain hardware checks.

## Inspect a source image without mounts or ARM execution

Run from the **ciri-arch project root on the build host**. The following only reads the chosen source image and writes inspection files into a newly created directory under `build/`. It does not mount a filesystem, execute guest programs or write a physical drive.

Allow enough free space for the root partition. The extracted file is sparse where possible, but its allocated size depends on the image contents. Keep downloaded/extracted data separate from authored executable scripts.

```sh
IMAGE=$(realpath images/arch-ciri-upright.img)
python3 -I -m json.tool "$IMAGE.json"
sgdisk -p "$IMAGE"

BOOT_START=$(cgpt show -i 2 -b "$IMAGE")
ROOT_START=$(cgpt show -i 3 -b "$IMAGE")
ROOT_SECTORS=$(cgpt show -i 3 -s "$IMAGE")

mkdir -p build
INSPECT=$(mktemp -d build/image-inspect.XXXXXXXX)
printf 'Inspection directory: %s\n' "$INSPECT"

mtype -i "$IMAGE@@$((BOOT_START * 512))" ::/grub/grub.cfg
mcopy -i "$IMAGE@@$((BOOT_START * 512))" ::/Image-ciri "$INSPECT/Image-ciri"
sha256sum "$INSPECT/Image-ciri"

# The destination is a NEW REGULAR FILE in the inspection directory, never /dev/*.
dd if="$IMAGE" of="$INSPECT/root.part" bs=4M \
  skip="$((ROOT_START * 512))" count="$((ROOT_SECTORS * 512))" \
  iflag=skip_bytes,count_bytes conv=sparse status=progress

debugfs -R 'cat /etc/fstab' "$INSPECT/root.part"
debugfs -R 'cat /boot/grub/grub.cfg' "$INSPECT/root.part"
debugfs -R 'cat /etc/pam.d/system-auth' "$INSPECT/root.part"
```

`debugfs` is read-only unless explicitly given `-w`; do not add it. Report shadow state without displaying any hash:

```sh
python3 -I - "$INSPECT/root.part" <<'PY'
import subprocess
import sys

result = subprocess.run(
    ['debugfs', '-R', 'cat /etc/shadow', sys.argv[1]],
    check=True, text=True, capture_output=True,
)
seen = set()
for line in result.stdout.splitlines():
    fields = line.split(':')
    if fields[0] not in ('root', 'alarm'):
        continue
    if len(fields) != 9:
        raise SystemExit('Malformed account entry')
    name, password = fields[:2]
    state = ('EMPTY' if password == '' else
             'LOCKED' if password.startswith(('!', '*')) else 'HASH/OTHER')
    print(f'{name}: {state}')
    seen.add(name)
if seen != {'root', 'alarm'}:
    raise SystemExit('Missing root/alarm entry or unreadable shadow file')
PY
```

Retain inspection files until comparisons are complete; remove only the specific inspection directory you created when no longer needed. The SHA-256 sidecar covers the whole disk image, whereas the kernel digest above covers only `Image-ciri`.

## Compare a written removable device

First identify it read-only:

```sh
lsblk -o NAME,SIZE,MODEL,TRAN,FSTYPE,UUID,MOUNTPOINTS
```

If its filesystems are already mounted, compare the boot mount's `grub/grub.cfg` and `Image-ciri` digest, and the root mount's `etc/fstab`, `boot/grub/grub.cfg`, PAM settings and root account state. Read access to shadow may require elevated privileges; do not change its permissions or print hashes to obtain evidence. Record UUIDs rather than assuming mount labels uniquely identify this image.

Mounting/replaying an ext4 journal or modifying a filesystem is not the same as read-only file inspection. This guide does not automatically mount, repair, rewrite or select a physical device. Do not edit shadow or GRUB, flash another image, or change ChromeOS partition flags until the exact target and intended operation are confirmed.

## Rootfs downloads repeat or cache verification fails

The current builder and password wrapper share `build/cache/arch-rootfs` by default. The old builder deleted its temporary downloads, so the first cache-enabled build requires one download.

- A warm build verifies retained archive/signature/public-key hashes and the pinned-key signature offline. Failed image assembly does not delete the cache.
- `--refresh-rootfs` intentionally downloads and publishes a new verified snapshot. Prior snapshots are retained; a failed refresh preserves the previously current snapshot.
- `--rootfs-cache DIR` selects another persistent location. Repeatedly supplying a new/temporary directory defeats reuse.
- A corrupt snapshot is rejected, not silently redownloaded. Inspect the error and use an intentional refresh if needed; do not disable checks or manually rewrite cache metadata to accept altered contents.
- `--rootfs FILE --rootfs-sha256 HASH` bypasses the cache. It cannot be combined with `--refresh-rootfs`.
- A file lock prevents concurrent builds duplicating cache downloads. No user GPG keyring or private signing key is needed for rootfs verification.

Use the current `ciri-arch/scripts/` tooling. The older copy under the Submarine source checkout predates caching, blank-root mode and rotation options.

## PermissionError writing etc/machine-id

The original Arch rootfs can contain `etc/machine-id` with mode `0444`. `fakeroot` simulates metadata ownership; it does not grant real write permission to an unprivileged process. The old builder failed when truncating that file in place.

The current builder unlinks and recreates the staged file empty, then restores mode `0444`, allowing the target to generate its own machine identity. Tests cover contents, regular-file type, ownership and permissions. It also removes staged SSH host keys. Do not work around the old failure by running all assembly as real root, changing cached inputs or reusing a partial work directory.

## Build success is not hardware validation

`make check` runs syntax/source-pin and unit checks. `make test` constructs a nonbootable fixture image. Its optional rotation test alters only a copied config fixture, not the binary. None of these tests authenticates a real console or renders text on Ciri.

Image manifests continue to record `hardware_boot_tested: false`; that is not an automatically updated device test log. Record observed hardware results, failures and exact image/kernel identifiers separately in [bring-up status](BRINGUP-STATUS.md).
