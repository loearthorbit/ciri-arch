#!/usr/bin/python3
"""Extract only the two retained ISP modules into a fresh operator install kit."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('isp_installer', REPO / 'scripts/install-ciri-isp-modules.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
MEMBERS = {
    'lib/modules/7.3.0-rc4-ciri/kernel/drivers/media/platform/mediatek/isp/isp_7x/imgsys/mtk_imgsys.ko': 'mtk_imgsys.ko',
    'lib/modules/7.3.0-rc4-ciri/kernel/drivers/media/platform/mediatek/aie/mtk-aie.ko': 'mtk-aie.ko',
}
CONFIG_SHA = '287c4281daf61395cc1736f49a906698c52b2e8e363222e683b9c1afb979d13b'


def extract_selected(stream, output):
    found = set()
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if member.name not in MEMBERS:
                continue  # Never extract other modules, links, images or scripts.
            name = MEMBERS[member.name]
            if not member.isfile() or member.size > 20_000_000 or name in found:
                raise ValueError('Invalid/duplicate selected archive member: ' + member.name)
            data = archive.extractfile(member).read()
            if len(data) != member.size or hashlib.sha256(data).hexdigest() != installer.MODULES[name]:
                raise ValueError('Selected module differs from pinned retained bytes: ' + name)
            with (output / name).open('xb') as target:
                target.write(data)
            found.add(name)
    if found != set(installer.MODULES):
        raise ValueError('Retained archive lacks a required module')


def stage(source):
    source = installer.checked(source)
    archive = source / 'modules.tar.zst'
    if installer.digest(installer.checked(archive)) != installer.ARCHIVE_SHA:
        raise ValueError('Retained module archive checksum mismatch')
    if installer.digest(installer.checked(source / 'kernel.config')) != CONFIG_SHA:
        raise ValueError('Not the matching retained kernel configuration')
    if (installer.checked(source / 'source-revision.txt').read_text().strip() !=
            'b8f841230b1615aa58b5d4389e11bfe66abbd993' or
            installer.checked(source / 'kernel-release.txt').read_text().strip() != installer.RELEASE):
        raise ValueError('Retained kernel revision/release mismatch')
    base = REPO / 'build/camera/updates'
    base.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='isp-modules.', dir=base))
    child = subprocess.Popen(['zstd', '-dc', str(archive)], stdout=subprocess.PIPE)
    try:
        extract_selected(child.stdout, output)
        # Drain tar padding so zstd can finish; never execute extracted code.
        while child.stdout.read(65536):
            pass
        if child.wait():
            raise ValueError('Retained archive decompression failed')
    finally:
        child.stdout.close()
        if child.poll() is None:
            child.kill()
        child.wait()
    shutil.copyfile(REPO / 'scripts/install-ciri-isp-modules.py', output / 'install-modules.py')
    (output / 'module-build-info.txt').write_text(
        'kernel-release=' + installer.RELEASE + '\narchitecture=AArch64\nmodule-only=true\n'
        'source-revision=b8f841230b1615aa58b5d4389e11bfe66abbd993\n'
        'source-archive-sha256=' + installer.ARCHIVE_SHA + '\n'
        'kernel-config-sha256=' + CONFIG_SHA + '\n'
        'No kernel/DTB/initramfs/firmware/userspace copied or rebuilt.\n')
    names = set(installer.MODULES) | {'install-modules.py', 'module-build-info.txt'}
    (output / 'SHA256SUMS').write_text(''.join(
        installer.digest(output / name) + '  ' + name + '\n' for name in sorted(names)))
    installer.validate_kit(output)
    print('ISP module-only kit:', output)
    print('Only retained mtk_imgsys/mtk-aie and authored installer/provenance copied; no build or device action.')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path,
                        default=REPO / 'build/camera/kernel/companion/download/ciri-camera-companion-yjsiaoyk')
    stage(parser.parse_args().source)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, tarfile.TarError) as error:
        raise SystemExit('ISP module kit preparation failed: ' + str(error))
