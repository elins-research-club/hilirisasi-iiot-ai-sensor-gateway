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

float updateOrNan(MovingAverage& average, float value) {
  return isfinite(value) ? average.update(value) : NAN;
}

}  // namespace

float MovingAverage::update(float value) {
  if (!isfinite(value)) {
    return NAN;
  }
  values_[index_] = value;
  index_ = (index_ + 1U) % kCapacity;
  if (count_ < kCapacity) {
    ++count_;
  }
  float sum = 0.0F;
  for (size_t i = 0; i < count_; ++i) {
    sum += values_[i];
  }
  return count_ ? sum / static_cast<float>(count_) : NAN;
}

PreprocessedSample Preprocessor::process(const SensorSample& input) {
  PreprocessedSample output{};
  output.health = input.health;

  const bool bme_values_ok =
      inRange(input.temperature_c, -10.0F, 80.0F) &&
      inRange(input.humidity_pct, 0.0F, 100.0F) &&
      inRange(input.pressure_hpa, 800.0F, 1200.0F) &&
      inRange(input.bme_gas_ohm, 100.0F, 10000000.0F);
  output.health.bme688_ok = input.health.bme688_ok && bme_values_ok;
  if (output.health.bme688_ok) {
    output.temperature_c = temperature_.update(input.temperature_c);
    output.humidity_pct = humidity_.update(input.humidity_pct);
    output.pressure_hpa = pressure_.update(input.pressure_hpa);
    output.bme_gas_ohm = bme_gas_.update(input.bme_gas_ohm);
  } else {
    appendFlag(output.flags, sizeof(output.flags), "bme688_invalid");
  }

  output.health.sen0466_ok = input.health.sen0466_ok && inRange(input.co_ppm, 0.0F, 1000.0F);
  if (output.health.sen0466_ok) {
    output.co_ppm = co_.update(input.co_ppm);
  } else {
    appendFlag(output.flags, sizeof(output.flags), "co_invalid");
  }

  const bool no2_mv_ok = inRange(input.no2_raw_mv, 0.0F, 3300.0F);
  const bool no2_ratio_ok = !isfinite(input.no2_ratio) || inRange(input.no2_ratio, 0.0F, 20.0F);
  output.health.sen0574_ok = input.health.sen0574_ok && no2_mv_ok && no2_ratio_ok;
  if (output.health.sen0574_ok) {
    output.no2_raw_mv = no2_mv_.update(input.no2_raw_mv);
    output.no2_ratio = updateOrNan(no2_ratio_, input.no2_ratio);
    if (!isfinite(input.no2_ratio)) {
      appendFlag(output.flags, sizeof(output.flags), "no2_ratio_unavailable");
    }
  } else {
    appendFlag(output.flags, sizeof(output.flags), "no2_invalid");
  }

  output.health.sen0321_ok = input.health.sen0321_ok && inRange(input.o3_ppm, 0.0F, 10.0F);
  if (output.health.sen0321_ok) {
    output.o3_ppm = o3_.update(input.o3_ppm);
  } else {
    appendFlag(output.flags, sizeof(output.flags), "o3_invalid");
  }

  output.health.mhz19_ok = input.health.mhz19_ok && inRange(input.co2_ppm, 0.0F, 10000.0F);
  if (output.health.mhz19_ok) {
    output.co2_ppm = co2_.update(input.co2_ppm);
  } else if (input.mhz19_warming) {
    appendFlag(output.flags, sizeof(output.flags), "co2_warmup");
  } else {
    appendFlag(output.flags, sizeof(output.flags), "co2_invalid");
  }

  const bool pm_values_ok =
      inRange(input.pm1_ug_m3, 0.0F, 5000.0F) &&
      inRange(input.pm25_ug_m3, 0.0F, 5000.0F) &&
      inRange(input.pm10_ug_m3, 0.0F, 5000.0F);
  output.health.pms7003t_ok = input.health.pms7003t_ok && pm_values_ok;
  if (output.health.pms7003t_ok) {
    output.pm1_ug_m3 = pm1_.update(input.pm1_ug_m3);
    output.pm25_ug_m3 = pm25_.update(input.pm25_ug_m3);
    output.pm10_ug_m3 = pm10_.update(input.pm10_ug_m3);
  } else if (input.pms7003t_warming) {
    appendFlag(output.flags, sizeof(output.flags), "pm_warmup");
  } else {
    appendFlag(output.flags, sizeof(output.flags), "pm_invalid");
  }

  const bool power_values_ok =
      inRange(input.battery_voltage, 0.0F, 60.0F) &&
      inRange(input.current_ma, -20000.0F, 20000.0F) &&
      inRange(input.power_mw, -1000000.0F, 1000000.0F);
  output.health.ina226_ok = input.health.ina226_ok && power_values_ok;
  if (output.health.ina226_ok) {
    output.battery_voltage = battery_voltage_.update(input.battery_voltage);
    output.current_ma = current_.update(input.current_ma);
    output.power_mw = power_.update(input.power_mw);
  } else {
    appendFlag(output.flags, sizeof(output.flags), "power_invalid");
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
  if (valid_groups == 7) {
    output.status = "ok";
    output.quality = "valid";
  } else if (valid_groups > 0) {
    output.status = "degraded";
    output.quality = "partial";
  } else {
    output.status = "sensor_error";
    output.quality = "invalid";
    appendFlag(output.flags, sizeof(output.flags), "sensor_error");
  }
  return output;
}

}  // namespace iiot
