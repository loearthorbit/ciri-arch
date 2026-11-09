# Ciri rear-camera colour-bar diagnostic

The operator report `ciri-sensor-controls-20261107-214353.log` confirms that the
GC08A3 driver exposes `test_pattern`, with `0: No Pattern` and `2: Colour Bar`.
It reports a 3264×2448 `MEDIA_BUS_FMT_SRGGB10_1X10` sensor format. The matching
libcamera fork maps GC08A3 colour bars to V4L2 menu index 2, and its draft
`TestPatternModeColorBars` control also has value 2. Driver enumeration supports
trying this diagnostic; it does not yet establish successful pattern generation.

The pipeline advertises colour bars unconditionally, ignores the sensor setter's
return value, and reports the requested pattern as metadata. Consequently,
`TestPatternMode = 2` is not independent proof that the sensor produced bars.
Check CameraSensor errors and the actual saved pixels.

## First capture failed: client fix required

The operator run `ciri-colour-bars-20261107-214759.log` requested 64 frames but
reported no completed frames. The IPA worker aborted with
`FATAL Serializer: A list of V4L2 controls requires an ControlInfoMap`, followed
by IPC broken-pipe errors. The operator interrupted the stalled client; shutdown
then asserted `completedRequests_.empty()` in `CamSysDevice::stop()`. No successful
colour-bar capture or image is established by this run.

The matching cam capture-script parser creates both its per-frame controls and
its empty fallback using default-constructed `ControlList` objects, which have
no control-ID map. Assignment to `request->controls()` loses the request's
libcamera ID-map association. The serializer consequently tags that list as
V4L2 with no info-map handle, and the receiving IPA worker aborts. The saved
`config/libcamera/ciri.patch` now initializes both script lists with
`controls::controls`, matching ordinary Request controls, and explicitly includes
`libcamera/control_ids.h`. This is a standard-client fix, not a kernel, ISP,
tuning or colour fix. The corrected cam was incrementally built from the retained
source/SDK/objects on 2026-11-07. Only its capture-script object was compiled and
cam was relinked; no core, IPA or kernel was rebuilt. The update is staged in
`build/camera/updates/cam-script.mbvaPEio`, with cam SHA256
`da173236ea70e9e7c25f17c2f10e3b89fe6a14632778dfb0fa4fe705fa9e8cf8`.
Static AArch64 dependency/provider checks passed, and the installed-client
build-tree RPATH was omitted. All 90 local tests passed. No device installation
or corrected-client capture has been performed by the assistant. Do not repeat
this scripted capture with the old cam.

The saved wrapper now has a 20-second timeout with a five-second forced-stop
allowance, so a future worker failure cannot leave it waiting indefinitely.

## Reproduce the cam-only build and install the update

On the build host, with the existing pinned source, SDK, baseline package and
objects retained, run:

```bash
bash scripts/build-ciri-libcamera-cam.sh
```

The script prints a fresh update directory and creates a four-file kit: `cam`,
`build-info.txt`, `install-cam-update.sh` and `SHA256SUMS`. It does not invoke a
whole-stack rebuild or copy recovered files. The installer uses the existing
checksummed executable-update implementation with `--cam`, refusing an unknown
installed cam, modified non-target runtime files, wrong architecture or a bad
kit checksum. It updates only cam and the runtime checksum manifest, without
backups or deletion of old backups. The installed ELF report is not regenerated;
the kit's build-info contains the new static-check result.

For the already-built kit, run these operator commands from the build host:

```bash
scp -r build/camera/updates/cam-script.mbvaPEio jono@192.168.0.191:/home/jono/
ssh -t jono@192.168.0.191 'sudo bash /home/jono/cam-script.mbvaPEio/install-cam-update.sh --cam'
```

Stop the camera producer before installing. A refusal or sync failure is not a
successful update; inspect its output rather than bypassing the checks.

The first operator installation attempt on 2026-11-07 at approximately 22:11
failed with `sync: error syncing '/opt/ciri-libcamera': Input/output error`.
The chained capture did not run. The installer flushes the filesystem both
before staging and after replacement; this error alone does not identify which
flush failed or establish the current installed cam/manifest state. Do not
bypass sync or retry installation before reading the installed cam hash and
manifest, identifying the backing filesystem/device, and checking kernel
storage errors. The corrected-client RAM capture below subsequently ran successfully,
but its image still has the colour defect.

The follow-up `ciri-storage-20261107-221404.log` identifies the root filesystem
as ext4 on `/dev/sda3`, backed by a SanDisk SDDR-B531 USB card reader, with
4.4 GB available. The installed cam and its manifest entry both still have the
original SHA256 `a8e0393e09af9b63eddb3b9dbb3e064bff51e355095fddfa1cc47599452e9661`.
The kernel records failed writes and ext4 potential-data-loss warnings at
21:05, 21:39 and 22:14, including SCSI Unit Attention / ASC 0x28 (medium changed)
and `device offline or changed`. This establishes storage-path failures, not
which card/reader/connection/power/driver component caused them, nor that the
Ciri installation is broken. No filesystem repair was performed. Camera diagnosis
continued using the temporary RAM capture below without installing the client.

## Temporary RAM test without installing the updated client

`scripts/test-ciri-camera-colour-bars-ram.sh` uses the already-transferred cam
update and existing colour-bar wrapper on Ciri. It copies the corrected client
into `/dev/shm`, verifies its pinned SHA256, and privately bind-mounts it over the
cam path only for this session. It also binds a fresh RAM capture directory over
the launcher's capture parent, so standard cam's frames remain in RAM. After the
namespace exits, it decodes frame 63 to a RAM PNG and grants the invoking user
access to that PNG. Installed cam, manifest, libraries and tuning are not
replaced. The temporary files disappear at reboot. This does not establish that
the root filesystem/storage path is healthy or prevent other OS writes. The operator
ran this wrapper on 2026-11-07: `ciri-colour-bars-ram-20261107-224442.log` records
worker return 0 and `/dev/shm/ciri-colour-ram.21EWlRzV/colour-bars.png`. The PNG and
frame 63 were retrieved for inspection; see the measured result below.

From the build host, the script can be sent through stdin without storing another
script file on Ciri:

```bash
ssh -t jono@192.168.0.191 "printf '%s' '$(base64 -w0 scripts/test-ciri-camera-colour-bars-ram.sh)' | base64 --decode | sudo bash" 2>&1 | tee "ciri-colour-bars-ram-$(date +%Y%m%d-%H%M%S).log"
```

Stop existing producers first. Inspect the PNG path printed at the end; do not
use a disk capture directory from an earlier run.

## Capture using standard cam after the client update

Stop any camera producer first. After building and installing the corrected cam,
run from the build host:

```bash
scp scripts/test-ciri-camera-colour-bars.sh jono@192.168.0.191:/home/jono/
ssh -t jono@192.168.0.191 'sudo bash /home/jono/test-ciri-camera-colour-bars.sh' 2>&1 | tee "ciri-colour-bars-$(date +%Y%m%d-%H%M%S).log"
```

The wrapper creates a root-private YAML file under `/dev/shm`, which the existing
launcher's recursive `/dev` bind makes accessible inside its private root. Host
`/tmp` and `/home/jono` are not suitable paths for `--script` in this launcher.
The YAML uses `loop: 1` with frame 0 setting `TestPatternMode: 2`, so standard
cam supplies the control on every request, including reused requests. No AE,
AWB, colour-correction or gain override is added.

This requests 64 separate 640×480 NV12 frames, about 30 MB, in the launcher's
fresh `Output directory:`. It changes the live sensor test-pattern control but
replaces no installed launcher, manifest, library, tuning database or kernel.
The temporary YAML is retained until reboot; captures remain on disk. The next
ordinary matching-libcamera session initializes the sensor pattern to OFF.
This is not an installation or a colour fix. The old-client hardware attempt
failed as recorded above; the corrected-client RAM capture completed but remains
green, as recorded below.

## Inspect a late frame

On Ciri, substitute the actual `Output directory:` from this run:

```bash
sudo ffmpeg -hide_banner -loglevel error -n -f rawvideo \
  -pixel_format nv12 -video_size 640x480 \
  -i /opt/ciri-libcamera/captures/run.XXXXXXXX/colour-bars-cam0-stream0-000063.nv12 \
  -frames:v 1 /home/jono/ciri-colour-bars.png
sudo chown "$(id -u):$(id -g)" /home/jono/ciri-colour-bars.png
```

The decoder refuses to overwrite an existing PNG. From the build host, retrieve
it to a new local name:

```bash
scp jono@192.168.0.191:/home/jono/ciri-colour-bars.png "ciri-colour-bars-$(date +%s).png"
```

Look for regular vertical bars replacing the scene. Covering the rear lens
during capture helps distinguish the generated pattern from ordinary scene
content. A black or recognizably ordinary scene, or setter errors, is not a
successful known-pattern test. If bars appear, report whether the colour bias
and dark upper half remain. The pattern still passes through the ISP and its
algorithms: it can provide discriminating evidence but cannot by itself prove
which sensor/ISP stage is faulty. No colour filter or chroma swap is applied.

## Retrieved RAM capture result

The image `ciri-colour-bars-1794091613.png` was visually inspected: a regular
repeating vertical pattern replaces the scene, but it remains overwhelmingly
green with a sharp dark-upper/bright-lower boundary halfway down. This supports
pattern generation, not correct rendering of the expected colours.

On 2026-11-08 the corresponding saved frame 63 was inspected locally at
`build/ciri-bars-raw.NHIKJMSR/frame-63.nv12`. Its length is 460800 bytes and SHA256
is `458f33f9f1fe15a0973a87b8a371397347b9ea49708b2cc7bb5048837c3b0e5b`.
Interpreted using the configured 640×480 linear NV12 layout:

- Upper-half Y mean 97.212, range 73–123; lower-half Y mean 132.769, range 71–189.
- Upper-half U/V means 72.459/58.236; lower-half U/V means 54.688/44.163.
- Both chroma channels contain varying data, not empty or untouched zero planes.
- The largest adjacent Y-row changes are at rows 240 and 241. The midpoint
  brightness discontinuity exists in the saved luma, not merely in the PNG.
- A local FFmpeg decode reproduces the green/midpoint defect. RGB bytes differ
  slightly from the device-generated PNG (mean absolute channel differences
  R/G/B 0.223/0.798/0.100), so bit-identical decoding is not claimed.
- Full-range BT.601 interpretation is also green-dominated; changing only
  limited/full-range interpretation does not restore the missing colours.

The retained kernel ImgSys format table describes NV12 as two planes with full
Y height and half UV height. The fork maps vendor `eImgFmt_NV12` to the same
V4L2 format. These source declarations agree with the current output layout;
they are not proof of running firmware/register behaviour. No output-layout
patch, chroma swap, colour filter, IPA rebuild or kernel rebuild follows from
these measurements alone.

## RAW handoff follow-up

A read-only trace of the retained fork found two source inconsistencies:

- `src/libcamera/pipeline/mtkisp7/camsys/camsys.cpp`, in `setupResource()`, passes
  a `v4l2_mbus_framefmt` with only width/height assigned to the kernel RAW-resource
  calculation. Its format code and other fields are uninitialised. The retained
  MTK control ABI specifies that this structure must come from G_FMT/S_FMT.
  This is an incomplete source-side handoff, not evidence that the running
  kernel consumes the missing fields or that this causes the colour defect.
- `src/ipa/mtkisp7/halisp/hal_isp.cpp` hard-codes P1 tuning's sensor test-pattern
  mode to OFF, even when the pipeline requests sensor colour bars. This source
  inconsistency is not a verified description of the recovered deployed IPA;
  editing that source alone would not change the deployed IPA.

The fixed RGGB10 Bayer mappings agree, and the examined rear preview/video/capture
crop tables describe full 3264×2448 geometry rather than a half-height crop.
The internal RAW layout is packed MTISP RGGB10 with stride 4080 and size
9987840. CamSys discards the driver's returned video format while the RAW pool
computes its layout independently. The operator's native format trace below
returned exactly that stride and size: the proposed 4096-byte stride adjustment
was not observed and is not a reason to enlarge buffers.

Standard `cam role=raw` is explicitly unsupported by this fork. Instead,
`--trace-formats` on the existing RAM wrapper runs the same standard capture under
`strace`, recording ioctl arguments/results and a current video-node/name map.
This can reveal the actual S_FMT widths, heights, strides and sizes without a
new capture loop or build. Proprietary resource-control payloads may remain
opaque in strace; a successful ioctl alone does not establish their contents.

Stop producers, then run from the build host:

```bash
ssh -t jono@192.168.0.191 "printf '%s' '$(base64 -w0 scripts/test-ciri-camera-colour-bars-ram.sh)' | base64 --decode | sudo bash -s -- --trace-formats" 2>&1 | tee "ciri-raw-formats-$(date +%Y%m%d-%H%M%S).log"
```

The script prints the fresh `/dev/shm` directory containing `ioctl.trace` and
`video-names.txt`. They are retained and made accessible to the invoking user
also if capture fails. Retrieve those two files into a new empty local directory,
then inspect only the relevant format ioctl lines rather than dumping the entire
trace into a review. No installed file is replaced. The added option passed shell
syntax and non-device/argument guard checks on the build host; its native strace
run has not been performed by the assistant.

### Observed native formats and resource reply

The operator ran `--trace-formats` on 2026-11-08, producing
`ciri-raw-formats-20261108-191334.log`. The retrieved files are
`build/ciri-raw-trace.JpFJVWYl/{ioctl.trace,video-names.txt}`. Node numbers below
refer only to that boot; identify future nodes by topology/name.

Relevant successful S_FMT replies:

| Node/name | Format | Dimensions | Returned strides | Returned plane sizes |
| --- | --- | --- | --- | --- |
| video80 / raw-0 main-stream | MBRA packed RGGB RAW | 3264×2448 | 4080 | 9987840 |
| video81 / raw-0 yuvo-1 | 12AP | 1280×960 | 1600 | 2304000 |
| video82 / raw-0 yuvo-2 | 12CP | 640×480 | 960 | 691200 |
| video89 / raw-0 rzh1n2to-1 | NV12, one physical plane | 640×480 | 640 | 460800 |
| video16 / DIP Imgi Input | U02A, two planes | 1280×960 | 1600 / 1600 | 1536000 / 768000 |
| video37 / DIP WROTO A Output | NV12, two planes | 640×480 | 640 / 640 | 307200 / 153600 |

RAW main-stream was actually queued and streamed. The final returned split-plane
NV12 layout matches the current allocations. This rules out the hypothesized
returned-stride adjustment; successful negotiation does not prove hardware pixels
or register programming. Subdevice-format ioctl contents were not decoded by
this strace, which showed their pointers rather than pointed-to pad formats.

The 72-byte RAW-resource control payload (`0x9819e2`) was decoded using the
retained UAPI field offsets. The reply reports `raw_max=2`, `raw_min=1`,
`raw_used=1`, `bin=0`, `path_sel=0`, `pixel_mode=0`, `status=1`. No negotiated
multiple-RAW-engine configuration was observed. Do not assign further meanings
to status/pixel_mode without the ABI, or treat the reply as a colour diagnosis.
The resource request uses interval 1/30, hblank 376, maximum vblank 63072 and
pixel rate 268800000; queried sensor vblank was 133. No causal defect from that
difference has been established. The resource's sink-format pointed-to bytes
were not captured. No resource/geometry/colour patch follows from this trace.

## Temporary P1 RAW/intermediate export

The existing pipeline already streams RAW and exports P1 IMGO, YUVO_R1 and
YUVO_R2 through its on-device-tuner (ODT) hooks. No new allocation/request/capture
application is needed. The isolated root has neither the original ChromeOS
`/mnt/stateful_partition/vendor/camera_dump` directory nor a shell for the tool's
`system("mkdir -p ...")` call.

The initial P1 diagnostic patch changed only the core-side dump work directory to
`/captures/p1-dump` and replaces shell directory creation with the non-throwing
`std::filesystem::create_directories(path, error_code)` overload, logging and
returning a negative error on failure. It changes no sensor control, resource
strategy, image layout, colour processing or tuning. The recovered IPA remains
unchanged and may leave its separate original-path ODT facility disabled.
Pipeline-side export is the intended diagnostic; IPA proprietary dump behaviour
has not been established.

### Build on the host

```bash
bash scripts/build-ciri-libcamera-dump-core.sh
```

The original P1 build used the retained matching ChromiumOS libcamera source at
revision `a5a75eeabdce349d8f794caacb3177c0e71287d5`, SDKs and objects. It explicitly
compiled only `pipeline_mtkisp7_odt_on_device_tuner.cpp.o`, then reused the retained
core link command with a fresh staged output. The current builder additionally
refreshes the three objects needed for separate P2 plane allocations and dump
synchronization; see the correction section below. It does not invoke a full core build,
reconfigure Meson, rebuild IPA/kernel, assemble recovered files, execute the
recovered runtime, or install anything. The original build-tree core is preserved.
Static checks require the same AArch64 SONAME/DT_NEEDED, all baseline exports,
strong import providers in the existing closure, and no RPATH/RUNPATH.

The assistant's build attempt was blocked by the permission gate before
compilation/relink. The operator subsequently completed the build on 2026-11-08,
producing `build/camera/updates/p1-dump.23bgs8Zg`. The staged core SHA256 is
`2f8d152c649e5de265e2d682d0fd76cb12522db465d9bfaf4ae290a519fe5d02`.
All six kit-file checksums passed. Readelf-only verification passed for the staged
AArch64 SONAME/dependencies, retained exports and absence of RPATH/RUNPATH.
The initial provider check accidentally retained the separate baseline SONAME
copy; the corrected check below excludes every baseline-core copy by SONAME.
No recovered runtime was executed by the assistant.

The operator's first SCP transfer failed, so that attempted capture never started.
A tar-over-SSH transfer into a fresh RAM directory subsequently succeeded.
`ciri-p1-dump-20261108-201641.log` records 64 final frames and worker return 0,
with PNG `/dev/shm/ciri-p1-dump.v0ps8DoJ/colour-bars.png`, but no P1 payloads.
Pipeline ODT initialization reported config-open failure; its old source line
number corroborates that the unchanged core was still selected. This run does
not establish upstream pixels.

The wrapper had bound only `libcamera.so.0.1.0`. The package materializes
`libcamera.so`, `libcamera.so.0.1` and `libcamera.so.0.1.0` as separate regular
files; DT_NEEDED selects `libcamera.so.0.1`, bypassing that mount. The fixed wrapper
privately binds the diagnostic core over all three names. This is a wrapper/loader
selection correction, not a further core, IPA, tuning or kernel change.

The corrected kit is `build/camera/updates/p1-dump-mount.jv6ic2ti`, restaged without
compilation using the identical core/hash. The static provider check passed with
all old-core copies excluded by SONAME. All seven targeted diagnostic tests passed,
including executing the namespace block with a recording mount stub that verifies
all three read-only alias mounts; no real mount or camera runtime ran in that test.
The corrected wrapper has not yet been observed running on Ciri.

Local verification passed: all 96 tests, including export-config, saved-patch,
launcher opt-in, incremental-build scope, shell syntax and argument-guard checks.
The saved patch also passed reverse-application checking against the retained
source. Whitespace checking passed with `core.whitespace=-space-before-tab`
(the unified patch contains tab-indented context). These are local checks, not
a compiled diagnostic core or proof of native dump functionality.

### Operator RAM test after a successful build

Use the actual `Diagnostic directory:` printed by the builder. From the build
host, substitute that directory in `kit`:

```bash
tar -C build/camera/updates/p1-dump-mount.jv6ic2ti -cf - . |
  ssh jono@192.168.0.191 \
    'umask 077; mkdir /dev/shm/ciri-p1-kit-mount.jv6ic2ti &&
     tar -xf - -C /dev/shm/ciri-p1-kit-mount.jv6ic2ti' &&
  ssh -t jono@192.168.0.191 \
    'sudo bash /dev/shm/ciri-p1-kit-mount.jv6ic2ti/test-p1-dump-ram.sh /dev/shm/ciri-p1-kit-mount.jv6ic2ti' \
    2>&1 | tee "ciri-p1-dump-$(date +%Y%m%d-%H%M%S).log"
```

Stop the camera producer first. The previously transferred corrected cam must
still be at `/home/jono/cam-script.mbvaPEio/cam`; its pinned hash is verified.
The kit contains the staged core, build-info, checksum manifest, authored
launcher, ordinary colour-bar script, RAM wrapper and export-only dump config.
There is no installer.

The wrapper copies the kit to a fresh root-private `/dev/shm` directory and
checks its hashes before use. Temporary private bind mounts select the diagnostic
core, corrected standard cam and authored launcher, while the capture parent is
RAM-backed. The launcher creates fresh `/run/camera/enable_tuning` and an export
trigger for **two early requests** only. It does not create import/raw-injection
or forced-LPNR flags. All installed files and the installed manifest remain
untouched. No assistant device execution or installation has been performed.

The three CSV rows enable P1 IMGO/YUVO_R1/YUVO_R2 export and disable reimport-config
writing. Filename keys include request/frame identifiers, port, geometry/stride,
bit depth, Bayer order and plane padding identifiers. Files ending in
`packed_word` are not ordinary linear NV12. Determine their actual packing before
interpreting pixels. The wrapper decodes only final standard-cam frame 63 as
640×480 NV12 and lists nonempty upstream files. Missing upstream dumps cause a
failure; config parsing or file presence alone is not proof of correct pixels.
Early P1 exports are not assumed to have the same converged tuning as frame 63;
retain all final frames for matching by dump request/frame identifiers.

Captures and dumps are retained and made accessible even after capture fails.
Once the wrapper prints the actual RAM directory, retrieve it into a new empty
local directory (substitute the real directory, not the placeholder):

```bash
out=$(mktemp -d "$PWD/build/ciri-p1-result.XXXXXXXX") &&
scp -r jono@192.168.0.191:/dev/shm/ciri-p1-dump.XXXXXXXX/captures "$out/"
```

RAM files disappear on reboot. The retrieved comparison below now establishes
the first observed bad boundary. This remains an observability update, not a
colour fix.

## Retrieved P1 pixel comparison — 2026-11-08

The successful operator log `ciri-p1-dump-20261108-204607.log` records config
loading, pipeline tuning enabled, P1 files for internal request/frame 4, final
capture completion and worker return 0. It also records register dumps; those
are not pixel evidence. The retrieved directory is
`build/ciri-p1-result.8kVGK670`, containing the five packed pixel planes under
`captures/run.j1s3Ot8P/p1-dump/UKey204614687`, all 64 final NV12 files, and the PNG.
The empty earlier retrieval directory is preserved.

The offline decoder interprets each 5-byte group as four contiguous
little-endian 10-bit samples, and each 3-byte group as two 12-bit samples. This
interpretation is supported by actual known-pattern data, not merely the format
names: RAW white samples decode to 1020, 10-bit white Y/chroma to 1023/512/512,
and 12-bit white Y/chroma to 4092/2048/2048. Both intermediate outputs produce
the same distinct repeating white/yellow/cyan/green/magenta/red/blue/black bars.
The retained format table's 64-pixel/80-byte and 64-pixel/96-byte group sizes
agree; that table alone does not document bit order. No alternative packing was
silently substituted into the camera pipeline.

After unpacking, previews use standard FFmpeg: RAW is shifted into RGGB16LE;
10/12-bit YUV is shifted down to 8-bit NV12. These are offline inspection copies,
not a colour-calibration test or an output filter. RAW and both P1 YUV previews
were visually inspected and have full-height distinct colours with **no midpoint
brightness seam**. Final NV12 previews, including the first client frame and
frame 63, remain green with the dark-upper/bright-lower boundary.

Measured means below put P1 YUV on an 8-bit-equivalent scale by dividing 10-bit
values by 4 and 12-bit values by 16, before rounding. U/V use their actual half-
height planes; the top/bottom boundary is aligned with the image midpoint.

| Stage | Upper/lower Y mean | Upper/lower U mean | Upper/lower V mean |
| --- | --- | --- | --- |
| P1 YUVO1, 1280×960, packed 10-bit | 132.974 / 132.974 | 126.497 / 126.498 | 125.388 / 125.388 |
| P1 YUVO2, 640×480, packed 12-bit | 132.972 / 132.972 | 126.529 / 126.529 | 125.425 / 125.425 |
| Final NV12, client frame 0 | 65.027 / 118.951 | 90.765 / 62.101 | 81.135 / 51.721 |
| Final NV12, client frame 63 | 97.780 / 133.038 | 72.290 / 54.560 | 57.933 / 44.003 |

RAW upper/lower means are both 525.625 in 10-bit units, range 0–1020. Its adjacent
row means do not jump at all. P1 YUV adjacent row-mean changes are below 0.002
10-bit Y units (YUVO1) and below 0.005 12-bit Y units (YUVO2). Final frame 0 has
adjacent Y-row mean jumps of 19.553 at row 240 and 23.387 at row 241. The defect
is therefore present in final image bytes, rather than introduced by PNG display
or the loopback bridge.

Internal dump ID 4 must not be equated with client sequence 4. The retained
pipeline inserts four initial RAW/3A padding requests (`kRawMetaDelay=4`) before
the first ordinary client request. The source path and log ordering associate
internal request 4 with the first client capture (sequence 0); no assertion of
byte-for-byte agreement or identical tuning between it and late frame 63 is
made. All inspected early client frames 0–6 also exhibit the final-stage defect.

**Conclusion:** this known-pattern run demonstrates correct pattern generation
and full-height colour-bearing pixels at RAW and both P1 YUV outputs. The first
observed corruption is after P1 and before the final external NV12 buffer, in
the P2/ImgSys/MCNR input-processing-output path. This narrows the investigation
away from sensor pattern generation, RAW Bayer-order speculation, returned RAW
stride adjustment, the client decoder and loopback transport. It does not yet
identify a particular P2 import/format, tuning, register, firmware or output
handoff defect, nor establish general scene colour calibration.

The retained source connects both P1 YUVO buffers to MCNR in `mtkisp7.cpp` around
lines 1640–1655. The next useful boundary is the P2 import and successive MCNR/DIP
outputs. No kernel, IPA or colour-processing patch was applied for this comparison.

### Reproduce the offline comparison

```bash
python3 -I scripts/compare-ciri-p1-pixels.py \
  /home/jono/Source/ciri-arch/build/ciri-p1-result.8kVGK670 \
  /home/jono/Source/ciri-arch/build/ciri-p1-comparison.NEW
```

The output directory must not exist. The script reads saved pixels only and
never executes recovered camera code. It emits unpacked preview buffers, PNGs
and `pixel-report.json` with source sizes/SHA256s and per-plane measurements.
Four offline decoder tests passed, covering known white/neutral bytes, complete
sample-value round trips, invalid layouts and midpoint-edge measurement.

Saved results: `build/ciri-p1-comparison-20261108`. The labelled
`comparison.png` shows RAW, both P1 YUV previews and final client frame 0 together.
An earlier exploratory decode is retained at `build/ciri-p1-pixels.joex7y8b`;
no previous sources, captures or results were replaced or cleaned up.

### Next diagnostic: existing TR/P2/MCNR pixel hooks (2026-11-08)

`config/libcamera/p2-dump.cfg` selects 19 export-only image entries:

- Both P1 YUV buffers as same-run references (no repeat RAW dump).
- Five current-image TR downscale outputs, through `TR_Y2Y_F1` and `TR_Y2Y_F4`.
- DIP1 `IMG3O` at `P2_MS_F_SMALL`, F4, F3, F2 and F1; F1 current input and `IMG4O`.
- DIP2 (`WPE_P2_PQDIP_MS_F0`) current input, `RECI_D1`, `IMG3O`, `IMG4O`
  and the final client buffer.

All 19 entries were matched by read-only inspection against the retained source's
image metadata and existing export callers. `TrTask::notifyDone`,
`Dip1Task::notifyDone` and `Dip2Task::notifyDone` call these hooks after their
respective tasks complete. This establishes available diagnostic points, not
that every selected image has actually been exported on Ciri.

**Final-output naming:** `tuneDip2` labels either external client buffer as
`WPE_P2_PQDIP_MS_F0_WDMAO`. The hardware submission uses WDMAO for `videoOut1`
and WROTO for `videoOut2`, but selecting the ODT WROTO metadata would not dump
this client buffer. The config therefore selects `WDMAO`.

Each filename includes a literal stage and module label. The fork currently
renders `[Port]` and `[Plane]` as empty strings, so those keys cannot distinguish
same-size intermediates. `[Padding]` adds the Y/chroma plane label and
PW/PH/BW fields. Some intermediate allocations have scan alignment 64; the
returned file sizes, valid dimensions and row stride must be checked before
comparison. Do **not** run the unpadded, old-filename P1 comparison script on
this new dataset unchanged. This selection does not cover every temporal,
motion or confidence branch, and a bad intermediate will narrow the boundary
rather than prove a particular format/tuning/kernel cause.

Reproduce staging without compilation:

```bash
bash scripts/stage-ciri-camera-p2-dump.sh \
  /home/jono/Source/ciri-arch/build/camera/updates/p1-dump-mount.jv6ic2ti
```

The helper verifies the existing kit's manifest, reuses its identical core,
adds the current authored wrapper/launcher/capture script and P2 config, and
writes a fresh report and manifest. It does not invoke a compiler, execute the
camera runtime, copy a recovered runtime tree, install anything, or modify the
parent kit. The RAM wrapper still binds all three materialized core aliases
read-only; the existing launcher path and `p1-dump` output directory name are
retained for compatibility. Only status-message wording was generalized.

Staged kit: `build/camera/updates/p2-dump.ZdtaMxbh`. Core SHA256 remains
`2f8d152c649e5de265e2d682d0fd76cb12522db465d9bfaf4ae290a519fe5d02`.
There has not yet been a native P2 diagnostic capture.

Stop any camera producer, then run **on the build host**:

```bash
set -o pipefail
remote=$(ssh jono@192.168.0.191 'mktemp -d /dev/shm/ciri-p2-kit.XXXXXXXX') &&
tar -C /home/jono/Source/ciri-arch/build/camera/updates/p2-dump.ZdtaMxbh -cf - . |
  ssh jono@192.168.0.191 "tar -C '$remote' -xf -" &&
ssh -tt jono@192.168.0.191 "sudo bash '$remote/test-p1-dump-ram.sh' '$remote'" 2>&1 |
  tee "ciri-p2-dump-$(date +%Y%m%d-%H%M%S).log"
```

Transfer failure prevents capture. This operator command uses a fresh RAM kit,
the same standard `cam` colour-bar capture and the same two-request export
trigger; it does not permanently replace the installed stack. The wrapper
prints the retained captures/dumps path. Preserve/retrieve that directory
before reboot. File existence and a green final PNG do not establish which
intermediate is defective; the next step is offline inspection of the actual
returned labelled pixel planes.

Fourteen targeted P1/P2 diagnostic tests passed, including config labels,
unchanged parent/core staging, manifest checks and all-alias namespace binds.
The complete repository suite then passed all 104 tests in 19.141 seconds.
Staged kit checksums, shell syntax and the adjusted whitespace check
(`git -c core.whitespace=-space-before-tab diff --check`) also passed.

### Returned P2 pixels: first abnormal outputs are already at TR (2026-11-08)

The first P2 log, `ciri-p2-dump-20261108-211646.log`, loaded all 19 entries,
listed 37 image files and ended with worker return 0 and a saved PNG. Most
error-level messages were vendor NDD filename-validation and ODT_FWMM file-open
messages also present in the earlier successful P1 diagnostic. This comparison
does not establish those errors as harmless in general or as the colour cause.

The dataset actually retrieved for this comparison came from the later RAM
capture directory `/dev/shm/ciri-p1-dump.EOiPExGu`:

- Local input: `build/ciri-p2-result.csrHFI0r`.
- Run: `captures/run.uqGTPdrg`.
- ODT directory: `p1-dump/UKey212032577`.
- Image dumps: internal request/frame `0004-0004`, 18 packed Y/chroma pairs
  plus one final linear NV12 file, covering all 19 configured image entries.
- Client captures: 64 frames, each 460800 bytes.

`scripts/compare-ciri-p2-pixels.py` decodes each valid row using the labelled
byte stride, then discards padded columns and allocation scanlines. It handles
small chroma planes whose allocations have 64 rows even when their valid
height is only 15 or 8. Both Y and UV allocation lengths are checked separately;
UV allocation height is not assumed to be half of the Y allocation height.
Five offline decoder tests passed, covering row/scan padding, small chroma
allocations, invalid layouts, odd-height statistics and observed filenames.

**Byte-for-byte checks of the saved plane payloads:**

- P1 YUVO_R1 equals DIP2 `WPE_P2_PQDIP_MS_F0-IMGI_D1`, for both Y and UV.
- P1 YUVO_R2 equals DIP1 `P2_MS_F1-IMGI_D1`, for both Y and UV.
- DIP1 `P2_MS_F1-IMG3O` equals DIP2 `WPE_P2_PQDIP_MS_F0-RECI_D1`, for both planes.
- The final ODT NV12 file equals client capture `000000.nv12` exactly.
  This is a measured byte association, not an inferred internal/client ID offset.

The P1 inputs remain normal colour bars. However, **every selected TR output
chroma allocation is entirely zero**, including padding. The selected DIP
`IMG3O`/`IMG4O` output chroma allocations and the resulting DIP2 reconstruction
input are also entirely zero. This was checked in the original saved bytes,
not inferred from PNG colour or a particular unpacking interpretation.

Representative valid-image means (8-bit-equivalent Y; padding excluded):

| Saved image | Upper Y | Lower Y | Chroma allocation |
| --- | ---: | ---: | --- |
| P1 YUVO_R2 / DIP1 IMGI_D1 | 132.97 | 132.97 | Contains normal colour-bar chroma |
| First TR downscale, `TR_Y2Y_F1-YUVO_T2` | 126.05 | 132.97 | All bytes zero |
| DIP1 F1 `IMG4O` | 5.12 | 132.81 | All bytes zero |
| DIP2 F0 `IMG4O` | 2.59 | 132.79 | All bytes zero |
| Final ODT / client frame 0 | 65.02 | 118.95 | Nonzero, but defective colour |

DIP1 `IMG3O`/`IMG4O` and later outputs have a midpoint luma discontinuity.
The final upper half still contains image data; it is not a missing half-frame.
The first selected abnormal colour-image allocations are therefore already at
TR, before the later MCNR/DIP outputs. Final ODT/client equality rules out
client save/plane concatenation as the origin of this run's visible defect.

**Scope of the conclusion:** unchanged dumped input memory does not prove the
hardware imported/read it correctly. All-zero dumped chroma does not by itself
identify a bad FD offset, descriptor ABI, packed format, register/tuning setting
or kernel/firmware behaviour, nor rule out misplaced writes elsewhere in the
shared allocation. The next read-only target is the P2 input/output descriptor
and plane-offset/stride translation feeding TR and DIP, not another sensor or
client test. No colour fix, source rebuild or device installation was performed.

Reproduce into a **new** output directory:

```bash
python3 -I scripts/compare-ciri-p2-pixels.py \
  /home/jono/Source/ciri-arch/build/ciri-p2-result.csrHFI0r \
  /home/jono/Source/ciri-arch/build/ciri-p2-comparison.NEW
```

Saved previews and report: `build/ciri-p2-comparison-20261108`. In particular,
`P1-YUVO_R2.png` shows normal bars; `TR_Y2Y_F1-YUVO_T2.png`,
`P2_MS_F1-IMG4O.png` and `ODT-final.png` show the progression to defective green
output. The report records source hashes, valid-image statistics and exact
payload matches. The current script also records whole-allocation chroma byte
counts when rerun. No recovered code executes during offline analysis.

### Source-confirmed CAPTURE offset loss and P2 allocation correction (2026-11-08)

Read-only inspection of kernel revision
`b8f841230b1615aa58b5d4389e11bfe66abbd993` confirms that generic VB2 copies
userspace `data_offset` only for OUTPUT queues. CAPTURE plane state is cleared
before filling, and ImgSys's custom QBUF/prepare/queue callbacks do not restore
those offsets. Its standard-mode descriptor builder copies the resulting zero
plane offsets and each DMA allocation's base address. Internal shared-FD output
planes therefore address the start of the same allocation instead of distinct
Y/chroma regions. The packed U02A/U02C mappings and vendor plane-VA contract
were checked separately; neither showed a translation defect.

An additional offline check interprets the first TR output's Y prefix as
misplaced interleaved UV. A representative 320-sample row has correlation
0.994496 with a simple 2x2 box-downscale of P1 chroma, versus -0.388624 with
similarly downscaled luma. All 13 components selected from locally flat input
chroma regions match exactly. The box filter is a comparison model, not a claim
about the hardware's exact filter. Valid misplaced UV writes end at row 120;
the intended 61440-byte UV allocation is entirely zero. These observations
support the source-derived overwrite mechanism, without observing live hardware
descriptors or proving corrected colour.

The saved `config/libcamera/ciri.patch` and retained source now add the nonvirtual
`InfoFramePool::createSeparatePlaneBuffers()` method. It allocates one FD per
logical plane, each with offset zero and the existing plane length/stride.
Fourteen MCNR pool call sites opt in: seven `img3o_` pools, two `img4o` pools,
`idi_`, and four two-plane `WARP2P_MTISP` motion-map pools. Counts, heap types,
formats, alignment and scanline padding are unchanged. The original
`createBuffers()` implementation/signature, P1 CAMSYS allocation, class object
layouts, single-plane pools, Lazy pools and Elastic pools are unchanged. This
correction targets the active MCNR preview/video route, not the other still
capture paths. The dump adapter now holds a DMA synchronization guard for each
distinct plane FD throughout export/import, syncing shared-FD P1 only once.

**Build the corrected core, rather than restaging an old diagnostic core:**

```bash
bash scripts/build-ciri-libcamera-dump-core.sh --p2-planes
```

The builder uses the retained SDKs/objects and refreshes only these four core
objects before linking to a new `build/camera/updates/p2-planes.XXXXXXXX` kit:

- `info_frame.cpp.o`;
- `pipeline_mtkisp7_imgsys_mcnr.cpp.o`;
- `pipeline_mtkisp7_odt_imagiq_adapter_imagiq_adapter.cpp.o`;
- `pipeline_mtkisp7_odt_on_device_tuner.cpp.o`.

It preserves the build-tree DSO, reuses all other retained objects, uses the
19-entry P2 export config and existing standard-cam RAM wrapper, and retains the
SONAME/dependency/export/import-closure/RPATH checks. Build info records the
compiled targets, separate-plane change and dump-config hash. There is no IPA
or kernel rebuild, recovered-runtime assembly/execution, installation or cleanup.
The no-argument mode still selects the P1 export config, but also refreshes all
four objects so it cannot silently link stale allocation code.

Local verification: **115 repository tests passed**. New native tests compile
only the authored method/helper against inert test doubles; they cover independent
FDs/zero offsets, unchanged supplied packed/padded layouts, independent UV writes,
partial-allocation FD cleanup, heap selection, distinct-FD synchronization and
unchanged P1/shared-FD behavior. The full-build consistency check now includes
tracked headers as well as `src`/`utils`. Saved-patch equality, reverse-application,
shell syntax and adjusted whitespace checks passed. At the end of source
implementation, no libcamera cross-build or Ciri test had been performed for this
correction; the subsequent operator build and native result are recorded below.

### Corrected native P2 result: full-height colour bars (2026-11-09)

The operator built `build/camera/updates/p2-planes.q0SJX15h`. Its manifest passed
local checks, build info records all four required objects and no missing strong
imports, and the corrected core SHA256 is
`db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d`.
The assistant did not perform that cross-build or execute any device commands.
The operator tested it through the existing standard-cam RAM-only wrapper, with
no permanent installation:

- Native log: `ciri-p2-planes-20261109-103644.log`.
- Retained RAM capture directory: `/dev/shm/ciri-p1-dump.c82JYRUs/captures`.
- Retrieved input: `build/ciri-p2-planes-result.ej8zvoPw`.
- Run: `captures/run.EzdGhRYR`; ODT directory: `p1-dump/UKey103658782`.
- Offline output: `build/ciri-p2-planes-analysis.nv9Dob7o/comparison`.

The same padding-aware decoder analysed 18 packed Y/chroma pairs and the final
NV12 image. **All 18 packed chroma allocations now contain nonzero data**,
including every selected TR and DIP output that was previously entirely zero.
The first TR chroma allocation contains 53040 nonzero bytes of 61440; its valid
upper/lower Y means are 133.1762/133.1764 instead of 126.0472/132.9729.
DIP1 F1 IMG4O upper/lower Y is now 133.1951/133.1952 instead of
5.1158/132.8079. The previous midpoint luma discontinuities are gone.

The final ODT NV12 dump still equals client frame 0 byte-for-byte. Its
upper/lower means are Y 118.0342/118.0344, U 127.3132/127.3134 and
V 126.8645/126.8648. Across all 64 correctly sized client frames, the maximum
upper/lower Y mean difference is 0.241 in 8-bit units; no client's entire UV
plane is zero. Direct inspection of `ODT-final.png` and `client-000063.png`
shows full-height colour bars without the green/dark-half corruption.
Brightness/saturation differ between the first and late preview, so this result
is not a claim of fixed scene calibration, AWB/CCM/shading or stable colour over
time. A normal scene and sustained streaming remain to be tested.

This is native pixel verification of the separate-plane allocation correction
for the colour-bar test, not merely a successful build or completed requests.
The installed core remains unchanged; only the operator's temporary RAM test
used the corrected core.

### Normal-scene RAM test prepared (2026-11-09)

The existing standard-cam capture script and RAM wrapper now accept `--scene`.
The capture script explicitly requests `TestPatternMode: 0` on every request,
leaves AE/AWB automatic without adding exposure/gain/CCM overrides, and saves
180 rear-camera viewfinder frames at 640x480 NV12. This is about six seconds at
30 fps, plus startup; the command has a 30-second timeout and five-second kill
grace. Keep the camera still and point it at a well-lit scene containing a
white/grey item and coloured objects.

Scene mode unsets `CIRI_P1_DUMP_CONFIG` in both the outer RAM namespace and the
capture script. The launcher's fresh `/run` therefore has no ODT tuning/export
flags; the packaged dump config is not enabled. The wrapper still binds the
corrected core read-only over all three installed aliases, binds the same
known standard-cam replacement and saves output in a new RAM capture directory.
It decodes client frame 179 to `scene.png` and does not require pipeline dumps.
The original no-flag colour-bar/P1/P2 diagnostic behaviour is preserved.

No rebuild was required. Config-only restaging reused the operator-built
`p2-planes.q0SJX15h` core, unchanged at SHA256
`db6c0b1462ae72dcc6df55362600aa5e2c98204422dfbc2ffdca4e211932661d`:

```bash
bash scripts/stage-ciri-camera-p2-dump.sh \
    /home/jono/Source/ciri-arch/build/camera/updates/p2-planes.q0SJX15h
```

The kit prepared for this test is `build/camera/updates/p2-dump.QnOAX3KF`.
Transfer it to a fresh directory on Ciri, stop any existing camera producer,
then run as the operator (substitute the actual transferred directory):

```bash
sudo bash /dev/shm/ciri-scene-kit.XXXXXXXX/test-p1-dump-ram.sh \
    /dev/shm/ciri-scene-kit.XXXXXXXX --scene
```

Retain the complete capture log and all saved frames for early/late scene and
3A comparison. The wrapper prints the retained capture directory even after a
capture failure; do not reboot before retrieval. Successful PNG decoding is
not evidence of accurate colour or converged calibration. At preparation time,
**120 repository tests passed**, including inert fixtures for explicit pattern
selection, bounded standard-cam arguments, inherited ODT suppression, all
read-only alias mounts, late scene-frame selection without dumps, and missing
final-frame failure. At preparation time no normal-scene device run or permanent
installation had been performed; the subsequent operator result follows.

### Normal-scene native result (2026-11-09)

The operator ran scene mode and retrieved the capture. No device commands were
executed by the assistant, and the installed core remains unchanged.

- Native log: `ciri-scene-20261109-121621.log`.
- Retained device capture: `/dev/shm/ciri-p1-dump.1fbpaBW2/captures`.
- Retrieved input: `build/ciri-scene-result.VV3pV2o8`.
- Run: `captures/run.qK1M2ntd`.
- Offline previews and pixel/metadata reports:
  `build/ciri-scene-analysis.hjgr91k4`.

All 180 expected client frames are present, each exactly 460800 bytes, with
180 distinct payloads and no entirely zero UV plane. No pipeline dumps were
retained. All 180 metadata blocks report `TestPatternMode = 0`. The assistant
decoded and inspected frames 0, 30, 90 and 179 using ordinary FFmpeg NV12
conversion, with no colour correction, denoising or averaging.

Frame 0 is very dark, but contains recognizable scene detail rather than the
previous green/dark-half corruption. Frames 30, 90 and 179 show the full real
scene with broadly plausible colours; the cardboard box, cables and background
are visible without the earlier midpoint fault. They are visibly noisy. The
operator reported: “first image is dark but the rest look great.” This extends
the allocation-fix pixel evidence to an ordinary rear-camera scene, not only
sensor colour bars. It does not certify absolute colour calibration.

Startup metadata is consistent with AE adjustment: frame 0 reports AE searching,
exposure 30004 and gain 103.125; AE first reports convergence at frame 19.
Frames 60–179 all report AE/AWB converged, exposure 60008 and gain 1439.0625.
These are the reported control values, not an assertion that every vendor
processing operation or gain unit is verified. Across those late frames, mean
8-bit Y ranges 112.36–114.44, U 128.24–129.80 and V 126.14–126.73.

The first-to-last sensor timestamp interval is 10.705 seconds, corresponding to
16.72 fps, with a late median inter-frame interval of 60.20 ms. This is **not a
30-fps result**: the settled 60-ms exposure is consistent with the longer frame
period in this scene. The bounded test completed, decoded frame 179 and the IPA
worker returned 0. The log still contains vendor startup warnings/errors,
including missing 3A history and flicker data; do not call it error-free.
Permanent installation and normal sustained streaming of this corrected core
remain separate, unperformed steps.
