# Automatic Ciri cameras

## Native front-camera result — 2026-11-12

After restoring the required modules and updating the unit, the operator reports
that front-camera video works through the demand service with:

```bash
ffplay -fflags nobuffer -framedrop -f v4l2 -framerate 18 \
  -video_size 640x480 -input_format yuyv422 /dev/ciri-camera-front
```

The operator subsequently confirmed that explicit frame-rate/format and
low-buffering ffplay options resolved noticeably laggy playback. These are
player-specific settings, not changes to the camera pipeline.

The earlier command without `-f v4l2` returned `Invalid data found when processing
input` despite activating the front camera. Explicitly selecting the V4L2 demuxer
resolved it; documentation now uses that flag for both stable symlink names.
This is an operator-reported front-stream result, not an assistant device test.
Rear demand capture, repeated stop/idle/handover, reboot, browser integration and
CPU/power measurements still need their own confirmation.

## Startup dependencies — 2026-11-12

After reinstalling the retained matching loopback module, the operator reached
Python startup but received `Requested physical cameras not registered: back,front`.
The isolated `cam --list` printed no cameras. Host `v4l2-ctl --list-devices` showed
`mtk-cam`, but neither `mtk-imgsys` nor `mtk-aie-5.3`.

The matching libcamera pipeline requires all three media devices before camera
registration (`mtkisp7.cpp`, `PipelineHandlerMtkISP7::match`). The service initially
loaded only loopback, omitting explicit ImgSys/AIE loading. Its unit now also runs
separate `modprobe mtk_imgsys` and `modprobe mtk-aie` pre-start commands. Kernel
configuration builds both as modules. AIE registration is required by pipeline
matching even with face processing disabled; this does not enable face detection.
No camera core/kernel rebuild is needed for this unit change. Native module
availability/probing and camera enumeration still require operator verification.

For immediate diagnosis on Ciri, with the service stopped:

```bash
sudo modprobe mtk_imgsys &&
  sudo modprobe mtk-aie &&
  sudo /opt/ciri-libcamera/run-libcamera --list
```

If a module is missing or probing fails, preserve its exact error rather than
rebuilding or bypassing checks. Loading successfully for this boot is not the
same as installing the updated service unit for subsequent boots.

## Restore missing ImgSys/AIE modules without replacing the kernel

The operator's next `modprobe mtk_imgsys` failed because that module was absent
from the installed `7.3.0-rc4-ciri` module set. Both already-built modules remain in
`build/camera/kernel/companion/download/ciri-camera-companion-yjsiaoyk/modules.tar.zst`.
The retained archive SHA256 is
`a1153a67f0de9930efcfa7a6aceb9a90b7bfebc39223c5be31205b8d27075bc4`;
both binaries report `7.3.0-rc4-ciri SMP preempt mod_unload aarch64`.
The kernel configuration SHA256 is the same snapshot recorded by the retained
loopback build. This is reuse of existing outputs, not a rebuild or load test.

Prepare the module-only kit on the build host:

```bash
python3 -I /home/jono/Source/ciri-arch/scripts/stage-ciri-isp-modules.py
```

This validates the pinned archive/config/revision, reads only two regular tar
members (no general extraction), and creates a fresh `isp-modules.XXXXXXXX` kit.
It copies only `mtk_imgsys.ko`, `mtk-aie.ko`, the authored installer, provenance
and checksums. Kernel images, DTBs, initramfs, other modules and recovered
userspace are neither copied nor executed.

Transfer the printed kit, substituting its actual path:

```bash
kit=/home/jono/Source/ciri-arch/build/camera/updates/isp-modules.XXXXXXXX
tar -C "$kit" -cf - . | ssh jono@192.168.0.191 \
  'work=$(mktemp -d /dev/shm/ciri-isp-modules.XXXXXXXX) && tar -C "$work" -xf - && printf "kit=%s\\n" "$work"'
```

On Ciri, use the printed remote directory:

```bash
kit=/dev/shm/ciri-isp-modules.XXXXXXXX
sudo systemctl stop ciri-camera.service &&
  sudo python3 -I "$kit/install-modules.py" "$kit" &&
  sudo modprobe mtk_imgsys &&
  sudo modprobe mtk-aie &&
  sudo /opt/ciri-libcamera/run-libcamera --list
```

The installer checks exact binary hashes, ELF/ARM64, kernel version, module names,
existing destinations and kernel dependencies before writing. It atomically
publishes only the missing pair into
`/usr/lib/modules/7.3.0-rc4-ciri/updates/ciri-camera`, then runs `depmod` and sync.
Different existing modules, compressed/alternate module candidates, symlinks and
missing dependencies are refused. Identical partial installations can be repaired
by rerunning the same kit; publication is not one whole transaction. Sync/depmod
failures are fatal. Only its own temporary files are deleted. No module is loaded
by the installer, no backups are created/deleted, and loopback/audio/kernel files
are preserved. The assistant does not perform device installation/loading.

After successful native enumeration, start the service. The repository service
unit now loads the pair automatically; its updated service kit must also be
installed for boot startup, not just manually loading modules for this boot.

## Earlier startup correction — 2026-11-10

The operator reports that both physical cameras work. Both are enabled by default.
The operator installed the first service kit, but startup failed repeatedly with
`ENOENT`, followed by `start-limit-hit`. The retrieved journal is
`ciri-camera-service-journal.7uG4FDTz.log` (2026-11-09 boot).

The event drain incorrectly accepted only `EAGAIN` for an empty queue. Linux's
nonblocking `v4l2_event_dequeue()` calls `__v4l2_event_dequeue()`, which returns
`-ENOENT` when empty. The corrected service accepts both errors **only at
VIDIOC_DQEVENT**, preserves other failures, and logs tracebacks for future errors.
See [the kernel implementation](https://github.com/torvalds/linux/blob/master/drivers/media/v4l2-core/v4l2-event.c).
A regression covers initially empty and drained/coalesced queues for both errno
values and confirms genuine ioctl errors remain fatal. All 142 local tests pass.

The corrected service still needs operator reinstallation and native verification.
Browser/PipeWire discovery, demand handling, repeated clean shutdown, inherited-FD
GStreamer output and CPU/power still require tests. The manual filesink stream is
unchanged. No kernel, libcamera, GStreamer or proprietary-library rebuild is needed.

## Behaviour

- `ciri-camera.service` starts at boot when enabled. It loads the already-installed
  matching v4l2loopback module without reloading/replacing an existing module,
  then ensures `mtk_imgsys` and `mtk-aie` are loaded before enumeration.
- The isolated standard client enumerates physical cameras before any producer
  starts. Boot registration is retried for a bounded period; CameraManager is not
  repeatedly enumerated while capture is running.
- Two dynamically numbered, driver-verified, exclusive-capability loopbacks get
  labels **Ciri rear camera** and **Ciri front camera**. Udev provides
  `/dev/ciri-camera-back` and `/dev/ciri-camera-front`, video-group permissions and
  active-local-seat `uaccess`. No fixed `/dev/video115` assumption.
- Each endpoint keeps one OUTPUT file description streaming, writing a cached
  640×480 YUYV black frame once per second when inactive. Idle userspace output
  is approximately 1.23 MB/s for both nodes, not a measured CPU/power estimate.
- The pinned driver's private capture STREAMON/OFF event drives capture. Merely
  opening a descriptor to discover cameras does not start physical capture.
- A real capture request starts matching `libcamerasrc → bounded leaky queue →
  videoconvert → YUY2 → fdsink`. This is standard GStreamer, not a new libcamera
  buffer-allocation/request loop. `fdsink` receives the SAME inherited OUTPUT
  file description held by the service; closing the pipeline therefore does not
  hide the webcam or break fresh-client negotiation. No MMAP/RW `v4l2sink`.
- Only **one physical camera pipeline** runs at once. The first requesting camera
  keeps the shared ISP. If the other is also requested, it supplies black frames
  and reports `waiting-for-isp`; it does not interrupt the first. After the first
  stops streaming, the waiting camera starts following shutdown/debounce.
- Physical capture stops two seconds after the driver reports stream inactivity.
  Quick reopen cancels that debounce. Applications retaining STREAMON without
  reading still count as active; this is stream ownership, not literal opens or
  a count of arbitrary simultaneous readers.
- Before returning to idle, every loopback output-ring slot is overwritten with
  black. Failed backends do not spin/restart repeatedly: close the affected client,
  wait for idle, then reopen to retry. Journal/status exposes the failure.

The module version is checked as **0.15.4**. Its client-usage event ABI is private
and version-pinned. `keep_format=1` without an active OUTPUT stream is insufficient
for reliable fresh-client negotiation in this source; the held descriptor avoids
that trap. The loopback driver permits one independent capture-buffer owner per
node, not unlimited concurrent capture applications. Two real simultaneous
front/rear producers are intentionally unsupported.

No recovered libraries, core aliases, IPA, algorithms, tuning, audio, kernel,
firmware, old clients or manual launchers are replaced. Normal launches explicitly
exclude ODT configuration. Nodes are not deleted and modules are not unloaded on
stop: external clients may still own descriptors. Format retention is released
on close, allowing buffers to be freed when no capture owner remains. Managed labels are reused after
restart and their configuration is checked before writing. Close clients before
restarting the service or using standalone diagnostics. Do not run the old manual
physical producer concurrently with this service.

## Prepare and transfer — build host, operator-run

```bash
bash scripts/stage-ciri-camera-service.sh
```

Use the printed fresh `build/camera/updates/camera-service.XXXXXXXX` directory.
It contains only authored service/installer/configuration text and SHA256SUMS.
No compilation or runtime assembly is needed. Transfer to a fresh Ciri RAM
directory, substituting the actual kit:

```bash
kit=build/camera/updates/camera-service.XXXXXXXX
tar -C "$kit" -cf - . | ssh jono@192.168.0.191 \
  'work=$(mktemp -d /dev/shm/ciri-camera-service.XXXXXXXX) && tar -C "$work" -xf - && printf "Kit on Ciri: %s\n" "$work"'
```

The assistant does not execute this SSH/transfer or the following device actions.

## Install and enable — on Ciri, operator-run

**Stop the existing manual producer first** (Ctrl-C in its terminal), and close
camera applications. Do not use broad process-kill commands. If upgrading this
service, stop it first with `sudo systemctl stop ciri-camera.service`.

```bash
kit=/dev/shm/ciri-camera-service.XXXXXXXX  # actual transferred location
sudo python3 -I "$kit/install-service.py" "$kit" &&
  sudo systemctl daemon-reload &&
  sudo udevadm control --reload-rules &&
  sudo systemctl enable --now ciri-camera.service
```

The installer verifies the exact kit inventory, complete installed camera runtime
manifest and all three native-tested core hashes. It refuses symlinks, unknown
collisions and altered service code; production installation paths must be
root-owned and not group/other writable. It installs only:

- `/usr/local/lib/ciri-camera/service.py`
- `/usr/local/lib/ciri-camera/SHA256SUMS` (installed-file receipt)
- `/etc/systemd/system/ciri-camera.service`
- `/etc/udev/rules.d/70-ciri-camera.rules`
- `/etc/ciri-camera.conf`

Existing operator-edited configuration is preserved on repeat installation once
owned by the receipt. File replacements are individually atomic, **not one
whole transaction**. A partial replacement or final-sync failure must be repaired
by successfully rerunning this same kit before enabling/restarting. Sync failures
are not bypassed. No backups are created or deleted. The installer itself neither
loads a module, reloads udev/systemd, enables a unit nor starts a camera.

## Native acceptance checks

Check boot/idle first:

```bash
systemctl status ciri-camera.service --no-pager
python3 -I /usr/local/lib/ciri-camera/service.py --status
ls -l /dev/ciri-camera-back /dev/ciri-camera-front
journalctl -u ciri-camera.service -b --no-pager
```

Idle status should have `active: null`, `backend_pid: null`, and both cameras
`idle`. Source startup/runtime warnings may remain; successful preflight is not
proof of correct pixels or delivered frame rate.

Test each camera separately, closing ffplay before testing the other:

```bash
ffplay -f v4l2 /dev/ciri-camera-back
# Close it, wait for idle, then:
ffplay -f v4l2 /dev/ciri-camera-front
```

Verify real, full-height, correctly coloured images, rather than treating a live
process or file write as proof. Expect black frames during startup and initial AE
settling. Confirm status becomes idle after each close, then repeat both start/stop
cycles. With both clients open, confirm the second remains black/`waiting-for-isp`
and takes over only when the first closes. Finally test browser/PipeWire device
listing and actual capture; discovery alone should not start a physical producer.
Check a reboot only after these checks pass.

For diagnostics, stop the service and close all its clients before using standard
`cam`/RAM test wrappers. Do not point the old manual launcher at service-owned
loopbacks while the service holds their OUTPUT description. No camera-related
kernel/module rebuild is part of this acceptance test.

## Use as a system webcam

The service already exposes ordinary V4L2 capture devices. ffplay is only a test
client: close it before opening another independent capture application.
Applications should list **Ciri front camera** and **Ciri rear camera**; native
path-based clients can use `/dev/ciri-camera-front` and `/dev/ciri-camera-back`.
No separate FFmpeg producer, OBS virtual camera or replacement system libcamera
is required.

- In a browser/video-call app, grant camera permission and select the desired
  Ciri camera in its device settings. Restart/reopen an app that cached the list
  before the service started.
- In OBS, add a **Video Capture Device (V4L2)** source and select the Ciri device.
- If manual format selection is offered, use 640×480 YUYV/YUY2 and requested
  18 fps, or negotiate automatically. ffplay's buffering flags are specific to
  that player and are not system webcam configuration.

Browser/OBS/PipeWire/Flatpak integration is not yet operator-confirmed. Sandboxed
or PipeWire-only applications may need camera portal/device-discovery checks;
diagnose the actual application's path before changing packages or restarting
PipeWire/WirePlumber, which also manage the working audio. The two physical
cameras still share one active ISP pipeline; a competing second camera waits
with black frames. Same-node independent capture clients can also contend for
the loopback driver's single capture-buffer owner.

## Measure CPU without extra packages

The read-only profile mode uses `/proc` for the service and owned backend process
session. At stable idle, run:

```bash
python3 -I /usr/local/lib/ciri-camera/service.py --profile 10
```

Repeat with rear capture running and settled, then front capture. **100% means one
Linux CPU core**, not the whole machine. The measurement includes the service,
standard GStreamer client and IPA/supervisor processes, including their syscall
CPU time. It excludes ffplay/browser rendering, independent kernel threads and
ISP/SCP/sensor power. A process/camera lifecycle change rejects the sample instead
of presenting a misleading percentage. It does not measure actual FPS, latency,
RAM, battery use or thermal throttling. CPU impact is not yet measured on Ciri.

## Configuration and stop

Root may edit `/etc/ciri-camera.conf`: `CIRI_CAMERAS=back,front` (or just `back` or
`front`), `CIRI_FPS=18` (15–30 requested, not guaranteed), and
`CIRI_IDLE_SECONDS=2` (0–30). Close clients and restart after changes:

```bash
sudo systemctl restart ciri-camera.service
```

To stop capture and prevent subsequent boot startup without deleting anything:

```bash
sudo systemctl disable --now ciri-camera.service
```

The shutdown signal reaches the supervisor first, allowing it to drain GStreamer
and then reap the IPA. A timeout kills only the owned backend process group;
systemd's mixed kill mode supplies a final service-cgroup fallback.

## Local verification

`python3 -I -m unittest discover -s tests -v` passes **147 tests**, including 14
service tests and five ISP module restoration tests. The ISP tests use synthetic
archives/modules and mocked dependency tools to cover selective extraction,
collision/symlink/dependency refusal, loopback preservation, idempotence,
partial-publication repair and fatal sync/depmod errors. No real module is loaded.
The service tests verify native Linux ioctl structure/offset constants with
an authored C fixture, output format guards, event coalescing, held-FD priming and
publication order, idle/start/stop/debounce/one-ISP scheduling, failure policy,
shutdown signals, CPU units, unit syntax, runtime preservation, installer refusal,
partial-receipt repair and fatal sync errors. All device/process/driver operations
in service tests are synthetic. This is not a native service/colour/browser result.
