#!/bin/bash
# Retained diagnostic objects + one staged link; never install or run the core.
set -euo pipefail
[[ $# == 0 || ( $# == 1 && $1 == --p2-planes ) ]] || {
    printf 'Usage: %s [--p2-planes]\n' "$0" >&2
    exit 2
}
kit_prefix=p1-dump
dump_config=config/libcamera/p1-dump.cfg
# The new pool method is nonvirtual; existing signatures and object layouts
# are unchanged. Only MCNR calls it, so other retained objects remain usable.
# Always refresh all four changed objects, even with the P1-only dump config.
compiled_targets=(src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_odt_on_device_tuner.cpp.o
                  src/libcamera/libcamera.so.0.1.0.p/info_frame.cpp.o
                  src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_imgsys_mcnr.cpp.o
                  src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_odt_imagiq_adapter_imagiq_adapter.cpp.o)
if [[ $# == 1 ]]; then
    kit_prefix=p2-planes
    dump_config=config/libcamera/p2-dump.cfg
fi
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
out=$(mktemp -d "$repo/build/camera/updates/$kit_prefix.XXXXXXXX")
printf 'Diagnostic directory: %s\n' "$out"
# Do not invoke the full core target or overwrite the retained build-tree DSO.
ninja -C "$objects" "${compiled_targets[@]}"
python3 -I - "$repo" "$objects" "$out" "$revision" "$dump_config" "${compiled_targets[@]}" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

repo, objects, out = map(Path, sys.argv[1:4])
name = 'libcamera.so.0.1.0'
relative = 'usr/lib/ciri-libcamera/' + name
target = 'src/libcamera/' + name
command = subprocess.check_output(
    ['ninja', '-C', str(objects), '-t', 'commands', '-s', target], text=True)
args = shlex.split(command)
if (not args or args[0] != str(repo / 'scripts/ciri-libcamera-cross-cc.sh') or
        args.count('-o') != 1 or args[args.index('-o') + 1] != target):
    raise SystemExit('Unexpected retained core link command; no link performed')
args[args.index('-o') + 1] = str(out / name)
args = [arg for arg in args if not arg.startswith('-Wl,-rpath,')]
subprocess.run(args, cwd=objects, check=True)
(out / name).chmod(0o755)

spec = importlib.util.spec_from_file_location(
    'ciri_elf', repo / 'scripts/verify-ciri-libcamera-elf.py')
elf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elf)
runtime = repo / 'build/camera/packages/libcamera/rootfs'
report = elf.verify(runtime)
if report['missing_strong_imports']:
    raise SystemExit('Baseline runtime does not pass static ELF checks')
old = elf.inspect(runtime / relative)
new = elf.inspect(out / name)
if new['soname'] != old['soname'] or new['soname'] != 'libcamera.so.0.1':
    raise SystemExit('Diagnostic core changed its SONAME')
if new['needed'] != old['needed']:
    raise SystemExit('Diagnostic core changed its DT_NEEDED dependencies')
if old['exports'] - new['exports']:
    raise SystemExit('Diagnostic core removed baseline exports')
# Recheck the entire existing closure with only the core replaced. No recovered
# files are copied, loaded or executed; readelf also checks versioned imports.
items = {path: elf.inspect(elf.safe_file(runtime, path))
         for path in report['elf_files']}
# Packaged aliases are independent regular files. Exclude every old-core alias
# by SONAME, not just its full-version path, when checking the replacement.
items = {path: item for path, item in items.items()
         if item['soname'] != old['soname']}
items[relative] = new
providers = set().union(*(item['exports'] for item in items.values()))
missing = {path: sorted(item['imports'] - providers)
           for path, item in items.items() if item['imports'] - providers}
if missing:
    raise SystemExit('Missing strong import providers: ' + repr(missing))
dynamic = subprocess.check_output(['readelf', '-d', str(out / name)], text=True)
if '(RPATH)' in dynamic or '(RUNPATH)' in dynamic:
    raise SystemExit('Build-tree RPATH remains in staged core')

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

report['elf_files'] = {path: item for path, item in report['elf_files'].items()
                       if item['soname'] != old['soname']}
report['elf_files'][relative] = {
    'soname': new['soname'], 'needed': new['needed'],
    'strong_imports': len(new['imports']),
}
report.update({
    'source_revision': sys.argv[4],
    'patch_sha256': digest(repo / 'config/libcamera/ciri.patch'),
    'baseline_core_sha256': digest(runtime / relative),
    'core_sha256': digest(out / name),
    'added_core_imports': sorted(new['imports'] - old['imports']),
    'compiled_targets': sys.argv[6:],
    'p2_separate_plane_buffers': True,
    'dump_config_sha256': digest(repo / sys.argv[5]),
    'link_command': args,
    'ipa_rebuilt': False, 'kernel_rebuilt': False,
    'hardware_tested': False, 'colour': 'unresolved',
})
(out / 'build-info.txt').write_text(json.dumps(report, indent=2) + '\n')
# Operator uses these authored files only in a temporary RAM mount namespace.
for source, dest in (
        ('scripts/run-ciri-libcamera.sh', 'run-libcamera'),
        ('scripts/test-ciri-camera-p1-dump-ram.sh', 'test-p1-dump-ram.sh'),
        ('scripts/test-ciri-camera-colour-bars.sh', 'test-colour-bars.sh'),
        (sys.argv[5], 'dump.cfg')):
    shutil.copyfile(repo / source, out / dest)
    (out / dest).chmod(0o644)
names = (name, 'build-info.txt', 'run-libcamera', 'test-p1-dump-ram.sh',
         'test-colour-bars.sh', 'dump.cfg')
(out / 'SHA256SUMS').write_text(''.join(digest(out / entry) + '  ' + entry + '\n' for entry in names))
print('Built/statically checked diagnostic core:', out)
print('Core SHA256:', report['core_sha256'])
print('No IPA/kernel build, recovered-runtime execution or device installation performed.')
PY
