#include "preprocessing.h"

#include <math.h>
#include <string.h>

namespace iiot {

namespace {
bool inRange(float value, float low, float high) {
  return !isnan(value) && value >= low && value <= high;
}

void appendFlag(char* flags, size_t size, const char* flag) {
  if (flags[0] != '\0') {
    strlcat(flags, "|", size);
  }
  strlcat(flags, flag, size);
}
}

float MovingAverage::update(float value) {
  if (isnan(value)) {
    return value;
  }
  values_[index_] = value;
  index_ = (index_ + 1) % 3;
  if (count_ < 3) {
    count_++;
  }
  float sum = 0.0f;
  for (int i = 0; i < count_; ++i) {
    sum += values_[i];
  }
  return sum / count_;
}

PreprocessedSample Preprocessor::process(const SensorSample& sample) {
  PreprocessedSample out;
  out.temperature_c = temperature_avg_.update(sample.temperature_c);
  out.humidity_pct = humidity_avg_.update(sample.humidity_pct);
  out.pressure_hpa = pressure_avg_.update(sample.pressure_hpa);
  out.bme_gas_raw = bme_gas_avg_.update(sample.bme_gas_raw);
  out.co_raw = co_avg_.update(sample.co_raw);

  bool valid = true;
  if (!sample.bme_ok) {
    appendFlag(out.flags, sizeof(out.flags), "bme_read_error");
    valid = false;
  }
  if (!sample.co_ok) {
    appendFlag(out.flags, sizeof(out.flags), "co_read_error");
    valid = false;
  }
  if (!inRange(out.temperature_c, -10.0f, 80.0f)) {
    appendFlag(out.flags, sizeof(out.flags), "range_temperature_c");
    valid = false;
  }
  if (!inRange(out.humidity_pct, 0.0f, 100.0f)) {
    appendFlag(out.flags, sizeof(out.flags), "range_humidity_pct");
    valid = false;
  }
  if (!inRange(out.pressure_hpa, 800.0f, 1200.0f)) {
    appendFlag(out.flags, sizeof(out.flags), "range_pressure_hpa");
    valid = false;
  }
  if (isnan(out.bme_gas_raw)) {
    appendFlag(out.flags, sizeof(out.flags), "missing_bme_gas_raw");
    valid = false;
  }
  if (isnan(out.co_raw)) {
    appendFlag(out.flags, sizeof(out.flags), "missing_co_raw");
    valid = false;
  }

  out.valid = valid;
  strlcpy(out.status, valid ? "ok" : "sensor_error", sizeof(out.status));
  strlcpy(out.quality, valid ? "valid" : "invalid", sizeof(out.quality));
  return out;
}

}
