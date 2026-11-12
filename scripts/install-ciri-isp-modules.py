#!/usr/bin/python3
"""Operator-run restoration of two pinned ISP modules, never a kernel install."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

RELEASE = '7.3.0-rc4-ciri'
ARCHIVE_SHA = 'a1153a67f0de9930efcfa7a6aceb9a90b7bfebc39223c5be31205b8d27075bc4'
MODULES = {
    'mtk_imgsys.ko': '9dd9f75918d2e332b275c08b444b79b5b05679fdbbe8ee707f162c273eb98052',
    'mtk-aie.ko': 'f3a401993c2ce15b5d8c06e21594cbf7e004612a8defe4d2d27bc47eab02a17f',
}


def checked(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('Symlink refused: ' + str(component))
    return path


def digest(path):
    with path.open('rb') as data:
        return hashlib.file_digest(data, 'sha256').hexdigest()


def validate_kit(source):
    source = checked(source)
    names = set(MODULES) | {'module-build-info.txt', 'install-modules.py'}
    if {p.name for p in source.iterdir()} != names | {'SHA256SUMS'}:
        raise ValueError('Unexpected ISP module-kit inventory')
    for path in source.iterdir():
        if checked(path).is_symlink() or not path.is_file():
            raise ValueError('Kit requires regular nonsymlinked files')
    rows = {}
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] not in names or match[2] in rows:
            raise ValueError('Malformed/duplicate module-kit checksum')
        rows[match[2]] = match[1]
    if set(rows) != names:
        raise ValueError('Incomplete module-kit manifest')
    for name, sha in rows.items():
        if digest(source / name) != sha:
            raise ValueError('Kit checksum mismatch: ' + name)
    info = set((source / 'module-build-info.txt').read_text().splitlines())
    required = {'kernel-release=' + RELEASE, 'architecture=AArch64', 'module-only=true',
                'source-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993',
                'source-archive-sha256=' + ARCHIVE_SHA}
    if not required.issubset(info):
        raise ValueError('Unexpected ISP module provenance')
    for name, sha in MODULES.items():
        if rows[name] != sha:
            raise ValueError('Not the pinned retained module: ' + name)
        with (source / name).open('rb') as data:
            header = data.read(20)
        if (header[:6] != b'\x7fELF\x02\x01' or len(header) != 20 or
                int.from_bytes(header[16:18], 'little') != 1 or
                int.from_bytes(header[18:20], 'little') != 183):
            raise ValueError('Expected an ELF64 ARM64 relocatable module')
    return source


def modinfo(field, path):
    return subprocess.run(['modinfo', '-F', field, str(path)], check=True,
                          capture_output=True, text=True).stdout.strip()


def install(source, root=Path('/')):
    source = validate_kit(source)
    root = checked(root)
    module_root = checked(root / 'usr/lib/modules' / RELEASE)
    if not module_root.is_dir():
        raise ValueError('Matching kernel module directory missing; no files changed')
    target_dir = checked(module_root / 'updates/ciri-camera')
    if root == Path('/'):
        for component in (target_dir, *target_dir.parents):
            if component.exists():
                info = component.stat()
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError('Unsafe module installation directory: ' + str(component))
    targets = {}
    # Validate both binaries, existing destinations and all dependencies before writes.
    for name, sha in MODULES.items():
        binary = source / name
        if not modinfo('vermagic', binary).startswith(RELEASE + ' '):
            raise ValueError('Module vermagic mismatch: ' + name)
        module_name = name.removesuffix('.ko').replace('-', '_')
        if modinfo('name', binary).replace('-', '_') != module_name:
            raise ValueError('Module name mismatch: ' + name)
        target = checked(target_dir / name)
        for spelling in {name, name.replace('-', '_')}:
            for candidate in module_root.rglob(spelling + '*'):
                if candidate != target:
                    raise ValueError('Another module candidate exists; inspect instead of shadowing: ' + str(candidate))
        if target.exists() and (not target.is_file() or digest(target) != sha):
            raise ValueError('Existing module differs; refusing overwrite: ' + str(target))
        for dependency in filter(None, modinfo('depends', binary).split(',')):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', dependency):
                raise ValueError('Invalid dependency name')
            if (root / 'sys/module' / dependency.replace('-', '_')).is_dir():
                continue  # Already loaded/built in; no load or unload is performed.
            result = subprocess.run(['modinfo', '-k', RELEASE, '-F', 'filename', dependency],
                                    capture_output=True, text=True)
            if result.returncode or not result.stdout.strip():
                raise ValueError('Missing kernel dependency: ' + dependency + '; no files changed')
        targets[name] = target
    staged = []
    try:
        subprocess.run(['sync', '-f', str(module_root)], check=True)
        target_dir.parent.mkdir(mode=0o755, exist_ok=True)
        target_dir.mkdir(mode=0o755, exist_ok=True)
        checked(target_dir)
        replacements = []
        for name, target in targets.items():
            if target.exists():
                continue  # Identical already-installed files: repair depmod only.
            fd, temp = tempfile.mkstemp(prefix='.ciri-isp-', dir=target_dir)
            staged.append(Path(temp))
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), 0o644)
                output.write((source / name).read_bytes())
                output.flush()
                os.fsync(output.fileno())
            if digest(Path(temp)) != MODULES[name]:
                raise ValueError('Staged module hash mismatch')
            replacements.append((Path(temp), target, MODULES[name]))
        for temp, target, sha in replacements:
            checked(target)
            try:
                os.link(temp, target)  # Atomic publication, never overwrite.
            except FileExistsError:
                if not target.is_file() or digest(target) != sha:
                    raise ValueError('Module appeared during staging; refusing overwrite')
        subprocess.run(['depmod', '-a', RELEASE], check=True)
        for name, target in targets.items():
            if digest(target) != MODULES[name]:
                raise ValueError('Final module hash mismatch')
        subprocess.run(['sync', '-f', str(module_root)], check=True)
    finally:
        for temp in staged:
            if temp.exists():
                temp.unlink()  # Only this invocation's temporary files.
    print('Restored mtk_imgsys and mtk-aie modules; depmod completed.')
    print('No kernel/DTB/initramfs/firmware/audio/loopback replaced; no modules loaded, no backups created or deleted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.machine() != 'aarch64' or platform.release() != RELEASE:
        parser.error('Run with sudo on Ciri booted into ' + RELEASE)
    install(args.kit)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit('ISP module restoration failed; inspect/repair before loading: ' + str(error))
