#!/bin/bash
# Promote the native-verified core only; no compile, runtime assembly or install.
set -euo pipefail
[[ $# == 1 && $1 == /* ]] || {
    printf 'Usage: %s /absolute/path/to/verified-p2-planes-kit\n' "$0" >&2
    exit 2
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
kit=$1
[[ -d $kit && ! -L $kit ]]
for name in libcamera.so.0.1.0 build-info.txt SHA256SUMS; do
    [[ -f $kit/$name && ! -L $kit/$name ]]
done
(cd "$kit" && sha256sum --check --strict SHA256SUMS)
python3 -I - "$repo" "$kit" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

repo, kit = map(Path, sys.argv[1:])
verified = 'db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d'
baseline = 'af1014e2f73934ab79ccc1eddff5806194b83b9fce925e5e443b81443487a335'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


info = json.loads((kit / 'build-info.txt').read_text())
if (digest(kit / 'libcamera.so.0.1.0') != verified or info.get('core_sha256') != verified or
        info.get('baseline_core_sha256') != baseline or
        info.get('architecture') != 'AArch64' or info.get('missing_strong_imports') != {} or
        info.get('p2_separate_plane_buffers') is not True):
    raise SystemExit('Not the core verified in the native colour-bar and scene tests')
out = Path(tempfile.mkdtemp(prefix='core-update.', dir=repo / 'build/camera/updates'))
shutil.copyfile(kit / 'libcamera.so.0.1.0', out / 'libcamera.so.0.1.0')
if digest(out / 'libcamera.so.0.1.0') != verified:
    raise SystemExit('Copied core hash changed; kit not staged')
shutil.copyfile(repo / 'scripts/update-ciri-libcamera-host.sh', out / 'install-core-update.sh')
info.update(parent_kit=str(kit), diagnostic='Core-only promotion for normal camera streaming',
            config_only_restage=True, hardware_tested=True, normal_streaming_verified=False,
            colour='Real-scene/colour-bar corruption fixed; absolute calibration not certified',
            native_verification={
                'date': '2026-11-09',
                'performed_by': 'operator',
                'colour_bars_log': 'ciri-p2-planes-20261109-103644.log',
                'scene_log': 'ciri-scene-20261109-121621.log',
                'scene_result': 'build/ciri-scene-result.VV3pV2o8',
                'scene_frames': 180,
            })
(out / 'build-info.txt').write_text(json.dumps(info, indent=2) + '\n')
names = ('libcamera.so.0.1.0', 'build-info.txt', 'install-core-update.sh')
(out / 'SHA256SUMS').write_text(''.join(digest(out / name) + '  ' + name + '\n' for name in names))
print('Core-only update kit:', out)
print('Core SHA256:', verified)
print('No rebuild, recovered-runtime copying/execution, device installation or stream start performed.')
PY
