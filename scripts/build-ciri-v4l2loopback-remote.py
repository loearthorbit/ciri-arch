#!/usr/bin/env -S python3 -I
"""Module-only build using a retained Docker toolchain and READ-ONLY kernel volume."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import quote
import uuid

REPO = Path(__file__).resolve().parent.parent
REVISION = '0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4'
spec = importlib.util.spec_from_file_location('remote_build', REPO / 'scripts/remote-build.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-url', default='http://127.0.0.1:23755')
    parser.add_argument('--retained-build', type=Path, default=REPO / 'build/camera/kernel/companion')
    parser.add_argument('--source-repository', type=Path, default=REPO / 'build/camera/repositories/v4l2loopback')
    parser.add_argument('--output', type=Path, default=REPO / 'build/camera/kernel/v4l2loopback')
    args = parser.parse_args()
    output = args.output.absolute()
    records = output.parent / (output.name + '-build-records')
    if any(path.exists() or path.is_symlink() for path in (output, records)):
        parser.error('Use fresh kit and build-record directories; nothing overwritten')
    repository = args.source_repository.resolve(strict=True)
    if output.resolve().is_relative_to(repository) or output.resolve().is_relative_to(args.retained_build.resolve()):
        parser.error('Output must be separate from retained inputs')
    if subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip() != REVISION:
        parser.error('Wrong retained loopback source revision; no fetch performed')
    container = (args.retained_build / 'container-id.txt').read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{64}', container):
        parser.error('Invalid retained container ID')
    docker = remote.Docker(args.docker_url)
    docker.api = '/v' + docker.json('GET', '/version')['ApiVersion']
    old = docker.json('GET', f'/containers/{container}/json')
    if old['State']['Status'] != 'exited' or old['State']['ExitCode'] != 0:
        parser.error('Require the successfully exited retained kernel build')
    labels = old['Config'].get('Labels') or {}
    if labels.get('purpose') != 'ciri-kernel' or labels.get('source') != 'https://github.com/mkopec/linux/tree/ciri-full':
        parser.error('Retained container is not the expected Ciri kernel build')
    mounts = [m for m in old['Mounts'] if m['Destination'] == '/work' and m['Type'] == 'volume']
    if len(mounts) != 1:
        parser.error('Require the retained named kernel volume')
    volume = mounts[0]['Name']
    output.mkdir(parents=True)
    records.mkdir()
    # Snapshot the toolchain layer only, not the kernel/source volume. No image pull.
    image = docker.json('POST', '/commit?container=' + container + '&pause=false')['Id']
    name = 'ciri-loopback-' + uuid.uuid4().hex[:12]
    command = ('set -euo pipefail; '
               'git -c safe.directory=/module/repository -c core.hooksPath=/dev/null '
               '-C /module/repository worktree add --detach /module/source ' + REVISION + '; '
               'export CIRI_KERNEL_SOURCE=/work/linux CIRI_KERNEL_BUILD=/work/out '
               'V4L2LOOPBACK_SOURCE=/module/source V4L2LOOPBACK_OUT=/module/kit '
               'KERNEL_MAKE_ARGS=CROSS_COMPILE=aarch64-linux-gnu-; '
               'bash /opt/ciri/scripts/build-ciri-v4l2loopback.sh')
    config = {
        'Image': image, 'Cmd': ['/bin/bash', '-c', command], 'Tty': True,
        'Labels': {'purpose': 'ciri-v4l2loopback', 'source': 'https://github.com/v4l2loopback/v4l2loopback', 'revision': REVISION},
        'HostConfig': {
            'Mounts': [{'Type': 'volume', 'Source': volume, 'Target': '/work', 'ReadOnly': True},
                       {'Type': 'volume', 'Source': name, 'Target': '/module'}],
            'NetworkMode': 'none', 'Memory': 2 * 1024**3, 'NanoCpus': 2 * 10**9,
            'RestartPolicy': {'Name': 'no'},
        },
    }
    (records / 'container.json').write_text(json.dumps(config, indent=2) + '\n')
    new = docker.json('POST', '/containers/create?name=' + quote(name), config)['Id']
    (records / 'container-id.txt').write_text(new + '\n')
    (records / 'retained-build.json').write_text(json.dumps({'container': container, 'volume': volume, 'kernel_read_only': True}, indent=2) + '\n')
    source_payload = io.BytesIO()
    with tarfile.open(fileobj=source_payload, mode='w') as archive:
        archive.add(repository / '.git', arcname='repository/.git')
    with docker.open('PUT', f'/containers/{new}/archive?path=/module', source_payload.getvalue(), 'application/x-tar') as response:
        response.read()
    script_payload = io.BytesIO()
    with tarfile.open(fileobj=script_payload, mode='w') as archive:
        for filename in ('build-ciri-v4l2loopback.sh', 'install-ciri-v4l2loopback.sh'):
            data = (REPO / 'scripts' / filename).read_bytes()
            (records / filename).write_bytes(data)
            member = tarfile.TarInfo('ciri/scripts/' + filename)
            member.mode, member.size = 0o755, len(data)
            archive.addfile(member, io.BytesIO(data))
    with docker.open('PUT', f'/containers/{new}/archive?path=/opt', script_payload.getvalue(), 'application/x-tar') as response:
        response.read()
    print('Building module only; retained kernel volume is READ-ONLY:', volume, flush=True)
    docker.json('POST', f'/containers/{new}/start')
    result = docker.json('POST', f'/containers/{new}/wait?condition=not-running', timeout=600)
    (records / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    with docker.open('GET', f'/containers/{new}/logs?stdout=1&stderr=1') as response:
        log = response.read()
    (records / 'build.log').write_bytes(log)
    print(log.decode(errors='replace'), flush=True)
    if result['StatusCode'] != 0 or result.get('Error'):
        raise RuntimeError('Module build failed; container/records retained: ' + new)
    with docker.open('GET', f'/containers/{new}/archive?path=/module/kit') as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Unexpectedly large module kit')
    # Read only the four known regular files; do not extract paths or links.
    expected = {'v4l2loopback.ko', 'module-build-info.txt', 'install-loopback.sh', 'SHA256SUMS'}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        files = [m for m in archive.getmembers() if not m.isdir()]
        if {m.name for m in files} != {'kit/' + name for name in expected} or len(files) != len(expected):
            raise ValueError('Unexpected module-kit archive inventory')
        for member in files:
            if not member.isfile():
                raise ValueError('Nonregular module-kit member')
            with archive.extractfile(member) as stream:
                (output / Path(member.name).name).write_bytes(stream.read())
    (output / 'install-loopback.sh').chmod(0o755)
    remote.verify_checksums(output)
    print('Verified module kit:', output)
    print('Build records:', records)
    print('Nothing installed or loaded. Build records are separate from the four-file install kit.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
