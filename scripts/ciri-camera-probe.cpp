// Standalone diagnostic for the recovered ChromiumOS libcamera ABI.
// Build with matching vendor headers and libc++, not Arch's libcamera headers.
#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/framebuffer_allocator.h>
#include <libcamera/request.h>
#include <libcamera/stream.h>

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
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

using namespace libcamera;

static int report(const char *operation, int error)
{
    std::cerr << operation << ": " << std::strerror(error < 0 ? -error : error)
              << " (" << error << ")\n";
    return 1;
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
        allocator_.reset();
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
        std::cout << "Stream: " << streamConfig.toString() << '\n';
        ret = camera_->configure(config_.get());
        if (ret)
            return report("Camera configure", ret);
        allocator_ = std::make_unique<FrameBufferAllocator>(camera_);
        ret = allocator_->allocate(streamConfig.stream());
        if (ret < 0)
            return report("Buffer allocation", ret);
        const auto &buffers = allocator_->buffers(streamConfig.stream());
        if (buffers.empty()) {
            std::cerr << "No capture buffers were allocated\n";
            return 1;
        }
        for (const auto &buffer : buffers) {
            auto request = camera_->createRequest(requests_.size());
            if (!request) {
                std::cerr << "Could not create a capture request\n";
                return 1;
            }
            ret = request->addBuffer(streamConfig.stream(), buffer.get());
            if (ret)
                return report("Request addBuffer", ret);
            requests_.push_back(std::move(request));
        }
        camera_->requestCompleted.connect(this, &Capture::completed);
        ret = camera_->start();
        if (ret)
            return report("Camera start", ret);
        started_ = true;
        for (const auto &request : requests_) {
            ret = camera_->queueRequest(request.get());
            if (ret)
                return report("Request queue", ret);
        }
        std::unique_lock<std::mutex> lock(mutex_);
        bool done = ready_.wait_for(lock, std::chrono::seconds(30), [this] {
            return failed_ || completed_ == requests_.size();
        });
        if (!done)
            std::cerr << "Timed out waiting for frames (30 seconds)\n";
        bool success = done && !failed_ && completed_ == requests_.size();
        lock.unlock();
        ret = camera_->stop();
        started_ = false;
        if (ret)
            return report("Camera stop", ret);
        camera_->requestCompleted.disconnect(this);
        if (success)
            std::cout << "CAPTURE OK: " << completed_ << " frames saved in /captures\n";
        return success ? 0 : 1;
    }

private:
    bool savePlane(const FrameBuffer::Plane &plane, unsigned int used,
                   uint64_t cookie, unsigned int index)
    {
        if (!used || used > plane.length ||
            plane.offset == std::numeric_limits<unsigned int>::max()) {
            std::cerr << "Invalid or empty frame plane\n";
            return false;
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
        std::string name = "/captures/frame-" + std::to_string(cookie) +
                           "-plane-" + std::to_string(index) + ".bin";
        std::ofstream output(name, std::ios::binary | std::ios::trunc);
        output.write(static_cast<const char *>(mapping) + plane.offset, used);
        output.close();
        bool success = output.good();
        if (!success)
            std::cerr << "Could not write " << name << '\n';
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
        if (request->status() == Request::RequestCancelled)
            return;
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
            for (unsigned int i = 0; i < planes.size(); ++i)
                success = savePlane(planes[i], metadata.planes()[i].bytesused,
                                    request->cookie(), i) && success;
            std::cout << "Frame " << request->cookie() << ": sequence="
                      << metadata.sequence << " timestamp=" << metadata.timestamp
                      << " status=" << (success ? "OK" : "ERROR") << '\n';
        }
        failed_ |= !success;
        ++completed_;
        ready_.notify_all();
    }

    std::shared_ptr<Camera> camera_;
    std::unique_ptr<CameraConfiguration> config_;
    std::unique_ptr<FrameBufferAllocator> allocator_;
    std::vector<std::unique_ptr<Request>> requests_;
    std::mutex mutex_;
    std::condition_variable ready_;
    size_t completed_ = 0;
    bool acquired_ = false;
    bool started_ = false;
    bool failed_ = false;
};

int main(int argc, char **argv)
{
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
