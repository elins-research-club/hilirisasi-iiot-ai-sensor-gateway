#include "sensors.h"

#include "config.h"

namespace iiot {

bool SensorReader::begin() {
#if IIOT_USE_MOCK_SENSORS
  return true;
#else
  // TODO: initialize I2C, BME688/BME668 driver, and ADC calibration for SEN0377.
  return true;
#endif
}

SensorSample SensorReader::read() {
  sample_count_++;
  SensorSample sample;
#if IIOT_USE_MOCK_SENSORS
  sample.temperature_c = 29.0f + (sample_count_ % 5) * 0.1f;
  sample.humidity_pct = 65.0f + (sample_count_ % 7) * 0.2f;
  sample.pressure_hpa = 1008.0f + (sample_count_ % 3) * 0.3f;
  sample.bme_gas_raw = 18000.0f + (sample_count_ % 11) * 25.0f;
  sample.co_raw = 0.010f + (sample_count_ % 4) * 0.001f;
  sample.bme_ok = true;
  sample.co_ok = true;
#else
  // TODO: replace with real BME688/BME668 reads.
  sample.bme_ok = false;

  // TODO: read calibrated SEN0377 ADC and convert to project co_raw scale.
  sample.co_raw = NAN;
  sample.co_ok = false;
#endif
  return sample;
}

}
