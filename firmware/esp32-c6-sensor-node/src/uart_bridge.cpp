#include "uart_bridge.h"

#include <algorithm>

#include "config.h"
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace iiot {
namespace {

constexpr uint8_t REG_RHR_THR_DLL = 0x00;
constexpr uint8_t REG_IER_DLH = 0x01;
constexpr uint8_t REG_FCR_IIR = 0x02;
constexpr uint8_t REG_LCR = 0x03;
constexpr uint8_t REG_TXLVL = 0x08;
constexpr uint8_t REG_RXLVL = 0x09;
constexpr uint8_t LCR_DLAB = 0x80;
constexpr uint8_t LCR_8N1 = 0x03;
constexpr uint8_t FCR_ENABLE_RESET = 0x07;
constexpr uint32_t I2C_TIMEOUT_MS = 100;
constexpr size_t FIFO_SIZE = 64;

bool addDevice(void* raw_bus, uint8_t address, i2c_master_dev_handle_t& device) {
  if (raw_bus == nullptr) {
    return false;
  }
  i2c_device_config_t config{};
  config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  config.device_address = address;
  config.scl_speed_hz = I2C_CLOCK_HZ;
  auto bus = static_cast<i2c_master_bus_handle_t>(raw_bus);
  return i2c_master_bus_add_device(bus, &config, &device) == ESP_OK;
}

uint32_t elapsedMs(int64_t started_us) {
  const int64_t elapsed = esp_timer_get_time() - started_us;
  return elapsed <= 0 ? 0U : static_cast<uint32_t>(elapsed / 1000);
}

}  // namespace

uint8_t Sc16is752Bridge::subaddress(Channel channel, uint8_t reg) {
  return static_cast<uint8_t>(((reg & 0x0FU) << 3U) |
                              ((static_cast<uint8_t>(channel) & 0x01U) << 1U));
}

bool Sc16is752Bridge::begin(void* i2c_bus, uint8_t address, uint32_t crystal_hz) {
  if (i2c_bus == nullptr || address > 0x7F || crystal_hz == 0) {
    return false;
  }
  i2c_bus_ = i2c_bus;
  address_ = address;
  crystal_hz_ = crystal_hz;
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  return i2c_master_probe(bus, address_, I2C_TIMEOUT_MS) == ESP_OK;
}

bool Sc16is752Bridge::configure(Channel channel, uint32_t baudrate) {
  if (i2c_bus_ == nullptr || crystal_hz_ == 0 || baudrate == 0) {
    return false;
  }
  const uint64_t denominator = static_cast<uint64_t>(16U) * baudrate;
  const uint64_t rounded = (static_cast<uint64_t>(crystal_hz_) + denominator / 2U) / denominator;
  if (rounded == 0 || rounded > 0xFFFFU) {
    return false;
  }
  const uint16_t divisor = static_cast<uint16_t>(rounded);
  if (!writeRegister(channel, REG_LCR, LCR_DLAB) ||
      !writeRegister(channel, REG_RHR_THR_DLL, static_cast<uint8_t>(divisor & 0xFFU)) ||
      !writeRegister(channel, REG_IER_DLH, static_cast<uint8_t>(divisor >> 8U)) ||
      !writeRegister(channel, REG_LCR, LCR_8N1) ||
      !writeRegister(channel, REG_IER_DLH, 0x00) ||
      !writeRegister(channel, REG_FCR_IIR, FCR_ENABLE_RESET)) {
    return false;
  }
  uint8_t lcr = 0;
  return readRegister(channel, REG_LCR, lcr) && lcr == LCR_8N1 && flushRx(channel);
}

bool Sc16is752Bridge::flushRx(Channel channel) {
  uint8_t available = 0;
  uint8_t discard[FIFO_SIZE] = {0};
  for (int pass = 0; pass < 4; ++pass) {
    if (!readRegister(channel, REG_RXLVL, available)) {
      return false;
    }
    if (available == 0) {
      return true;
    }
    const size_t chunk = std::min<size_t>(available, sizeof(discard));
    if (!readFifo(channel, discard, chunk)) {
      return false;
    }
  }
  return false;
}

bool Sc16is752Bridge::write(Channel channel, const uint8_t* data, size_t size,
                            uint32_t timeout_ms) {
  if (data == nullptr || size == 0 || timeout_ms == 0) {
    return false;
  }
  const int64_t started = esp_timer_get_time();
  size_t offset = 0;
  while (offset < size) {
    uint8_t free_bytes = 0;
    if (!readRegister(channel, REG_TXLVL, free_bytes)) {
      return false;
    }
    if (free_bytes == 0) {
      if (elapsedMs(started) >= timeout_ms) {
        return false;
      }
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }
    const size_t chunk = std::min<size_t>(free_bytes, size - offset);
    if (!writeFifo(channel, data + offset, chunk)) {
      return false;
    }
    offset += chunk;
  }
  return true;
}

size_t Sc16is752Bridge::read(Channel channel, uint8_t* output, size_t size,
                             uint32_t timeout_ms) {
  if (output == nullptr || size == 0 || timeout_ms == 0) {
    return 0;
  }
  const int64_t started = esp_timer_get_time();
  size_t offset = 0;
  while (offset < size) {
    uint8_t available = 0;
    if (!readRegister(channel, REG_RXLVL, available)) {
      return 0;
    }
    if (available == 0) {
      if (elapsedMs(started) >= timeout_ms) {
        break;
      }
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }
    const size_t chunk = std::min<size_t>(available, size - offset);
    if (!readFifo(channel, output + offset, chunk)) {
      return 0;
    }
    offset += chunk;
  }
  return offset;
}

bool Sc16is752Bridge::writeRegister(Channel channel, uint8_t reg, uint8_t value) const {
  i2c_master_dev_handle_t device = nullptr;
  if (!addDevice(i2c_bus_, address_, device)) {
    return false;
  }
  const uint8_t payload[] = {subaddress(channel, reg), value};
  const esp_err_t result = i2c_master_transmit(device, payload, sizeof(payload), I2C_TIMEOUT_MS);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK;
}

bool Sc16is752Bridge::readRegister(Channel channel, uint8_t reg, uint8_t& value) const {
  i2c_master_dev_handle_t device = nullptr;
  if (!addDevice(i2c_bus_, address_, device)) {
    return false;
  }
  const uint8_t command = subaddress(channel, reg);
  const esp_err_t result =
      i2c_master_transmit_receive(device, &command, 1, &value, 1, I2C_TIMEOUT_MS);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK;
}

bool Sc16is752Bridge::writeFifo(Channel channel, const uint8_t* data, size_t size) const {
  if (data == nullptr || size == 0 || size > FIFO_SIZE) {
    return false;
  }
  i2c_master_dev_handle_t device = nullptr;
  if (!addDevice(i2c_bus_, address_, device)) {
    return false;
  }
  uint8_t payload[FIFO_SIZE + 1] = {0};
  payload[0] = subaddress(channel, REG_RHR_THR_DLL);
  for (size_t index = 0; index < size; ++index) {
    payload[index + 1] = data[index];
  }
  const esp_err_t result =
      i2c_master_transmit(device, payload, size + 1, I2C_TIMEOUT_MS);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK;
}

bool Sc16is752Bridge::readFifo(Channel channel, uint8_t* output, size_t size) const {
  if (output == nullptr || size == 0 || size > FIFO_SIZE) {
    return false;
  }
  i2c_master_dev_handle_t device = nullptr;
  if (!addDevice(i2c_bus_, address_, device)) {
    return false;
  }
  const uint8_t command = subaddress(channel, REG_RHR_THR_DLL);
  const esp_err_t result =
      i2c_master_transmit_receive(device, &command, 1, output, size, I2C_TIMEOUT_MS);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK;
}

}  // namespace iiot
