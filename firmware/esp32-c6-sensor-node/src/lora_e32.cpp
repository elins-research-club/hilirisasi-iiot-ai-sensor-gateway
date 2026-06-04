#include "lora_e32.h"

#include <string.h>
#include "driver/gpio.h"
#include "driver/uart.h"
#include "config.h"

namespace iiot {

bool LoraE32Link::begin() {
  gpio_set_direction(static_cast<gpio_num_t>(LORA_M0_PIN), GPIO_MODE_OUTPUT);
  gpio_set_direction(static_cast<gpio_num_t>(LORA_M1_PIN), GPIO_MODE_OUTPUT);
  gpio_set_direction(static_cast<gpio_num_t>(LORA_AUX_PIN), GPIO_MODE_INPUT);
  gpio_set_level(static_cast<gpio_num_t>(LORA_M0_PIN), 0);
  gpio_set_level(static_cast<gpio_num_t>(LORA_M1_PIN), 0);

  uart_config_t uart_config = {};
  uart_config.baud_rate = LORA_BAUD;
  uart_config.data_bits = UART_DATA_8_BITS;
  uart_config.parity = UART_PARITY_DISABLE;
  uart_config.stop_bits = UART_STOP_BITS_1;
  uart_config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  uart_config.source_clk = UART_SCLK_DEFAULT;

  uart_param_config(static_cast<uart_port_t>(LORA_UART_PORT), &uart_config);
  uart_set_pin(static_cast<uart_port_t>(LORA_UART_PORT), LORA_TX_PIN, LORA_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
  uart_driver_install(static_cast<uart_port_t>(LORA_UART_PORT), 1024, 0, 0, nullptr, 0);
  return true;
}

bool LoraE32Link::sendLine(const char* payload) {
  if (payload == nullptr || payload[0] == '\0') {
    return false;
  }
  uart_write_bytes(static_cast<uart_port_t>(LORA_UART_PORT), payload, strlen(payload));
  uart_write_bytes(static_cast<uart_port_t>(LORA_UART_PORT), "\n", 1);
  return true;
}

}
