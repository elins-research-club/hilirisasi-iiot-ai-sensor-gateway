#pragma once

#include <stddef.h>
#include <stdint.h>

#include "sensor_types.h"

namespace iiot {

// Returns encoded compact_sensor.v3 byte count, or 0 when destination is too
// small/invalid. Values are hardware observations, not semantically smoothed.
size_t buildPayload(char* output, size_t output_size, const HardwareObservation& sample,
                    uint32_t sequence, uint64_t uptime_seconds, const char* boot_id);

}  // namespace iiot
