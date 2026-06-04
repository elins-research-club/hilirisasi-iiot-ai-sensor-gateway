#pragma once

#include "sensor_types.h"

namespace iiot {

class SensorReader {
 public:
  bool begin();
  SensorSample read();
 private:
  unsigned long sample_count_ = 0;
};

}
