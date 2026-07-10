#pragma once

#include <stdint.h>

namespace iiot {

class LoraE32Link {
 public:
  bool begin();
  bool sendLine(const char* payload);

 private:
  bool waitAuxHigh(uint32_t timeout_ms) const;
  bool ready_ = false;
};

}  // namespace iiot
