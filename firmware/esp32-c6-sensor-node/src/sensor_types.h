#pragma once

#include <math.h>
#include <stdint.h>

namespace iiot {

struct SensorHealth {
  bool bme688_ok = false;
  bool sen0466_ok = false;
  bool sen0574_ok = false;
  bool sen0321_ok = false;
  bool mhz19_ok = false;
  bool pms7003t_ok = false;
  bool ina226_ok = false;
};

struct SensorSample {
  bool mhz19_warming = false;
  bool pms7003t_warming = false;
  float temperature_c = NAN;
  float humidity_pct = NAN;
  float pressure_hpa = NAN;
  float bme_gas_ohm = NAN;
  float co_ppm = NAN;
  float no2_raw_mv = NAN;
  float no2_ratio = NAN;
  float o3_ppm = NAN;
  float co2_ppm = NAN;
  float pm1_ug_m3 = NAN;
  float pm25_ug_m3 = NAN;
  float pm10_ug_m3 = NAN;
  float battery_voltage = NAN;
  float current_ma = NAN;
  float power_mw = NAN;
  SensorHealth health{};
};

// Hardware-proximate observation for compact_sensor.v3. Values are direct
// engineering observations after protocol/checksum/warm-up/vendor compensation
// and broad physical-impossibility gates. No semantic smoothing is allowed here.
struct HardwareObservation {
  bool mhz19_warming = false;
  bool pms7003t_warming = false;
  float temperature_c = NAN;
  float humidity_pct = NAN;
  float pressure_hpa = NAN;
  float bme_gas_ohm = NAN;
  float co_ppm = NAN;
  float no2_raw_mv = NAN;
  float no2_ratio = NAN;
  float o3_ppm = NAN;
  float co2_ppm = NAN;
  float pm1_ug_m3 = NAN;
  float pm25_ug_m3 = NAN;
  float pm10_ug_m3 = NAN;
  float battery_voltage = NAN;
  float current_ma = NAN;
  float power_mw = NAN;
  SensorHealth health{};
  const char* hardware_summary = "error";
  char flags[256] = {0};
};

}  // namespace iiot
