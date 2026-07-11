#include <stdio.h>

#include "config.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lora_e32.h"
#include "payload.h"
#include "preprocessing.h"
#include "sensors.h"

namespace {
constexpr const char* TAG = "iiot_sensor_node";
}

extern "C" void app_main() {
  iiot::SensorReader sensors;
  iiot::HardwareIntegrityGate hardware_gate;
  iiot::LoraE32Link link;

  const bool sensors_initialized = sensors.begin();
  bool link_ready = link.begin();
  ESP_LOGI(TAG, "sensor profile init=%s lora init=%s mock=%d",
           sensors_initialized ? "ok" : "degraded", link_ready ? "ok" : "failed",
           IIOT_USE_MOCK_SENSORS);

  char boot_id[17] = {0};
  const uint64_t random_boot =
      (static_cast<uint64_t>(esp_random()) << 32) | static_cast<uint64_t>(esp_random());
  snprintf(boot_id, sizeof(boot_id), "%016llx",
           static_cast<unsigned long long>(random_boot));

  uint32_t sequence = 0;
  while (true) {
    if (!link_ready) {
      link_ready = link.begin();
    }
    const iiot::SensorSample raw = sensors.read();
    const iiot::HardwareObservation observation = hardware_gate.process(raw);
    char payload[1024] = {0};
    const uint64_t uptime_seconds = static_cast<uint64_t>(esp_timer_get_time() / 1000000ULL);
    const size_t payload_size =
        iiot::buildPayload(payload, sizeof(payload), observation, sequence, uptime_seconds, boot_id);
    if (payload_size == 0) {
      ESP_LOGE(TAG, "payload encoding failed; event not transmitted");
    } else if (!link_ready) {
      ESP_LOGW(TAG, "LoRa unavailable; event seq=%lu not transmitted",
               static_cast<unsigned long>(sequence));
    } else if (!link.sendLine(payload)) {
      ESP_LOGW(TAG, "LoRa transmit failed for seq=%lu",
               static_cast<unsigned long>(sequence));
      link_ready = false;
    } else {
      ESP_LOGI(TAG, "sent compact-v3 seq=%lu bytes=%u hardware_summary=%s",
               static_cast<unsigned long>(sequence), static_cast<unsigned>(payload_size),
               observation.hardware_summary);
    }
    ++sequence;
    vTaskDelay(pdMS_TO_TICKS(iiot::SAMPLE_INTERVAL_MS));
  }
}
