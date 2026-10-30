#!/usr/bin/env python3
"""Copy ARM64 initramfs tools and their ELF dependencies without executing them."""

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--rootfs", required=True)
parser.add_argument("--initrd", required=True)
parser.add_argument("--busybox")
args = parser.parse_args()
root = Path(args.rootfs).resolve()
initrd = Path(args.initrd).resolve()
seen = set()


def guest_file(name):
    parts = []
    remaining = list(PurePosixPath(name).parts)
    links = 0
    while remaining:
        part = remaining.pop(0)
        if part in ("/", ".", ""):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise SystemExit(f"Symlink loop in rootfs: {name}")
            target = os.readlink(candidate)
            if target.startswith("/"):
                parts = []
            remaining = list(PurePosixPath(target).parts) + remaining
        else:
            parts.append(part)
    path = root.joinpath(*parts)
    if not path.is_file():
        raise SystemExit(f"Missing ARM64 initramfs tool or library: {name}")
    return path


def copy_elf(source, target, require_static=False):
    source = Path(source)
    identity = (source, target)
    if identity in seen:
        return
    seen.add(identity)
    with source.open("rb") as stream:
        header = stream.read(64)
    if len(header) < 64 or header[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 183:
        raise SystemExit(f"Expected an ARM64 ELF64 tool or library: {source}")
    program = subprocess.check_output(["readelf", "--program-headers", str(source)], text=True)
    dynamic = subprocess.check_output(["readelf", "--dynamic", str(source)], text=True)
    interpreter = re.search(r"Requesting program interpreter: ([^\]]+)\]", program)
    if require_static and interpreter:
        raise SystemExit("An external --busybox must be statically linked; use the rootfs BusyBox for a dynamic build")
    destination = initrd / target.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if interpreter:
        name = interpreter.group(1)
        copy_elf(guest_file(name), name)
    for name in re.findall(r"\(NEEDED\).*\[([^\]]+)\]", dynamic):
        library = None
        for directory in ("/usr/lib", "/lib", "/usr/lib/aarch64-linux-gnu", "/lib/aarch64-linux-gnu"):
            try:
                library = guest_file(f"{directory}/{name}")
                break
            except SystemExit:
                continue
        if library is None:
            raise SystemExit(f"Cannot resolve {name}, required by {source}")
        copy_elf(library, f"/lib/{name}")


# Arch's mkinitcpio BusyBox omits mount, blkid, modprobe and switch_root. Include
# the real ARM64 tools and their libraries instead of symlinking those applets.
(initrd / "usr").mkdir(exist_ok=True)
(initrd / "usr/lib").symlink_to("../lib")
(initrd / "usr/bin").symlink_to("../bin")
if args.busybox:
    copy_elf(Path(args.busybox).resolve(), "/bin/busybox", require_static=True)
else:
    copy_elf(guest_file("/usr/lib/initcpio/busybox"), "/bin/busybox")
for name in ("mount", "blkid", "switch_root", "kmod"):
    copy_elf(guest_file(f"/usr/bin/{name}"), f"/bin/{name}")
(initrd / "bin/modprobe").symlink_to("kmod")
print("Copied ARM64 BusyBox, mount, blkid, switch_root, kmod and their ELF libraries")
