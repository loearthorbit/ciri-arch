#!/usr/bin/env -S python3 -I
"""Plan/check a streaming add-on; copy files only with explicit --assemble."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('camera_package', REPO / 'scripts/package-ciri-libcamera-stack.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
elf = base.elf
PLUGIN_DIR = 'usr/lib/ciri-libcamera/gstreamer-1.0/'
STREAM_ROOTS = ('usr/bin/gst-launch-1.0', 'usr/bin/gst-inspect-1.0',
                PLUGIN_DIR + 'libgstlibcamera.so', PLUGIN_DIR + 'libgstcoreelements.so',
                PLUGIN_DIR + 'libgstvideoconvertscale.so', PLUGIN_DIR + 'libgstvideo4linux2.so')
# Only these matching, open-source support libraries may be added from recovery.
# Never duplicate proprietary algorithm, tuning or IPA files in this add-on.
RECOVERED_SUPPORT = {'libgobject-2.0.so.0', 'libgmodule-2.0.so.0', 'libffi.so.6'}
KNOWN_HOSTS = ['1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45',
               '8c445f81219a0a631dd3c49607c63714720027ae2c89f5890e0e6c609b0816f5']


def plan(built, installed_package, sdk, recovery, host_package):
    built, installed_package, sdk, recovery, host_package = map(Path, (built, installed_package, sdk, recovery, host_package))
    verified_base = base.check_manifest(installed_package, installed_package / 'SHA256SUMS')
    verified_build = base.check_manifest(built, built / 'SHA256SUMS')
    verified_host = base.check_manifest(host_package, host_package / 'SHA256SUMS')
    verified_sdk = base.check_manifest(sdk, sdk / 'SHA256SUMS')
    verified_recovery = base.check_manifest(recovery / 'files', recovery / 'SHA256SUMS')
    inputs = json.loads((REPO / 'config/ciri-gstreamer-inputs.json').read_text())
    for item in inputs['packages']:
        if base.digest(sdk / item['name'] / 'package.deb') != item['sha256']:
            raise ValueError('Streaming archive checksum mismatch: ' + item['name'])
    root = (installed_package / 'rootfs').resolve()
    runtime = {str(path.relative_to(root)): path for path in verified_base if path.is_relative_to(root)}
    payload = {}

    def add(source, relative):
        source = source.resolve(strict=True)
        if source not in verified_build | verified_host | verified_sdk | verified_recovery:
            raise ValueError('Unverified runtime source: ' + str(source))
        relative = str(Path(relative))
        if relative.startswith('/') or '..' in Path(relative).parts:
            raise ValueError('Unsafe runtime destination')
        runtime[relative] = source
        payload['rootfs/' + relative] = source

    add(elf.safe_file(host_package, 'ciri-libcamera-host'), 'usr/bin/ciri-libcamera-host')
    add(elf.safe_file(built / 'rootfs', PLUGIN_DIR + 'libgstlibcamera.so'), PLUGIN_DIR + 'libgstlibcamera.so')
    selected = {
        'gstreamer1.0-tools': ('usr/bin/gst-launch-1.0', 'usr/bin/gst-inspect-1.0'),
        'libgstreamer1.0-0': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstcoreelements.so',),
        'gstreamer1.0-plugins-base': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstvideoconvertscale.so',),
        'gstreamer1.0-plugins-good': ('usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstvideo4linux2.so',),
    }
    for package, names in selected.items():
        for name in names:
            relative = name if name.startswith('usr/bin/') else PLUGIN_DIR + Path(name).name
            add(elf.safe_file(sdk / package / 'sysroot', name), relative)
    package_roots = [sdk / item['name'] / 'sysroot' for item in inputs['packages']]
    used = set(selected)
    discovered = {}

    def dependency(name):
        if '/' in name or name in ('.', '..'):
            raise ValueError('Unsafe DT_NEEDED: ' + name)
        # Preserve all existing runtime providers and the working core ABI.
        for directory in elf.SEARCH:
            relative = directory + '/' + name
            if relative in runtime:
                return relative
        if name in RECOVERED_SUPPORT:
            for directory in ('usr/lib64', 'lib64'):
                path = recovery / 'files' / directory / name
                if path.exists():
                    relative = directory + '/' + name
                    add(elf.safe_file(recovery / 'files', relative), relative)
                    return relative
        for package_root in package_roots:
            for directory in ('usr/lib/aarch64-linux-gnu', 'lib/aarch64-linux-gnu'):
                path = package_root / directory / name
                if path.exists():
                    relative = 'usr/lib/ciri-libcamera/' + name
                    add(elf.safe_file(package_root, directory + '/' + name), relative)
                    used.add(package_root.parent.name)
                    return relative
        raise ValueError('Missing streaming dependency: ' + name)

    def visit(relative):
        if relative in discovered:
            return
        if relative not in runtime:
            raise ValueError('Missing runtime root: ' + relative)
        item = elf.inspect(runtime[relative])
        discovered[relative] = item
        for needed in item['needed']:
            visit(dependency(needed))

    for relative in (*elf.ROOTS, *STREAM_ROOTS):
        visit(relative)
    exports = set().union(*(item['exports'] for item in discovered.values()))
    missing = {name: sorted(item['imports'] - exports) for name, item in discovered.items()
               if item['imports'] - exports}
    report = {'architecture': 'AArch64', 'runtime_executed': False,
              'check': 'Combined base/add-on DT_NEEDED closure and versioned strong providers',
              'limitations': 'Does not prove loader scopes, buffer mapping/coherency, hardware streaming or colour.',
              'elf_files': {name: {'needed': item['needed'], 'strong_imports': len(item['imports'])}
                            for name, item in sorted(discovered.items())},
              'missing_strong_imports': missing}
    if missing:
        raise ValueError('Missing strong streaming imports: ' + json.dumps(missing))
    for package in sorted(used):
        path = sdk / package / 'sysroot/usr/share/doc' / package / 'copyright'
        if path.is_file():
            source = path.resolve()
            if source not in verified_sdk:
                raise ValueError('Unverified copyright file')
            payload['streaming-licenses/' + package + '/copyright'] = source
    payload['run-libcamera'] = REPO / 'scripts/run-ciri-libcamera.sh'
    payload['stream-camera'] = REPO / 'scripts/stream-ciri-camera.sh'
    payload['streaming.patch'] = REPO / 'config/libcamera/ciri.patch'
    expected = {str(path.relative_to(installed_package.resolve())): base.digest(path)
                for path in verified_base}
    generated = {
        'streaming-elf-report.json': json.dumps(report, indent=2) + '\n',
        'expected-base.json': json.dumps({'files': expected, 'allowed_host_sha256': KNOWN_HOSTS}, indent=2) + '\n',
        'streaming-inputs.json': json.dumps(inputs, indent=2) + '\n',
        'streaming-build-info.txt': 'client=standard-GStreamer-libcamerasrc\nbase-core=unchanged\n'
                                   'output=640x480-NV12-to-YUY2-unbuffered-filesink\n'
                                   'loopback-configuration=host-v4l2-ctl\n'
                                   'registry-fork=disabled\nhardware-tested=false\ncolour=uncorrected\n' +
                                   (built / 'build-info.txt').read_text() + '\nHost delta:\n' +
                                   (host_package / 'build-info.txt').read_text(),
    }
    return payload, generated, report


def assemble(payload, generated, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a fresh output directory; nothing overwritten')
    output.mkdir(parents=True)
    for name, source in payload.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for name, content in generated.items():
        (output / name).write_text(content)
    shutil.copy2(REPO / 'scripts/install-ciri-gstreamer.sh', output / 'install-streaming.sh')
    for name in ('run-libcamera', 'stream-camera', 'install-streaming.sh'):
        (output / name).chmod(0o755)
    (output / 'SHA256SUMS').write_text(''.join(
        f'{base.digest(path)}  {path.relative_to(output)}\n'
        for path in sorted(output.rglob('*')) if path.is_file()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built', type=Path, default=REPO / 'build/camera/stack-streaming')
    parser.add_argument('--base', type=Path, default=REPO / 'build/camera/packages/libcamera')
    parser.add_argument('--sdk', type=Path, default=REPO / 'build/camera/sdk/gstreamer')
    parser.add_argument('--host-package', type=Path, default=REPO / 'build/camera/updates/gstreamer-host')
    parser.add_argument('--recovery', type=Path, default=REPO / 'build/camera/recovery/chromeos-16836.19.0')
    parser.add_argument('--output', type=Path, default=REPO / 'build/camera/packages/gstreamer-addon')
    parser.add_argument('--assemble', action='store_true', help='explicitly copy checked new/support files into a fresh local kit')
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if any(output.is_relative_to(path.resolve()) for path in (args.built, args.base, args.sdk, args.recovery, args.host_package)):
            raise ValueError('Output must be separate from all retained inputs')
        payload, generated, report = plan(args.built, args.base, args.sdk, args.recovery, args.host_package)
        print(f"Static streaming checks passed for {len(report['elf_files'])} ELF files.")
        if args.assemble:
            assemble(payload, generated, args.output)
            print('Add-on prepared:', args.output)
        else:
            print('Check only; no runtime files copied. Run with --assemble to create the local add-on.')
        print('No proprietary algorithms recopied, ARM code executed or device installed. Colour unresolved.')
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Streaming package failed: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
