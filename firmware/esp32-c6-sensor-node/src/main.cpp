#include "config.h"
#include "lora_e32.h"
#include "payload.h"
#include "preprocessing.h"
#include "sensors.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
constexpr const char* TAG = "iiot_node";

iiot::SensorReader sensor_reader;
iiot::Preprocessor preprocessor;
iiot::PayloadBuilder payload_builder;
iiot::LoraE32Link lora_link;

unsigned long sequence_number = 0;
char payload_buffer[320];
}

extern "C" void app_main(void) {
  ESP_LOGI(TAG, "IIoT ESP32-C6 sensor node starting");

  if (!sensor_reader.begin()) {
    ESP_LOGE(TAG, "sensor init failed");
  }
  if (!lora_link.begin()) {
    ESP_LOGE(TAG, "lora init failed");
  }

  while (true) {
    iiot::SensorSample raw = sensor_reader.read();
    iiot::PreprocessedSample processed = preprocessor.process(raw);
    unsigned long timestamp = static_cast<unsigned long>(xTaskGetTickCount() / configTICK_RATE_HZ);
    payload_builder.build(payload_buffer, sizeof(payload_buffer), processed, timestamp, sequence_number++);

    ESP_LOGI(TAG, "%s", payload_buffer);
    lora_link.sendLine(payload_buffer);
    vTaskDelay(pdMS_TO_TICKS(iiot::SAMPLE_INTERVAL_MS));
  }
}
