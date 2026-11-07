#!/usr/bin/env python3
"""Enable camera companion nodes in the retained carrier; never write a device."""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
import zlib

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_SHA256 = "39e376ffd9e9f431f9bd308141549d373bce59842b727f02c71dbed1c06df1ba"
CAMERA_NODES = (
    "/soc/imgsys-fw@15000000", "/soc/aie@15310000",
    "/soc/imgsys-l10", "/soc/imgsys-l11a", "/soc/imgsys-l11b",
    "/soc/imgsys-l11c", "/soc/imgsys-l15",
)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def require(condition, message):
    if not condition:
        raise ValueError(message)


def properties(blob):
    """Read FDT properties for a semantic comparison, including opaque FIT data."""
    header = struct.unpack_from(">10I", blob)
    require(header[0] == 0xD00DFEED and header[1] <= len(blob), "Invalid FDT header")
    pos, strings = header[2], header[3]
    limit = header[2] + header[9]
    stack, result = [], {}
    while pos < limit:
        token = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(b"\0", pos, limit)
            stack.append(blob[pos:end].decode())
            pos = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from(">II", blob, pos)
            pos += 8
            require(pos + length <= limit, "FDT property exceeds structure block")
            start = strings + name_offset
            end = blob.index(b"\0", start, strings + header[8])
            name = blob[start:end].decode()
            key = ("/" + "/".join(stack[1:]), name)
            require(key not in result, "Duplicate FDT property")
            result[key] = blob[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            require(not stack, "Unclosed FDT node")
            return result
        else:
            raise ValueError("Invalid FDT structure token")
    raise ValueError("Missing FDT end token")


def reservations(blob):
    offset = struct.unpack_from(">I", blob, 16)[0]
    start = offset
    while True:
        pair = struct.unpack_from(">QQ", blob, offset)
        offset += 16
        if pair == (0, 0):
            return blob[start:offset]


def set_bytes(path, node, prop, value):
    # The DTB is small enough for the native fdtput argument limit. Never pass
    # the much larger kernel data through command-line arguments.
    run("fdtput", "-t", "bx", str(path), node, prop, *(f"{x:02x}" for x in value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=ROOT / "images/arch-ciri.img")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/ciri-camera-carrier")
    args = parser.parse_args()
    require(args.image.is_file(), "Retained source image missing")
    require(not args.output_dir.exists(), "Output directory already exists; choose a fresh directory")
    start = int(run("cgpt", "show", "-i", "1", "-b", str(args.image)))
    sectors = int(run("cgpt", "show", "-i", "1", "-s", str(args.image)))
    require((start, sectors) == (2048, 65536), "Unexpected source carrier partition layout")
    with args.image.open("rb") as source:
        source.seek(start * 512)
        original = source.read(sectors * 512)
    require(hashlib.sha256(original).hexdigest() == ORIGINAL_SHA256,
            "Source carrier differs from the inspected, retained Ciri image")
    out = args.output_dir
    out.mkdir(mode=0o700)
    old = out / "original-carrier.partition"
    old.write_bytes(original)
    verified_before = run("futility", "vbutil_kernel", "--verify", str(old), "--verbose")
    (out / "original-verify.txt").write_bytes(verified_before)
    body_file = out / "original-vmlinuz"
    run("futility", "vbutil_kernel", "--get-vmlinuz", str(old), "--vmlinuz-out", str(body_file))
    body = body_file.read_bytes()
    require(original[65536:65536 + len(body)] == body, "Unexpected carrier body offset")
    fit_size = struct.unpack_from(">I", body, 4)[0]
    old_fit = properties(body)
    fdt_node = "/images/fdt-1"
    dtb = old_fit[(fdt_node, "data")]
    old_tree = properties(dtb)
    dtb_file = out / "mt8188-geralt-ciri-sku0.dtb"
    dtb_file.write_bytes(dtb)
    expected_tree = dict(old_tree)
    for node in CAMERA_NODES:
        require(old_tree.get((node, "status")) == b"disabled\0", f"Unexpected status at {node}")
        run("fdtput", "-t", "s", str(dtb_file), node, "status", "okay")
        expected_tree[(node, "status")] = b"okay\0"
    new_dtb = dtb_file.read_bytes()
    require(properties(new_dtb) == expected_tree, "DTB changed outside companion status properties")
    require(reservations(new_dtb) == reservations(dtb), "DTB memory reservations changed")
    require(struct.unpack_from(">3I", new_dtb, 20) == struct.unpack_from(">3I", dtb, 20),
            "DTB version or boot CPU changed")
    fit_file = out / "camera-fit.itb"
    fit_file.write_bytes(body[:fit_size])
    set_bytes(fit_file, fdt_node, "data", new_dtb)
    hash_node = fdt_node + "/hash"
    algorithm = old_fit[(hash_node, "algo")].rstrip(b"\0").decode()
    require(algorithm in ("crc32", "sha1", "sha256"), "Unexpected FIT hash algorithm")
    digest = (struct.pack(">I", zlib.crc32(new_dtb)) if algorithm == "crc32"
              else hashlib.new(algorithm, new_dtb).digest())
    set_bytes(fit_file, hash_node, "value", digest)
    patched_fit = fit_file.read_bytes()
    expected_fit = dict(old_fit)
    expected_fit[(fdt_node, "data")] = new_dtb
    expected_fit[(hash_node, "value")] = digest
    require(properties(patched_fit) == expected_fit, "FIT kernel or configuration changed")
    require(len(patched_fit) <= fit_size, "Modified FIT exceeds its original region")
    # Preserve the original body layout, command line, bootloader stub and all
    # data outside the FIT. vbutil_kernel --repack retains the original header.
    unsigned = bytearray(original)
    unsigned[65536:65536 + fit_size] = patched_fit.ljust(fit_size, b"\0")
    modified = out / "modified-carrier.partition"
    modified.write_bytes(unsigned)
    new = out / "camera-carrier.kpart"
    run("futility", "vbutil_kernel", "--repack", str(new),
        "--oldblob", str(modified),
        "--signprivate", "/usr/share/vboot/devkeys/kernel_data_key.vbprivk")
    packed = new.read_bytes()
    require(len(packed) <= len(original), "Repacked carrier exceeds its partition")
    new.write_bytes(packed.ljust(len(original), b"\0"))
    verified_after = run("futility", "vbutil_kernel", "--verify", str(new), "--verbose")
    (out / "camera-verify.txt").write_bytes(verified_after)
    require(verified_after == verified_before, "Carrier header, key, version or command line changed")
    checked_body = out / "camera-vmlinuz"
    run("futility", "vbutil_kernel", "--get-vmlinuz", str(new), "--vmlinuz-out", str(checked_body))
    require(checked_body.read_bytes() == bytes(unsigned[65536:65536 + len(body)]),
            "Repacking changed the verified payload")
    files = (old, new, dtb_file)
    (out / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files))
    (out / "build-info.txt").write_text(
        "source_image=images/arch-ciri.img\nciri_sku=0\n"
        "kernel_recompiled=false\nsubmarine_kernel_changed=false\n"
        "changes=IMGSYS,AIE,and five IMGSYS DMA companion node statuses\n"
        + "".join(f"enabled_node={node}\n" for node in CAMERA_NODES))
    print(f"Verified camera carrier: {new}")
    print("Embedded Submarine kernel, command line, signing key and non-camera DT properties unchanged.")
    print("Nothing written to a physical device.")


if __name__ == "__main__":
    main()
