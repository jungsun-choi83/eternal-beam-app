#pragma once

#include "hardware/hardware_config.h"
#include "hardware/hardware_interface.h"

namespace eb::hardware {

/// No-hardware backend used whenever the target platform isn't Linux (dev
/// machines) or when hardware_config.yaml's active_board resolves to
/// something CreateHardware() doesn't recognize. Poll() never emits events
/// on its own — call SimulateEvent() (e.g. from a keyboard-driven dev tool)
/// to exercise the AppController/renderer pipeline without real sensors.
class MockHardware : public HardwareInterface {
 public:
  explicit MockHardware(HardwareConfig config);

  bool Initialize() override;
  void Shutdown() override;
  void Poll() override;
  void SetStatusLed(bool on) override;
  std::string BoardName() const override { return "mock"; }

  /// Test/dev hook — pushes a synthetic sensor event through the same
  /// callback real hardware backends use.
  void SimulateEvent(ActionEvent action, std::string payload = "");

 private:
  HardwareConfig config_;
  bool status_led_on_ = false;
};

}  // namespace eb::hardware
