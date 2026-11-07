#!/usr/bin/env -S python3 -I
"""Prepare pinned ARM64 GStreamer data inputs; offline unless --download is given."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('camera_sdk', REPO / 'scripts/prepare-ciri-libcamera-sdk.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def prepare(root, cache, inputs, download):
    root.mkdir(parents=True, exist_ok=True)
    if root.absolute() != root.resolve():
        raise ValueError('SDK directory symlink refused')
    for name in ('lib', 'pkgconfig', 'SHA256SUMS'):
        if (root / name).is_symlink():
            raise ValueError('SDK staging symlink refused: ' + name)
    for item in inputs['packages']:
        filename = f"{item['name']}_{item['version']}_arm64.deb"
        path = Path(item['path'])
        if (not re.fullmatch(r'[a-z0-9.+-]+', item['name']) or path.name != filename or
                path.is_absolute() or '..' in path.parts or
                not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
            raise ValueError('Unsafe or inconsistent pinned package input')
        base.prepare_package(root, cache, item, item['version'],
                             'https://deb.debian.org/debian/' + str(path.parent), download)
    (root / 'lib').mkdir(exist_ok=True)
    (root / 'pkgconfig').mkdir(exist_ok=True)
    # Repair aliases from the initial overly broad '*.so*' scan. These are only
    # our generated links into this SDK, never the retained data files themselves.
    for link in (root / 'lib').iterdir():
        if link.is_symlink():
            actual = link.resolve(strict=True)
            if actual.is_relative_to(root.resolve()) and actual.name.endswith('-gdb.py'):
                link.unlink()
    # Linker staging only: actual runtime packaging follows verified ELF closure.
    for item in inputs['packages']:
        directory = root / item['name'] / 'sysroot'
        for path in directory.rglob('*.so*'):
            if not path.is_file():
                continue
            actual = path.resolve(strict=True)
            if not actual.is_relative_to(directory.resolve()):
                raise ValueError('Runtime library escapes extracted sysroot')
            with actual.open('rb') as stream:
                if stream.read(4) != b'\x7fELF':
                    continue
            names = {path.name, path.name.split('.so', 1)[0] + '.so'}
            for name in names:
                target = root / 'lib' / name
                if target.is_symlink():
                    if target.resolve(strict=True) != actual:
                        raise ValueError('Conflicting linker alias: ' + name)
                elif target.exists():
                    raise ValueError('Unexpected regular linker staging file: ' + name)
                else:
                    target.symlink_to(actual)
    glib = root / 'libglib2.0-dev/sysroot'
    gst = root / 'libgstreamer1.0-dev/sysroot'
    gstbase = root / 'libgstreamer-plugins-base1.0-dev/sysroot'
    cflags = ' '.join('-I' + str(path) for path in (
        glib / 'usr/include/glib-2.0',
        glib / 'usr/lib/aarch64-linux-gnu/glib-2.0/include',
        gst / 'usr/include/gstreamer-1.0',
        gst / 'usr/lib/aarch64-linux-gnu/gstreamer-1.0/include',
        gstbase / 'usr/include/gstreamer-1.0'))
    definitions = (
        ('glib-2.0', '2.74.6', '-lglib-2.0', ''),
        ('gobject-2.0', '2.74.6', '-lgobject-2.0', 'glib-2.0'),
        ('gmodule-2.0', '2.74.6', '-lgmodule-2.0', 'glib-2.0'),
        ('gstreamer-1.0', '1.22.0', '-lgstreamer-1.0', 'gobject-2.0'),
        ('gstreamer-base-1.0', '1.22.0', '-lgstbase-1.0', 'gstreamer-1.0'),
        ('gstreamer-video-1.0', '1.22.0', '-lgstvideo-1.0', 'gstreamer-base-1.0'),
        ('gstreamer-allocators-1.0', '1.22.0', '-lgstallocators-1.0', 'gstreamer-1.0'),
    )
    for name, version, libs, requires in definitions:
        (root / 'pkgconfig' / (name + '.pc')).write_text(
            f'Name: {name}\nDescription: Pinned ARM64 streaming SDK\nVersion: {version}\n'
            f'Requires: {requires}\nLibs: -L{root / "lib"} {libs}\nCflags: {cflags}\n')
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink() and path.name != 'SHA256SUMS':
            with path.open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            rows.append(f'{sha}  {path.relative_to(root)}\n')
    (root / 'SHA256SUMS').write_text(''.join(rows))
    print('Prepared GStreamer cross SDK:', root)
    print('No ARM programs executed, host packages installed or device changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-dir', type=Path, default=REPO / 'build/camera/sdk/gstreamer')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    inputs = json.loads((REPO / 'config/ciri-gstreamer-inputs.json').read_text())
    try:
        prepare(args.sdk_dir.absolute(), (args.cache_dir or args.sdk_dir).absolute(), inputs, args.download)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, 'Streaming SDK preparation failed: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
