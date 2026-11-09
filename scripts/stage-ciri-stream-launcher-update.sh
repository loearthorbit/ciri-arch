#!/bin/bash
# Launcher-only kit; reuse the existing core/plugin/runtime without rebuilding.
set -euo pipefail
[[ $# == 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 -I - "$repo" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

repo = Path(sys.argv[1])
source = repo / 'scripts/stream-ciri-camera.sh'
text = source.read_text()
if ('\n    filesink ' not in text or 'buffer-mode=unbuffered' not in text or
        '\n    v4l2sink ' in text):
    raise SystemExit('Expected the authored unbuffered filesink streaming launcher')
out = Path(tempfile.mkdtemp(prefix='stream-update.', dir=repo / 'build/camera/updates'))
shutil.copyfile(source, out / 'stream-camera')
shutil.copyfile(repo / 'scripts/update-ciri-libcamera-host.sh', out / 'install-stream-update.sh')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


info = {'component': 'stream-launcher-only', 'filesink_bridge': True,
        'baseline_stream_launcher_sha256': '0489e6babd3c7ec7180579be6cbe12dc25e1c41f4f1b23845a205b1f875b4b83',
        'stream_launcher_sha256': digest(out / 'stream-camera'),
        'runtime_copied': False, 'libraries_rebuilt': False,
        'kernel_rebuilt': False, 'device_installation_performed': False}
(out / 'build-info.txt').write_text(json.dumps(info, indent=2) + '\n')
names = ('stream-camera', 'build-info.txt', 'install-stream-update.sh')
(out / 'SHA256SUMS').write_text(''.join(digest(out / name) + '  ' + name + '\n' for name in names))
print('Streaming-launcher update kit:', out)
print('No core/runtime copying, rebuild, device installation or module loading performed.')
PY
