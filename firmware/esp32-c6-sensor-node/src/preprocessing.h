#pragma once

#include "sensor_types.h"

namespace iiot {

// The historical filename remains to minimize build-system churn, but the class
// no longer performs semantic preprocessing. It only enforces hardware-level
// integrity before compact_sensor.v3 encoding.
class HardwareIntegrityGate {
 public:
  HardwareObservation process(const SensorSample& input) const;
};

}  // namespace iiot
