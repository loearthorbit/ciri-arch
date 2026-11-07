"""Test runtime config creation without mounts or executing vendor libraries."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts/run-ciri-camera-probe.sh"


class CameraLauncherTests(unittest.TestCase):
    def test_model_and_readonly_udev_database_available_before_client_exec(self):
        source = LAUNCHER.read_text()
        match = re.search(r"/bin/bash -c '\n(.*?)\n' ciri-camera-probe", source, re.DOTALL)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory(prefix="ciri-launcher-test.") as directory:
            work = Path(directory)
            rootfs = work / "rootfs"
            for name in ("dev", "proc", "sys", "run", "tmp"):
                (rootfs / name).mkdir(parents=True)
            tools = work / "bin"
            tools.mkdir()
            # Override both mount and env. In particular, env must NOT dispatch
            # chroot/the recovered executable; it checks our fixture instead.
            mount = tools / "mount"
            mount.write_text(
                '#!/bin/bash\nset -euo pipefail\n'
                'printf "%s\\n" "$*" >> "$TEST_MOUNT_LOG"\n'
                'if [[ $1 == --bind && $2 == /run/udev/data ]]; then\n'
                '    printf "initialized\\n" > "$3/c81:0"\n'
                'fi\n'
            )
            env_tool = tools / "env"
            env_tool.write_text(
                '#!/bin/bash\nset -euo pipefail\n'
                'read -r model < "$TEST_ROOT/run/chromeos-config/v1/name"\n'
                '[[ $model == ciri ]]\n'
                '[[ -d $TEST_ROOT/run/camera ]]\n'
                '[[ -f $TEST_ROOT/run/udev/data/c81:0 ]]\n'
                'printf "private model and udev database verified\\n"\n'
            )
            mount.chmod(0o755)
            env_tool.chmod(0o755)
            environment = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                               TEST_ROOT=str(rootfs), TEST_MOUNT_LOG=str(work / "mount.log"),
                               LIBCAMERA_LOG_LEVELS="*:DEBUG")
            result = subprocess.run(
                ["/bin/bash", "-c", match.group(1), "test", str(rootfs), "--list"],
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "private model and udev database verified\n")
            self.assertEqual((rootfs / "run/chromeos-config/v1/name").read_text(), "ciri\n")
            calls = (work / "mount.log").read_text().splitlines()
            self.assertIn(f"--bind /run/udev/data {rootfs}/run/udev/data", calls)
            self.assertIn(f"-o remount,bind,ro,nosuid,nodev,noexec {rootfs}/run/udev/data", calls)
            self.assertFalse(any("/run/udev/control" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
