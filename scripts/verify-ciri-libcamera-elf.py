#!/usr/bin/env -S python3 -I
"""Static AArch64 dependency/symbol checks. Never execute the camera runtime."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

SEARCH = ('usr/lib/ciri-libcamera', 'usr/lib64', 'lib64',
          'usr/share/cros-camera/libfs')
ROOTS = ('usr/bin/cam', 'usr/bin/ciri-libcamera-host',
         'usr/lib64/mtkisp7_ipa_proxy.so', 'usr/lib64/libcamera/ipa_mtkisp7.so',
         'usr/lib64/libgc05a2_mipi_raw_tuning.so',
         'usr/lib64/libgc08a3_mipi_raw_tuning.so')


def inspect(path):
    result = subprocess.run(['readelf', '-h', '-d', '--dyn-syms', '--wide', str(path)],
                            check=True, capture_output=True, text=True,
                            env={**os.environ, 'LC_ALL': 'C'})
    text = result.stdout
    if not re.search(r'Class:\s+ELF64', text) or not re.search(r'Machine:\s+AArch64', text):
        raise ValueError(f'Not an ELF64 AArch64 runtime file: {path}')
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
    soname = re.findall(r'\(SONAME\).*?\[([^\]]+)\]', text)
    exports, imports = set(), set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8 or not re.fullmatch(r'\d+:', fields[0]):
            continue
        _, _, _, _, binding, visibility, index, name = fields[:8]
        if index == 'UND':
            if binding == 'GLOBAL':
                imports.add(name.replace('@@', '@'))
        elif binding in ('GLOBAL', 'WEAK', 'UNIQUE') and visibility in ('DEFAULT', 'PROTECTED'):
            if '@@' in name:
                exports.add(name.split('@@')[0])
            exports.add(name.replace('@@', '@'))
    return {'needed': needed, 'soname': soname[0] if soname else None,
            'exports': exports, 'imports': imports}


def safe_file(root, relative):
    relative = Path(relative)
    if (not relative.parts or relative.is_absolute() or '..' in relative.parts or
            any(char in str(relative) for char in ('\n', '\r', '\\', '\0'))):
        raise ValueError(f'Unsafe runtime relative path: {relative}')
    path = root / relative
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f'Runtime path escapes root or is not a file: {relative}')
    return resolved


def verify(root):
    root = root.resolve()
    # Fail rather than silently falling back to the original recovered core.
    for name in ('libcamera.so.0.1.0', 'libcamera-base.so.0.1.0',
                 'libcamera-mtkisp7-platform.so'):
        safe_file(root, 'usr/lib/ciri-libcamera/' + name)
    discovered = {}

    def visit(path):
        path = path.resolve()
        if path in discovered:
            return
        item = inspect(path)
        discovered[path] = item
        for name in item['needed']:
            if '/' in name or name in ('.', '..'):
                raise ValueError(f'Unexpected DT_NEEDED path: {name}')
            for directory in SEARCH:
                candidate = root / directory / name
                if candidate.exists():
                    visit(safe_file(root, str(candidate.relative_to(root))))
                    break
            else:
                raise ValueError(f'Missing {name}, required by {path.relative_to(root)}')

    for name in ROOTS:
        visit(safe_file(root, name))
    # Runtime modules may use the executable/global scope. Check provider presence
    # in that combined closure, not just a plugin's immediate DT_NEEDED entries.
    # This is NOT a proof of C++ layout, tuning, dlopen scopes or hardware colour.
    exports = set().union(*(item['exports'] for item in discovered.values()))
    missing = {str(path.relative_to(root)): sorted(item['imports'] - exports)
               for path, item in discovered.items() if item['imports'] - exports}
    report = {
        'architecture': 'AArch64', 'runtime_executed': False,
        'check': 'DT_NEEDED closure and strong symbol providers (including versions)',
        'limitations': 'Provider presence does not prove dlopen scopes, C++ ABI or hardware compatibility.',
        'elf_files': {str(path.relative_to(root)): {
            'soname': item['soname'], 'needed': item['needed'],
            'strong_imports': len(item['imports'])
        } for path, item in sorted(discovered.items())},
        'missing_strong_imports': missing,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.rootfs)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'ELF verification failed: {error}\n')
    if args.report:
        if args.report.exists():
            parser.exit(1, 'Refusing to overwrite an existing ELF report.\n')
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    if report['missing_strong_imports']:
        print(json.dumps(report['missing_strong_imports'], indent=2))
        return 1
    print(f"Static checks passed for {len(report['elf_files'])} ELF files; runtime/hardware not tested.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
