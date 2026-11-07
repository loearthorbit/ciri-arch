// Standalone diagnostic for the recovered ChromiumOS libcamera ABI.
// Build with matching vendor headers and libc++, not Arch's libcamera headers.
#include "ciri-camera-ipa-worker.h"
#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/base/unique_fd.h>
#include <libcamera/internal/gyro_sensor.h>
#include <libcamera/request.h>
#include <libcamera/stream.h>

#include <array>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <fcntl.h>
#include <glob.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <linux/media.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

using namespace libcamera;

namespace libcamera {
namespace controls::draft {
// Exact public declaration generated from the pinned control_ids_draft.yaml
// (float, size [4]); the retained source has no generated control_ids.h.
extern const Control<Span<const float, 4>> ColorCorrectionGains;
extern const Control<int32_t> AeState;
extern const Control<int32_t> AwbState;
} // namespace controls::draft

// Override only this optional initializer in the standalone executable. The
// recovered library calls it through the PLT. ChromeOS HAL normally supplies a
// Mojo token, but this client has none and SensorHalClient dereferences it.
// Returning -ENXIO makes the pipeline pass nullptr to its gyro consumers.
int GyroSensor::init(Location)
{
    std::cerr << "Standalone client: ChromeOS gyroscope disabled (no Mojo token)\n";
    return -ENXIO;
}
} // namespace libcamera

static void setColourControls(ControlList &controls)
{
    // Mirror the ChromeOS preview template. The recovered IPA's missing-gain
    // fallback is {1, 1, 1, 0}, confirmed in source and the compiled library.
    // Supplying all four neutral gains avoids it without disabling auto AWB.
    const std::array<float, 4> gains{ 1.0f, 1.0f, 1.0f, 1.0f };
    controls.set(controls::draft::ColorCorrectionGains, gains);
}

// Detect untouched DMA memory even if the vendor reports request success but
// leaves raw-output bytesused unset. A varying pattern avoids matching a solid
// colour image plane by accident.
static unsigned char unwrittenByte(size_t index)
{
    return static_cast<unsigned char>((index * 37) ^ (index >> 8) ^ 0xa5);
}

static int report(const char *operation, int error)
{
    std::cerr << operation << ": " << std::strerror(error < 0 ? -error : error)
              << " (" << error << ")\n";
    return 1;
}

static bool checkMediaDevices()
{
    // The vendor handler requires all three; camsys alone cannot register cameras.
    const char *drivers[] = { "mtk-cam", "mtk-imgsys", "mtk-aie-5.3" };
    const char *options[] = { "VIDEO_MTK_ISP_71_CAMSYS", "VIDEO_MTK_ISP_71_IMGSYS", "VIDEO_MTK_AIE" };
    bool found[] = { false, false, false };
    glob_t devices{};
    int ret = glob("/dev/media[0-9]*", 0, nullptr, &devices);
    if (ret != 0 && ret != GLOB_NOMATCH) {
        std::cerr << "Could not enumerate media devices\n";
        globfree(&devices);
        return false;
    }
    for (size_t i = 0; i < devices.gl_pathc; ++i) {
        UniqueFD fd(open(devices.gl_pathv[i], O_RDONLY | O_CLOEXEC));
        media_device_info info{};
        if (!fd.isValid() || ioctl(fd.get(), MEDIA_IOC_DEVICE_INFO, &info) < 0)
            continue;
        std::string driver(info.driver, strnlen(info.driver, sizeof(info.driver)));
        std::cout << devices.gl_pathv[i] << ": driver=" << driver << '\n';
        for (size_t j = 0; j < 3; ++j)
            found[j] |= driver == drivers[j];
    }
    globfree(&devices);
    bool complete = true;
    for (size_t i = 0; i < 3; ++i) {
        if (!found[i]) {
            std::cerr << "Missing required media driver: " << drivers[i]
                      << " (CONFIG_" << options[i] << ")\n";
            complete = false;
        }
    }
    return complete;
}

class Capture {
public:
    explicit Capture(std::shared_ptr<Camera> camera) : camera_(std::move(camera)) {}
    ~Capture()
    {
        if (started_)
            camera_->stop();
        camera_->requestCompleted.disconnect(this);
        requests_.clear();
        buffers_.clear();
        if (acquired_)
            camera_->release();
    }

    int run()
    {
        int ret = camera_->acquire();
        if (ret)
            return report("Camera acquire", ret);
        acquired_ = true;
        config_ = camera_->generateConfiguration({ StreamRole::Viewfinder });
        if (!config_ || config_->empty()) {
            std::cerr << "No viewfinder configuration offered by this camera\n";
            return 1;
        }
        auto &streamConfig = config_->at(0);
        streamConfig.size.width = 640;
        streamConfig.size.height = 480;
        if (config_->validate() == CameraConfiguration::Invalid) {
            std::cerr << "Camera rejected the stream configuration\n";
            return 1;
        }
        std::cout << "Stream: " << streamConfig.toString()
                  << " stride=" << streamConfig.stride << '\n';
        ret = camera_->configure(config_.get());
        if (ret)
            return report("Camera configure", ret);
        // This vendor pipeline's exportFrameBuffers() returns -EINVAL.
        // It expects externally allocated buffers, as supplied by ChromeOS HAL.
        if (!allocateBuffers(streamConfig))
            return 1;
        for (const auto &buffer : buffers_) {
            auto request = camera_->createRequest(requests_.size());
            if (!request) {
                std::cerr << "Could not create a capture request\n";
                return 1;
            }
            setColourControls(request->controls());
            ret = request->addBuffer(streamConfig.stream(), buffer.get());
            if (ret)
                return report("Request addBuffer", ret);
            requests_.push_back(std::move(request));
        }
        target_ = warmupFrames_ + requests_.size();
        queued_ = 0;
        std::cout << "Standalone client: colour-correction gains=1/1/1/1 (auto AWB retained)\n";
        std::cout << "Standalone client: " << warmupFrames_
                  << " warm-up frames, then save " << requests_.size() << " frames\n";
        camera_->requestCompleted.connect(this, &Capture::completed);
        ret = camera_->start();
        if (ret)
            return report("Camera start", ret);
        started_ = true;
        // Serialize initial accounting with completion callbacks. If a queue
        // fails, drain only the requests actually accepted before stopping.
        std::unique_lock<std::mutex> lock(mutex_);
        for (const auto &request : requests_) {
            ret = camera_->queueRequest(request.get());
            if (ret) {
                report("Request queue", ret);
                failed_ = true;
                queueStopped_ = true;
                break;
            }
            ++queued_;
        }
        bool done = ready_.wait_for(lock, std::chrono::seconds(30), [this] {
            // Do not stop on the first bad plane while other requests are
            // still in flight: this vendor CAMSYS aborts during that cleanup.
            return completed_ == queued_ && (queued_ == target_ || queueStopped_);
        });
        if (!done)
            std::cerr << "Timed out waiting for frames (30 seconds)\n";
        bool success = done && !failed_ && completed_ == target_ && saved_ == requests_.size();
        // A timeout must not let a late completion recycle work during stop().
        queueStopped_ = true;
        lock.unlock();
        ret = camera_->stop();
        started_ = false;
        if (ret)
            return report("Camera stop", ret);
        camera_->requestCompleted.disconnect(this);
        if (success)
            std::cout << "CAPTURE OK: " << saved_ << " frames saved in /captures after "
                      << warmupFrames_ << " warm-up frames\n";
        return success ? 0 : 1;
    }

private:
    bool allocateBuffers(const StreamConfiguration &config)
    {
        if (config.pixelFormat.toString() != "NV12" || !config.size.height ||
            config.size.height % 2 || !config.size.width || config.size.width % 2 ||
            config.stride < config.size.width || !config.bufferCount || config.bufferCount > 64) {
            std::cerr << "Unsupported NV12 buffer configuration\n";
            return false;
        }
        uint64_t ySize = static_cast<uint64_t>(config.stride) * config.size.height;
        uint64_t uvSize = ySize / 2;
        if (ySize + uvSize > std::numeric_limits<unsigned int>::max()) {
            std::cerr << "Capture buffer is too large\n";
            return false;
        }
        UniqueFD heap(open("/dev/dma_heap/system", O_RDWR | O_CLOEXEC));
        if (!heap.isValid()) {
            report("Open system DMA heap", errno);
            return false;
        }
        for (unsigned int i = 0; i < config.bufferCount; ++i) {
            // Multi-planar capture queues a DMA address for each plane. Use
            // distinct allocations at offset zero to avoid relying on shared-
            // FD data_offset handling in the kernel/firmware output path.
            std::vector<FrameBuffer::Plane> planes;
            for (uint64_t length : { ySize, uvSize }) {
                dma_heap_allocation_data allocation{};
                allocation.len = length;
                allocation.fd_flags = O_RDWR | O_CLOEXEC;
                int ret;
                do {
                    ret = ioctl(heap.get(), DMA_HEAP_IOCTL_ALLOC, &allocation);
                } while (ret < 0 && errno == EINTR);
                if (ret < 0) {
                    report("Allocate system DMA buffer", errno);
                    return false;
                }
                SharedFD fd(static_cast<int>(allocation.fd));
                if (!primeBuffer(fd.get(), static_cast<size_t>(allocation.len)))
                    return false;
                planes.push_back({ fd, 0, static_cast<unsigned int>(length), config.stride });
            }
            buffers_.push_back(std::make_unique<FrameBuffer>(planes));
        }
        std::cout << "Allocated " << buffers_.size()
                  << " NV12 buffers (separate Y/UV DMA allocations)\n";
        return true;
    }

    bool primeBuffer(int fd, size_t length)
    {
        void *mapping = mmap(nullptr, length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) {
            report("Prime buffer mmap", errno);
            return false;
        }
        dma_buf_sync sync{};
        sync.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE;
        bool synced = ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) == 0;
        if (!synced && errno != ENOTTY) {
            report("Prime buffer write sync", errno);
            munmap(mapping, length);
            return false;
        }
        auto *bytes = static_cast<unsigned char *>(mapping);
        for (size_t i = 0; i < length; ++i)
            bytes[i] = unwrittenByte(i);
        bool success = true;
        if (synced) {
            sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE;
            if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync)) {
                report("Prime buffer end sync", errno);
                success = false;
            }
        }
        munmap(mapping, length);
        return success;
    }

    bool savePlane(const FrameBuffer::Plane &plane, unsigned int used,
                   uint64_t cookie, unsigned int index)
    {
        if (!plane.length || used > plane.length ||
            plane.offset == std::numeric_limits<unsigned int>::max()) {
            std::cerr << "Invalid frame plane: frame=" << cookie << " plane=" << index
                      << " used=" << used << " length=" << plane.length
                      << " offset=" << plane.offset << '\n';
            return false;
        }
        // The ChromeOS HAL checks request/frame status, not payload lengths.
        // For our validated, uncompressed NV12 output the full plane length is
        // known. Only use it when bytesused is absent; reject oversize metadata.
        if (!used) {
            used = plane.length;
            std::cerr << "Frame " << cookie << " plane " << index
                      << ": bytesused unset; checking and saving " << used
                      << " fixed NV12 bytes\n";
        }
        size_t length = static_cast<size_t>(plane.offset) + plane.length;
        void *mapping = mmap(nullptr, length, PROT_READ, MAP_SHARED, plane.fd.get(), 0);
        if (mapping == MAP_FAILED) {
            report("Plane mmap", errno);
            return false;
        }
        dma_buf_sync sync{};
        sync.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ;
        bool synced = ioctl(plane.fd.get(), DMA_BUF_IOCTL_SYNC, &sync) == 0;
        if (!synced && errno != ENOTTY) {
            report("DMA buffer read sync", errno);
            munmap(mapping, length);
            return false;
        }
        auto *bytes = static_cast<const unsigned char *>(mapping);
        bool changed = false;
        for (size_t i = plane.offset; i < static_cast<size_t>(plane.offset) + used; ++i) {
            if (bytes[i] != unwrittenByte(i)) {
                changed = true;
                break;
            }
        }
        bool success = changed;
        if (!changed) {
            std::cerr << "Frame " << cookie << " plane " << index
                      << ": DMA memory untouched; refusing capture success\n";
        } else {
            std::string name = "/captures/frame-" + std::to_string(cookie) +
                               "-plane-" + std::to_string(index) + ".bin";
            std::ofstream output(name, std::ios::binary | std::ios::trunc);
            output.write(static_cast<const char *>(mapping) + plane.offset, used);
            output.close();
            success = output.good();
            if (!success)
                std::cerr << "Could not write " << name << '\n';
        }
        if (synced) {
            sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
            if (ioctl(plane.fd.get(), DMA_BUF_IOCTL_SYNC, &sync)) {
                report("DMA buffer end sync", errno);
                success = false;
            }
        }
        munmap(mapping, length);
        return success;
    }

    void completed(Request *request)
    {
        // A plain C++ receiver runs directly on libcamera's completion thread.
        std::lock_guard<std::mutex> lock(mutex_);
        if (request->status() == Request::RequestCancelled) {
            std::cerr << "Frame " << request->cookie() << ": request cancelled\n";
            failed_ = true;
            queueStopped_ = true;
            ++completed_;
            ready_.notify_all();
            return;
        }
        const size_t frame = completed_;
        const bool save = frame >= warmupFrames_;
        bool success = request->status() == Request::RequestComplete;
        for (const auto &entry : request->buffers()) {
            FrameBuffer *buffer = entry.second;
            const auto &metadata = buffer->metadata();
            const auto &planes = buffer->planes();
            if (metadata.status != FrameMetadata::FrameSuccess ||
                metadata.planes().size() != planes.size()) {
                std::cerr << "Frame metadata reports an error\n";
                success = false;
                continue;
            }
            if (save) {
                for (unsigned int i = 0; i < planes.size(); ++i)
                    success = savePlane(planes[i], metadata.planes()[i].bytesused,
                                        frame - warmupFrames_, i) && success;
            }
        }
        if (save || frame % 16 == 0 || !success) {
            const auto &metadata = request->metadata();
            std::cout << (save ? "Frame " : "Warm-up ")
                      << (save ? frame - warmupFrames_ : frame)
                      << ": AE=" << metadata.get(controls::draft::AeState).value_or(-1)
                      << " AWB=" << metadata.get(controls::draft::AwbState).value_or(-1)
                      << " status=" << (success ? "OK" : "ERROR") << '\n';
        }
        if (save && success)
            ++saved_;
        failed_ |= !success;
        ++completed_;
        if (!queueStopped_ && queued_ < target_) {
            bool primed = true;
            for (const auto &entry : request->buffers()) {
                for (const auto &plane : entry.second->planes())
                    primed = primeBuffer(plane.fd.get(),
                                         static_cast<size_t>(plane.offset) + plane.length) && primed;
            }
            if (primed) {
                request->reuse(Request::ReuseBuffers);
                // reuse() clears controls as well as metadata.
                setColourControls(request->controls());
                int ret = camera_->queueRequest(request);
                if (ret == 0)
                    ++queued_;
                else {
                    report("Requeue capture request", ret);
                    failed_ = true;
                    queueStopped_ = true;
                }
            } else {
                failed_ = true;
                queueStopped_ = true;
            }
        }
        ready_.notify_all();
    }

    std::shared_ptr<Camera> camera_;
    std::unique_ptr<CameraConfiguration> config_;
    std::vector<std::unique_ptr<FrameBuffer>> buffers_;
    std::vector<std::unique_ptr<Request>> requests_;
    std::mutex mutex_;
    std::condition_variable ready_;
    static constexpr size_t warmupFrames_ = 120;
    size_t target_ = 0;
    size_t queued_ = 0;
    size_t completed_ = 0;
    size_t saved_ = 0;
    bool queueStopped_ = false;
    bool acquired_ = false;
    bool started_ = false;
    bool failed_ = false;
};

int main(int argc, char **argv)
{
    if (argc == 2 && std::strcmp(argv[1], "--help") == 0) {
        std::cout << "Usage: ciri-camera-probe [--list | --capture CAMERA_INDEX]\n";
        return 0;
    }
    bool capture = argc == 3 && std::strcmp(argv[1], "--capture") == 0;
    bool list = argc == 1 || (argc == 2 && std::strcmp(argv[1], "--list") == 0);
    if (!capture && !list) {
        std::cerr << "Usage: ciri-camera-probe [--list | --capture CAMERA_INDEX]\n";
        return 2;
    }
    size_t index = 0;
    if (capture) {
        char *end = nullptr;
        errno = 0;
        unsigned long value = std::strtoul(argv[2], &end, 10);
        if (errno || end == argv[2] || *end || argv[2][0] == '-') {
            std::cerr << "CAMERA_INDEX must be a non-negative integer\n";
            return 2;
        }
        index = value;
    }
    std::cout.setf(std::ios::unitbuf);
    if (!checkMediaDevices())
        return 1;
    // Fork before constructing CameraManager: never fork its running threads.
    // The socket is private to this launcher invocation, and capture needs the
    // real recovered IPA implementation, not a stub or ChromeOS service.
    StandaloneIPAWorker ipaWorker("/run/camera/camera-algo-libcamera.sock",
                                 "/usr/lib64/mtkisp7_ipa_proxy.so");
    if (capture && !ipaWorker.start())
        return 1;
    CameraManager manager;
    int ret = manager.start();
    if (ret)
        return report("Camera manager start", ret);
    int result = 0;
    {
        const auto cameras = manager.cameras();
        std::cout << "Detected cameras: " << cameras.size() << '\n';
        for (size_t i = 0; i < cameras.size(); ++i)
            std::cout << i << ": " << cameras[i]->id() << '\n';
        if (cameras.empty()) {
            std::cerr << "No cameras registered by the vendor pipeline\n";
            result = 1;
        } else if (capture) {
            if (index >= cameras.size()) {
                std::cerr << "Camera index is out of range\n";
                result = 2;
            } else {
                Capture session(cameras[index]);
                result = session.run();
            }
        }
    }
    manager.stop();
    return result;
}
