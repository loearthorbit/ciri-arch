# Reproducing the isolated Ciri libcamera build

This is an incremental cross-build of the matching ChromeOS libcamera fork and its standard `cam` client, with library-side Ciri compatibility changes. It is **not** a clean-room replacement for MediaTek's proprietary camera algorithms, a stock Arch libcamera recipe, or proof of working desktop/webcam integration. Source builds, SDK preparation, runtime packaging, device installation and hardware validation are separate steps.

**Status, 2026-11-07:** core libcamera, libcamera-base, the MTK platform library, generated IPC tools, standard `cam` and the IPA host helper are cross-built. The device owner confirms capture at approximately **18 fps** after the helper-only `RTLD_GLOBAL` update, but the image remains **strongly green**. Both cameras previously enumerated through this stack; the latest brief capture confirmation does not establish a new per-camera or colour-quality result. Optional matching GStreamer `libcamerasrc` and its authored supervisor are also cross-built. The combined streaming static audit passes for 123 ELF files, and all 82 local tests pass. The streaming add-on has not been assembled or installed by the assistant, the matching loopback module is now cross-built using the read-only retained Docker kernel volume but has not been installed or loaded by the assistant, and user-run GStreamer now delivers images through unbuffered `filesink` and loopback to `ffplay`/FFmpeg. The resulting PNG remains strongly green with a dark upper half, matching an older raw-capture preview. Correct colour, sustained rate, clean shutdown and browser integration are not established. See [standard streaming](ciri-camera-streaming.md).

The preparation script defaults to **offline** operation. The commands below are operator instructions, not a claim that all steps have passed.

## Workspace layout and source worktrees

Retained experiment directories have been grouped without deleting their contents:

```text
build/camera/
  repositories/libcamera-cache/libcamera/  # retained Git repository, shared objects
  source/libcamera/                        # pinned detached Git worktree + local patch
  sdk/compiler/                           # original ARM64 SDK and generator cache
  sdk/libcamera/                          # dedicated venv, PLY, libevent, linker staging
  recovery/chromeos-16836.19.0/             # recovered proprietary inputs and manifests
  objects/libcamera                       # link to retained build/ciri-libcamera-meson
  diagnostics/                            # previous client variants, retained as evidence
  kernel/                                 # previous kernel/carrier outputs; not rebuilt
  captures/                               # copied frame sets
  logs/                                   # capture/build logs
  stack/                                  # successful build staging, when available
  packages/libcamera/                     # statically verified isolated package, when available
```

Old random-named paths remain compatibility symlinks for earlier scripts and cached compiler paths. They are not duplicate copies. The physical Meson object directory stays in place because its Ninja rules contain relative source paths; the next authorized build reconfigures it against the new worktree and reuses objects. No cache, backup or diagnostic result was deleted.

The migration is scripted and repeatable:

```sh
python3 -I scripts/organize-ciri-camera-workspace.py          # inspect/dry-run
python3 -I scripts/organize-ciri-camera-workspace.py --apply  # local renames + pinned worktree
```

The source worktree shares Git objects with the retained repository, rather than downloading or cloning another copy. Future independent source experiments should use another detached worktree from that repository and a separate Meson object directory. SDKs, recovered binaries and captures are **not Git source trees** and belong in the named workspace directories, not worktrees. Authored code/patches/scripts/docs stay in this repository; ignored `build/` holds caches and artifacts.

## Exact retained inputs

`config/ciri-camera-client-inputs.json` records the source and original compiler SDK inputs. `config/ciri-libcamera-build-inputs.json` records the additional build SDK inputs.

| Input | Exact retained value |
| --- | --- |
| Source URL | `https://chromium.googlesource.com/chromiumos/third_party/libcamera` |
| Branch | `release-R156-16836.B-mtkisp7` |
| Revision | `a5a75eeabdce349d8f794caacb3177c0e71287d5` |
| ChromeOS recovery version | `16836.19.0` |
| Source checkout | `build/camera/source/libcamera` |
| Original compiler/generator SDK | `build/camera/sdk/compiler` |
| Extracted recovery | `build/camera/recovery/chromeos-16836.19.0` |
| Additional libcamera SDK | `build/camera/sdk/libcamera` |

The branch name identifies provenance, not a floating update policy. Retain/check out the exact revision. SDK preparation does **not** clone source, fetch the original compiler SDK or obtain/extract a recovery image. Those retained inputs must already be supplied. Recovery runtime inputs are under `files/`, with checksums in `SHA256SUMS`; do not treat the recovery image or its programs as trusted host tools.

The original SDK contains separately unpacked Debian/LLVM archives:

- `libc6-dev-arm64-cross`: glibc development files `2.36-8cross1`.
- `linux-libc-dev-arm64-cross`: Linux userspace headers `6.1.4-1cross1`.
- `libgcc-dev`: GCC 12 cross startup objects, `12.2.0-14cross1`.
- `libcxx`: LLVM 22 libc++ development headers, from the exact long-version ARM64 URL in `config/ciri-camera-client-inputs.json`.

That manifest retains each original archive's URL and SHA256. This new preparation script only supplies the additional inputs below; it does not recreate or independently attest the extracted original SDK or recovery.

### Additional approved archives

All four Debian filenames have architecture `arm64`, version `2.1.12-stable-8`, and URL base `https://deb.debian.org/debian/pool/main/libe/libevent/`.

| Filename | SHA256 |
| --- | --- |
| `libevent-dev_2.1.12-stable-8_arm64.deb` | `f2a7d6919016a361174f7dce7e98fad87649d7dddab9b0fb1f9d18101fa58693` |
| `libevent-2.1-7_2.1.12-stable-8_arm64.deb` | `f8abaf5b501fbe8ab4bf0cd1d1be60b6980265f055d65e32fd90f54b4297d387` |
| `libevent-core-2.1-7_2.1.12-stable-8_arm64.deb` | `b2ffdb6e02105a89fb1f306a6736be3cd302cdb225dc6d48f2e623baaf123efe` |
| `libevent-pthreads-2.1-7_2.1.12-stable-8_arm64.deb` | `f2c7078ef6174892055894ff5b7f7b7dd8f7ac83d226c1a5ab20fbea5068b2ec` |
| `ply-3.11-py2.py3-none-any.whl` | `096f9b8350b65ebd2fd1346b12452efe5b9607f7482813ffca50c22722a807ce` |

PLY's pinned metadata URL is `https://pypi.org/pypi/ply/3.11/json`. With explicit download permission, the script selects only the wheel with the exact pinned filename and SHA256; metadata cannot change the trusted hash. The retained wheel URL is:

```text
https://files.pythonhosted.org/packages/a3/58/35da89ee790598a0700ea49b2a66594140f44dec458c07e8e3d4979137fc/ply-3.11-py2.py3-none-any.whl
```

No source distribution, newer release, transitive dependency, libevent OpenSSL/extra runtime, or host package installation is selected. The development archive includes some linker aliases for unselected runtime libraries; their presence is not a reason to download more packages.

## Preparing the dedicated SDK offline

Use a host CPython matching the retained generator cache. The actual original layout is:

```text
build/camera/sdk/compiler/
  header-generator-venv/lib/python3.14/site-packages/
    jinja2/                 jinja2-3.1.6.dist-info/
    yaml/                  pyyaml-6.0.3.dist-info/
    _yaml/
    markupsafe/             markupsafe-3.0.4.dist-info/
```

The retained PyYAML and MarkupSafe modules include **CPython 3.14 x86_64 Linux native extensions**. Do not copy them into a different Python/host ABI. The script checks the minor version and extension suffix, copies these existing modules and metadata rather than searching PyPI, and does not copy the original venv's pip or startup hooks. These retained generator modules do not have new independent archive hashes in the additional-input manifest: retain their original provenance. A machine without that matching cache cannot bootstrap all prerequisites using `--download` alone.

From the repository root:

```sh
python3 -I scripts/prepare-ciri-libcamera-sdk.py --help
python3 -I scripts/prepare-ciri-libcamera-sdk.py
```

The second command verifies retained archive hashes, reuses existing package sysroots, safely extracts missing sysroots from retained `.deb` bytes, and creates/reuses only the additional SDK's `venv/`. PLY is installed as a wheel with isolated pip, `--no-index --no-deps --only-binary=:all: --no-compile`; no source-package setup/build scripts run. The venv uses the host interpreter's bundled `ensurepip`, not a downloaded pip bootstrap. Installing a wheel writes Python files; it does not import PLY during preparation. Later code generation deliberately executes the approved source/generator code as part of the separately invoked build.

Always invoke the preparation script with **`python3 -I`**. Its executable shebang is `#!/usr/bin/env -S python3 -I` for systems supporting `env -S`. `-I` excludes the current/script directory and user Python environment from import search; do not replace it with a plain `python3` invocation from a directory containing downloaded/extracted files. Downloaded archives are untrusted data, not working directories for interpreters or build tools.

Offline mode never attempts metadata lookup, pip indexes or network fallback. A missing input or mismatched hash is an error. Supply the pinned file locally or explicitly choose the network mode below. An existing bad archive is not silently overwritten, even with `--download`.

To reconstruct into a separate output directory from the retained archive cache:

```sh
export CAMERA_SDK="$PWD/build/camera/sdk/compiler"
export LIBCAMERA_STACK_SDK="$PWD/build/ciri-libcamera-sdk-recreated"
python3 -I scripts/prepare-ciri-libcamera-sdk.py \
  --cache-dir "$PWD/build/camera/sdk/libcamera"
```

The default cache remains the manifest's retained SDK directory even when the output is overridden. CLI `--sdk-dir` and `--camera-sdk` take precedence over the corresponding environment variables. `--recreate-venv` removes/recreates **only the dedicated output SDK's `venv/`**, not the original generator venv, archives, sysroots or build objects. Recreate if the existing dedicated venv was made with the wrong interpreter; use a matching host Python first.

Prepared layout:

```text
<LIBCAMERA_STACK_SDK>/
  libevent-dev/{package.deb,sysroot/}
  libevent-2.1-7/{package.deb,sysroot/}
  libevent-core-2.1-7/{package.deb,sysroot/}
  libevent-pthreads-2.1-7/{package.deb,sysroot/}
  ply-3.11-py2.py3-none-any.whl
  venv/
```

Archive hashes authenticate the retained compressed inputs, **not a modified existing extracted sysroot**. To reconstruct a suspect cache, use a new output directory and verified archives rather than reusing that extracted tree. The original compiler SDK, recovery extraction and generator cache remain separate retained trust inputs.

## Explicit network opt-in

Only the following preparation invocation permits downloads:

```sh
python3 -I scripts/prepare-ciri-libcamera-sdk.py --download
```

It downloads **missing** approved inputs only, via HTTPS to the pinned Debian/PyPI hosts (PLY wheel on `files.pythonhosted.org`), verifies the manifest hash before extraction or installation, and prints each requested URL. Each downloaded archive starts in its own newly created empty staging directory. PLY metadata and the wheel also have separate directories. Staging files are cleaned on success/failure; verified archives are retained in the layout above. Nothing is installed through `dpkg`, `apt`, or a package maintainer script.

The Debian extractor reads only the `ar` data archive. It rejects absolute/traversing paths, duplicate members, hardlinks, device/special entries, absolute/parent-traversing symlinks and symlink ancestors of written members; allowed relative links are created last. Extraction has input/member/output size limits, ignores ownership and strips privileged mode bits. The wheel is checked for traversal, links, startup hooks and unexpected top-level contents before pip sees it. These checks reduce extraction hazards; pinned hashes/provenance and a separately reviewed build are still required. **No network preparation was run while implementing this recipe.**

## Toolchain and path overrides

`scripts/ciri-libcamera-cross-cc.sh` targets `aarch64-linux-gnu` using host Clang/Clang++ and LLD. It selects the original SDK's glibc headers/ARM64 startup objects and LLVM 22 **libc++** headers, then links to recovered ARM64 `libc++.so.1.0` and `libc++abi.so.1.0`. GCC 12 contributes startup objects and `libgcc_s`, **not libstdc++**. Do not substitute Arch's default GNU C++ ABI: the recovered C++/IPA dependencies require the matching libc++ boundary. Header/library version selection is deliberate, not a claim that arbitrary LLVM versions or host C++ runtimes are interchangeable.

The wrapper avoids host CRT objects and the SDK's absolute glibc linker script. Recovery linker aliases and libevent linker aliases are build-only staging; they are not a relocatable runtime bundle.

| Variable | Default path relative to repository | Purpose |
| --- | --- | --- |
| `LIBCAMERA_SOURCE` | `build/camera/source/libcamera` | Exact retained checkout |
| `CAMERA_SDK` | `build/camera/sdk/compiler` | Original split ARM64 compiler SDK and generator cache |
| `CAMERA_RECOVERY` | `build/camera/recovery/chromeos-16836.19.0` | Recovery files plus `SHA256SUMS` |
| `LIBCAMERA_STACK_SDK` | `build/camera/sdk/libcamera` | Additional SDK prepared here |
| `LIBCAMERA_STACK_BUILD` | `build/ciri-libcamera-meson` | Incremental Meson/Ninja objects |
| `LIBCAMERA_STACK_OUT` | `build/camera/stack` | Staged build output, not device installation |
| `BUILD_JOBS` | `4` | Ninja parallelism |

Export absolute paths for relocated inputs. Preparation reads `CAMERA_SDK` and `LIBCAMERA_STACK_SDK`; recovery/source/object/output variables are consumed by the build scripts, not downloaded or changed by preparation. The wrapper accepts `CC`/`CXX` when used directly, but the stack build currently unsets them and uses Clang from `PATH`.

## Offline incremental source build

This step **does run source build/code generation**; it is not part of SDK preparation or the validation performed for this change. Required host tools must already be installed: matching Python with venv/ensurepip, Git, Meson, Ninja, Clang/Clang++, LLD, pkg-config, GNU ar and llvm-strip. The build also stages the host's narrow C API headers `/usr/include/yaml.h` and `/usr/include/libudev.h` if not already retained, then checks their exact hashes in `c_api_headers` in the additional-input manifest. On another host, supply the retained matching headers under `<LIBCAMERA_STACK_SDK>/include/`; mismatched host headers are rejected. The observed host versions are Clang 23.1.4, Meson 1.12.1 and Ninja 1.13.2. Host tools and generator modules are not fully archive-pinned, so this is an exact-source/input recipe with retained prerequisites, **not a promise of bit-for-bit builds on arbitrary hosts**.

```sh
# Leave these unset to use retained defaults, or export absolute replacements.
export LIBCAMERA_SOURCE="$PWD/build/camera/source/libcamera"
export CAMERA_SDK="$PWD/build/camera/sdk/compiler"
export CAMERA_RECOVERY="$PWD/build/camera/recovery/chromeos-16836.19.0"
export LIBCAMERA_STACK_SDK="$PWD/build/camera/sdk/libcamera"
export LIBCAMERA_STACK_BUILD="$PWD/build/ciri-libcamera-meson"
export LIBCAMERA_STACK_OUT="$PWD/build/camera/stack"
bash scripts/build-ciri-libcamera-stack.sh
```

The stack build checks the exact source HEAD, original compiler archive hashes, the four libevent archive hashes, PLY's hash, pinned staged C API headers and recovery checksums. It applies/verifies `config/libcamera/ciri.patch`, copies the matching local DMA/gyro additions, isolates pkg-config to the additional SDK and uses Meson's `--wrap-mode=nodownload`. It reconfigures an existing `build.ninja` instead of resetting/cleaning source or objects. Keep unrelated source modifications outside the selected checkout: unexpected changes in the checked `src`/`utils` diff are rejected. Choose a fresh object/output path yourself if deliberately replacing the SDK/toolchain or changing configuration.

Selected build scope is `mtkisp7`, standard `cam`, standalone/non-Android, with udev enabled. `ipas=[]` means **no new IPA module is built**; the matching recovered IPA/proxy and proprietary algorithm/tuning libraries remain runtime requirements. The build stages under `rootfs/` using `DESTDIR` and writes the additional-input manifest/build metadata. It does not deploy files to a USB stick, install host libraries, boot a device or perform a camera capture. Existing staged output is not automatically cleaned; do not treat stale files as new build evidence.

The library-side changes expose separate zero-offset Y/UV DMA allocations for the supported fixed NV12 layout, preserve error status while filling missing successful-frame payload metadata, repair DMA heap open/ioctl handling, and supply four neutral colour gains for otherwise-empty standard-client controls. Face-detection scheduling/hardware streaming and standalone ChromeOS gyro/Mojo integration are deliberately disabled. These compatibility changes are in the library; the old diagnostic probe and its client-side symbol overrides are not prerequisites for a standard `cam` binary.

## Runtime packaging and current limits

The packaging/launcher/installer recipes exist and the source build has completed. Package assembly, precedence, manifests, missing-symbol failure and installer preflight were tested with synthetic fixtures. The assistant's actual runtime assembly was separately blocked for copying recovered proprietary components; it was not bypassed. A real local base package is present, with an existing static report listing 99 ELF files and no missing strong imports. User-run device logs demonstrate installation and enumeration; the device owner subsequently confirms capture at approximately 18 fps after the loader-scope update. The image remains strongly green. **Static imports and enumeration alone are not proof of working capture, and working capture is not proof of correct colour.** The optional [streaming add-on](ciri-camera-streaming.md) preserves this base package rather than recopying proprietary algorithms.

After a successful build, prepare a **fresh** package directory:

```sh
python3 -I scripts/package-ciri-libcamera-stack.py
# Overrides: --built PATH --recovery PATH --sdk PATH --output NEW_DIRECTORY
```

The default is `build/camera/packages/libcamera`. An existing output is refused, not overwritten/backed up. The output must be separate from all retained input directories. Packaging checks build/recovery/archive checksums, rejects malformed/duplicate/escaping manifest paths, requires each copied built/recovered ELF and camera-data file to be covered by its input checksums, copies real ELF files (no build-machine symlinks), chooses the new libraries and thread-enabled libevent first, retains recovered IPA/algorithm/tuning/configuration inputs and the IPA signature, generates SONAME aliases, checks AArch64 DT_NEEDED closure and strong versioned-symbol provider presence, and writes `elf-report.json` plus `SHA256SUMS`. Missing dependencies/imports are fatal; partial failed output is retained for inspection and is not installable. Static provider checks **do not prove C++ layouts, dlopen scopes, ISP tuning or correct colours**.

The standard client owns camera configuration, buffers and requests: `cam`, or the optional matching GStreamer `libcamerasrc`. `ciri-libcamera-host` is a small process supervisor: it hosts the recovered `ipaRun(int)` worker, starts an allowlisted standard program through the recovered loader, and reaps its own children. It is not another capture application. Generated IPC code is still built even though the proprietary IPA itself is reused.

### Helper-only IPA loader-scope update

The earlier failure was `Failed to create IPA interface instance`, followed by a broken pipe. The recovered worker separately loads the IPA and casts its interface using C++ RTTI. The old diagnostic linked libcamera at startup; the new supervisor initially loaded the worker and its libcamera dependencies with `RTLD_LOCAL`. This can leave interface RTTI in separate loader groups. The supervisor now explicitly selects `RTLD_GLOBAL`; the old diagnostic keeps its default local scope. No interface cast is bypassed, and no capture loop is added. A native mock test reproduces the RTTI-address mismatch. The device owner's subsequent successful capture is consistent with this correction, although no detailed post-fix hardware log was supplied. The scope change does not resolve colour.

Build **only the authored helper**, reusing the retained SDK and runtime libraries:

```sh
bash scripts/build-ciri-libcamera-host.sh
# Fresh output override: LIBCAMERA_HOST_OUT=/absolute/new/directory
# Baseline override: LIBCAMERA_HOST_BASELINE=/path/to/original/ciri-libcamera-host
```

This does not run Meson/Ninja, rebuild libraries/kernel, download inputs, copy proprietary components or execute ARM64 code. It uses the same compiler/linker arguments as the full build. The baseline must match the delivered helper SHA256 `1e300cd036fc30ae5121a18ea79c5bdda5e70730ced8619be0e3ef0cad2f2b45`. Static checks require ELF64 AArch64 and unchanged DT_NEEDED; any new strong imports must have matching versioned exports in the retained recovered libc. These are not hardware validation. The default output `build/camera/updates/ipa-scope` already holds the historical scope-only kit, so another build requires a fresh output override. Current helper source additionally supports `--gst-launch`/`--gst-inspect`, introducing the verified import `strcmp@GLIBC_2.17`. The latest build is under `build/camera/updates/gstreamer-host`; the streaming add-on checks its full combined closure and uses the streaming installer, which recognizes the earlier installed scope helper.

The commands below are for the **existing historical scope-only kit**, not the latest GStreamer helper. For streaming, use the [add-on workflow](ciri-camera-streaming.md#assemble-the-add-on-on-the-build-host), not the helper-only updater. If the original scope correction is still required, stop any capture session, copy that historical kit to Ciri, then **run the update yourself**:

```sh
# On the build host (substitute the actual SSH destination):
scp -r build/camera/updates/ipa-scope USER@CIRI:~/
# On Ciri:
sudo bash ~/ipa-scope/install-host-update.sh
```

The updater defaults to `/opt/ciri-libcamera`; an explicitly supplied second argument can instead name the existing prefix on a mounted Ciri root:

```sh
sudo bash scripts/update-ciri-libcamera-host.sh \
  build/camera/updates/ipa-scope /run/media/jono/ARCHCIRI/opt/ciri-libcamera
```

It verifies kit checksums, every installed checksum entry, the passing existing ELF report, the replacement architecture and the expected old/new helper identity. It refuses symlinked runtime paths. Only `rootfs/usr/bin/ciri-libcamera-host` is replaced, via a flushed temporary file and atomic rename, followed by a checksum-manifest update and final verification/sync. It makes **no backups** and touches no libraries, camera data, kernel, firmware, boot files, audio or services. The original full-build metadata remains unchanged; retain the update kit's `build-info.txt` as delta provenance. If replacement succeeds but the manifest update/final sync fails, failure is reported; rerunning can repair the recognized new-helper/old-manifest state, provided all other files still match. Do not rebuild or recopy the entire runtime for this change.

The historical scope-only replacement SHA256 is `8c445f81219a0a631dd3c49607c63714720027ae2c89f5890e0e6c609b0816f5`. The device owner subsequently confirms working standard `cam` capture at approximately 18 fps, with a strong green tint. No automatic device update or test was performed by the assistant. Retain logs and late-frame metadata when investigating colour.

### User-run installation and capture

**Do not run until the package has successfully passed its checks.** The installer is for the mounted Ciri root (`/run/media/jono/ARCHCIRI`, pinned UUID `9d00d9e7-22f1-4f32-8006-19dae6343c92`):

```sh
sudo bash scripts/install-ciri-libcamera-usb.sh
# Optional single argument: package directory
```

It validates manifest coverage, checksums, package contents and filesystem identity, checks pending filesystem write errors, then creates a new `/opt/ciri-libcamera`. It refuses an existing destination, makes no old-client backups, and does not touch `/opt/ciri-camera`, system libraries, kernel, boot files, firmware, audio or services. Nothing here has been installed automatically. Safely eject the USB filesystems before unplugging.

After booting Ciri, the intended standard-client commands are:

```sh
sudo /opt/ciri-libcamera/run-libcamera --list
# cam camera indices start at 1 (the old diagnostic started at 0).
sudo /opt/ciri-libcamera/run-libcamera --camera 1 \
  --stream role=viewfinder,width=640,height=480,pixelformat=NV12 \
  --strict-formats --capture=128 --metadata --file='frame-#.nv12'
```

The launcher prints a fresh `/opt/ciri-libcamera/captures/run.*` output directory. `cam` saves every requested frame; inspect late frames for warm-up rather than assuming automatic convergence. Confirm the printed layout, size and stride before decoding. For the expected 640×480, stride-640 NV12 layout, each file should be 460800 bytes:

```sh
ffmpeg -n -f rawvideo -pixel_format nv12 -video_size 640x480 \
  -i frame-CAMERA-STREAM-SEQUENCE.nv12 -frames:v 1 frame.png
```

Replace the input with an actual filename. Do not force chroma offsets/swaps/rescaling to disguise the existing tint. Keep metadata/logs and compare both cameras.

The runtime uses private mount namespace/chroot paths, private `/run` and `/tmp`, private model `ciri`, and read-only host `/run/udev/data` (not the control socket). New library search order is `/usr/lib/ciri-libcamera:/usr/lib64:/lib64:/usr/share/cros-camera/libfs` **inside the isolated root only**. It loads heap modules if required but starts no ChromeOS services and sets no global library paths. This is path/library isolation, **not a security sandbox**. Never globally replace Arch's libcamera or loader with these artifacts.

The default build disables GStreamer; it is optionally enabled with `LIBCAMERA_GSTREAMER=enabled` using the [pinned streaming SDK/recipe](ciri-camera-streaming.md). qcam/Qt, Android integration, Python bindings, the V4L2 compatibility layer, lc-compliance, upstream tests, tracing and generated documentation remain excluded. The V4L2 compatibility layer is not needed for GStreamer's standard `v4l2sink`. No PipeWire, browser, GNOME or third-party application compatibility has been demonstrated. Gyro and face detection are intentionally unavailable here; firmware, kernel/media topology and camera permissions are separate bring-up prerequisites. Proprietary recovered files are not made open source by rebuilding libcamera, and recovery redistribution rights remain unresolved; keep blobs out of Git and review notices/licences before redistributing any bundle.

**Colour remains unresolved.** Avoiding the upstream three-initializer/zero-blue fallback with four unity gains is a specific defect fix, not validated AWB/AE, tuning, colour-space/range correctness, or a calibrated image-quality result. Successful enumeration, compilation, request completion or frame-file output must not be reported as proof of correct colour or production-ready capture. Hardware capture and colour validation are still separate work.
