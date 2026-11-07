"""Test raw-buffer checks with regular temporary files, never vendor code."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/ciri-camera-probe.cpp"


class CameraCaptureBufferTests(unittest.TestCase):
    def test_wait_drains_requests_even_after_a_failed_frame(self):
        source = SOURCE.read_text()
        wait = re.search(r"ready_\.wait_for\(.*?\n        \}\);", source, re.DOTALL)
        self.assertIsNotNone(wait)
        self.assertIn("return completed_ == queued_ && (queued_ == target_ || queueStopped_);", wait.group(0))
        self.assertNotIn("return failed_ ||", wait.group(0))
        cancelled = re.search(r"if \(request->status\(\) == Request::RequestCancelled\) \{.*?\n        \}",
                              source, re.DOTALL)
        self.assertIsNotNone(cancelled)
        self.assertIn("failed_ = true;", cancelled.group(0))
        self.assertIn("queueStopped_ = true;", cancelled.group(0))
        self.assertIn("++completed_;", cancelled.group(0))
        self.assertIn("ready_.notify_all();", cancelled.group(0))

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_nv12_planes_have_independent_zero_offset_allocations(self):
        source = SOURCE.read_text()
        allocate = re.search(r"    bool allocateBuffers\(.*?\n    \}", source, re.DOTALL)
        prime = re.search(r"    bool primeBuffer\(.*?\n    \}", source, re.DOTALL)
        pattern = re.search(r"static unsigned char unwrittenByte\(.*?\n\}", source, re.DOTALL)
        report = re.search(r"static int report\(.*?\n\}", source, re.DOTALL)
        for method in (allocate, prime, pattern, report):
            self.assertIsNotNone(method)
        # Substitute only heap open/allocation; use real authored sizing,
        # ownership, per-plane layout and priming with regular-file backing.
        allocator = allocate.group(0).replace('open("/dev/dma_heap/system",', 'mockHeapOpen("/dev/dma_heap/system",')
        allocator = allocator.replace('ioctl(heap.get(), DMA_HEAP_IOCTL_ALLOC,', 'mockAllocate(heap.get(), DMA_HEAP_IOCTL_ALLOC,')
        with tempfile.TemporaryDirectory(prefix="ciri-plane-allocation-test.") as directory:
            work = Path(directory)
            (work / "allocation.cpp").write_text(r'''
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <vector>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
struct SharedFD {
    std::shared_ptr<int> fd;
    explicit SharedFD(int value) : fd(new int(value), [](int *p) { if (*p >= 0) close(*p); delete p; }) {}
    int get() const { return *fd; }
    bool isValid() const { return get() >= 0; }
};
using UniqueFD = SharedFD;
struct FrameBuffer {
    struct Plane { SharedFD fd; unsigned int offset; unsigned int length; unsigned int stride; };
    std::vector<Plane> planes;
    explicit FrameBuffer(const std::vector<Plane> &value) : planes(value) {}
};
struct Format { std::string toString() const { return "NV12"; } };
struct StreamConfiguration {
    Format pixelFormat;
    struct { unsigned int width = 640; unsigned int height = 480; } size;
    unsigned int stride = 640;
    unsigned int bufferCount = 8;
};
static std::string directory;
static std::vector<int> allocations;
static bool failSecond = false;
static int mockHeapOpen(const char *, int flags) { return open("/dev/null", flags); }
static int mockAllocate(int, unsigned long command, dma_heap_allocation_data *data)
{
    if (command != DMA_HEAP_IOCTL_ALLOC || data->fd_flags != (O_RDWR | O_CLOEXEC)) return -1;
    if (failSecond && allocations.size() == 1) { errno = ENOMEM; return -1; }
    std::string path = directory + "/plane-" + std::to_string(allocations.size());
    int fd = open(path.c_str(), O_RDWR | O_CLOEXEC | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return -1;
    if (ftruncate(fd, data->len)) { close(fd); return -1; }
    data->fd = fd;
    allocations.push_back(fd);
    return 0;
}
''' + pattern.group(0) + '\n' + report.group(0) + '\n'
                + 'class Capture { public:\n' + allocator + '\n' + prime.group(0)
                + '\nstd::vector<std::unique_ptr<FrameBuffer>> buffers_;\n};\n' + r'''
int main(int argc, char **argv)
{
    if (argc != 2) return 1;
    directory = argv[1];
    StreamConfiguration config;
    Capture capture;
    if (!capture.allocateBuffers(config) || capture.buffers_.size() != 8 || allocations.size() != 16) return 2;
    for (const auto &buffer : capture.buffers_) {
        if (buffer->planes.size() != 2) return 3;
        if (buffer->planes[0].fd.get() == buffer->planes[1].fd.get()) return 4;
        for (unsigned int p = 0; p < 2; ++p) {
            const auto &plane = buffer->planes[p];
            struct stat info{};
            unsigned int expected = p ? 153600 : 307200;
            if (plane.offset || plane.length != expected || plane.stride != 640) return 5;
            if (fstat(plane.fd.get(), &info) || info.st_size != expected) return 6;
            unsigned char last;
            if (pread(plane.fd.get(), &last, 1, expected - 1) != 1 || last != unwrittenByte(expected - 1)) return 7;
        }
    }
    capture.buffers_.clear();
    for (int fd : allocations) if (fcntl(fd, F_GETFD) != -1 || errno != EBADF) return 8;
    // Failed second plane must release the first without retaining a partial frame.
    directory += "/failure";
    if (mkdir(directory.c_str(), 0700)) return 9;
    allocations.clear();
    failSecond = true;
    if (capture.allocateBuffers(config) || !capture.buffers_.empty() || allocations.size() != 1) return 10;
    if (fcntl(allocations[0], F_GETFD) != -1 || errno != EBADF) return 11;
    return 0;
}
''')
            compiled = subprocess.run(
                ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                 str(work / "allocation.cpp"), "-o", str(work / "allocation")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(work / "allocation"), str(work)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_zero_payload_metadata_needs_changed_dma_plane_and_bounds(self):
        source = SOURCE.read_text()
        pattern = re.search(r"static unsigned char unwrittenByte\(size_t index\)\n\{.*?\n\}",
                            source, re.DOTALL)
        report = re.search(r"static int report\(const char \*operation, int error\)\n\{.*?\n\}",
                           source, re.DOTALL)
        prime = re.search(r"    bool primeBuffer\(int fd, size_t length\)\n    \{.*?\n    \}",
                          source, re.DOTALL)
        save = re.search(r"    bool savePlane\(.*?\n    \}", source, re.DOTALL)
        for method in (pattern, report, prime, save):
            self.assertIsNotNone(method)
        with tempfile.TemporaryDirectory(prefix="ciri-buffer-test.") as directory:
            work = Path(directory)
            # Change only the output directory in the extracted authored method.
            save_text = save.group(0).replace('"/captures/frame-"', '"' + str(work) + '/frame-"')
            (work / "probe.cpp").write_text('''
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
struct FD { int value; int get() const { return value; } };
struct FrameBuffer { struct Plane { FD fd; unsigned int offset; unsigned int length; }; };
''' + pattern.group(0) + '\n' + report.group(0) + '\n'
                + 'class Capture { public:\n' + prime.group(0) + '\n' + save_text + '\n};\n'
                + r'''
int main(int argc, char **argv)
{
    if (argc != 2) return 1;
    int fd = open(argv[1], O_RDWR | O_CREAT | O_EXCL, 0600);
    if (fd < 0 || ftruncate(fd, 64)) return 2;
    Capture capture;
    if (!capture.primeBuffer(fd, 64)) return 3;
    unsigned char initial[64];
    if (pread(fd, initial, 64, 0) != 64) return 4;
    for (size_t i = 0; i < 64; ++i)
        if (initial[i] != unwrittenByte(i)) return 5;
    FrameBuffer::Plane plane{{fd}, 16, 17};
    if (capture.savePlane(plane, 0, 0, 1)) return 6; // untouched cannot succeed
    unsigned char changed = initial[23] ^ 0xff;
    if (pwrite(fd, &changed, 1, 23) != 1) return 7;
    if (!capture.savePlane(plane, 0, 1, 1)) return 8; // fallback uses fixed length
    if (capture.savePlane(plane, 18, 2, 1)) return 9; // reject oversize metadata
    plane.offset = std::numeric_limits<unsigned int>::max();
    if (capture.savePlane(plane, 0, 3, 1)) return 10;
    plane.offset = 0;
    plane.length = 16;
    if (capture.savePlane(plane, 0, 4, 0)) return 11; // other plane still untouched
    plane.offset = 16;
    plane.length = 17;
    if (!capture.savePlane(plane, 17, 5, 1)) return 12; // valid reported length
    close(fd);
    return 0;
}
''')
            compiled = subprocess.run(
                ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                 str(work / "probe.cpp"), "-o", str(work / "probe")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(work / "probe"), str(work / "dma-file")],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DMA memory untouched", result.stderr)
            self.assertIn("bytesused unset", result.stderr)
            expected = (work / "dma-file").read_bytes()[16:33]
            self.assertEqual((work / "frame-1-plane-1.bin").read_bytes(), expected)
            self.assertEqual((work / "frame-5-plane-1.bin").read_bytes(), expected)
            for cookie, plane in ((0, 1), (2, 1), (3, 1), (4, 0)):
                self.assertFalse((work / f"frame-{cookie}-plane-{plane}.bin").exists())


if __name__ == "__main__":
    unittest.main()
