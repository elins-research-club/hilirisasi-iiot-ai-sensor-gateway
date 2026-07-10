#include "payload.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "config.h"

namespace iiot {
namespace {

void formatNumber(char* output, size_t output_size, float value, int precision) {
  if (output == nullptr || output_size == 0) {
    return;
  }
  if (!isfinite(value)) {
    snprintf(output, output_size, "null");
    return;
  }
  snprintf(output, output_size, "%.*f", precision, static_cast<double>(value));
}

const char* booleanJson(bool value) { return value ? "true" : "false"; }

}  // namespace

size_t buildPayload(char* output, size_t output_size, const PreprocessedSample& sample,
                    uint32_t sequence, uint64_t uptime_seconds, const char* boot_id) {
  if (output == nullptr || output_size == 0 || boot_id == nullptr || boot_id[0] == '\0') {
    return 0;
  }
  char tc[24], h[24], p[24], bme[24], co[24], n2mv[24], n2r[24], o3[24], co2[24];
  char pm1[24], pm25[24], pm10[24], bv[24], bi[24], bp[24];
  formatNumber(tc, sizeof(tc), sample.temperature_c, 2);
  formatNumber(h, sizeof(h), sample.humidity_pct, 2);
  formatNumber(p, sizeof(p), sample.pressure_hpa, 2);
  formatNumber(bme, sizeof(bme), sample.bme_gas_ohm, 1);
  formatNumber(co, sizeof(co), sample.co_ppm, 3);
  formatNumber(n2mv, sizeof(n2mv), sample.no2_raw_mv, 2);
  formatNumber(n2r, sizeof(n2r), sample.no2_ratio, 4);
  formatNumber(o3, sizeof(o3), sample.o3_ppm, 4);
  formatNumber(co2, sizeof(co2), sample.co2_ppm, 1);
  formatNumber(pm1, sizeof(pm1), sample.pm1_ug_m3, 1);
  formatNumber(pm25, sizeof(pm25), sample.pm25_ug_m3, 1);
  formatNumber(pm10, sizeof(pm10), sample.pm10_ug_m3, 1);
  formatNumber(bv, sizeof(bv), sample.battery_voltage, 3);
  formatNumber(bi, sizeof(bi), sample.current_ma, 2);
  formatNumber(bp, sizeof(bp), sample.power_mw, 2);

  const int written = snprintf(
      output, output_size,
      "{\"v\":2,\"gw\":\"%s\",\"n\":\"%s\",\"r\":\"%s\",\"ts\":%llu,"
      "\"seq\":%lu,\"bid\":\"%s\",\"st\":\"%s\",\"q\":\"%s\",\"f\":\"%s\","
      "\"ok\":{\"bme688\":%s,\"sen0466\":%s,\"sen0574\":%s,\"sen0321\":%s,"
      "\"mhz19\":%s,\"pms7003t\":%s,\"ina226\":%s},"
      "\"s\":{\"tc\":%s,\"h\":%s,\"p\":%s,\"bme\":%s,\"co\":%s,"
      "\"n2mv\":%s,\"n2r\":%s,\"o3\":%s,\"co2\":%s,\"pm1\":%s,"
      "\"pm25\":%s,\"pm10\":%s,\"bv\":%s,\"bi\":%s,\"bp\":%s}}",
      GATEWAY_ID, NODE_ID, ROOM_ID, static_cast<unsigned long long>(uptime_seconds),
      static_cast<unsigned long>(sequence), boot_id, sample.status, sample.quality, sample.flags,
      booleanJson(sample.health.bme688_ok), booleanJson(sample.health.sen0466_ok),
      booleanJson(sample.health.sen0574_ok), booleanJson(sample.health.sen0321_ok),
      booleanJson(sample.health.mhz19_ok), booleanJson(sample.health.pms7003t_ok),
      booleanJson(sample.health.ina226_ok), tc, h, p, bme, co, n2mv, n2r, o3, co2, pm1, pm25,
      pm10, bv, bi, bp);
  if (written < 0 || static_cast<size_t>(written) >= output_size) {
    output[0] = '\0';
    return 0;
  }
  return static_cast<size_t>(written);
}

}  // namespace iiot
