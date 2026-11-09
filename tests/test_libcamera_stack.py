"""Test library-side Ciri compatibility without executing vendor libraries."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LibcameraStackTests(unittest.TestCase):
    def test_capture_script_lists_keep_libcamera_control_id_map(self):
        patch = (ROOT / "config/libcamera/ciri.patch").read_text()
        client_patch = patch.split("diff --git a/src/apps/cam/capture_script.cpp", 1)[1].split("diff --git ", 1)[0]
        added = "\n".join(line[1:] for line in client_patch.splitlines()
                          if line.startswith("+") and not line.startswith("+++"))
        self.assertIn("#include <libcamera/control_ids.h>", added)
        self.assertIn("static ControlList controls{ controls::controls };", added)
        self.assertIn("\n\tControlList controls{ controls::controls };", added)
        self.assertNotIn("ControlList controls{};", added)
        self.assertNotIn("camera_->controls()", added)

    def test_patch_routes_standard_allocator_and_keeps_errors(self):
        patch = (ROOT / "config/libcamera/ciri.patch").read_text()
        added = "\n".join(line[1:] for line in patch.splitlines()
                          if line.startswith("+") and not line.startswith("+++"))
        self.assertIn("ciriExportFrameBuffers(stream->configuration(), dmaHeap_.get(), buffers)", added)
        self.assertIn("frame.status == FrameMetadata::FrameSuccess", added)
        self.assertIn("frame.planes().size() == planes.size()", added)
        self.assertIn("if (!frame.planes()[p].bytesused)", added)
        self.assertIn("request_->sequence()", added)
        self.assertIn("controls::SensorTimestamp", added)
        self.assertIn("cc.find_library('mtkcam_metadata', required : true)", added)
        self.assertIn("if (!gCrosMojoToken)", added)
        self.assertIn("return -ENXIO", added)
        self.assertIn("ret < 0 && errno == EINTR", added)
        self.assertNotIn("frame.status = FrameMetadata::FrameSuccess", added)

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_standard_buffers_bounds_and_partial_allocation_cleanup(self):
        header = (ROOT / "config/libcamera/ciri_dma_buffers.h").read_text()
        body = re.sub(r"^#(?:include|pragma).*\n", "", header, flags=re.MULTILINE)
        fixture = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <limits>
#include <memory>
#include <unistd.h>
#include <vector>
namespace libcamera {
namespace formats { constexpr int NV12 = 1; }
struct StreamConfiguration {
    int pixelFormat = formats::NV12;
    struct { unsigned int width = 640, height = 480; } size;
    unsigned int stride = 640, bufferCount = 8;
};
struct UniqueFD {
    int fd;
    explicit UniqueFD(int value = -1) : fd(value) {}
    UniqueFD(const UniqueFD &) = delete;
    UniqueFD(UniqueFD &&other) : fd(other.fd) { other.fd = -1; }
    ~UniqueFD() { if (fd >= 0) close(fd); }
};
struct SharedFD {
    std::shared_ptr<int> fd;
    explicit SharedFD(UniqueFD &&owned) {
        fd = std::shared_ptr<int>(new int(owned.fd), [](int *p) {
            if (*p >= 0) close(*p);
            delete p;
        });
        owned.fd = -1;
    }
    bool isValid() const { return *fd >= 0; }
    int get() const { return *fd; }
};
struct FrameBuffer {
    struct Plane { SharedFD fd; unsigned int offset, length, stride; };
    std::vector<Plane> planes;
    explicit FrameBuffer(const std::vector<Plane> &value) : planes(value) {}
};
struct DmaHeap {
    enum Type { System, CMA };
    bool valid = true;
    size_t calls = 0, failAt = 0;
    std::vector<int> allocated;
    bool isValid() const { return valid; }
    UniqueFD alloc(size_t length, Type type) {
        assert(type == System);
        if (++calls == failAt) return UniqueFD();
        char name[] = "/tmp/ciri-libcamera-buffer-test.XXXXXX";
        int fd = mkstemp(name);
        assert(fd >= 0 && unlink(name) == 0 && ftruncate(fd, length) == 0);
        allocated.push_back(fd);
        return UniqueFD(fd);
    }
};
} // namespace libcamera
''' + body + r'''
int main() {
    using namespace libcamera;
    StreamConfiguration config;
    DmaHeap heap;
    std::vector<std::unique_ptr<FrameBuffer>> buffers;
    assert(ciriExportFrameBuffers(config, &heap, &buffers) == 8);
    assert(buffers.size() == 8 && heap.allocated.size() == 16);
    for (const auto &buffer : buffers) {
        assert(buffer->planes.size() == 2);
        const auto &y = buffer->planes[0], &uv = buffer->planes[1];
        assert(y.fd.get() != uv.fd.get() && y.offset == 0 && uv.offset == 0);
        assert(y.length == 307200 && uv.length == 153600);
        assert(y.stride == 640 && uv.stride == 640);
        assert(lseek(y.fd.get(), 0, SEEK_END) == 307200);
        assert(lseek(uv.fd.get(), 0, SEEK_END) == 153600);
    }
    buffers.clear();
    for (int fd : heap.allocated) assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
    heap.allocated.clear();
    heap.failAt = heap.calls + 2;
    assert(ciriExportFrameBuffers(config, &heap, &buffers) == -ENOMEM);
    assert(buffers.empty() && heap.allocated.size() == 1);
    assert(fcntl(heap.allocated[0], F_GETFD) == -1 && errno == EBADF);
    heap.failAt = 0;
    heap.valid = false;
    assert(ciriExportFrameBuffers(config, &heap, &buffers) == -ENODEV);
    assert(ciriExportFrameBuffers(config, nullptr, &buffers) == -ENODEV);
    heap.valid = true;
    assert(ciriExportFrameBuffers(config, &heap, nullptr) == -EINVAL);
    size_t calls = heap.calls;
    for (int test = 0; test < 8; ++test) {
        StreamConfiguration bad = config;
        if (test == 0) bad.pixelFormat = 2;
        if (test == 1) bad.size.width = 0;
        if (test == 2) bad.size.height = 481;
        if (test == 3) bad.size.width = 639;
        if (test == 4) bad.stride = 639;
        if (test == 5) bad.bufferCount = 0;
        if (test == 6) bad.bufferCount = 65;
        if (test == 7) { bad.stride = UINT32_MAX; bad.size.height = 480; }
        assert(ciriExportFrameBuffers(bad, &heap, &buffers) == (test == 7 ? -EOVERFLOW : -EINVAL));
    }
    assert(heap.calls == calls && buffers.empty());
    // Partial allocation must also preserve caller-owned existing buffers.
    assert(ciriExportFrameBuffers(config, &heap, &buffers) == 8);
    FrameBuffer *first = buffers[0].get();
    heap.failAt = heap.calls + 2;
    assert(ciriExportFrameBuffers(config, &heap, &buffers) == -ENOMEM);
    assert(buffers.size() == 8 && buffers[0].get() == first);
}
'''
        with tempfile.TemporaryDirectory(prefix="ciri-libcamera-stack-test.") as directory:
            work = Path(directory)
            (work / "buffers.cpp").write_text(fixture)
            compiled = subprocess.run(
                ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                 str(work / "buffers.cpp"), "-o", str(work / "buffers")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(work / "buffers")], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
