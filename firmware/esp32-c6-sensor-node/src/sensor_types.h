#pragma once

#include <math.h>

namespace iiot {

struct SensorSample {
  float temperature_c = NAN;
  float humidity_pct = NAN;
  float pressure_hpa = NAN;
  float bme_gas_raw = NAN;
  float co_raw = NAN;
  bool bme_ok = false;
  bool co_ok = false;
};

struct PreprocessedSample {
  float temperature_c = NAN;
  float humidity_pct = NAN;
  float pressure_hpa = NAN;
  float bme_gas_raw = NAN;
  float co_raw = NAN;
  bool valid = false;
  char status[20] = "invalid";
  char quality[20] = "invalid";
  char flags[160] = "";
};

}
