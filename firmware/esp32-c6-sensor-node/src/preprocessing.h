#pragma once

#include <math.h>

#include "sensor_types.h"

namespace iiot {

class MovingAverage {
 public:
  float update(float value);

 private:
  float values_[3] = {NAN, NAN, NAN};
  int index_ = 0;
  int count_ = 0;
};

class Preprocessor {
 public:
  PreprocessedSample process(const SensorSample& sample);

 private:
  MovingAverage temperature_avg_;
  MovingAverage humidity_avg_;
  MovingAverage pressure_avg_;
  MovingAverage bme_gas_avg_;
  MovingAverage co_avg_;
};

}
