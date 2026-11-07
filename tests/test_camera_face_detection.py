"""Test face-detection ELF overrides against a mock DSO, never vendor code."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = (
    "_ZN9libcamera12FaceDetector5startEv",
    "_ZN9libcamera12FaceDetector4stopEv",
    "_ZN9libcamera14FaceDetectTask3runEv",
)


class CameraFaceDetectionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_hardware_calls_skipped_and_virtual_task_still_completes(self):
        recipe = (ROOT / "scripts/build-ciri-camera-client.sh").read_text()
        for symbol in SYMBOLS:
            self.assertIn(symbol, recipe)
        self.assertIn('"${face_exports[@]}"', recipe)
        self.assertIn('"$repo/scripts/ciri-camera-no-face-detection.cpp"', recipe)
        with tempfile.TemporaryDirectory(prefix="ciri-face-test.") as directory:
            work = Path(directory)
            header = work / "libcamera/internal/task_scheduler.h"
            header.parent.mkdir(parents=True)
            header.write_text('''#pragma once
namespace libcamera {
class Task {
public:
    virtual ~Task() = default;
    virtual void notifyDone();
    virtual void run() = 0;
    bool done = false;
};
class FaceDetectTask : public Task { public: void run() override; };
class FaceDetector { public: int start(); int stop(); };
}
''')
            (work / "mock.cpp").write_text('''
#include <cstdlib>
#include <libcamera/internal/task_scheduler.h>
void libcamera::Task::notifyDone() { done = true; }
void libcamera::FaceDetectTask::run() { std::abort(); }
int libcamera::FaceDetector::start() { return 999; }
int libcamera::FaceDetector::stop() { return 999; }
extern "C" libcamera::Task *make_face_task() { return new libcamera::FaceDetectTask; }
''')
            # Separate translation units preserve PLT and virtual dispatch.
            (work / "caller.cpp").write_text('''
#include <libcamera/internal/task_scheduler.h>
extern "C" libcamera::Task *make_face_task();
extern "C" int exercise_face_detector() {
    libcamera::FaceDetector detector;
    if (detector.start() != 0) return 1;
    libcamera::Task *task = make_face_task();
    task->run();
    bool done = task->done;
    delete task;
    if (!done) return 2;
    return detector.stop() == 0 ? 0 : 3;
}
''')
            (work / "probe.cpp").write_text('''
extern "C" int exercise_face_detector();
int main() { return exercise_face_detector(); }
''')
            common = ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{work}"]
            subprocess.run(
                common + ["-shared", "-fPIC", str(work / "mock.cpp"),
                          str(work / "caller.cpp"), "-o", str(work / "libface-mock.so")],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                common + [str(ROOT / "scripts/ciri-camera-no-face-detection.cpp"),
                          str(work / "probe.cpp"), f"-L{work}", "-lface-mock",
                          f"-Wl,-rpath,{work}", "-o", str(work / "probe")]
                + [f"-Wl,--export-dynamic-symbol={symbol}" for symbol in SYMBOLS],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run([str(work / "probe")], capture_output=True,
                                    text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AIE face detection disabled", result.stderr)


if __name__ == "__main__":
    unittest.main()
