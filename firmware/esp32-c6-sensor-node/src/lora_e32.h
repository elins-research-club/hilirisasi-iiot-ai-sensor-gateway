#pragma once

namespace iiot {

class LoraE32Link {
 public:
  bool begin();
  bool sendLine(const char* payload);
};

}
