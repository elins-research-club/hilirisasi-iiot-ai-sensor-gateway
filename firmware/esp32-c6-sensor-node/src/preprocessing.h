#pragma once

#include <stddef.h>

#include "sensor_types.h"

namespace iiot {

class MovingAverage {
 public:
  static constexpr size_t kCapacity = 5;
  float update(float value);

 private:
  float values_[kCapacity] = {0.0F};
  size_t count_ = 0;
  size_t index_ = 0;
};

class Preprocessor {
 public:
  PreprocessedSample process(const SensorSample& input);

 private:
  MovingAverage temperature_;
  MovingAverage humidity_;
  MovingAverage pressure_;
  MovingAverage bme_gas_;
  MovingAverage co_;
  MovingAverage no2_mv_;
  MovingAverage no2_ratio_;
  MovingAverage o3_;
  MovingAverage co2_;
  MovingAverage pm1_;
  MovingAverage pm25_;
  MovingAverage pm10_;
  MovingAverage battery_voltage_;
  MovingAverage current_;
  MovingAverage power_;
};

}  // namespace iiot
