#pragma once

#include <Arduino.h>
#include "sensor_types.h"

namespace iiot {

class PayloadBuilder {
 public:
  size_t build(char* buffer, size_t size, const PreprocessedSample& sample, unsigned long timestamp, unsigned long sequence);
};

}
