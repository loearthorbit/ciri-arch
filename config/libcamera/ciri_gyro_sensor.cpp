/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* Optional gyro backend for standalone libcamera, without ChromeOS Mojo. */
#include <cerrno>
#include <cstdint>
#include <libcamera/internal/gyro_sensor.h>

namespace libcamera {

class GyroSensor::Private : public Extensible::Private
{
    LIBCAMERA_DECLARE_PUBLIC(GyroSensor)
public:
    int init([[maybe_unused]] Location location) { return -ENXIO; }
    bool startReading([[maybe_unused]] double frequency) { return false; }
    void stopReading() {}
    SensorSample getLatestSample() { return {}; }
};

PUBLIC_GYRO_SENSOR_IMPLEMENTATION

} // namespace libcamera
