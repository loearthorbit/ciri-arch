#!/usr/bin/env -S python3 -I
"""Build only uinput in a fresh container using the retained read-only kernel cache."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from urllib.parse import quote
import uuid

REPO = Path(__file__).resolve().parent.parent
IMAGE = 'sha256:ae243b4846988fa5f254eae14039e6622ccff49abec418d9f58b950fd8a6b185'
VOLUME = 'ciri-kernel-20261104-203923-bcb2ff0d'
spec = importlib.util.spec_from_file_location('remote_build', REPO / 'scripts/remote-build.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-url', default='http://127.0.0.1:23755')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    name = 'ciri-uinput-' + uuid.uuid4().hex[:12]
    output = args.output.absolute() if args.output else REPO / 'build/camera/kernel' / name
    records = output.parent / (output.name + '-build-records')
    if output.exists() or output.is_symlink() or records.exists() or records.is_symlink():
        parser.error('Use fresh output/build-record directories; nothing overwritten')
    docker = remote.Docker(args.docker_url)
    docker.api = '/v' + docker.json('GET', '/version')['ApiVersion']
    docker.json('GET', '/images/' + IMAGE + '/json')
    docker.json('GET', '/volumes/' + VOLUME)
    config = {
        'Image': IMAGE, 'Entrypoint': None,
        'Cmd': ['/bin/bash', '/opt/ciri/build-ciri-uinput.sh'], 'Tty': True,
        'Labels': {'purpose': 'ciri-uinput-module-only'},
        'HostConfig': {'Mounts': [
            {'Type': 'volume', 'Source': VOLUME, 'Target': '/work', 'ReadOnly': True},
            {'Type': 'volume', 'Source': name, 'Target': '/module'}],
            'NetworkMode': 'none', 'Memory': 2 * 1024**3, 'NanoCpus': 2 * 10**9,
            'RestartPolicy': {'Name': 'no'}},
    }
    output.mkdir(parents=True)
    records.mkdir()
    (records / 'container.json').write_text(json.dumps(config, indent=2) + '\n')
    container = docker.json('POST', '/containers/create?name=' + quote(name), config)['Id']
    (records / 'container-id.txt').write_text(container + '\n')
    payload = io.BytesIO()
    builder = (REPO / 'scripts/build-ciri-uinput.sh').read_bytes()
    (records / 'build-ciri-uinput.sh').write_bytes(builder)
    with tarfile.open(fileobj=payload, mode='w') as archive:
        entry = tarfile.TarInfo('ciri/build-ciri-uinput.sh')
        entry.mode, entry.size = 0o755, len(builder)
        archive.addfile(entry, io.BytesIO(builder))
    with docker.open('PUT', f'/containers/{container}/archive?path=/opt', payload.getvalue(), 'application/x-tar') as response:
        response.read()
    print('Building only uinput; retained kernel volume is READ-ONLY.', flush=True)
    docker.json('POST', f'/containers/{container}/start')
    result = docker.json('POST', f'/containers/{container}/wait?condition=not-running', timeout=600)
    (records / 'result.json').write_text(json.dumps(result) + '\n')
    with docker.open('GET', f'/containers/{container}/logs?stdout=1&stderr=1') as response:
        logs = response.read()
    (records / 'build.log').write_bytes(logs)
    print(logs.decode(errors='replace'), flush=True)
    if result.get('StatusCode') != 0 or result.get('Error'):
        raise RuntimeError('uinput build failed; container/output/records retained: ' + container)
    with docker.open('GET', f'/containers/{container}/archive?path=/module/uinput/kit') as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Unexpectedly large module kit')
    expected = {'kit/uinput.ko', 'kit/build-info.txt', 'kit/SHA256SUMS'}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = [entry for entry in archive if not entry.isdir()]
        if len(files) != len(expected) or {entry.name for entry in files} != expected:
            raise ValueError('Unexpected uinput kit inventory')
        for entry in files:
            if not entry.isfile():
                raise ValueError('Nonregular uinput kit entry')
            (output / Path(entry.name).name).write_bytes(archive.extractfile(entry).read())
    remote.verify_checksums(output)
    print('Verified uinput module kit:', output)
    print('Build records:', records)
    print('No kernel rebuilt/replaced; no remote module installation or loading performed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
