"""P2 plane allocation tests using authored code and inert native FD fixtures.

No libcamera cross-build, DMA heaps, recovered libraries or camera execution.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def patch_section(path):
    patch = (ROOT / 'config/libcamera/ciri.patch').read_text()
    return patch.split(f'diff --git a/{path} b/{path}\n', 1)[1].split('diff --git ', 1)[0]


def additions(section):
    return '\n'.join(line[1:] for line in section.splitlines()
                     if line.startswith('+') and not line.startswith('+++'))


class CameraP2BufferTests(unittest.TestCase):
    def test_only_mcnr_multiplane_call_sites_change_and_arguments_are_preserved(self):
        section = patch_section('src/libcamera/pipeline/mtkisp7/imgsys/mcnr.cpp')
        old = [line[1:] for line in section.splitlines()
               if line.startswith('-') and not line.startswith('---')]
        new = [line[1:] for line in section.splitlines()
               if line.startswith('+') and not line.startswith('+++')]
        self.assertEqual(len(old), 14)
        self.assertEqual(new, [line.replace('.createBuffers(', '.createSeparatePlaneBuffers(')
                               for line in old])
        expected = {f'meMmap{i}_' for i in range(4)} | {'idi_', 'img4oF0_', 'img4oF1_'}
        expected |= {f'img3o_[{i}]' for i in range(7)}
        self.assertEqual({line.strip().split('.', 1)[0] for line in new}, expected)
        for line in new:
            self.assertRegex(line, r'formats::(?:WARP2P_MTISP|NV21|NV12_(?:10|12)P_MTISP),')

    def test_contiguous_allocator_lazy_pools_and_class_layout_remain_unchanged(self):
        for path in ('include/libcamera/internal/info_frame.h', 'src/libcamera/info_frame.cpp'):
            section = patch_section(path)
            self.assertFalse(any(line.startswith('-') and not line.startswith('---')
                                 for line in section.splitlines()))
        header = additions(patch_section('include/libcamera/internal/info_frame.h'))
        self.assertIn('int createSeparatePlaneBuffers(', header)
        self.assertNotIn('virtual', header)
        self.assertNotIn('bool ', header)  # No new layout-policy member.
        body = additions(patch_section('src/libcamera/info_frame.cpp'))
        self.assertNotIn('LazyInfoFramePool::', body)
        self.assertNotIn('ElasticInfoFramePool::', body)
        self.assertIn('plane.offset = 0;', body)
        self.assertIn('dmaHeap->alloc(plane.length, type)', body)

    def test_full_builder_checks_header_diff_as_well_as_sources(self):
        builder = (ROOT / 'scripts/build-ciri-libcamera-stack.sh').read_text()
        self.assertIn("'diff', '--', 'include', 'src', 'utils'", builder)

    def test_diagnostic_builder_selects_required_retained_objects_and_p2_config(self):
        builder = (ROOT / 'scripts/build-ciri-libcamera-dump-core.sh').read_text()
        self.assertIn('$1 == --p2-planes', builder)
        self.assertIn('kit_prefix=p2-planes', builder)
        self.assertIn('dump_config=config/libcamera/p2-dump.cfg', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/info_frame.cpp.o', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_imgsys_mcnr.cpp.o', builder)
        self.assertIn('src/libcamera/libcamera.so.0.1.0.p/pipeline_mtkisp7_odt_imagiq_adapter_imagiq_adapter.cpp.o)', builder)
        self.assertIn('ninja -C "$objects" "${compiled_targets[@]}"', builder)
        self.assertIn("'p2_separate_plane_buffers': True", builder)
        self.assertIn("'compiled_targets': sys.argv[6:]", builder)
        self.assertIn("(sys.argv[5], 'dump.cfg')", builder)
        self.assertIn("args[args.index('-o') + 1] = str(out / name)", builder)

    @unittest.skipUnless(shutil.which('c++'), 'Native C++ compiler unavailable')
    def test_dump_syncs_each_distinct_fd_once_until_cpu_access_finishes(self):
        section = patch_section('src/libcamera/pipeline/mtkisp7/odt/imagiq_adapter/imagiq_adapter.cpp')
        added = additions(section)
        self.assertEqual(added.count('auto syncers = syncFramePlanes(dump.frame->buffer());'), 2)
        helper = re.search(r'/\* Sync each.*?^}', added, re.DOTALL | re.MULTILINE).group(0)
        fixture = r'''
#include <algorithm>
#include <cassert>
#include <initializer_list>
#include <memory>
#include <unordered_set>
#include <vector>
std::vector<int> started, ended;
struct DmaSyncer {
    int fd;
    explicit DmaSyncer(int f) : fd(f) { started.push_back(f); }
    ~DmaSyncer() { ended.push_back(fd); }
};
struct FrameBuffer {
    struct FD { int value; int get() const { return value; } };
    struct Plane { FD fd; };
    std::vector<Plane> p;
    const std::vector<Plane> &planes() const { return p; }
};
''' + helper + r'''
void check(std::initializer_list<int> planes, std::vector<int> expected) {
    started.clear(); ended.clear();
    FrameBuffer buffer;
    for (int fd : planes) buffer.p.push_back({{fd}});
    {
        auto guards = syncFramePlanes(&buffer);
        assert(started == expected && ended.empty());
        auto moved = std::move(guards);
        assert(ended.empty());  // Returning/moving guards must not end DMA access.
    }
    std::sort(expected.begin(), expected.end());
    std::sort(ended.begin(), ended.end());
    assert(ended == expected);
}
int main() {
    check({11,11}, {11});  // Contiguous P1: one allocation, two logical planes.
    check({11,22}, {11,22});  // P2: both independent allocations must be synced.
    check({11,22,11}, {11,22});
    check({11}, {11});
}
'''
        with tempfile.TemporaryDirectory(prefix='ciri-p2-sync-test.') as directory:
            work = Path(directory)
            (work / 'fixture.cpp').write_text(fixture)
            result = subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                                     str(work / 'fixture.cpp'), '-o', str(work / 'fixture')],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(work / 'fixture')], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('c++'), 'Native C++ compiler unavailable')
    def test_native_allocator_plane_layouts_independent_writes_and_failure_cleanup(self):
        # Compile only our added method against local test doubles. The fixture
        # does not include, link or execute the downloaded libcamera sources.
        body = additions(patch_section('src/libcamera/info_frame.cpp'))
        declaration = additions(patch_section('include/libcamera/internal/info_frame.h'))
        fixture = r'''
#include <array>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <fcntl.h>
#include <memory>
#include <unistd.h>
#include <vector>
#include <cstdlib>
namespace libcamera {
struct Size { unsigned int width, height; };
struct PixelFormat {
    unsigned int n, strideAlign, scanAlign;
    Size size;
    std::array<unsigned int, 3> lengths, strides;
};
struct PixelFormatInfo {
    const PixelFormat &format;
    static PixelFormatInfo info(const PixelFormat &f) { return {f}; }
    unsigned int numPlanes() const { return format.n; }
    unsigned int planeSize(const Size &s, unsigned int p, unsigned int a, unsigned int b) const {
        assert(s.width == format.size.width && s.height == format.size.height);
        assert(a == format.strideAlign && b == format.scanAlign);
        return format.lengths[p];
    }
    unsigned int stride(unsigned int width, unsigned int p, unsigned int a) const {
        assert(width == format.size.width && a == format.strideAlign);
        return format.strides[p];
    }
};
struct UniqueFD {
    int fd;
    explicit UniqueFD(int f = -1) : fd(f) {}
    UniqueFD(const UniqueFD &) = delete;
    UniqueFD(UniqueFD &&other) : fd(other.fd) { other.fd = -1; }
    ~UniqueFD() { if (fd >= 0) close(fd); }
};
struct SharedFD {
    std::shared_ptr<int> fd;
    SharedFD() = default;
    explicit SharedFD(UniqueFD &&owned) {
        fd = std::shared_ptr<int>(new int(owned.fd), [](int *p) {
            if (*p >= 0) close(*p);
            delete p;
        });
        owned.fd = -1;
    }
    bool isValid() const { return fd && *fd >= 0; }
    int get() const { return *fd; }
};
struct FrameBuffer {
    struct Plane { SharedFD fd; unsigned int offset, length, stride; };
    std::vector<Plane> planes;
    explicit FrameBuffer(const std::vector<Plane> &p) : planes(p) {}
};
struct DmaHeap {
    enum Type { System, CMA };
    Type expectedType = System;
    unsigned int calls = 0, failAt = 0;
    std::vector<int> allocated;
    UniqueFD alloc(unsigned int length, Type type) {
        assert(type == expectedType);
        if (++calls == failAt) return UniqueFD();
        char path[] = "/tmp/ciri-p2-buffer-fixture.XXXXXX";
        int fd = mkstemp(path);
        assert(fd >= 0 && unlink(path) == 0 && ftruncate(fd, length) == 0);
        allocated.push_back(fd);
        return UniqueFD(fd);
    }
};
struct InfoFramePool {
    std::vector<std::unique_ptr<FrameBuffer>> buffers;
''' + declaration + r'''
    void release() { buffers.clear(); }
    int setBuffers(const PixelFormat &, const Size &, std::vector<std::unique_ptr<FrameBuffer>> &v,
                   unsigned int, unsigned int) { buffers = std::move(v); return 0; }
};
''' + body + r'''
}
int main() {
    using namespace libcamera;
    const PixelFormat cases[] = {
        {2, 1, 1, {1280,960}, {1536000,768000,0}, {1600,1600,0}},
        {2, 1, 1, {640,480}, {460800,230400,0}, {960,960,0}},
        {2, 1, 64, {640,480}, {491520,245760,0}, {960,960,0}},
        {2, 1, 64, {320,240}, {122880,61440,0}, {480,480,0}},
        {2, 1, 64, {160,120}, {36864,18432,0}, {288,288,0}},
        {2, 1, 64, {80,60}, {12288,12288,0}, {192,192,0}},
        {2, 1, 64, {40,30}, {6144,6144,0}, {96,96,0}},
        {2, 1, 64, {20,16}, {6144,6144,0}, {96,96,0}},
        {2, 1, 1, {20,16}, {320,160,0}, {20,20,0}},
        {2, 1168, 217, {289,217}, {253456,253456,0}, {1168,1168,0}},
        {1, 1, 1, {64,1}, {64,0,0}, {64,0,0}},
        {3, 1, 1, {16,16}, {256,64,64}, {16,8,8}}
    };
    for (const auto &format : cases) {
        DmaHeap heap;
        InfoFramePool pool;
        assert(pool.createSeparatePlaneBuffers(&heap, format, format.size, 2,
               DmaHeap::System, format.strideAlign, format.scanAlign) == 0);
        assert(pool.buffers.size() == 2 && heap.allocated.size() == 2 * format.n);
        for (const auto &buffer : pool.buffers) {
            assert(buffer->planes.size() == format.n);
            for (unsigned int j = 0; j < format.n; j++) {
                const auto &plane = buffer->planes[j];
                assert(plane.offset == 0 && plane.length == format.lengths[j]);
                assert(plane.stride == format.strides[j]);
                assert(lseek(plane.fd.get(), 0, SEEK_END) == plane.length);
                for (unsigned int k = 0; k < j; k++)
                    assert(plane.fd.get() != buffer->planes[k].fd.get());
            }
            // Writes to UV must not overwrite the Y prefix.
            if (format.n >= 2) {
                char marker = 42, y = -1;
                assert(pwrite(buffer->planes[1].fd.get(), &marker, 1, 0) == 1);
                assert(pread(buffer->planes[0].fd.get(), &y, 1, 0) == 1 && y == 0);
            }
        }
        pool.release();
        for (int fd : heap.allocated) assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
    }
    // Fail every allocation position, including later planes/later images.
    for (unsigned int fail = 1; fail <= 6; fail++) {
        DmaHeap heap;
        heap.failAt = fail;
        InfoFramePool pool;
        assert(pool.createSeparatePlaneBuffers(&heap, cases[0], cases[0].size, 3) == -EBUSY);
        assert(pool.buffers.empty() && heap.allocated.size() == fail - 1);
        for (int fd : heap.allocated) assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
    }
    DmaHeap heap;
    heap.expectedType = DmaHeap::CMA;
    InfoFramePool pool;
    assert(pool.createSeparatePlaneBuffers(&heap, cases[0], cases[0].size, 1, DmaHeap::CMA) == 0);
}
'''
        with tempfile.TemporaryDirectory(prefix='ciri-p2-buffer-test.') as directory:
            work = Path(directory)
            (work / 'fixture.cpp').write_text(fixture)
            result = subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                                     str(work / 'fixture.cpp'), '-o', str(work / 'fixture')],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(work / 'fixture')], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
