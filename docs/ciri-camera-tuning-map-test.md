# Temporary Ciri tuning-map test

This is an experiment, not a colour fix or an installation. The 2026-11-07
rear-camera log reports a missing module-specific lookup for `gc08a3_mipi_raw`,
module ID 0. Static inspection of the recovered `libmtkcam_mapping_mgr.so`
function `IdxMgrImpl::parseSnesorDBMapTbl` at address `0x17ac4` established that
it constructs **`gc08a3_mipi_raw_0` as a SensorName**, rather than filtering the
numeric `ModuleID` column. Its SELECT retrieves only `SensorName` and `DBName`,
including the unsuffixed sensor and `default_sensor` entries. When the suffixed
name is absent, its fallback prefers the unsuffixed sensor over `default_sensor`.
Therefore this warning alone is not evidence that generic or wrong-sensor tuning
was selected. An alias to the existing GC08A3 DB may only remove the warning.

The first operator test, logged in `ciri-tuning-map-20261107-212211.log`, changed
the wrong field: it added a duplicate unsuffixed row with numeric `ModuleID=0`.
It captured 64 frames and preserved the installed database checksum, but the
lookup warning remained and frames 60–63 reported AWB searching (1). The operator
reports that the saved image is still green; the assistant has not visually
inspected that image. Those state differences cannot be attributed to the alias
without controlling the scene/lighting and confirming applied tuning.

The corrected `scripts/test-ciri-camera-tuning-map.sh` opens the installed
database read-only, validates the original GC08A3/2211/MT8188 entry, and clones
it into a fresh root-private temporary directory. It adds only the suffixed
SensorName `gc08a3_mipi_raw_0`, pointing to the existing `gc08a3_mipi_raw` DBName,
retaining the unsuffixed entry and every other tuning entry. The clone is
bind-mounted read-only over the database path inside a private mount namespace.
The existing launcher inherits that mount. After the test namespace exits, the
original namespace checks that the installed database checksum is unchanged.
No installed launcher, manifest, library, kernel or database is replaced. The
temporary clone is retained for inspection; the mount does not persist.

The corrected test is available for explicit lookup diagnostics; a further
capture solely to silence this warning is not currently justified as a colour
fix. The next diagnostic is described in `ciri-camera-colour-bars.md`.

Stop any existing camera producer before running. On the build host:

```bash
scp scripts/test-ciri-camera-tuning-map.sh jono@192.168.0.191:/home/jono/
ssh -t jono@192.168.0.191 'sudo bash /home/jono/test-ciri-camera-tuning-map.sh' 2>&1 | tee "ciri-tuning-map-$(date +%Y%m%d-%H%M%S).log"
```

The standard `cam` client captures 64 rear-camera NV12 frames at 640×480, prints
request metadata, and saves frames in the fresh `Output directory:` printed by
the launcher. The capture files total approximately 30 MB. Capture failures
remain failures even if the final checksum-preservation check succeeds.

To decode the late frame on Ciri, substitute the actual printed run directory:

```bash
sudo ffmpeg -n -f rawvideo -pixel_format nv12 -video_size 640x480 \
  -i /opt/ciri-libcamera/captures/run.XXXXXXXX/frame-cam0-stream0-000063.nv12 \
  -frames:v 1 "$HOME/ciri-tuning-map-test.png"
```

Then retrieve that PNG from the build host:

```bash
( set -C; ssh jono@192.168.0.191 'base64 /home/jono/ciri-tuning-map-test.png' | base64 --decode > ciri-tuning-map-test.png )
```

The transfer refuses to overwrite an existing local PNG. The decode also refuses
to overwrite an existing device-side PNG. Compare the pixels with the earlier
green/dark-upper-half image, and check whether the GC08A3 module-0 lookup warning
disappeared. AWB state 2, successful capture, a resolved lookup, or an unchanged
installed checksum alone does not establish correct colour. No colour filter or
chroma swap is applied. Stopping or completing this test restores normal tuning
selection for the next ordinary launcher invocation without a reinstall.
