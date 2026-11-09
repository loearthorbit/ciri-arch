#!/bin/bash
# Incremental standard-cam update. Reuse retained objects; do not rebuild core.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
objects=$repo/build/ciri-libcamera-meson
source=$repo/build/camera/source/libcamera
revision=a5a75eeabdce349d8f794caacb3177c0e71287d5
[[ $(git -C "$source" rev-parse HEAD) == "$revision" && -f $objects/build.ninja ]]
git -C "$source" apply --check --reverse "$repo/config/libcamera/ciri.patch"
export LIBCAMERA_STACK_SDK="$repo/build/camera/sdk/libcamera"
export CAMERA_SDK="$repo/build/camera/sdk/compiler"
export CAMERA_RECOVERY="$repo/build/camera/recovery/chromeos-16836.19.0"
unset CC CXX
mkdir -p "$repo/build/camera/updates"
out=$(mktemp -d "$repo/build/camera/updates/cam-script.XXXXXXXX")
printf 'Update directory: %s\n' "$out"
# Building the complete cam target would also regenerate/relink core's version
# object. Compile only the changed cam object, then reuse its existing link rule.
ninja -C "$objects" src/apps/cam/cam.p/capture_script.cpp.o
python3 -I - "$repo" "$objects" "$out" "$revision" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

repo, objects, out = map(Path, sys.argv[1:4])
target = 'src/apps/cam/cam'
command = subprocess.check_output(
    ['ninja', '-C', str(objects), '-t', 'commands', '-s', target], text=True)
args = shlex.split(command)
if (not args or args[0] != str(repo / 'scripts/ciri-libcamera-cross-cc.sh') or
        args.count('-o') != 1 or args[args.index('-o') + 1] != target):
    raise SystemExit('Unexpected retained cam link command; no link performed')
args[args.index('-o') + 1] = str(out / 'cam')
# Match the installed cam: no build-tree RPATH. The launcher supplies private
# library paths explicitly. Leave the retained executable and all core DSOs alone.
args = [arg for arg in args if not arg.startswith('-Wl,-rpath,')]
subprocess.run(args, cwd=objects, check=True)
(out / 'cam').chmod(0o755)

spec = importlib.util.spec_from_file_location(
    'ciri_elf', repo / 'scripts/verify-ciri-libcamera-elf.py')
elf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elf)
runtime = repo / 'build/camera/packages/libcamera/rootfs'
report = elf.verify(runtime)
if report['missing_strong_imports']:
    raise SystemExit('Baseline runtime does not pass static ELF checks')
old = elf.inspect(runtime / 'usr/bin/cam')
new = elf.inspect(out / 'cam')
if new['needed'] != old['needed']:
    raise SystemExit('Updated cam changed its DT_NEEDED dependencies')
# Unchanged imports were checked above. The only new references must be supplied
# by the already-installed libcamera/core ABI, not a new provider or library.
providers = set()
for name in ('libcamera.so.0.1.0', 'libcamera-base.so.0.1.0'):
    providers |= elf.inspect(runtime / 'usr/lib/ciri-libcamera' / name)['exports']
added = new['imports'] - old['imports']
missing = added - providers - new['exports']
if missing:
    raise SystemExit('Missing providers for new cam imports: ' + repr(sorted(missing)))
dynamic = subprocess.check_output(['readelf', '-d', str(out / 'cam')], text=True)
if '(RPATH)' in dynamic or '(RUNPATH)' in dynamic:
    raise SystemExit('Build-tree RPATH remains in staged cam')

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

report['elf_files']['usr/bin/cam'] = {
    'soname': new['soname'], 'needed': new['needed'],
    'strong_imports': len(new['imports']),
}
report.update({
    'source_revision': sys.argv[4],
    'patch_sha256': digest(repo / 'config/libcamera/ciri.patch'),
    'baseline_cam_sha256': digest(runtime / 'usr/bin/cam'),
    'cam_sha256': digest(out / 'cam'),
    'added_cam_imports': sorted(added),
    'compiled_target': 'src/apps/cam/cam.p/capture_script.cpp.o',
    'link_command': args,
    'libraries_rebuilt': False,
    'hardware_tested': False,
    'colour': 'unresolved',
})
(out / 'build-info.txt').write_text(json.dumps(report, indent=2) + '\n')
shutil.copyfile(repo / 'scripts/update-ciri-libcamera-host.sh', out / 'install-cam-update.sh')
(out / 'install-cam-update.sh').chmod(0o755)
names = ('cam', 'build-info.txt', 'install-cam-update.sh')
(out / 'SHA256SUMS').write_text(''.join(digest(out / name) + '  ' + name + '\n' for name in names))
print('Built/statically checked cam-only update:', out)
print('Cam SHA256:', report['cam_sha256'])
print('No core, IPA, kernel or device installation performed; colour remains unresolved.')
PY
