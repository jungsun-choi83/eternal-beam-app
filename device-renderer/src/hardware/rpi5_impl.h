#pragma once

#include "linux_common_hardware.h"

namespace eb::hardware {

/// Raspberry Pi 5 hardware backend — the current/reference board. Kept as
/// its own subclass (rather than instantiating LinuxCommonHardware
/// directly) purely to demonstrate the porting pattern new boards should
/// follow; today it overrides nothing beyond the board name.
class Rpi5Hardware : public LinuxCommonHardware {
 public:
  explicit Rpi5Hardware(HardwareConfig config);

 protected:
  bool OnInitialize() override;
  void OnShutdown() override;
};

}  // namespace eb::hardware
