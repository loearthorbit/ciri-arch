"""Exercise the optional gyro override against a mock DSO, never vendor code."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "_ZN9libcamera10GyroSensor4initENS0_8LocationE"


class CameraGyroTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_executable_override_returns_enxio_through_shared_library_call(self):
        source = (ROOT / "scripts/ciri-camera-probe.cpp").read_text()
        override = re.search(r"int GyroSensor::init\(Location\)\n\{.*?\n\}",
                             source, re.DOTALL)
        self.assertIsNotNone(override)
        recipe = (ROOT / "scripts/build-ciri-camera-client.sh").read_text()
        self.assertIn(f"gyro_symbol={SYMBOL}", recipe)
        self.assertIn('--export-dynamic-symbol="$gyro_symbol"', recipe)
        with tempfile.TemporaryDirectory(prefix="ciri-gyro-test.") as directory:
            work = Path(directory)
            (work / "gyro.h").write_text(
                '#pragma once\nnamespace libcamera {\n'
                'class GyroSensor { public: enum class Location { kLid = 2 };\n'
                'int init(Location); };\n}\n'
            )
            # Separate translation units prevent compiler inlining from hiding
            # the PLT call that is present in the recovered libcamera.
            (work / "mock.cpp").write_text(
                '#include "gyro.h"\n'
                'int libcamera::GyroSensor::init(Location) { return 999; }\n'
            )
            (work / "caller.cpp").write_text(
                '#include "gyro.h"\n'
                'extern "C" int initialize_gyro() {\n'
                'libcamera::GyroSensor sensor;\n'
                'return sensor.init(libcamera::GyroSensor::Location::kLid);\n}\n'
            )
            (work / "probe.cpp").write_text(
                '#include <cerrno>\n#include <iostream>\n#include "gyro.h"\n'
                'namespace libcamera {\n' + override.group(0) + '\n}\n'
                'extern "C" int initialize_gyro();\n'
                'int main() { return initialize_gyro() == -ENXIO ? 0 : 1; }\n'
            )
            subprocess.run(
                ["c++", "-shared", "-fPIC", "-O2", str(work / "mock.cpp"),
                 str(work / "caller.cpp"), "-o", str(work / "libgyro-mock.so")],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["c++", "-std=c++17", "-O2", str(work / "probe.cpp"),
                 f"-L{work}", "-lgyro-mock", f"-Wl,-rpath,{work}",
                 f"-Wl,--export-dynamic-symbol={SYMBOL}",
                 "-o", str(work / "probe")],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run([str(work / "probe")], capture_output=True,
                                    text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ChromeOS gyroscope disabled (no Mojo token)", result.stderr)


if __name__ == "__main__":
    unittest.main()
