#!/bin/bash
# Temporary rear-camera SensorDBMap alias; no installed files are replaced.
set -euo pipefail
[[ $# == 0 && $EUID == 0 && $(uname -m) == aarch64 ]] || {
    printf 'Run with sudo on Ciri, with no arguments. Stop the camera producer first.\n' >&2
    exit 2
}
prefix=/opt/ciri-libcamera
db=$prefix/rootfs/etc/camera/bin/crossbuild/DataSet/SQLiteModule/db/tuning_DB/ISP_info.db
launcher=$prefix/run-libcamera
[[ -f $db && ! -L $db && -f $launcher && ! -L $launcher && -x $launcher ]] || {
    printf 'Installed camera launcher or regular tuning database missing.\n' >&2
    exit 1
}
for tool in unshare mount mktemp python3 sha256sum; do command -v "$tool" >/dev/null; done
before=$(sha256sum "$db")
status=0
unshare --mount --propagation private /bin/bash -s -- "$db" "$launcher" <<'NAMESPACE' || status=$?
set -euo pipefail
umask 077
db=$1
launcher=$2
work=$(mktemp -d /tmp/ciri-tuning-map.XXXXXXXX)
printf 'Test-only database directory (retained): %s\n' "$work"
python3 -I - "$db" "$work/ISP_info.db" <<'PY'
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

source, destination = map(Path, sys.argv[1:])
sensor = "gc08a3_mipi_raw"
# The vendor query selects SensorName/DBName, not the numeric ModuleID column.
# parseSnesorDBMapTbl builds this module-specific name, then falls back to sensor.
sensor_alias = sensor + "_0"
with closing(sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as original:
    rows = original.execute(
        "SELECT SensorName, ModuleID, DBName, ChipID FROM db_SensorDBMap WHERE SensorName=? AND ModuleID=?",
        (sensor, 2211),
    ).fetchall()
    if rows != [(sensor, 2211, sensor, "MT8188")]:
        raise SystemExit("Expected exactly one GC08A3/2211/MT8188 tuning entry; nothing mounted")
    if original.execute("SELECT 1 FROM db_SensorDBMap WHERE SensorName=?", (sensor_alias,)).fetchone():
        raise SystemExit("GC08A3 suffixed-name mapping already exists; refusing to replace it")
    # Reserve a fresh destination. The installed database is opened read-only.
    with destination.open("xb"):
        pass
    with closing(sqlite3.connect(str(destination))) as alias:
        original.backup(alias)
        with alias:
            alias.execute(
                "INSERT INTO db_SensorDBMap (SensorName, ModuleID, DBName, ChipID) VALUES (?, 0, ?, ?)",
                (sensor_alias, rows[0][2], rows[0][3]),
            )
        print("Temporary name alias: gc08a3_mipi_raw_0 -> existing gc08a3_mipi_raw/MT8188 tuning; unsuffixed entry retained", flush=True)
PY
mount --bind "$work/ISP_info.db" "$db"
mount -o remount,bind,ro "$db"
printf 'Alias mounted read-only in this test namespace only. Capturing 64 frames.\n'
exec "$launcher" --camera /base/soc/i2c@11ec1000/camera-main-sensor@31 \
    --stream role=viewfinder,width=640,height=480,pixelformat=NV12 \
    --strict-formats --capture=64 --metadata --file='frame-#.nv12'
NAMESPACE
# This runs back in the original namespace, including after a failed capture.
[[ $(sha256sum "$db") == "$before" ]] || {
    printf 'ERROR: original installed database checksum changed.\n' >&2
    exit 1
}
printf 'Original installed database checksum unchanged; temporary mount namespace exited.\n'
exit "$status"
