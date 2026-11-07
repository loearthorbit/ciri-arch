"""Exercise the authored recycling callback with mocks, never vendor code."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/ciri-camera-probe.cpp"


class CameraWarmupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_recycling_controls_frame_ordinals_and_error_drains(self):
        source = SOURCE.read_text()
        callback = re.search(r"    void completed\(Request \*request\)\n    \{.*?\n    \}", source, re.DOTALL)
        colour = re.search(r"static void setColourControls\(ControlList &controls\)\n\{.*?\n\}", source, re.DOTALL)
        members = re.search(r"    static constexpr size_t warmupFrames_ = .*?    bool failed_ = false;", source, re.DOTALL)
        initial = re.search(r"        std::unique_lock<std::mutex> lock\(mutex_\);\n        for .*?(?=        bool done =)", source, re.DOTALL)
        predicate = re.search(r"return completed_ == queued_.*?;", source)
        success = re.search(r"bool success = (done && .*?);", source)
        for extracted in (callback, colour, members, initial, predicate, success):
            self.assertIsNotNone(extracted)
        self.assertIn("queueStopped_ = true;\n        lock.unlock();", source)
        fixture = r'''
#include <array>
#include <cassert>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>
namespace controls::draft {
constexpr int ColorCorrectionGains = 17, AeState = 18, AwbState = 19;
}
struct ControlList {
    bool gainsSet = false;
    bool missing = false;
    std::array<float, 4> gains{};
    void set(int id, const std::array<float, 4> &value) {
        assert(id == controls::draft::ColorCorrectionGains);
        gainsSet = true;
        gains = value;
    }
    std::optional<int> get(int) const {
        return missing ? std::nullopt : std::optional<int>(2);
    }
};
''' + colour.group(0) + r'''
struct FD { int value; int get() const { return value; } };
struct FrameMetadata {
    static constexpr int FrameSuccess = 0;
    int status = FrameSuccess;
    struct Payload { unsigned int bytesused = 0; };
    std::vector<Payload> payload{ {}, {} };
    const auto &planes() const { return payload; }
};
struct FrameBuffer {
    struct Plane { FD fd; unsigned int offset = 0, length = 16; };
    std::vector<Plane> data;
    FrameMetadata info;
    const auto &planes() const { return data; }
    const auto &metadata() const { return info; }
};
struct Request {
    enum { RequestComplete, RequestCancelled, RequestPending, ReuseBuffers };
    int state = RequestPending;
    size_t id;
    size_t reuses = 0;
    FrameBuffer buffer;
    ControlList ctl, meta;
    std::vector<std::pair<int, FrameBuffer *>> entries;
    explicit Request(size_t cookie) : id(cookie) {
        buffer.data = { { FD{int(cookie * 2)}, 0, 32 },
                        { FD{int(cookie * 2 + 1)}, 0, 16 } };
        entries.emplace_back(0, &buffer);
    }
    int status() const { return state; }
    size_t cookie() const { return id; }
    const auto &buffers() const { return entries; }
    ControlList &controls() { return ctl; }
    const ControlList &metadata() const { return meta; }
    void reuse(int flag) {
        assert(flag == ReuseBuffers);
        ++reuses;
        state = RequestPending;
        ctl = {};
        meta = {};
    }
};
std::set<int> primedFDs;
struct Camera {
    size_t calls = 0, failAt = 0;
    std::deque<Request *> pending;
    int queueRequest(Request *r) {
        ++calls;
        assert(r->state == Request::RequestPending);
        assert(r->ctl.gainsSet);
        for (float gain : r->ctl.gains) assert(gain == 1.0f);
        for (const auto &plane : r->buffer.data)
            assert(primedFDs.erase(plane.fd.get()) == 1);
        if (calls == failAt) return -5;
        pending.push_back(r);
        return 0;
    }
};
static int report(const char *, int) { return 1; }
struct Capture {
    std::shared_ptr<Camera> camera_ = std::make_shared<Camera>();
    std::vector<std::unique_ptr<Request>> requests_;
    std::mutex mutex_;
    std::condition_variable ready_;
    size_t primeCalls = 0;
    bool primeFails = false, saveFails = false;
    std::vector<std::pair<uint64_t, unsigned int>> saves;
''' + members.group(0) + r'''
    Capture() {
        primedFDs.clear();
        for (size_t i = 0; i < 8; ++i) {
            auto r = std::make_unique<Request>(i);
            setColourControls(r->controls());
            for (const auto &plane : r->buffer.data)
                primedFDs.insert(plane.fd.get());
            requests_.push_back(std::move(r));
        }
        target_ = warmupFrames_ + requests_.size();
    }
    void seed() {
        int ret;
''' + initial.group(0) + r'''
    }
    bool drained() const { ''' + predicate.group(0) + r''' }
    bool succeeded() const {
        bool done = drained();
        return ''' + success.group(1) + r''';
    }
    bool primeBuffer(int fd, size_t length) {
        assert(length == 32 || length == 16);
        ++primeCalls;
        if (primeFails) return false;
        primedFDs.insert(fd);
        return true;
    }
    bool savePlane(const FrameBuffer::Plane &, unsigned int,
                   uint64_t ordinal, unsigned int plane) {
        saves.emplace_back(ordinal, plane);
        return !saveFails;
    }
''' + callback.group(0) + r'''
    void next(bool cancelled = false, bool frameError = false, bool missing = false) {
        assert(!camera_->pending.empty());
        Request *r = camera_->pending.front();
        camera_->pending.pop_front();
        r->state = cancelled ? Request::RequestCancelled : Request::RequestComplete;
        r->buffer.info.status = frameError ? 1 : FrameMetadata::FrameSuccess;
        r->meta.missing = missing;
        completed(r);
    }
};
int main(int argc, char **argv) {
    assert(argc == 2);
    std::string mode = argv[1];
    Capture c;
    if (mode == "initial-fail") c.camera_->failAt = 4;
    c.seed();
    if (mode == "initial-fail") {
        assert(c.queued_ == 3 && c.queueStopped_ && c.failed_);
        while (!c.camera_->pending.empty()) {
            assert(!c.drained());
            c.next();
        }
        assert(c.drained() && !c.succeeded() && c.completed_ == 3);
        assert(c.camera_->calls == 4 && c.primeCalls == 0);
        return 0;
    }
    assert(c.queued_ == 8 && !c.drained());
    if (mode == "requeue-fail") c.camera_->failAt = 9;
    if (mode == "prime-fail") c.primeFails = true;
    if (mode == "save-fail") c.saveFails = true;
    if (mode == "cancel" || mode == "requeue-fail" || mode == "prime-fail") {
        c.next(mode == "cancel");
        assert(c.queueStopped_ && c.failed_ && !c.drained());
        while (!c.camera_->pending.empty()) c.next();
        assert(c.completed_ == 8 && c.queued_ == 8);
        assert(c.drained() && !c.succeeded() && c.saves.empty());
        assert(c.camera_->calls == (mode == "requeue-fail" ? 9u : 8u));
        return 0;
    }
    // Deliberately vary callback order: ordinals must not be request cookies.
    std::swap(c.camera_->pending[0], c.camera_->pending[3]);
    for (size_t i = 0; i < 128; ++i) {
        assert(!c.drained());
        assert(c.camera_->pending.size() <= 8);
        c.next(false, mode == "frame-fail" && i == 1,
               mode == "missing" && i == 0);
        if (i < 120) assert(c.saves.empty());
    }
    assert(c.drained() && c.camera_->pending.empty());
    assert(c.queued_ == 128 && c.completed_ == 128 && c.camera_->calls == 128);
    assert(c.primeCalls == 240 && c.saves.size() == 16);
    for (size_t i = 0; i < 16; ++i)
        assert(c.saves[i] == std::make_pair(uint64_t(i / 2), unsigned(i % 2)));
    for (const auto &r : c.requests_) assert(r->reuses == 15);
    assert(c.succeeded() == (mode != "save-fail" && mode != "frame-fail"));
    assert(c.saved_ == (mode == "save-fail" ? 0u : 8u));
}
'''
        with tempfile.TemporaryDirectory(prefix="ciri-warmup-test.") as directory:
            work = Path(directory)
            (work / "warmup.cpp").write_text(fixture)
            compiled = subprocess.run(
                ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                 str(work / "warmup.cpp"), "-o", str(work / "warmup")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for mode in ("ok", "initial-fail", "requeue-fail", "prime-fail", "cancel",
                         "save-fail", "frame-fail", "missing"):
                with self.subTest(mode=mode):
                    result = subprocess.run([str(work / "warmup"), mode],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if mode == "missing":
                        self.assertIn("Warm-up 0: AE=-1 AWB=-1", result.stdout)


if __name__ == "__main__":
    unittest.main()
