#pragma once

#include <stddef.h>
#include <stdint.h>

#include "sensor_types.h"

namespace iiot {

class Sc16is752Bridge;

class SensorReader {
 public:
  bool begin();
  SensorSample read();

 private:
#if !IIOT_USE_MOCK_SENSORS
  bool probeI2c(uint8_t address) const;
  bool readBme688(SensorSample& sample) const;
  bool readSen0466(SensorSample& sample) const;
  bool readSen0574(SensorSample& sample) const;
  bool readSen0321(SensorSample& sample) const;
  bool readMhz19(SensorSample& sample) const;
  bool readPms7003t(SensorSample& sample) const;
  bool readIna226(SensorSample& sample) const;
  bool writeI2c(uint8_t address, const uint8_t* data, size_t size) const;
  bool readI2c(uint8_t address, const uint8_t* command, size_t command_size,
               uint8_t* output, size_t output_size, uint32_t timeout_ms = 100) const;
  bool readI2cRegister16(uint8_t address, uint8_t reg, uint16_t& value) const;
  bool writeI2cRegister16(uint8_t address, uint8_t reg, uint16_t value) const;

  void* i2c_bus_ = nullptr;
  void* adc_unit_ = nullptr;
  void* adc_cali_ = nullptr;
  void* bme68x_dev_ = nullptr;
  Sc16is752Bridge* uart_bridge_ = nullptr;
  int adc_channel_ = -1;
  int64_t started_us_ = 0;
#endif
  uint32_t mock_step_ = 0;
};

}  // namespace iiot
