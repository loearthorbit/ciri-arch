#!/bin/bash
# Service-only kit: authored Python/config files, no build or runtime copying.
set -euo pipefail
[[ $# == 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 -I - "$repo" <<'PY'
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
repo = Path(sys.argv[1])
outputs = {
    'usr/local/lib/ciri-camera/service.py': 'scripts/ciri-camera-service.py',
    'etc/systemd/system/ciri-camera.service': 'config/camera/ciri-camera.service',
    'etc/udev/rules.d/70-ciri-camera.rules': 'config/camera/70-ciri-camera.rules',
    'etc/ciri-camera.conf': 'config/camera/ciri-camera.conf',
    'install-service.py': 'scripts/install-ciri-camera-service.py',
}
for source in outputs.values():
    path = repo / source
    if not path.is_file() or path.is_symlink():
        raise SystemExit('Missing authored service input: ' + source)
base = repo / 'build/camera/updates'
base.mkdir(parents=True, exist_ok=True)
out = Path(tempfile.mkdtemp(prefix='camera-service.', dir=base))
for name, source in outputs.items():
    target = out / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo / source, target)
(out / 'SHA256SUMS').write_text(''.join(
    hashlib.sha256((out / name).read_bytes()).hexdigest() + '  ' + name + '\n'
    for name in sorted(outputs)))
print('Camera service kit:', out)
print('Both cameras enabled; no runtime copying, build, installation or camera execution performed.')
PY
