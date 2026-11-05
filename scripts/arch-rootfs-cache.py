#!/usr/bin/env -S python3 -I
"""Keep a signed Arch Linux ARM rootfs for offline reuse until explicitly refreshed."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


ROOTFS_URL = "https://de3.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz"
SIGNING_KEYS = (
    "02922214DE8981D14DC2ACABBC704E86B823CD25",
    "9D22B7BB678DC056B1F7723CB55C5315DCD9EE1A",
    "69DD6C8FD314223E14362848BF7EEF7A9C6B5765",
    "68B3537F39A313B3E574D06777193F152BDBE6A6",
)
CACHED_FILES = ("ArchLinuxARM-aarch64-latest.tar.gz", "rootfs.sig", "signing-keys.gpg")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Unsafe or missing cache file: {path}")


def valid_signer(status):
    for line in status.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            for fingerprint in (fields[2], fields[-1]):
                if fingerprint in SIGNING_KEYS:
                    return fingerprint
    raise ValueError("Rootfs signature is not from an allowed Arch Linux ARM key")


def verify_signature(snapshot):
    # Use only retained public keys, never the caller's keyring or network retrieval.
    with tempfile.TemporaryDirectory(prefix=".verify.", dir=snapshot.parent) as temporary:
        result = subprocess.run([
            "gpg", "--no-options", "--homedir", temporary, "--batch",
            "--no-auto-key-retrieve", "--no-auto-key-import", "--no-default-keyring",
            "--keyring", str(snapshot / "signing-keys.gpg"), "--status-fd", "1",
            "--verify", str(snapshot / "rootfs.sig"), str(snapshot / CACHED_FILES[0]),
        ], text=True, stdout=subprocess.PIPE, check=True)
    return valid_signer(result.stdout)


def download_signed_rootfs(snapshot):
    for filename, url in (("rootfs.sig", ROOTFS_URL + ".sig"), (CACHED_FILES[0], ROOTFS_URL)):
        subprocess.run([
            "curl", "--fail", "--location", "--show-error", "--retry", "3",
            "--connect-timeout", "30", "--output", str(snapshot / filename), url,
        ], check=True)
    # Official fingerprints: https://archlinuxarm.org/about/package-signing
    with tempfile.TemporaryDirectory(prefix=".keys.", dir=snapshot.parent) as temporary:
        command = ["gpg", "--no-options", "--homedir", temporary, "--batch"]
        subprocess.run(command + ["--keyserver", "hkps://keyserver.ubuntu.com", "--recv-keys", *SIGNING_KEYS], check=True)
        with (snapshot / "signing-keys.gpg").open("wb") as output:
            subprocess.run(command + ["--export", *SIGNING_KEYS], stdout=output, check=True)


def validate_snapshot(snapshot):
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError(f"Unsafe or missing cache snapshot: {snapshot}")
    metadata = snapshot / "provenance.json"
    regular_file(metadata)
    record = json.loads(metadata.read_text())
    if (not isinstance(record, dict) or record.get("source_url") != ROOTFS_URL
            or record.get("signing_key") not in SIGNING_KEYS or not isinstance(record.get("sha256"), dict)):
        raise ValueError(f"Invalid cached rootfs provenance: {metadata}")
    for filename in CACHED_FILES:
        path = snapshot / filename
        regular_file(path)
        expected = record.get("sha256", {}).get(filename, "")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(path) != expected:
            raise ValueError(f"Cached rootfs checksum mismatch: {path}; use --refresh-rootfs to fetch a new snapshot")
    if verify_signature(snapshot) != record["signing_key"]:
        raise ValueError(f"Cached rootfs signing key differs from provenance: {snapshot}")
    return {
        "rootfs": str(snapshot / CACHED_FILES[0]),
        "sha256": record["sha256"][CACHED_FILES[0]],
        "provenance": str(metadata),
    }


def ensure_rootfs(cache, refresh=False):
    cache = Path(cache).resolve()
    if any(cache == Path(base) or Path(base) in cache.parents for base in ("/dev", "/proc", "/sys")):
        raise ValueError("Rootfs cache must not be a device or system path")
    if "\n" in str(cache) or "\r" in str(cache):
        raise ValueError("Rootfs cache path must not contain newlines")
    cache.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(cache / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = cache / "current.json"
        if (current.exists() or current.is_symlink()) and not refresh:
            regular_file(current)
            pointer = json.loads(current.read_text())
            name = pointer.get("snapshot", "") if isinstance(pointer, dict) else ""
            if not isinstance(name, str) or not re.fullmatch(r"rootfs-[0-9a-f]{32}", name):
                raise ValueError(f"Invalid cache snapshot selector: {current}")
            result = validate_snapshot(cache / name)
            print(f"Reusing verified Arch rootfs: {result['rootfs']}", file=sys.stderr)
            return result

        print("Downloading and retaining a signed Arch Linux ARM rootfs...", file=sys.stderr)
        staging = Path(tempfile.mkdtemp(prefix=".download.", dir=cache))
        selector = None
        try:
            download_signed_rootfs(staging)
            for filename in CACHED_FILES:
                regular_file(staging / filename)
            signer = verify_signature(staging)
            record = {
                "source_url": ROOTFS_URL,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "signing_key": signer,
                "sha256": {name: sha256(staging / name) for name in CACHED_FILES},
            }
            (staging / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
            name = "rootfs-" + uuid.uuid4().hex
            snapshot = cache / name
            staging.rename(snapshot)
            # Publish the pointer last. Failed refreshes leave the previous cache usable.
            descriptor, filename = tempfile.mkstemp(prefix=".current.", dir=cache)
            selector = Path(filename)
            with os.fdopen(descriptor, "w") as output:
                json.dump({"snapshot": name}, output)
                output.write("\n")
            if current.exists() or current.is_symlink():
                regular_file(current)
            selector.replace(current)
            return {
                "rootfs": str(snapshot / CACHED_FILES[0]),
                "sha256": record["sha256"][CACHED_FILES[0]],
                "provenance": str(snapshot / "provenance.json"),
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if selector is not None and selector.exists():
                selector.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--refresh", action="store_true", help="Download a new snapshot, preserving previous snapshots")
    args = parser.parse_args()
    try:
        result = ensure_rootfs(args.cache_dir, args.refresh)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result))


if __name__ == "__main__":
    main()
