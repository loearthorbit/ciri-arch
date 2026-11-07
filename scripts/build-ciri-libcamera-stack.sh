#!/bin/bash
# Offline incremental build of the matching libcamera fork and standard cam.
# Approved prerequisites are retained separately; this script downloads nothing.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source=${LIBCAMERA_SOURCE:-$repo/build/camera/source/libcamera}
sdk=${CAMERA_SDK:-$repo/build/camera/sdk/compiler}
recovery=${CAMERA_RECOVERY:-$repo/build/camera/recovery/chromeos-16836.19.0}
stack_sdk=${LIBCAMERA_STACK_SDK:-$repo/build/camera/sdk/libcamera}
objects=${LIBCAMERA_STACK_BUILD:-$repo/build/ciri-libcamera-meson}
out=${LIBCAMERA_STACK_OUT:-$repo/build/camera/stack}
gstreamer=${LIBCAMERA_GSTREAMER:-disabled}
[[ $gstreamer == disabled || $gstreamer == enabled ]] || { printf 'LIBCAMERA_GSTREAMER must be enabled or disabled\n' >&2; exit 1; }
gst_sdk=${GSTREAMER_SDK:-$repo/build/camera/sdk/gstreamer}
revision=a5a75eeabdce349d8f794caacb3177c0e71287d5
[[ $(git -C "$source" rev-parse HEAD) == "$revision" ]]
python3 -I - "$stack_sdk" "$repo/config/ciri-libcamera-build-inputs.json" "$sdk" "$repo/config/ciri-camera-client-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root=Path(sys.argv[1]); inputs=json.loads(Path(sys.argv[2]).read_text())
for item in inputs['libevent_packages']:
    with (root/item['name']/'package.deb').open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual != item['sha256']: raise SystemExit('libevent input checksum mismatch: '+item['name'])
with (root/inputs['ply']['filename']).open('rb') as stream:
    actual=hashlib.file_digest(stream,'sha256').hexdigest()
if actual != inputs['ply']['sha256']: raise SystemExit('PLY input checksum mismatch')
sdk=Path(sys.argv[3]); compiler_inputs=json.loads(Path(sys.argv[4]).read_text())
for item in compiler_inputs['sdk_packages']:
    filename=item['url'].rsplit('/',1)[-1]
    with (sdk/item['directory']/filename).open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual != item['sha256']: raise SystemExit('Compiler input checksum mismatch: '+filename)
PY
(cd "$recovery/files"; sha256sum --check --status "$recovery/SHA256SUMS")
# Apply only our pinned, reviewable library patch. Never clean or reset source.
if git -C "$source" apply --check "$repo/config/libcamera/ciri.patch" 2>/dev/null; then
    git -C "$source" apply "$repo/config/libcamera/ciri.patch"
else
    git -C "$source" apply --check --reverse "$repo/config/libcamera/ciri.patch"
fi
python3 -I - "$source" "$repo/config/libcamera/ciri.patch" <<'PY'
from pathlib import Path
import subprocess, sys
actual = subprocess.check_output(['git', '-C', sys.argv[1], 'diff', '--', 'src', 'utils'], text=True)
expected = Path(sys.argv[2]).read_text()
# Patch editors may remove the single context marker from a blank context line;
# git apply accepts either form. All nonblank patch lines must still match exactly.
def canonical(text):
    return [line if line != ' ' else '' for line in text.splitlines()]
if canonical(actual) != canonical(expected):
    raise SystemExit('Source diff differs from the checked Ciri patch')
PY
for pair in 'ciri_dma_buffers.h:include/libcamera/internal/ciri_dma_buffers.h' \
            'ciri_gyro_sensor.cpp:src/libcamera/ciri_gyro_sensor.cpp'; do
    input=${pair%%:*}; target=${pair#*:}
    if [[ -e $source/$target ]]; then
        cmp "$repo/config/libcamera/$input" "$source/$target"
    else
        cp "$repo/config/libcamera/$input" "$source/$target"
    fi
done
mkdir -p "$stack_sdk/include" "$stack_sdk/lib" "$stack_sdk/pkgconfig"
# Stage narrow C API headers, never the entire host include tree.
for header in yaml.h libudev.h; do
    if [[ ! -e $stack_sdk/include/$header ]]; then
        cp "/usr/include/$header" "$stack_sdk/include/$header"
    fi
done
if [[ ! -e $stack_sdk/include/event2 ]]; then
    cp -a "$stack_sdk/libevent-dev/sysroot/usr/include/event2" "$stack_sdk/include/"
fi
python3 -I - "$stack_sdk" "$repo/config/ciri-libcamera-build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root=Path(sys.argv[1]); inputs=json.loads(Path(sys.argv[2]).read_text())
for name, expected in inputs['c_api_headers'].items():
    with (root/'include'/name).open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual != expected: raise SystemExit('C API header differs from pinned staging: '+name)
PY
# Reuse the retained generator packages in the dedicated new venv.
python3 -I - "$sdk" "$stack_sdk" "$recovery" <<'PY'
from pathlib import Path
import shutil, subprocess, sys
sdk, root, recovery=map(Path,sys.argv[1:])
site=Path(subprocess.check_output([str(root/'venv/bin/python'),'-I','-c',
    'import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
old=next((sdk/'header-generator-venv/lib').glob('python*/site-packages'))
for module in ('jinja2','yaml','markupsafe'):
    target=site/module
    if not target.exists(): shutil.copytree(old/module,target)
# Build-only linker aliases. Runtime packaging copies real ELF files, not these.
for directory in (recovery/'files/usr/lib64', recovery/'files/lib64'):
    for path in directory.glob('*.so*'):
        if not path.is_file(): continue
        names={path.name, path.name.split('.so',1)[0]+'.so'}
        for name in names:
            target=root/'lib'/name
            if not target.exists(): target.symlink_to(path.resolve())
for package in ('libevent-2.1-7','libevent-core-2.1-7','libevent-pthreads-2.1-7'):
    for path in (root/package/'sysroot/usr/lib/aarch64-linux-gnu').glob('*.so*'):
        if not path.is_file(): continue
        names={path.name, path.name.split('.so',1)[0]+'.so'}
        for name in names:
            target=root/'lib'/name
            if target.is_symlink(): target.unlink()
            elif target.exists(): raise SystemExit('Unexpected linker staging file: '+str(target))
            target.symlink_to(path.resolve())
for path in (root/'libevent-dev/sysroot/usr/lib/aarch64-linux-gnu').glob('*.so'):
    if path.name not in ('libevent.so','libevent_core.so','libevent_pthreads.so'):
        continue
    if path.is_symlink():
        wanted=root/'lib'/path.readlink().name
        if not wanted.exists(): raise SystemExit('Missing approved libevent provider: '+str(wanted))
        target=root/'lib'/path.name
        if target.is_symlink(): target.unlink()
        elif target.exists(): raise SystemExit('Unexpected linker staging file: '+str(target))
        target.symlink_to(wanted.name)
for name, version, libs, requires in (
    ('yaml-0.1','0.2.5','-lyaml',''),
    ('libudev','1','-ludev',''),
    ('libevent','2.1.12','-levent',''),
    ('libevent_pthreads','2.1.12','-levent_pthreads','libevent')):
    (root/'pkgconfig'/(name+'.pc')).write_text(
        f'prefix={root}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n'
        f'Name: {name}\nDescription: Isolated Ciri cross dependency\nVersion: {version}\n'
        f'Requires: {requires}\nLibs: -L${{libdir}} {libs}\nCflags: -I${{includedir}}\n')
PY
export CAMERA_SDK="$sdk" CAMERA_RECOVERY="$recovery" LIBCAMERA_STACK_SDK="$stack_sdk"
export PATH="$stack_sdk/venv/bin:$PATH"
export PKG_CONFIG_LIBDIR="$stack_sdk/pkgconfig" PKG_CONFIG_PATH=
if [[ $gstreamer == enabled ]]; then
    [[ -d $gst_sdk/pkgconfig ]] || { printf 'Prepare the GStreamer SDK first.\n' >&2; exit 1; }
    python3 -I - "$gst_sdk" "$repo/config/ciri-gstreamer-inputs.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
for item in json.loads(Path(sys.argv[2]).read_text())['packages']:
    with (root / item['name'] / 'package.deb').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
            raise SystemExit('Streaming archive checksum mismatch: ' + item['name'])
PY
    export GSTREAMER_SDK="$gst_sdk"
    export PKG_CONFIG_LIBDIR="$PKG_CONFIG_LIBDIR:$gst_sdk/pkgconfig"
else
    unset GSTREAMER_SDK
fi
export PYTHONNOUSERSITE=1
unset CC CXX
chmod +x "$repo/scripts/ciri-libcamera-cross-cc.sh"
python3 -I - "$repo" "$stack_sdk" "$PKG_CONFIG_LIBDIR" <<'PY'
from pathlib import Path
import sys
repo, root=map(Path,sys.argv[1:3])
wrapper=str(repo/'scripts/ciri-libcamera-cross-cc.sh')
(root/'cross.ini').write_text(
    f"[binaries]\nc = ['{wrapper}', 'c']\ncpp = ['{wrapper}', 'cpp']\n"
    "pkg-config = '/usr/bin/pkg-config'\nar = '/usr/bin/ar'\nstrip = '/usr/bin/llvm-strip'\n"
    "[host_machine]\nsystem = 'linux'\ncpu_family = 'aarch64'\ncpu = 'armv8-a'\nendian = 'little'\n"
    f"[properties]\nneeds_exe_wrapper = true\npkg_config_libdir = '{sys.argv[3]}'\n")
PY
args=(--cross-file "$stack_sdk/cross.ini" --wrap-mode=nodownload
    --prefix=/usr --libdir=lib/ciri-libcamera --libexecdir=libexec/ciri-libcamera
    -Dpipelines=mtkisp7 -Dipas=[] -Dandroid=disabled -Dcam=enabled
    -Dqcam=disabled -Dlc-compliance=disabled -Ddocumentation=disabled
    -Dgstreamer="$gstreamer" -Dpycamera=disabled -Dtracing=disabled -Dudev=enabled
    -Dtest=false -Dv4l2=false -Db_lundef=false -Db_pie=true -Dbuildtype=release)
if [[ -f $objects/build.ninja ]]; then
    meson setup --reconfigure "$objects" "$source" "${args[@]}"
else
    meson setup "$objects" "$source" "${args[@]}"
fi
ninja -C "$objects" -j"${BUILD_JOBS:-4}"
mkdir -p "$out"
DESTDIR="$out/rootfs" meson install -C "$objects" --no-rebuild
# The only custom executable is an IPA process host; cam owns all capture logic.
"$repo/scripts/ciri-libcamera-cross-cc.sh" cpp -std=c++17 -O2 -Wall -Wextra -Werror \
    -pthread -fPIE -pie -Wl,--dynamic-linker=/lib64/ld-linux-aarch64.so.1 \
    -Wl,--eh-frame-hdr,-z,relro,-z,now "$repo/scripts/ciri-libcamera-host.cpp" \
    -o "$out/rootfs/usr/bin/ciri-libcamera-host"
cp "$repo/config/ciri-libcamera-build-inputs.json" "$out/build-inputs.json"
if [[ $gstreamer == enabled ]]; then cp "$repo/config/ciri-gstreamer-inputs.json" "$out/gstreamer-inputs.json"; fi
printf 'source=https://chromium.googlesource.com/chromiumos/third_party/libcamera\nbranch=release-R156-16836.B-mtkisp7\nrevision=%s\narchitecture=AArch64\npipeline=mtkisp7\nclient=cam\nproprietary-components=recovered-runtime-libraries\ngyro=disabled\nface-detection=disabled\ncolour=not-hardware-verified\n' "$revision" > "$out/build-info.txt"
printf 'gstreamer=%s\n' "$gstreamer" >> "$out/build-info.txt"
for tool in clang meson ninja; do
    printf '\n%s: ' "$tool" >> "$out/build-info.txt"
    "$tool" --version >> "$out/build-info.txt"
done
sha256sum "$repo/config/libcamera/ciri.patch" "$repo/config/libcamera/ciri_dma_buffers.h" \
    "$repo/config/libcamera/ciri_gyro_sensor.cpp" "$repo/scripts/ciri-libcamera-host.cpp" \
    "$repo/scripts/ciri-camera-ipa-worker.h" "$repo/scripts/ciri-libcamera-cross-cc.sh" \
    "$repo/scripts/build-ciri-libcamera-stack.sh" >> "$out/build-info.txt"
python3 -I - "$out" <<'PY'
from pathlib import Path
import hashlib, sys
root=Path(sys.argv[1]).resolve()
rows=[]
for path in sorted(root.rglob('*')):
    if path.name == 'SHA256SUMS' or not path.is_file(): continue
    if not path.resolve().is_relative_to(root): raise SystemExit('Installed path escapes staging root')
    with path.open('rb') as stream:
        sha=hashlib.file_digest(stream,'sha256').hexdigest()
    rows.append(f'{sha}  {path.relative_to(root)}\n')
(root/'SHA256SUMS').write_text(''.join(rows))
PY
printf '\nBuilt/staged: %s\nNext: package-ciri-libcamera-stack.py performs runtime ELF checks before delivery.\n' "$out"
