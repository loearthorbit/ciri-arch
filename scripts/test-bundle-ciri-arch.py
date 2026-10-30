#!/usr/bin/env python3
"""Test image construction with real kernel artifacts but a nonbootable rootfs fixture."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zlib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kernel-dir", required=True)
parser.add_argument("--firmware-dir", required=True)
parser.add_argument("--submarine-kernel", required=True)
parser.add_argument("--cgpt-dir", help="Use actual cgpt build artifacts instead of the metadata-only fixture")
args = parser.parse_args()
repo = Path(__file__).resolve().parent.parent
builder = repo / "scripts/bundle-ciri-arch.sh"


def run(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def fixture_elf(needed=(), interpreter=None):
    # Metadata-only ARM64 ELF fixtures. They test dependency copying, not execution.
    data = bytearray(2048)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", data, 16, 2, 183, 1)
    struct.pack_into("<Q", data, 32, 64)
    headers = [(1, 5, 0, 0, 0, len(data), len(data), 4096)]
    if interpreter:
        raw = interpreter.encode() + b"\0"
        data[512:512 + len(raw)] = raw
        headers.append((3, 4, 512, 512, 512, len(raw), len(raw), 1))
    if needed:
        strings = bytearray(b"\0")
        entries = []
        for name in needed:
            entries.append((1, len(strings)))
            strings.extend(name.encode() + b"\0")
        data[1024:1024 + len(strings)] = strings
        entries.extend(((5, 1024), (10, len(strings)), (0, 0)))
        for i, entry in enumerate(entries):
            struct.pack_into("<QQ", data, 768 + i * 16, *entry)
        headers.append((2, 4, 768, 768, 768, len(entries) * 16, len(entries) * 16, 8))
    for i, header in enumerate(headers):
        struct.pack_into("<IIQQQQQQ", data, 64 + i * 56, *header)
    struct.pack_into("<HHHHHH", data, 52, 64, 56, len(headers), 64, 0, 0)
    return bytes(data)


with tempfile.TemporaryDirectory(prefix="ciri-image-test.", dir=repo / "build") as temporary:
    work = Path(temporary)
    elf = fixture_elf()
    cgpt = work / "cgpt-artifacts"
    cgpt.mkdir()
    if args.cgpt_dir:
        for name in ("cgpt", "LICENSE", "source-revision.txt", "build-info.txt", "SHA256SUMS"):
            shutil.copy2(Path(args.cgpt_dir) / name, cgpt / name)
    else:
        (cgpt / "cgpt").write_bytes(elf)
        (cgpt / "LICENSE").write_text("Non-executable test fixture; not an upstream binary.\n")
        (cgpt / "source-revision.txt").write_text("test-fixture\n")
        (cgpt / "build-info.txt").write_text("Metadata-only ARM64 ELF fixture, not a usable cgpt.\n")
    cgpt_binary = (cgpt / "cgpt").read_bytes()
    cgpt_revision = (cgpt / "source-revision.txt").read_text().strip()

    def cgpt_checksums():
        (cgpt / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((cgpt / name).read_bytes()).hexdigest()}  {name}\n"
            for name in ("cgpt", "LICENSE", "source-revision.txt", "build-info.txt")
        ))

    if not args.cgpt_dir:
        cgpt_checksums()
    files = {
        "etc/passwd": b"root:x:0:0:root:/root:/bin/sh\nalarm:x:1000:1000:alarm:/home/alarm:/bin/sh\n",
        "etc/shadow": b"root:root:1::::::\nalarm:alarm:1::::::\n",
        "etc/ssh/sshd_config": b"# fixture\nPasswordAuthentication yes\n",
        "etc/machine-id": b"0123456789abcdef0123456789abcdef\n",
        "etc/ssh/ssh_host_test_key": b"fixture host key\n",
        "usr/lib/initcpio/busybox": fixture_elf(("libfixture.so.1",), "/lib/ld-linux-aarch64.so.1"),
        "usr/lib/ld-linux-aarch64.so.1": elf,
        "usr/lib/guest-library.so": fixture_elf(("libfixture-two.so.1",)),
        "usr/lib/libfixture-two.so.1": elf,
        "usr/bin/mount": elf,
        "usr/bin/blkid": elf,
        "usr/bin/switch_root": elf,
        "usr/bin/kmod": elf,
        "usr/lib/systemd/system/sshd.service": b"[Service]\nExecStart=/usr/bin/sshd\n",
        "usr/lib/systemd/system/systemd-networkd.service": b"[Service]\n",
        "usr/lib/systemd/system/systemd-resolved.service": b"[Service]\n",
    }
    directories = {"boot", "root", "home/alarm", "usr/lib/modules"}
    for name in files:
        directories.update(str(parent) for parent in Path(name).parents if str(parent) != ".")
    archive = work / "rootfs.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in sorted(directories):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            tar.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith("busybox") or name.startswith("usr/bin/") else 0o644
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        for name, target in (("lib", "usr/lib"), ("usr/lib/libfixture.so.1", "/usr/lib/guest-library.so")):
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            member.mode = 0o777
            tar.addfile(member)
    with archive.open("rb") as stream:
        archive_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    key = work / "key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    output = work / "fixture.img"
    command = [
        "bash", str(builder), "--sku", "0", "--kernel-dir", str(Path(args.kernel_dir).resolve()),
        "--firmware-dir", str(Path(args.firmware_dir).resolve()), "--cgpt-dir", str(cgpt),
        "--submarine-kernel", str(Path(args.submarine_kernel).resolve()),
        "--rootfs", str(archive), "--rootfs-sha256", archive_sha,
        "--ssh-key", str(key) + ".pub", "--size-gib", "2", "--output", str(output),
    ]
    print("Testing complete image assembly using a nonbootable rootfs fixture", flush=True)
    log = run(command)
    assert output.stat().st_size == 2 * 1024**3
    manifest = json.loads(Path(str(output) + ".json").read_text())
    assert manifest["ciri_sku"] == 0 and manifest["chromeos_kernel_partition_mib"] == 32
    assert manifest["hardware_boot_tested"] is False
    assert manifest["arch_rootfs_sha256"] == archive_sha
    assert manifest["cgpt_path"] == "/usr/local/bin/cgpt"
    assert manifest["cgpt_sha256"] == hashlib.sha256(cgpt_binary).hexdigest()
    assert manifest["cgpt_source_revision"] == cgpt_revision
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(512)
        header = stream.read(512)
        assert header[:8] == b"EFI PART"
        length, crc = struct.unpack_from("<II", header, 12)
        checked = bytearray(header[:length])
        checked[16:20] = b"\0" * 4
        assert zlib.crc32(checked) == crc
        entries_lba, count, size, entries_crc = struct.unpack_from("<QIII", header, 72)
        stream.seek(entries_lba * 512)
        entries = stream.read(count * size)
        assert zlib.crc32(entries) == entries_crc
        partitions = [struct.unpack_from("<QQ", entries, i * size + 32) for i in range(3)]
        assert (partitions[0][1] - partitions[0][0] + 1) * 512 == 32 * 1024**2
        assert (partitions[1][1] - partitions[1][0] + 1) * 512 == 256 * 1024**2
        stream.seek((output.stat().st_size // 512 - 1) * 512)
        assert stream.read(8) == b"EFI PART"
    assert Path(str(output) + ".sha256").read_text().split()[0] == checksum
    boot_offset = partitions[1][0] * 512
    config = run(["mtype", "-i", f"{output}@@{boot_offset}", "::/grub/grub.cfg"])
    assert f"search --no-floppy --fs-uuid --set=root {manifest['boot_uuid']}" in config
    assert f"linux /Image-ciri root=UUID={manifest['root_uuid']}" in config
    assert "devicetree " not in config
    assert "initrd /initramfs-ciri.img" in config
    initrd = work / "initramfs.img"
    run(["mcopy", "-i", f"{output}@@{boot_offset}", "::/initramfs-ciri.img", str(initrd)])
    incoming = work / "initrd-inspection"
    incoming.mkdir()
    cpio = incoming / "initrd.cpio"
    cpio.write_bytes(gzip.decompress(initrd.read_bytes()))
    names = run(["bsdtar", "-tf", str(cpio)])
    for name in ("init", "bin/busybox", "bin/mount", "bin/blkid", "bin/kmod", "bin/switch_root", "lib/libfixture.so.1", "lib/libfixture-two.so.1", "lib/ld-linux-aarch64.so.1", "lib/firmware/himax_i2chid_1002.bin", "lib/firmware/mediatek/mt8188/scp-dual.img"):
        assert any(line.removeprefix("./") == name for line in names.splitlines()), name
    init = run(["bsdtar", "-xOf", str(cpio), "./init"])
    run(["bash", "-n"], input=init)
    root_part = work / "root.part"
    with output.open("rb") as source, root_part.open("wb") as destination:
        offset, last = partitions[2]
        source.seek(offset * 512)
        left = (last - offset + 1) * 512
        while left:
            data = source.read(min(4 * 1024**2, left))
            assert data
            if data.strip(b"\0"):
                destination.write(data)
            else:
                destination.seek(len(data), os.SEEK_CUR)
            left -= len(data)
        destination.truncate()
    for name, expected in (
        ("/etc/fstab", f"UUID={manifest['root_uuid']} / ext4"),
        ("/etc/shadow", "root:!:"),
        ("/etc/ssh/sshd_config", "Include /etc/ssh/sshd_config.d/*.conf"),
        ("/etc/ssh/sshd_config.d/10-ciri-image.conf", "PasswordAuthentication no"),
    ):
        dump = work / "dump"
        run(["debugfs", "-R", f"dump {name} {dump}", str(root_part)])
        assert expected in dump.read_text(), name
        dump.unlink()
    installed_cgpt = work / "installed-cgpt"
    run(["debugfs", "-R", f"dump /usr/local/bin/cgpt {installed_cgpt}", str(root_part)])
    assert installed_cgpt.read_bytes() == cgpt_binary
    stat = run(["debugfs", "-R", "stat /usr/local/bin/cgpt", str(root_part)])
    assert re.search(r"User:\s+0\s+Group:\s+0", stat), stat
    assert re.search(r"Mode:\s+0755", stat), stat
    provenance = work / "cgpt-source"
    run(["debugfs", "-R", f"dump /usr/share/doc/ciri-cgpt/source-revision.txt {provenance}", str(root_part)])
    assert provenance.read_text().strip() == cgpt_revision
    stat = run(["debugfs", "-R", "stat /root/.ssh/authorized_keys", str(root_part)])
    assert re.search(r"User:\s+0\s+Group:\s+0", stat), stat
    stat = run(["debugfs", "-R", "stat /home/alarm/.ssh/authorized_keys", str(root_part)])
    assert re.search(r"User:\s+1000\s+Group:\s+1000", stat), stat
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0 and "Refusing to replace" in result.stderr
    rejected_output = work / "rejected.img"
    rejected_command = command[:-1] + [str(rejected_output)]
    wrong_arch = bytearray(elf)
    struct.pack_into("<H", wrong_arch, 18, 62)
    for data, message in (
        (bytes(wrong_arch), "Expected a static ARM64 ELF64 cgpt executable"),
        (fixture_elf(interpreter="/lib/ld-linux-aarch64.so.1"), "ARM64 cgpt must be statically linked"),
        (fixture_elf(("libfixture.so.1",)), "ARM64 cgpt must be statically linked"),
    ):
        (cgpt / "cgpt").write_bytes(data)
        cgpt_checksums()
        result = subprocess.run(rejected_command, text=True, capture_output=True)
        assert result.returncode != 0 and message in result.stderr, result.stderr
        assert not rejected_output.exists()
    (cgpt / "cgpt").write_bytes(elf + b"tampered")
    result = subprocess.run(rejected_command, text=True, capture_output=True)
    assert result.returncode != 0 and "Checksum mismatch" in result.stderr, result.stderr
    assert not rejected_output.exists()
    assert not list(work.glob(".arch-ciri-work.*"))
    assert not list(work.glob(".arch-ciri-image.*"))
    print("PASS: GPT, filesystem owners, boot config, firmware initramfs, native cgpt inclusion/validation, login hardening, checksums, cleanup and overwrite refusal")
    print("The rootfs fixture is not bootable. This test does not establish hardware boot support.")
