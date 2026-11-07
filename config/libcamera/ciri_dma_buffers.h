/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* Ciri standard-client allocation using libcamera's existing DMA heap helper. */
#pragma once

#include <cerrno>
#include <cstdint>
#include <limits>
#include <memory>
#include <vector>

#include <libcamera/formats.h>
#include <libcamera/framebuffer.h>
#include <libcamera/stream.h>
#include <libcamera/internal/dma_heaps.h>

namespace libcamera {

inline int ciriExportFrameBuffers(const StreamConfiguration &config,
                                 DmaHeap *heap,
                                 std::vector<std::unique_ptr<FrameBuffer>> *buffers)
{
    if (!buffers || config.pixelFormat != formats::NV12 || !config.size.width ||
        !config.size.height || config.size.width % 2 || config.size.height % 2 ||
        config.stride < config.size.width || !config.bufferCount ||
        config.bufferCount > 64)
        return -EINVAL;

    const uint64_t ySize = static_cast<uint64_t>(config.stride) * config.size.height;
    const uint64_t uvSize = ySize / 2;
    if (ySize + uvSize > std::numeric_limits<unsigned int>::max())
        return -EOVERFLOW;
    if (!heap || !heap->isValid())
        return -ENODEV;

    // Allocate into a temporary vector: a partial failure must not publish any
    // frames, discard caller-owned buffers, or leave DMA descriptors open.
    std::vector<std::unique_ptr<FrameBuffer>> allocated;
    for (unsigned int i = 0; i < config.bufferCount; ++i) {
        std::vector<FrameBuffer::Plane> planes;
        for (uint64_t length : { ySize, uvSize }) {
            SharedFD fd(heap->alloc(length, DmaHeap::System));
            if (!fd.isValid())
                return -ENOMEM;
            // Current Ciri capture requires independent, zero-offset planes.
            planes.push_back({ fd, 0, static_cast<unsigned int>(length),
                               config.stride });
        }
        allocated.push_back(std::make_unique<FrameBuffer>(planes));
    }

    *buffers = std::move(allocated);
    return config.bufferCount;
}

} // namespace libcamera
