#include "preprocessing.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace iiot {
namespace {

bool inRange(float value, float low, float high) {
  return isfinite(value) && value >= low && value <= high;
}

void appendFlag(char* target, size_t size, const char* flag) {
  if (target == nullptr || size == 0 || flag == nullptr || flag[0] == '\0') {
    return;
  }
  const size_t used = strnlen(target, size);
  if (used >= size - 1) {
    return;
  }
  const int written = snprintf(target + used, size - used, "%s%s", used ? "|" : "", flag);
  if (written < 0 || static_cast<size_t>(written) >= size - used) {
    target[size - 1] = '\0';
  }
}

}  // namespace

HardwareObservation HardwareIntegrityGate::process(const SensorSample& input) const {
  HardwareObservation output{};
  output.mhz19_warming = input.mhz19_warming;
  output.pms7003t_warming = input.pms7003t_warming;
  output.health = input.health;

  const bool bme_values_ok =
      inRange(input.temperature_c, -40.0F, 85.0F) &&
      inRange(input.humidity_pct, 0.0F, 100.0F) &&
      inRange(input.pressure_hpa, 300.0F, 1250.0F) &&
      inRange(input.bme_gas_ohm, 1.0F, 1000000000.0F);
  output.health.bme688_ok = input.health.bme688_ok && bme_values_ok;
  if (output.health.bme688_ok) {
    output.temperature_c = input.temperature_c;
    output.humidity_pct = input.humidity_pct;
    output.pressure_hpa = input.pressure_hpa;
    output.bme_gas_ohm = input.bme_gas_ohm;
  } else {
    appendFlag(output.flags, sizeof(output.flags), "bme688_hardware_invalid");
  }

  output.health.sen0466_ok =
      input.health.sen0466_ok && inRange(input.co_ppm, 0.0F, 10000.0F);
  if (output.health.sen0466_ok) {
    output.co_ppm = input.co_ppm;
  } else {
    appendFlag(output.flags, sizeof(output.flags), "sen0466_hardware_invalid");
  }

  const bool no2_mv_ok = inRange(input.no2_raw_mv, 0.0F, 3300.0F);
  const bool no2_ratio_ok =
      !isfinite(input.no2_ratio) || inRange(input.no2_ratio, 0.0F, 100.0F);
  output.health.sen0574_ok = input.health.sen0574_ok && no2_mv_ok && no2_ratio_ok;
  if (output.health.sen0574_ok) {
    output.no2_raw_mv = input.no2_raw_mv;
    output.no2_ratio = input.no2_ratio;
    if (!isfinite(input.no2_ratio)) {
      appendFlag(output.flags, sizeof(output.flags), "no2_ratio_unavailable");
    }
  } else {
    appendFlag(output.flags, sizeof(output.flags), "sen0574_hardware_invalid");
  }

  output.health.sen0321_ok =
      input.health.sen0321_ok && inRange(input.o3_ppm, 0.0F, 100.0F);
  if (output.health.sen0321_ok) {
    output.o3_ppm = input.o3_ppm;
  } else {
    appendFlag(output.flags, sizeof(output.flags), "sen0321_hardware_invalid");
  }

  output.health.mhz19_ok =
      input.health.mhz19_ok && inRange(input.co2_ppm, 0.0F, 50000.0F);
  if (output.health.mhz19_ok) {
    output.co2_ppm = input.co2_ppm;
  } else if (input.mhz19_warming) {
    appendFlag(output.flags, sizeof(output.flags), "co2_warmup");
  } else {
    appendFlag(output.flags, sizeof(output.flags), "mhz19_hardware_invalid");
  }

  const bool pm_values_ok =
      inRange(input.pm1_ug_m3, 0.0F, 10000.0F) &&
      inRange(input.pm25_ug_m3, 0.0F, 10000.0F) &&
      inRange(input.pm10_ug_m3, 0.0F, 10000.0F);
  output.health.pms7003t_ok = input.health.pms7003t_ok && pm_values_ok;
  if (output.health.pms7003t_ok) {
    output.pm1_ug_m3 = input.pm1_ug_m3;
    output.pm25_ug_m3 = input.pm25_ug_m3;
    output.pm10_ug_m3 = input.pm10_ug_m3;
  } else if (input.pms7003t_warming) {
    appendFlag(output.flags, sizeof(output.flags), "pm_warmup");
  } else {
    appendFlag(output.flags, sizeof(output.flags), "pms7003t_hardware_invalid");
  }

  const bool power_values_ok =
      inRange(input.battery_voltage, 0.0F, 100.0F) &&
      inRange(input.current_ma, -100000.0F, 100000.0F) &&
      inRange(input.power_mw, -10000000.0F, 10000000.0F);
  output.health.ina226_ok = input.health.ina226_ok && power_values_ok;
  if (output.health.ina226_ok) {
    output.battery_voltage = input.battery_voltage;
    output.current_ma = input.current_ma;
    output.power_mw = input.power_mw;
  } else {
    appendFlag(output.flags, sizeof(output.flags), "ina226_hardware_invalid");
  }

  const bool states[] = {
      output.health.bme688_ok,
      output.health.sen0466_ok,
      output.health.sen0574_ok,
      output.health.sen0321_ok,
      output.health.mhz19_ok,
      output.health.pms7003t_ok,
      output.health.ina226_ok,
  };
  int valid_groups = 0;
  for (bool state : states) {
    valid_groups += state ? 1 : 0;
  }
  const int warming_groups =
      (input.mhz19_warming && !output.health.mhz19_ok ? 1 : 0) +
      (input.pms7003t_warming && !output.health.pms7003t_ok ? 1 : 0);
  if (valid_groups == 7) {
    output.hardware_summary = "ok";
  } else if (warming_groups > 0 && valid_groups + warming_groups == 7) {
    output.hardware_summary = "warming";
  } else if (valid_groups > 0 || warming_groups > 0) {
    output.hardware_summary = "partial";
  } else {
    output.hardware_summary = "error";
    appendFlag(output.flags, sizeof(output.flags), "sensor_hardware_error");
  }
  return output;
}

}  // namespace iiot
