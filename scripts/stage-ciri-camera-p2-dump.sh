#!/bin/bash
# Config-only diagnostic kit: reuse the operator-built core; never compile/install.
set -euo pipefail
[[ $# == 1 && $1 == /* ]] || {
    printf 'Usage: %s /absolute/path/to/existing-p1-dump-kit\n' "$0" >&2
    exit 2
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
kit=$1
[[ -d $kit && ! -L $kit ]]
for name in libcamera.so.0.1.0 build-info.txt run-libcamera test-p1-dump-ram.sh test-colour-bars.sh dump.cfg SHA256SUMS; do
    [[ -f $kit/$name && ! -L $kit/$name ]]
done
(cd "$kit" && sha256sum --check --strict SHA256SUMS)
out=$(mktemp -d "$repo/build/camera/updates/p2-dump.XXXXXXXX")
cp "$kit/libcamera.so.0.1.0" "$out/libcamera.so.0.1.0"
cp "$repo/scripts/run-ciri-libcamera.sh" "$out/run-libcamera"
cp "$repo/scripts/test-ciri-camera-p1-dump-ram.sh" "$out/test-p1-dump-ram.sh"
cp "$repo/scripts/test-ciri-camera-colour-bars.sh" "$out/test-colour-bars.sh"
cp "$repo/config/libcamera/p2-dump.cfg" "$out/dump.cfg"
python3 -I - "$kit" "$out" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

kit, out = map(Path, sys.argv[1:])
info = json.loads((kit / 'build-info.txt').read_text())
core_hash = hashlib.sha256((out / 'libcamera.so.0.1.0').read_bytes()).hexdigest()
if core_hash != info['core_sha256']:
    raise SystemExit('Core hash differs from the existing build report; kit not staged')
info.update(parent_kit=str(kit), config_only_restage=True,
            diagnostic='P2/MCNR pixel export', hardware_tested=False,
            dump_config_sha256=hashlib.sha256((out / 'dump.cfg').read_bytes()).hexdigest())
(out / 'build-info.txt').write_text(json.dumps(info, indent=2) + '\n')
PY
(cd "$out" && sha256sum libcamera.so.0.1.0 build-info.txt run-libcamera test-p1-dump-ram.sh test-colour-bars.sh dump.cfg > SHA256SUMS)
printf '\nConfig-only P2 kit: %s\nNo compilation, runtime execution or installation performed.\n' "$out"
