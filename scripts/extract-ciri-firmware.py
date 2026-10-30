#!/usr/bin/env -S python3 -I
"""Extract Ciri firmware into a new, self-contained output directory.

Run with python3 -I and supply a separately reviewed ciri-firmware-update.
This wrapper executes that trusted script; it is not an OS-level sandbox.
Requires unzip and debugfs on PATH, several GiB of free disk space, and no root
privileges. A supplied --zip is copied locally and is NOT index-verified.

The upstream updater header declares SPDX-License-Identifier: MIT. That license
does not establish redistribution rights for the extracted firmware blobs.
"""

import sys

# Do not let a recovery archive, the working directory, or the updater's directory
# supply Python modules. Check before importing any non-builtin modules.
if not sys.flags.isolated:
    raise SystemExit("Run this CLI with python3 -I (or its isolated-mode shebang).")
sys.dont_write_bytecode = True

import argparse
import datetime
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil


FIRMWARE = {
    "/lib/firmware/himax_i2chid_1002.bin": "himax_i2chid_1002.bin",
    "/lib/firmware/himax_i2chid_1003.bin": "himax_i2chid_1003.bin",
    "/lib/firmware/himax_i2chid_1004.bin": "himax_i2chid_1004.bin",
    "/lib/firmware/mediatek/mt8188/scp-dual.img": "mediatek/mt8188/scp-dual.img",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--updater", required=True, type=Path,
                        help="path to a separately reviewed upstream updater (trusted code)")
    parser.add_argument("--output", required=True, type=Path,
                        help="new directory; its parent must exist (existing paths are refused)")
    parser.add_argument("--zip", type=Path,
                        help="already downloaded recovery zip; copied, not index-verified")
    args = parser.parse_args()
    try:
        args.updater = args.updater.expanduser().resolve(strict=True)
        if not args.updater.is_file():
            raise ValueError("--updater must name a regular file")
        if args.zip is not None:
            args.zip = args.zip.expanduser().resolve(strict=True)
            if not args.zip.is_file():
                raise ValueError("--zip must name a regular file")
        requested = args.output.expanduser().absolute()
        if requested.exists() or requested.is_symlink():
            raise ValueError(f"--output already exists: {requested}")
        args.output = requested.resolve()
        if not args.output.parent.is_dir():
            raise ValueError("--output parent must be an existing directory")
        for system_firmware in (Path("/lib/firmware"), Path("/usr/lib/firmware")):
            if args.output.is_relative_to(system_firmware.resolve()):
                raise ValueError("--output must not be inside the system firmware directory")
        # Upstream interpolates its temporary output path into a debugfs request
        # without quoting. Refuse paths its parser cannot safely handle.
        if any(c.isspace() or c in "\"'\\" for c in str(args.output)):
            raise ValueError("--output cannot contain whitespace, quotes, or backslashes "
                             "because of upstream debugfs path handling")
        missing = [name for name in ("unzip", "debugfs") if shutil.which(name) is None]
        if missing:
            raise ValueError("missing required tools on PATH: " + ", ".join(missing))
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return args


def load_updater(path, source):
    loader = importlib.machinery.SourceFileLoader("ciri_updater", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    # Execute precisely the source bytes recorded in provenance, never a cached
    # .pyc beside the separately reviewed upstream script.
    exec(compile(source, str(path), "exec"), module.__dict__)
    if module.FIRMWARE != FIRMWARE:
        raise ValueError("updater's firmware mapping differs from the reviewed Ciri mapping")
    for name in ("select_image", "download", "main"):
        if not callable(getattr(module, name, None)):
            raise ValueError(f"updater is missing the expected {name} API")
    return module


def extract(args):
    updater_source = args.updater.read_bytes()
    # mkdir is the final, atomic existing-path check. Do not create any parents,
    # delete failed runs, or merge with a previous output directory.
    args.output.mkdir(mode=0o700)
    metadata_path = args.output / "provenance.json"
    metadata = {
        "schema_version": 1,
        "status": "in-progress",
        "started_at": timestamp(),
        "updater": {
            "path": str(args.updater),
            "sha256": hashlib.sha256(updater_source).hexdigest(),
            "license_header": next((line.strip() for line in
                                    updater_source.decode("utf-8").splitlines()[:20]
                                    if "SPDX-License-Identifier:" in line), None),
        },
        "extractor": {"path": str(Path(__file__).resolve()),
                      "sha256": sha256(Path(__file__).resolve())},
        "firmware_license": "Not established by the updater's source license",
        "hardware_validation": "Not performed",
    }
    try:
        artifacts = args.output / "artifacts"
        recovery = args.output / "recovery"
        work = args.output / "work"
        for directory in (artifacts, recovery, work):
            directory.mkdir(mode=0o700)
        # Even incidental Python/tool caches and temporary files belong to this
        # run. Keep recovered data in recovery/work, away from the script/CWD.
        local_env = {
            "HOME": "state/home", "XDG_CACHE_HOME": "state/cache",
            "XDG_CONFIG_HOME": "state/config", "XDG_DATA_HOME": "state/data",
            "XDG_STATE_HOME": "state/state", "XDG_RUNTIME_DIR": "state/runtime",
            "TMPDIR": "work/tmp", "TMP": "work/tmp", "TEMP": "work/tmp",
        }
        for variable, relative in local_env.items():
            directory = args.output / relative
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.environ[variable] = str(directory)
        os.chdir(args.output)
        write_json(metadata_path, metadata)
        updater = load_updater(args.updater, updater_source)
        archive = recovery / "recovery.zip"
        if args.zip is None:
            image = updater.select_image("stable")
            write_json(recovery / "recovery-index-entry.json", image)
            updater.log(f"using ChromeOS {image['version']} ({image['channel']})")
            updater.download(image, str(archive))
            metadata["recovery"] = {
                "source": "stable-recovery-index",
                "index_url": updater.RECOVERY_INDEX,
                "index_entry": "recovery/recovery-index-entry.json",
                "verification": "Size and SHA-1 checked by updater.download against index",
                "expected_size": int(image["zipfilesize"]),
                "expected_sha1": image["sha1"],
            }
        else:
            shutil.copyfile(args.zip, archive)
            metadata["recovery"] = {
                "source": "provided-zip", "original_path": str(args.zip),
                "verification": "Not checked against Google's recovery index; local copy only",
            }
        metadata["recovery"].update({"archive": "recovery/recovery.zip",
                                     "size": archive.stat().st_size,
                                     "sha256": sha256(archive)})
        updater_argv = [str(args.updater), "--zip", str(archive),
                        "--dest", str(artifacts), "--workdir", str(work), "--no-state"]
        metadata["updater"]["argv"] = updater_argv
        write_json(metadata_path, metadata)
        previous_argv = sys.argv
        try:
            sys.argv = updater_argv
            updater.main()
        finally:
            sys.argv = previous_argv
        files = {}
        for path in artifacts.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"unexpected symlink in artifacts: {path}")
            if path.is_file():
                files[path.relative_to(artifacts).as_posix()] = path
            elif not path.is_dir():
                raise ValueError(f"unexpected non-regular artifact: {path}")
        if set(files) != set(FIRMWARE.values()):
            raise ValueError("extraction did not produce exactly the four expected firmware blobs")
        firmware = []
        for source, relative in sorted(FIRMWARE.items(), key=lambda item: item[1]):
            path = files[relative]
            size = path.stat().st_size
            if size == 0:
                raise ValueError(f"extracted firmware is empty: {relative}")
            firmware.append({"path": relative, "source_path": source,
                             "size": size, "sha256": sha256(path)})
        metadata.update({"status": "complete", "completed_at": timestamp(),
                         "artifacts_directory": "artifacts", "firmware": firmware})
        write_json(metadata_path, metadata)
        # Publish the checksum manifest only after all four nonempty blobs and
        # provenance are complete. Relative names are suitable for sha256sum -c.
        (artifacts / "SHA256SUMS").write_text(
            "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in firmware),
            encoding="utf-8")
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        metadata.update({"status": "failed", "failed_at": timestamp(), "error": str(error)})
        write_json(metadata_path, metadata)
        print(f"Extraction failed; partial output retained at {args.output}", file=sys.stderr)
        raise
    print(f"Firmware artifacts: {artifacts}")
    print(f"Recovery archive retained: {archive}")
    print(f"Provenance: {metadata_path}")


def main():
    args = parse_args()
    try:
        extract(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"extract-ciri-firmware: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
