#include "payload.h"

#include <stdio.h>
#include "config.h"

namespace iiot {

size_t PayloadBuilder::build(char* buffer, size_t size, const PreprocessedSample& sample, unsigned long timestamp, unsigned long sequence) {
  int written = snprintf(
      buffer,
      size,
      "{\"v\":1,\"n\":\"%s\",\"r\":\"%s\",\"ts\":%lu,\"seq\":%lu,\"st\":\"%s\",\"q\":\"%s\",\"f\":\"%s\",\"s\":{\"tc\":%.2f,\"h\":%.2f,\"p\":%.2f,\"bme\":%.2f,\"co\":%.5f}}",
      IIOT_NODE_ID,
      IIOT_ROOM_ID,
      timestamp,
      sequence,
      sample.status,
      sample.quality,
      sample.flags,
      sample.temperature_c,
      sample.humidity_pct,
      sample.pressure_hpa,
      sample.bme_gas_raw,
      sample.co_raw);
  if (written < 0) {
    buffer[0] = '\0';
    return 0;
  }
  if (static_cast<size_t>(written) >= size) {
    buffer[size - 1] = '\0';
    return size - 1;
  }
  return static_cast<size_t>(written);
}

}
