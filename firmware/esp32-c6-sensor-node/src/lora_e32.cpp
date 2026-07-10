#include "lora_e32.h"

#include <string.h>

#include "config.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace iiot {
namespace {
constexpr const char* TAG = "lora_e32";
}

bool LoraE32Link::waitAuxHigh(uint32_t timeout_ms) const {
  const int64_t deadline_us = esp_timer_get_time() + static_cast<int64_t>(timeout_ms) * 1000;
  while (esp_timer_get_time() < deadline_us) {
    if (gpio_get_level(static_cast<gpio_num_t>(LORA_AUX_PIN)) == 1) {
      return true;
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  return false;
}

bool LoraE32Link::begin() {
  ready_ = false;
  gpio_config_t output_config{};
  output_config.pin_bit_mask = (1ULL << LORA_M0_PIN) | (1ULL << LORA_M1_PIN);
  output_config.mode = GPIO_MODE_OUTPUT;
  output_config.pull_up_en = GPIO_PULLUP_DISABLE;
  output_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  output_config.intr_type = GPIO_INTR_DISABLE;
  esp_err_t err = gpio_config(&output_config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "mode pin config failed: %s", esp_err_to_name(err));
    return false;
  }
  gpio_set_level(static_cast<gpio_num_t>(LORA_M0_PIN), 0);
  gpio_set_level(static_cast<gpio_num_t>(LORA_M1_PIN), 0);

  gpio_config_t aux_config{};
  aux_config.pin_bit_mask = 1ULL << LORA_AUX_PIN;
  aux_config.mode = GPIO_MODE_INPUT;
  aux_config.pull_up_en = GPIO_PULLUP_ENABLE;
  aux_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  aux_config.intr_type = GPIO_INTR_DISABLE;
  err = gpio_config(&aux_config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "AUX pin config failed: %s", esp_err_to_name(err));
    return false;
  }

  uart_config_t uart_config{};
  uart_config.baud_rate = LORA_BAUD;
  uart_config.data_bits = UART_DATA_8_BITS;
  uart_config.parity = UART_PARITY_DISABLE;
  uart_config.stop_bits = UART_STOP_BITS_1;
  uart_config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  uart_config.source_clk = UART_SCLK_DEFAULT;
  const uart_port_t port = static_cast<uart_port_t>(LORA_UART_PORT);
  err = uart_param_config(port, &uart_config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "uart_param_config failed: %s", esp_err_to_name(err));
    return false;
  }
  err = uart_set_pin(port, LORA_TX_PIN, LORA_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "uart_set_pin failed: %s", esp_err_to_name(err));
    return false;
  }
  err = uart_driver_install(port, 1024, 0, 0, nullptr, 0);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "uart_driver_install failed: %s", esp_err_to_name(err));
    return false;
  }
  if (!waitAuxHigh(LORA_AUX_TIMEOUT_MS)) {
    ESP_LOGE(TAG, "E32 AUX did not become ready");
    return false;
  }
  ready_ = true;
  return true;
}

bool LoraE32Link::sendLine(const char* payload) {
  if (!ready_ || payload == nullptr || payload[0] == '\0') {
    return false;
  }
  if (!waitAuxHigh(LORA_AUX_TIMEOUT_MS)) {
    ESP_LOGW(TAG, "E32 busy before transmit");
    return false;
  }
  const uart_port_t port = static_cast<uart_port_t>(LORA_UART_PORT);
  const size_t length = strlen(payload);
  const int payload_written = uart_write_bytes(port, payload, length);
  const int newline_written = uart_write_bytes(port, "\n", 1);
  if (payload_written != static_cast<int>(length) || newline_written != 1) {
    ESP_LOGE(TAG, "UART short write: payload=%d/%u newline=%d", payload_written,
             static_cast<unsigned>(length), newline_written);
    return false;
  }
  esp_err_t err = uart_wait_tx_done(port, pdMS_TO_TICKS(LORA_TX_TIMEOUT_MS));
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "UART TX wait failed: %s", esp_err_to_name(err));
    return false;
  }
  if (!waitAuxHigh(LORA_AUX_TIMEOUT_MS)) {
    ESP_LOGW(TAG, "E32 AUX did not confirm transmit completion");
    return false;
  }
  return true;
}

}  // namespace iiot
