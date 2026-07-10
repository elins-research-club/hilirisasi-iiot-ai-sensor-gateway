#pragma once

#include <stddef.h>
#include <stdint.h>

namespace iiot {

class Sc16is752Bridge {
 public:
  enum class Channel : uint8_t { A = 0, B = 1 };

  bool begin(void* i2c_bus, uint8_t address, uint32_t crystal_hz);
  bool configure(Channel channel, uint32_t baudrate);
  bool flushRx(Channel channel);
  bool write(Channel channel, const uint8_t* data, size_t size, uint32_t timeout_ms);
  size_t read(Channel channel, uint8_t* output, size_t size, uint32_t timeout_ms);

 private:
  bool writeRegister(Channel channel, uint8_t reg, uint8_t value) const;
  bool readRegister(Channel channel, uint8_t reg, uint8_t& value) const;
  bool writeFifo(Channel channel, const uint8_t* data, size_t size) const;
  bool readFifo(Channel channel, uint8_t* output, size_t size) const;
  static uint8_t subaddress(Channel channel, uint8_t reg);

  void* i2c_bus_ = nullptr;
  uint8_t address_ = 0;
  uint32_t crystal_hz_ = 0;
};

}  // namespace iiot
