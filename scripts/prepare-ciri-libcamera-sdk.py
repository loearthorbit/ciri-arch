#!/usr/bin/env -S python3 -I
"""Prepare the isolated libcamera build SDK; offline unless --download is given."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import urllib.parse
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config/ciri-libcamera-build-inputs.json"
CAMERA_SDK = ROOT / "build/camera/sdk/compiler"
PACKAGES = {"libevent-dev", "libevent-2.1-7", "libevent-core-2.1-7",
            "libevent-pthreads-2.1-7"}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXTRACTED = 128 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(path, expected):
    require(path.is_file() and not path.is_symlink(), f"Missing regular input: {path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == expected, f"SHA256 mismatch: {path}; refusing to replace it")


def https_url(url, hosts):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in hosts
            and parsed.port in (None, 443) and not parsed.username
            and not parsed.password, f"Unapproved download URL: {url}")
    return url


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        https_url(newurl, {urllib.parse.urlsplit(request.full_url).hostname})
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, destination, hosts, limit=MAX_ARCHIVE):
    """Only called after explicit opt-in, into a newly created empty directory."""
    https_url(url, hosts)
    print(f"Download: {url}")
    opener = urllib.request.build_opener(HTTPSRedirects())
    with opener.open(url, timeout=60) as response, destination.open("xb") as output:
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            require(total <= limit, f"Download exceeds size limit: {url}")
            output.write(block)


def member_path(name):
    require("\\" not in name and "\0" not in name, f"Unsafe archive name: {name!r}")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts,
            f"Archive path escapes extraction root: {name!r}")
    return path


def safe_link(path, target):
    require(target and "\\" not in target and "\0" not in target,
            f"Unsafe symlink target: {target!r}")
    link = PurePosixPath(target)
    # Reject parent traversal even when it is lexically inside the root: a
    # chain such as a -> b/.., b -> dir/.. can otherwise escape after resolution.
    # The approved Debian inputs use simple relative SONAME/manpage links.
    require(not link.is_absolute() and ".." not in link.parts,
            f"Absolute or parent-traversing archive symlink at {path}: {target!r}")


def deb_payload(archive):
    """Read only the Debian ar data member, never control/maintainer scripts."""
    require(archive.stat().st_size <= MAX_ARCHIVE, f"Archive too large: {archive}")
    payload = None
    version = None
    with archive.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", f"Not a Debian ar archive: {archive}")
        while header := stream.read(60):
            require(len(header) == 60 and header[58:] == b"`\n", "Invalid ar header")
            name = header[:16].decode("ascii").strip().removesuffix("/")
            size = int(header[48:58].decode("ascii").strip())
            require(0 <= size <= MAX_ARCHIVE, "Invalid ar member size")
            data = stream.read(size)
            require(len(data) == size, "Truncated ar member")
            if size % 2:
                require(len(stream.read(1)) == 1, "Truncated ar padding")
            if name == "debian-binary":
                require(version is None, "Duplicate Debian version member")
                version = data
            elif name in ("data.tar", "data.tar.gz", "data.tar.xz", "data.tar.bz2"):
                require(payload is None, "Multiple Debian data archives")
                payload = data
            else:
                require(name.startswith("control.tar"), f"Unsupported ar member: {name}")
    require(version == b"2.0\n" and payload is not None, "Unsupported Debian archive layout")
    return payload


def extract_deb(archive, destination):
    """Extract data into a new root without ever following an archive link."""
    destination.mkdir(mode=0o700)  # Must not already exist, even if empty.
    with tarfile.open(fileobj=io.BytesIO(deb_payload(archive)), mode="r:*") as data:
        entries = []
        seen = set()
        total = 0
        for entry in data:
            require(len(entries) < 20000, "Too many archive members")
            path = member_path(entry.name)
            if not path.parts:
                require(entry.isdir(), "Non-directory archive root")
                continue
            require(path not in seen, f"Duplicate archive member: {entry.name}")
            seen.add(path)
            require(entry.isdir() or entry.isreg() or entry.issym(),
                    f"Unsupported archive type (hardlinks/devices rejected): {entry.name}")
            require(entry.size >= 0, "Invalid archive member size")
            total += entry.size
            require(total <= MAX_EXTRACTED, "Extracted archive exceeds size limit")
            if entry.issym():
                safe_link(path, entry.linkname)
            entries.append((entry, path))
        # Materialize links LAST. Even a safe-looking chain cannot redirect a
        # subsequent file write; links can never be ancestors of other members.
        links = {path for entry, path in entries if entry.issym()}
        for _, path in entries:
            require(not any(parent in links for parent in path.parents),
                    f"Archive member has a symlink ancestor: {path}")
        for entry, path in entries:
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.isdir():
                target.mkdir(exist_ok=True)
            elif entry.isreg():
                with data.extractfile(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                # Strip setuid/setgid/sticky and writable-by-others permissions.
                target.chmod(0o755 if entry.mode & 0o111 else 0o644)
        for entry, path in entries:
            if entry.issym():
                destination.joinpath(*path.parts).symlink_to(entry.linkname)


def check_wheel(path, version):
    """Accept only PLY module/data and metadata, not scripts or startup hooks."""
    with zipfile.ZipFile(path) as wheel:
        seen = set()
        total = 0
        for entry in wheel.infolist():
            name = member_path(entry.filename)
            require(name.parts and name.parts[0] in ("ply", f"ply-{version}.dist-info"),
                    f"Unexpected PLY wheel member: {entry.filename}")
            require(name not in seen, f"Duplicate wheel member: {entry.filename}")
            seen.add(name)
            mode = entry.external_attr >> 16
            require(not stat.S_ISLNK(mode) and not entry.filename.endswith(".pth"),
                    f"Unsafe wheel member: {entry.filename}")
            total += entry.file_size
            require(total <= MAX_EXTRACTED and len(seen) <= 20000, "Wheel exceeds size limit")


def prepare_package(root, cache, package, version, base_url, allow_download):
    name = package["name"]
    directory = root / name
    archive = directory / "package.deb"
    require(not directory.is_symlink(), f"Refusing package directory symlink: {directory}")
    if archive.exists() or archive.is_symlink():
        verify(archive, package["sha256"])
        if (directory / "sysroot").exists():
            require((directory / "sysroot").is_dir()
                    and not (directory / "sysroot").is_symlink(), "Unsafe cached sysroot")
            print(f"Reuse verified archive and retained sysroot: {name}")
            return
    elif directory.exists():
        require(directory.is_dir() and not any(directory.iterdir()),
                f"Incomplete nonempty package directory: {directory}")
    with tempfile.TemporaryDirectory(prefix=f".prepare-{name}.", dir=root) as work:
        staging = Path(work)
        staged_archive = staging / "package.deb"
        cached = cache / name / "package.deb"
        if archive.is_file():
            shutil.copyfile(archive, staged_archive)
        elif cached.exists() or cached.is_symlink():
            verify(cached, package["sha256"])
            shutil.copyfile(cached, staged_archive)
        else:
            require(allow_download, f"Offline input missing: {cached}; supply it or opt into --download")
            filename = f"{name}_{version}_arm64.deb"
            download(base_url.rstrip("/") + "/" + filename, staged_archive, {"deb.debian.org"})
        verify(staged_archive, package["sha256"])
        extract_deb(staged_archive, staging / "sysroot")
        if directory.exists():
            require(not (directory / "sysroot").is_symlink(), "Refusing dangling sysroot symlink")
            if not archive.exists():
                staged_archive.rename(archive)
            (staging / "sysroot").rename(directory / "sysroot")
        else:
            staging.rename(directory)
        print(f"Prepared verified ARM64 data files: {name}")


def prepare_ply(root, cache, ply, allow_download):
    archive = root / ply["filename"]
    if not archive.exists() and not archive.is_symlink():
        with tempfile.TemporaryDirectory(prefix=".prepare-ply.", dir=root) as work:
            staging = Path(work)
            staged = staging / ply["filename"]
            cached = cache / ply["filename"]
            if cached.exists() or cached.is_symlink():
                verify(cached, ply["sha256"])
                shutil.copyfile(cached, staged)
            else:
                require(allow_download, f"Offline input missing: {cached}; supply it or opt into --download")
                # Metadata is not executable and never supplies the trusted hash.
                metadata = staging / "metadata.json"
                download(ply["metadata_url"], metadata, {"pypi.org"}, 2 * 1024 * 1024)
                records = json.loads(metadata.read_text())["urls"]
                matches = [item for item in records if item["filename"] == ply["filename"]
                           and item["digests"]["sha256"] == ply["sha256"]
                           and item["packagetype"] == "bdist_wheel"]
                require(len(matches) == 1, "PyPI metadata lacks the exact pinned PLY wheel")
                # Give the wheel its OWN new empty download directory as well.
                wheel_dir = staging / "wheel"
                wheel_dir.mkdir(mode=0o700)
                staged = wheel_dir / ply["filename"]
                download(matches[0]["url"], staged, {"files.pythonhosted.org"})
            verify(staged, ply["sha256"])
            check_wheel(staged, ply["version"])
            staged.rename(archive)
    verify(archive, ply["sha256"])
    check_wheel(archive, ply["version"])
    return archive


def generator_inputs(sdk):
    # This retained SDK has a venv, NOT a wheel cache at its top level.
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = sdk / "header-generator-venv/lib" / version / "site-packages"
    require(site.is_dir(), f"Retained generator cache missing for {version}: {site}; "
            "use the original host Python ABI (retained cache: CPython 3.14 x86_64)")
    names = ["jinja2", "yaml", "_yaml", "markupsafe"]
    require(all((site / name).is_dir() for name in names), "Incomplete retained generator modules")
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    for module, extension in (("yaml", "_yaml"), ("markupsafe", "_speedups")):
        require(suffix and (site / module / (extension + suffix)).is_file(),
                f"Retained {module} native extension does not match this host Python ABI")
    for pattern in ("jinja2-*.dist-info", "pyyaml-*.dist-info", "markupsafe-*.dist-info"):
        matches = list(site.glob(pattern))
        require(len(matches) == 1, f"Ambiguous/missing generator metadata: {site / pattern}")
        names.append(matches[0].name)
    return site, names


def prepare_venv(root, old_site, names, wheel, recreate):
    target = root / "venv"
    require(not target.is_symlink(), f"Refusing venv symlink: {target}")
    if target.exists():
        require((target / "pyvenv.cfg").is_file(), f"Not a dedicated venv: {target}")
        if recreate:
            shutil.rmtree(target)
    if not target.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    python = target / "bin/python"
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=ROOT, text=True).strip())
    require(site.is_relative_to(target) and site.is_dir(), "Unexpected venv site-packages path")
    require(site.parent.name == old_site.parent.name, "Existing venv Python ABI differs; use --recreate-venv")
    # Bootstrap pip and wheel installation run trusted Python/pip, not vendor
    # code or a source-package installer. Disable indexes, deps and bytecode.
    subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "install",
                    "--disable-pip-version-check", "--no-index", "--no-deps",
                    "--only-binary=:all:", "--no-compile", "--force-reinstall", str(wheel)],
                   cwd=ROOT, check=True)
    for name in names:
        destination = site / name
        require(not destination.is_symlink(), f"Refusing module symlink: {destination}")
        if destination.exists():
            require(destination.is_dir(), f"Unexpected generator cache file: {destination}")
            shutil.rmtree(destination)
        shutil.copytree(old_site / name, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Dedicated venv ready: {target} (PLY + retained Jinja2/PyYAML/MarkupSafe)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        "Invoke with python3 -I. No downloads without --download; no builds or device installation.")
    parser.add_argument("--sdk-dir", type=Path, help="output SDK (LIBCAMERA_STACK_SDK or manifest default)")
    parser.add_argument("--camera-sdk", type=Path,
                        default=Path(os.environ.get("CAMERA_SDK", str(CAMERA_SDK))),
                        help="original SDK containing header-generator-venv (CAMERA_SDK)")
    parser.add_argument("--cache-dir", type=Path, help="retained archive cache (default: manifest SDK directory)")
    parser.add_argument("--download", action="store_true", help="explicitly allow HTTPS downloads of missing pinned inputs")
    parser.add_argument("--recreate-venv", action="store_true", help="replace only this SDK's dedicated venv; keep archives/sysroots")
    args = parser.parse_args()
    require(sys.flags.isolated, "Run this preparation script with python3 -I")
    inputs = json.loads(MANIFEST.read_text())
    require(len(inputs["libevent_packages"]) == 4
            and {item["name"] for item in inputs["libevent_packages"]} == PACKAGES,
            "Manifest must contain exactly the four approved libevent inputs")
    require(inputs["ply"]["version"] == "3.11"
            and inputs["ply"]["filename"] == "ply-3.11-py2.py3-none-any.whl", "Unapproved PLY input")
    for item in [*inputs["libevent_packages"], inputs["ply"]]:
        digest = item["sha256"]
        require(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "Invalid pinned SHA256")
    require("/" not in inputs["libevent_version"] and "\\" not in inputs["libevent_version"], "Unsafe package version")
    retained = ROOT / "build" / inputs["sdk_directory"]
    root = (args.sdk_dir or Path(os.environ.get("LIBCAMERA_STACK_SDK", str(retained)))).resolve()
    cache = (args.cache_dir or retained).resolve()
    sdk = args.camera_sdk.resolve()
    require(not root.is_relative_to(sdk) and not sdk.is_relative_to(root),
            "Dedicated SDK must not overlap the original camera SDK")
    old_site, names = generator_inputs(sdk)
    print("Mode: explicit downloads permitted" if args.download else "Mode: OFFLINE (no network)")
    root.mkdir(parents=True, exist_ok=True)
    for package in inputs["libevent_packages"]:
        prepare_package(root, cache, package, inputs["libevent_version"],
                        inputs["libevent_url_base"], args.download)
    wheel = prepare_ply(root, cache, inputs["ply"], args.download)
    prepare_venv(root, old_site, names, wheel, args.recreate_venv)
    print(f"Prepared SDK: {root}\nNothing built, installed on a device, or executed from vendor code.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile,
            subprocess.CalledProcessError) as error:
        print(f"SDK preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
