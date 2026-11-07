#!/usr/bin/env -S python3 -I
"""Prepare a fresh isolated runtime from built libcamera and retained recovery inputs."""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ciri_elf', REPO / 'scripts/verify-ciri-libcamera-elf.py')
elf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elf)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_manifest(directory, manifest):
    verified, names = set(), set()
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError(f'Malformed input checksum row in {manifest}')
        expected, name = match.groups()
        normalized = str(Path(name))
        if normalized in names:
            raise ValueError(f'Duplicate input checksum path: {name}')
        names.add(normalized)
        file = elf.safe_file(directory, name)
        if digest(file) != expected:
            raise ValueError(f'Input checksum mismatch: {name}')
        verified.add(file)
    if not verified:
        raise ValueError(f'Empty input checksum manifest: {manifest}')
    return verified


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or digest(source) != digest(destination):
            raise ValueError(f'Conflicting package file: {destination}')
    else:
        shutil.copy2(source, destination)


def package(built, recovery, sdk, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Package directory already exists; choose a fresh --output, nothing overwritten.')
    built, recovery, sdk = built.resolve(), recovery.resolve(), sdk.resolve()
    output = output.resolve()
    if any(output.is_relative_to(path) for path in (built, recovery, sdk)):
        raise ValueError('Package output must be separate from all retained input directories.')
    # Validate successful-build outputs before creating even a partial package.
    verified_build = check_manifest(built, built / 'SHA256SUMS')
    verified_recovery = check_manifest(recovery / 'files', recovery / 'SHA256SUMS')
    files = recovery / 'files'
    inputs = json.loads((REPO / 'config/ciri-libcamera-build-inputs.json').read_text())
    for item in inputs['libevent_packages']:
        if digest(sdk / item['name'] / 'package.deb') != item['sha256']:
            raise ValueError('libevent package checksum mismatch: ' + item['name'])
    for required in ('usr/bin/cam', 'usr/bin/ciri-libcamera-host',
                     'usr/lib/ciri-libcamera/libcamera.so.0.1.0',
                     'usr/lib/ciri-libcamera/libcamera-base.so.0.1.0',
                     'usr/lib/ciri-libcamera/libcamera-mtkisp7-platform.so'):
        elf.safe_file(built / 'rootfs', required)
    output.mkdir(parents=True)
    root = output / 'rootfs'
    root.mkdir()
    # Copy installed Meson outputs with checked, root-contained symlinks flattened.
    for path in sorted((built / 'rootfs').rglob('*')):
        if path.is_dir():
            if path.is_symlink():
                raise ValueError('Unexpected directory symlink in installed build: ' + str(path))
            continue
        source = elf.safe_file(built / 'rootfs', str(path.relative_to(built / 'rootfs')))
        if source not in verified_build:
            raise ValueError(f'Built file not covered by checksums: {source}')
        copy_file(source, root / path.relative_to(built / 'rootfs'))
    # Prefer new core and Debian's thread-enabled event libraries for dependencies.
    search = [(root, name) for name in elf.SEARCH]
    search += [(files, name) for name in ('usr/lib64', 'lib64', 'usr/share/cros-camera/libfs')]
    event_files = []
    for name in ('libevent-2.1-7', 'libevent-core-2.1-7', 'libevent-pthreads-2.1-7'):
        directory = sdk / name / 'sysroot/usr/lib/aarch64-linux-gnu'
        search.insert(0, (sdk / name / 'sysroot', 'usr/lib/aarch64-linux-gnu'))
        event_files.extend(path for path in directory.glob('*.so*') if path.is_file())
    seen = set()

    def install_elf(source, relative):
        source = source.resolve()
        # Inventory/DT_NEEDED strings are untrusted paths, never output paths.
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError(f'Unsafe package relative path: {relative}')
        if source.is_relative_to(files) and source not in verified_recovery:
            raise ValueError(f'Recovered ELF not covered by checksums: {source}')
        destination = root / relative
        copy_file(source, destination)
        item = elf.inspect(source)
        if item['soname']:
            soname = item['soname']
            if '/' in soname or soname in ('.', '..'):
                raise ValueError('Unsafe SONAME: ' + soname)
            alias = destination.parent / soname
            if alias != destination:
                # Actual bytes, not build-only absolute aliases. Small duplication
                # avoids relying on an installer's preservation of linker symlinks.
                copy_file(source, alias)
        if source in seen:
            return
        seen.add(source)
        for needed in item['needed']:
            if '/' in needed or needed in ('.', '..'):
                raise ValueError('Unsafe DT_NEEDED: ' + needed)
            for base, directory in search:
                candidate = base / directory / needed
                if not candidate.exists():
                    continue
                actual = elf.safe_file(base, str(candidate.relative_to(base)))
                if base == root:
                    install_elf(actual, str(actual.relative_to(root)))
                elif base == files:
                    install_elf(actual, str(actual.relative_to(files)))
                else:
                    install_elf(actual, 'usr/lib/ciri-libcamera/' + actual.name)
                break
            else:
                raise ValueError(f'Missing dependency {needed} required by {source}')

    # Give the built library aliases priority before inspecting recovered imports.
    for path in list((root / 'usr/lib/ciri-libcamera').glob('*.so*')):
        install_elf(path, str(path.relative_to(root)))
    for path in event_files:
        install_elf(path, 'usr/lib/ciri-libcamera/' + path.resolve().name)
    inventory = json.loads((recovery / 'camera-dependencies.json').read_text())
    old_core = {'libcamera.so.0.1.0', 'libcamera-base.so.0.1.0', 'libcamera-mtkisp7-platform.so'}
    seeds = [name for name in inventory['dependency_files'] if Path(name).name not in old_core]
    seeds += ['usr/lib64/mtkisp7_ipa_proxy.so']
    for name in seeds:
        install_elf(elf.safe_file(files, name), name)
    for name in ('usr/bin/cam', 'usr/bin/ciri-libcamera-host'):
        install_elf(root / name, name)
    # Camera calibration/configuration/models stay at their vendor paths. Read
    # data only, never run recovered service programs or package scripts.
    for directory in ('etc/camera', 'usr/share/cros-camera', 'usr/share/libcamera', 'usr/lib64/libcamera'):
        source = files / directory
        if not source.exists():
            continue
        for path in sorted(source.rglob('*')):
            if path.is_symlink() and path.is_dir():
                raise ValueError('Unexpected directory symlink in camera data: ' + str(path))
            if path.is_dir():
                continue
            actual = elf.safe_file(files, str(path.relative_to(files)))
            if actual not in verified_recovery:
                raise ValueError(f'Recovered camera data not covered by checksums: {actual}')
            copy_file(actual, root / path.relative_to(files))
    for name in ('dev', 'proc', 'sys', 'run', 'tmp', 'captures'):
        (root / name).mkdir(exist_ok=True)
    (root / 'tmp').chmod(0o1777)
    report = elf.verify(root)
    (output / 'elf-report.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['missing_strong_imports']:
        raise ValueError('Strong imports lack providers; inspect elf-report.json. Package is NOT ready.')
    copy_file(REPO / 'scripts/run-ciri-libcamera.sh', output / 'run-libcamera')
    (output / 'run-libcamera').chmod(0o755)
    for name in ('build-info.txt', 'build-inputs.json'):
        copy_file(built / name, output / name)
    copy_file(recovery / 'camera-dependencies.json', output / 'recovery-dependencies.json')
    copy_file(REPO / 'config/libcamera/ciri.patch', output / 'ciri.patch')
    (output / 'SHA256SUMS').write_text(''.join(
        f'{digest(path)}  {path.relative_to(output)}\n'
        for path in sorted(output.rglob('*')) if path.is_file()))
    print(f'Package prepared and statically checked: {output}\nNo device installation or hardware test performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built', type=Path, default=REPO / 'build/camera/stack')
    parser.add_argument('--recovery', type=Path, default=REPO / 'build/camera/recovery/chromeos-16836.19.0')
    parser.add_argument('--sdk', type=Path, default=REPO / 'build/camera/sdk/libcamera')
    parser.add_argument('--output', type=Path, default=REPO / 'build/camera/packages/libcamera')
    args = parser.parse_args()
    try:
        package(args.built, args.recovery, args.sdk, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Package preparation failed (partial output retained, no device modified): {error}\n')


if __name__ == '__main__':
    main()
