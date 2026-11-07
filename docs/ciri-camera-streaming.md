# Standard Ciri camera streaming

Expose the working isolated camera stack to ordinary V4L2 camera clients, without replacing Arch's libcamera or writing another capture application:

```text
matching libcamerasrc → bounded leaky queue → videoconvert → YUY2 → filesink
                                                                    ↓
                                                              v4l2loopback
                                                                    ↓
                                                        desktop/browser client
```

This is an interoperability bridge, **not a colour correction** or a modern libcamera ABI port. The proprietary runtime remains private under `/opt/ciri-libcamera`. Nothing here replaces the kernel, firmware, audio setup, Arch libraries or `/opt/ciri-camera`.

## Verified status — 2026-11-07

- The device owner confirms standard `cam` capture at approximately **18 fps**, with a **strong green tint**, after the IPA loader-scope update.
- The matching ChromiumOS fork's `libgstlibcamera.so` is cross-built, reusing retained Meson/Ninja objects. No kernel rebuild was needed.
- The latest authored supervisor is separately cross-built and allows only standard `cam`, `gst-launch-1.0` and `gst-inspect-1.0`.
- A **read-only** composed base/add-on audit passes for **123 ELF files**, with no missing strong versioned imports. No runtime files were copied by that check.
- **82 local tests pass**, including synthetic add-on assembly/installation, core preservation, idempotence, partial-manifest repair, final-sync failure, path/checksum refusal, SDK alias filtering and actual-driver/capability-query fixtures. Bash and Python syntax checks pass.
- The original **MMAP `v4l2sink` buffer pool failed** with “buffer 0 was not queued.” Shutdown then hit a libcamera request-tracking assertion. The subsequent RW attempt stayed alive but printed **“implement write()”**: GStreamer 1.22's output RW branch only warns and returns success without writing. The kernel's support for `write()` does not make that GStreamer branch implemented.
- The user-run replacement **`filesink buffer-mode=unbuffered`** now delivers images through loopback to `ffplay` and FFmpeg. The host `v4l2-ctl` sets 640×480 YUYV and the requested rate before streaming. The trace shows single-iovec writes returning **614400 bytes**. The authored launcher now matches that tested command; the assistant has not assembled, installed or updated the real add-on.
- The saved loopback PNG is **strongly green**, with detail in a much darker upper half. An older raw-capture preview has the same appearance, so this is not simply half the frame lost in the new bridge. The owner reports auto exposure responding; AWB, colour/calibration, full image correctness, sustained rate, clean shutdown and browser discovery remain unverified.
- `v4l2loopback` **0.15.4 is now cross-built** for `7.3.0-rc4-ciri`, using the retained Docker GCC 14 toolchain and complete matching kernel objects. The kernel/source volume was mounted **read-only**; only the external module was compiled. The four-file kit passed checksums, ELF64/AArch64, vermagic and installer preflight. The device owner's logs now show module installation/loading and a loopback output at `/dev/video115` in that run. The initial pre-install `sync` failed with an I/O error; a later successful retry does not establish storage health. The complete kernel object tree remains remote, not in the local output-only kernel directories.

Static import checks do not prove loader scopes, C++ layout compatibility, camera operation or image quality. A synthetic module-header test is not evidence that a real kernel module compiles or loads.

## Retained inputs and outputs

See [the base build guide](ciri-libcamera-build.md) for the compiler SDK, recovered runtime and existing package. Streaming adds:

| Path | Purpose |
| --- | --- |
| `config/ciri-gstreamer-inputs.json` | Exact Debian ARM64 filenames, versions, pool paths and SHA256 pins |
| `build/camera/sdk/gstreamer/` | Retained archives, separately extracted package sysroots and build-only aliases |
| `build/camera/stack-streaming/` | Successful GStreamer-enabled source staging |
| `build/camera/updates/gstreamer-host/` | Latest separately compiled authored supervisor and provenance |
| `build/camera/packages/libcamera/` | Existing checked base package; remains unchanged |
| `build/camera/packages/gstreamer-addon/` | Proposed fresh add-on output; not yet assembled |
| `build/camera/source/v4l2loopback/` | Pinned local module source worktree, sharing retained Git objects |
| `build/camera/kernel/v4l2loopback/` | Built, checked four-file module install kit |
| `build/camera/kernel/v4l2loopback-build-records/` | Separate Docker configuration, recipe, result and build log |

The source remains ChromiumOS libcamera revision `a5a75eeabdce349d8f794caacb3177c0e71287d5`, branch `release-R156-16836.B-mtkisp7`. GStreamer core/base/good inputs are Debian bookworm **1.22.0** with exact package revisions recorded in the JSON. Build headers use GLib **2.74.6**; the runtime preserves its recovered GLib **2.76.4** provider. Missing matching GObject/GModule/libffi support may be added from recovery; proprietary algorithms are not recopied into this add-on.

Verified current binary SHA256 values:

```text
a8ea9956796430c0430ebd3bdfc4fe81b3c2cb972968486123a5cc58caab8e20  gstreamer-host/ciri-libcamera-host
f5403550b8e8fef0b8332d4f35ee82638a0e52bcb49c20e994a4bb4c7209a630  gstreamer-1.0/libgstlibcamera.so
```

The helper embedded in `stack-streaming` predates the latest GStreamer program selection. The add-on planner deliberately takes its helper from the separately verified `gstreamer-host` kit, not that older staging file.

## Optional rebuild — not needed for the retained binaries

Run from this repository. Preparation is offline by default:

```sh
python3 -I scripts/prepare-ciri-gstreamer-sdk.py
# Only when deliberately obtaining missing pinned inputs:
# python3 -I scripts/prepare-ciri-gstreamer-sdk.py --download
```

Preparation does not run Debian maintainer scripts, install host packages or execute ARM64 programs. Each archive is extracted separately; only actual ELF libraries become build-only aliases. Existing extracted inputs are retained trust inputs: archive pins alone do not authenticate later modifications to a sysroot.

To deliberately rebuild the exact ChromiumOS source with GStreamer enabled:

```sh
LIBCAMERA_GSTREAMER=enabled \
  LIBCAMERA_STACK_OUT="$PWD/build/camera/stack-streaming" \
  bash scripts/build-ciri-libcamera-stack.sh
```

This executes the named external source/code generators. It reconfigures the existing object tree rather than cleaning it, uses controlled pkg-config paths and `--wrap-mode=nodownload`, and retains `pipelines=mtkisp7`, `ipas=[]` and standalone settings. GStreamer's `v4l2sink` does **not** require libcamera's `-Dv4l2=true` compatibility layer.

The saved Ciri patch includes a narrow GStreamer allocation-error cleanup and a plugin-local Clang warning adjustment. The source plugin wraps the existing allocator's independent Y and UV FDs as separate `GstMemory` objects. Logical UV buffer offset is 307200, while each FD's own plane offset stays zero. This matches the fixed 640×480, stride-640 NV12 layout; the old plugin lacks general padded-stride/GstVideoMeta support, so do not broaden formats blindly.

If rebuilding only the authored supervisor, choose a fresh kit directory:

```sh
LIBCAMERA_HOST_OUT="$PWD/build/camera/updates/gstreamer-host-new" \
  bash scripts/build-ciri-libcamera-host.sh
```

This does not rebuild external libcamera or the kernel. It checks unchanged DT_NEEDED and any new strong imports against the retained recovered libc; current source adds `strcmp@GLIBC_2.17`. Pass `--host-package "$PWD/build/camera/updates/gstreamer-host-new"` to the planner when using that new kit. **Do not use its historical helper-only installer to upgrade an already installed scope-only helper; use the streaming installer below.**

## Assemble the add-on on the build host

The default command is **check only**:

```sh
python3 -I scripts/package-ciri-gstreamer.py
```

To explicitly assemble a fresh local kit yourself:

```sh
python3 -I scripts/package-ciri-gstreamer.py --assemble
# Optional: --built PATH --base PATH --sdk PATH --recovery PATH
#           --host-package PATH --output NEW_DIRECTORY
```

The existing base package and every retained input remain unchanged. The planner checks manifests and pinned archives, overlays only the new supervisor/tools/plugin, preserves baseline library providers, selects `coreelements`, `videoconvertscale` and `video4linux2`, resolves their dependency closure, and fails on missing strong imports. It does not bundle unrelated audio, display, network or encoding plugin sets.

Assembly copies the selected new/support files, notices, current launchers and patch, then writes the expected base inventory, combined ELF report, input/build metadata, installer and `SHA256SUMS`. An existing or input-overlapping output is refused. Proprietary IPA/algorithms/tuning are neither duplicated nor replaced.

Keep recovered blobs out of Git. Review licences and recovery redistribution rights before distributing any package, including its base-runtime prerequisite.

## Install the add-on yourself on Ciri

Stop existing camera captures. Copy only the newly assembled kit to the intended Ciri device, substituting its actual SSH destination:

```sh
# Build host; performed by the operator, not automatically:
scp -r build/camera/packages/gstreamer-addon USER@CIRI:~/

# Booted Ciri:
sudo bash ~/gstreamer-addon/install-streaming.sh
```

The default runtime prefix is `/opt/ciri-libcamera`. For an explicitly chosen mounted root, a second installer argument may name its existing isolated prefix:

```sh
sudo bash scripts/install-ciri-gstreamer.sh \
  build/camera/packages/gstreamer-addon /path/to/mounted-root/opt/ciri-libcamera
```

The installer checks the kit's exact inventory/checksums, combined report and expected installed base. It recognizes the original and historical scope-only helper. Only the helper and `run-libcamera` may change among baseline files; baseline libraries, algorithms and data must retain their expected bytes. New files are staged, flushed and atomically replaced, with the installed checksum manifest merged and verified. No backups are made. A failed update is reported; recognized same-kit partial installation can be repaired by rerunning it after investigating filesystem errors.

No module is loaded, camera stream started, service enabled, kernel flashed or audio setup changed by add-on installation.

## Matching loopback module prerequisite

First check the **booted Ciri**, not the build host:

```sh
uname -r
modinfo v4l2loopback
```

If a compatible module already exists for the running kernel, do not install a second copy. If unavailable, the module recipe is pinned to:

- Module source: `https://github.com/v4l2loopback/v4l2loopback`, tag `v0.15.4`, revision `0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4`.
- Kernel source: `https://github.com/mkopec/linux.git`, revision `b8f841230b1615aa58b5d4389e11bfe66abbd993`.
- Kernel release: **`7.3.0-rc4-ciri`**.

Use an existing module repository/worktree if available. Only if no retained copy exists, the operator may explicitly obtain the pinned source and create a worktree:

```sh
git clone --no-checkout --branch v0.15.4 --depth 1 \
  https://github.com/v4l2loopback/v4l2loopback \
  "$PWD/build/camera/repositories/v4l2loopback"
git -C "$PWD/build/camera/repositories/v4l2loopback" worktree add --detach \
  "$PWD/build/camera/source/v4l2loopback" \
  0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4
```

The complete **matching retained kernel build directory** is required, including `Module.symvers`, `include/generated/autoconf.h` and `include/config/kernel.release`, with the original configuration/toolchain. Output-only kernel packages, stock Arch headers, the separate Submarine kernel and `modules_prepare` alone are not substitutes. Do not rebuild a full kernel speculatively or reuse an unrelated symbol table.

Once those inputs are actually available, deliberately build only the external module:

```sh
CIRI_KERNEL_SOURCE=/absolute/path/to/retained/mkopec-linux \
  CIRI_KERNEL_BUILD=/absolute/path/to/complete/matching/kernel-objects \
  bash scripts/build-ciri-v4l2loopback.sh
# Optional: V4L2LOOPBACK_SOURCE, V4L2LOOPBACK_OUT
# KERNEL_MAKE_ARGS selects the exact retained toolchain.
# Default: CROSS_COMPILE=aarch64-linux-gnu-; LOCALVERSION=-ciri is preserved.
```

The script checks source revisions, required objects, release and module architecture/vermagic, then writes a fresh checksummed module kit. It performs no kernel installation or module loading. Compilation against this kernel has now succeeded in the retained Docker toolchain.

For the existing remote kernel objects, the reproducible module-only runner is:

```sh
python3 -I scripts/build-ciri-v4l2loopback-remote.py
# Existing output is refused; choose --output /absolute/new/kit when rebuilding.
# Optional: --docker-url, --retained-build, --source-repository
```

It requires the pinned loopback Git repository already fetched locally and the forwarded Docker endpoint already open. It snapshots only the retained container's dependency/toolchain layer, mounts the existing kernel/source volume read-only, creates a separate module volume/worktree, and runs the authored module builder with networking disabled. No kernel or dependency downloads, kernel rebuild, installation, module loading or automatic cleanup occur. Build records are retained separately from the four-file install kit.

Verified module SHA256: `779cdf0681d6c627a88ea4dc37173d84336e0589ec8223611234de01a0bc00a3`.
Verified vermagic: `7.3.0-rc4-ciri SMP preempt mod_unload aarch64`.
User-run module loading is now demonstrated; sustained end-to-end streaming remains unverified.

Only after a successful build, the operator can transfer and install the kit:

```sh
# Build host:
scp -r build/camera/kernel/v4l2loopback USER@CIRI:~/
# Booted Ciri, with the matching kernel and no loaded loopback module:
sudo bash ~/v4l2loopback/install-loopback.sh
```

The installer checks exact kit coverage, checksums, pinned provenance, ELF64/AArch64, module name and running-kernel vermagic. It refuses an existing destination and a loaded module, stages a new file and publishes it without overwrite under `/usr/lib/modules/7.3.0-rc4-ciri/updates/ciri-camera/`, then runs `depmod`. It does not replace a kernel or load a module. If `depmod`/sync fails, an installed file may remain; inspect before retrying. No automatic rollback or backups are performed.

## Create the output device and stream

These are **operator-run commands on Ciri**, only after the add-on and matching loopback module are ready. `/dev/video42` may already be a physical camera/ISP node; never assume it is free. First inspect any already loaded loopback module rather than reconfiguring it blindly. With no loopback module loaded, select a free video number:

```sh
nr=100
while [ "$nr" -lt 256 ] && [ -e "/dev/video$nr" ]; do nr=$((nr + 1)); done
[ "$nr" -lt 256 ] &&
sudo modprobe v4l2loopback video_nr="$nr" card_label="Ciri camera" exclusive_caps=1 &&
sudo /opt/ciri-libcamera/stream-camera back "/dev/video$nr" 18
# Alternatively, stop the back stream first, then:
# sudo /opt/ciri-libcamera/stream-camera front "/dev/video$nr" 18
```

If all candidate numbers 100–255 are occupied, the bound check stops without loading anything; inspect the device list rather than overwriting a node.

The loopback node is created at runtime and disappears on reboot. No automatic loading/service is configured. With no loopback module already loaded and node 115 free, the owner can recreate the observed node and inspect it:

```sh
test ! -e /dev/video115 &&
  sudo modprobe v4l2loopback video_nr=115 card_label="Ciri camera" exclusive_caps=1
sudo v4l2-ctl -d /dev/video115 --all
```

Do not reinstall the module just because the node disappeared. If normal-user access returns `Permission denied`, verify the node/driver with sudo and grant only the logged-in user a temporary ACL (requires `setfacl`):

```sh
sudo setfacl -m "u:$(id -un):rw" /dev/video115
v4l2-ctl -d /dev/video115 --all
```

This ACL is lost when the node is recreated; it does not make the device world-accessible. Producer/root access and normal-user viewer access are separate.

Run one producer at a time. Start the desktop/browser camera client **after** the producer is running: with `exclusive_caps=1`, the device advertises capture capability once its output is in use. Select **Ciri camera** in the client, and confirm the logged-in user/session can access the node. PipeWire/WirePlumber/browser discovery may still need diagnosis; it is not claimed to work yet.

The streamer defaults to back camera, `/dev/video42` and requested 18 fps; integer requests 15–30 are allowed, not guaranteed delivered rates. It verifies a real nonsymlinked character node, actual driver `v4l2 loopback` and VIDEO_OUTPUT capability before launching. It refuses physical sensor/capture nodes and unavailable output capability. This is a preflight guard, not a concurrent-producer lock.

The source format stays **640×480 NV12**. The bounded downstream-leaky queue limits accumulated latency; standard `videoconvert` changes NV12 to broadly compatible **YUY2**. The launcher requires host `v4l-utils`, sets the output's V4L2 format to **YUYV** and requested rate with `v4l2-ctl`, then uses **`filesink buffer-mode=unbuffered`** for actual frame writes. This bypasses both the failing MMAP pool and GStreamer 1.22's unimplemented output RW branch. `filesink` comes from the already bundled `coreelements` plugin; no new plugin, kernel build or capture application is needed. The observed frame is 640×480×2 = **614400 bytes**, with an expected **1280-byte** stride. Full-sized writes do not prove valid pixel contents or correct colour.

If an older installed `stream-camera` still selects `v4l2sink`, do not use it for this test or overwrite its tracked bytes directly. Stop that producer and run the equivalent command without changing the installed manifest:

```sh
sudo v4l2-ctl -d /dev/video115 \
  --set-fmt-video-out=width=640,height=480,pixelformat=YUYV --set-output-parm=18 &&
sudo /opt/ciri-libcamera/run-libcamera --gst-launch -e \
  libcamerasrc camera-name=/base/soc/i2c@11ec1000/camera-main-sensor@31 ! \
  'video/x-raw,format=NV12,width=640,height=480,framerate=18/1' ! \
  queue max-size-buffers=4 max-size-bytes=0 max-size-time=0 leaky=downstream ! \
  videoconvert ! 'video/x-raw,format=YUY2' ! \
  filesink location=/dev/video115 buffer-mode=unbuffered sync=false
```

Here `/dev/video115` must be the actual verified loopback node, not a physical camera device. To persist the authored launcher change later, assemble a **fresh** add-on kit from retained inputs and use its checksum-preserving installer; no source rebuild is needed.

Use Ctrl+C to stop the foreground producer. No persistent service or automatic startup is configured.

## Diagnostics and remaining validation

Verify plugin elements inside the private runtime, not against Arch's system GStreamer:

```sh
sudo /opt/ciri-libcamera/run-libcamera --gst-inspect libcamerasrc
sudo /opt/ciri-libcamera/run-libcamera --gst-inspect videoconvert
sudo /opt/ciri-libcamera/run-libcamera --gst-inspect filesink
# With v4l-utils already installed, inspect the actual host-side loopback:
v4l2-ctl -d /dev/video115 --all
```

The launcher uses only `/usr/lib/ciri-libcamera/gstreamer-1.0` inside its private root, clears system plugin search, sets a private tmpfs registry and uses `GST_REGISTRY_FORK=no` for in-process scanning. It does not execute Debian's external scanner through a missing loader path or set global recovered-library environment variables.

For a user-run diagnostic stream, preserve both normal output and errors:

```sh
sudo env GST_DEBUG=3 LIBCAMERA_LOG_LEVELS='*:DEBUG' \
  /opt/ciri-libcamera/stream-camera back /dev/video42 18 2>&1 | tee ciri-stream.log
```

With the producer running, preview as the normal user:

```sh
ffplay -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 18 -i /dev/video115
```

The inspected 640×480 loopback PNG contains scene detail in both halves, but the upper half is much darker and the whole frame is green. The older diagnostic preview and retained NV12 captures show the same appearance. This does not establish the cause, but it is not a newly missing half caused by `ffplay` or a short write. In the retained warm-up frame 0, both chroma components vary (U 34–86, V 19–75); they are not untouched zero planes. These historical values are not a measurement of the current source capture, nor proof of the tuning/format interpretation.

Check negotiated format/stride, delivered timestamps/rate, bounded latency, clean shutdown and browser discovery. The real `ffplay`/PNG result is distinct from cross-build/static evidence, and complete writes are not proof of valid pixels. The owner's apparent AE response does not validate AWB, control translation, actual vendor statistics/results or sensor-specific tuning/calibration. No arbitrary chroma swap or green-removal filter is applied here.
