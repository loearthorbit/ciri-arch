"""Test explicit request gains without executing recovered vendor code."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/ciri-camera-probe.cpp"


class CameraColourControlTests(unittest.TestCase):
    def test_every_request_sets_gains_before_adding_its_buffer(self):
        source = SOURCE.read_text()
        loop = re.search(r"for \(const auto &buffer : buffers_\) \{.*?requests_\.push_back", source, re.DOTALL)
        self.assertIsNotNone(loop)
        self.assertIn("setColourControls(request->controls());", loop.group(0))
        self.assertLess(loop.group(0).index("setColourControls"), loop.group(0).index("request->addBuffer"))

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_all_four_unity_gains_replace_zero_fourth_gain(self):
        source = SOURCE.read_text()
        method = re.search(r"static void setColourControls\(ControlList &controls\)\n\{.*?\n\}", source, re.DOTALL)
        self.assertIsNotNone(method)
        with tempfile.TemporaryDirectory(prefix="ciri-colour-controls-test.") as directory:
            work = Path(directory)
            (work / "colour.cpp").write_text(r'''
#include <array>
namespace controls::draft { constexpr int ColorCorrectionGains = 17; }
struct ControlList {
    bool setCalled = false;
    std::array<float, 4> gains{ 1.0f, 1.0f, 1.0f, 0.0f };
    void set(int id, const std::array<float, 4> &values) {
        if (id != controls::draft::ColorCorrectionGains) return;
        setCalled = true;
        gains = values;
    }
};
''' + method.group(0) + r'''
int main()
{
    ControlList request;
    if (request.gains[3] != 0.0f) return 1;
    setColourControls(request);
    if (!request.setCalled) return 2;
    for (float gain : request.gains) if (gain != 1.0f) return 3;
    return 0;
}
''')
            compiled = subprocess.run(
                ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                 str(work / "colour.cpp"), "-o", str(work / "colour")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(work / "colour")], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
