#pragma once

#include "linux_common_hardware.h"

namespace eb::hardware {

/// RK3566-specific hardware backend. GPIO chip / I2C bus / ALSA card all
/// come from hardware_config.yaml (boards.rk3566.*) via LinuxCommonHardware
/// — this subclass exists only for the pieces that are genuinely tied to
/// the Rockchip SoC rather than expressible as config, e.g. DRM/KMS display
/// bring-up. If you find yourself adding config lookups here instead of to
/// hardware_config.yaml, that's a sign the value isn't actually board-specific.
class Rk3566Hardware : public LinuxCommonHardware {
 public:
  explicit Rk3566Hardware(HardwareConfig config);

 protected:
  bool OnInitialize() override;
  void OnShutdown() override;
};

}  // namespace eb::hardware
