#pragma once

#include <stddef.h>
#include <stdint.h>

#include "sensor_types.h"

namespace iiot {

// Returns encoded byte count, or 0 when the destination is too small/invalid.
size_t buildPayload(char* output, size_t output_size, const PreprocessedSample& sample,
                    uint32_t sequence, uint64_t uptime_seconds, const char* boot_id);

}  // namespace iiot
