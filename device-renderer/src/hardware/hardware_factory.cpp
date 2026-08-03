#include "hardware/hardware_factory.h"

#include "mock_impl.h"
#include "udp_bridge_impl.h"

#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
#include "rk3566_impl.h"
#include "rpi5_impl.h"
#endif

namespace eb::hardware {

std::unique_ptr<HardwareInterface> CreateHardware(const HardwareConfig &config) {
  // udp_bridge is cross-platform (no gpiod/i2c-dev dependency) — preserves
  // the existing Python UDP sensor bridges untouched; see
  // src/hardware/udp_bridge_impl.h.
  if (config.board() == "udp_bridge") {
    return std::make_unique<UdpBridgeHardware>(config);
  }
#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
  if (config.board() == "rk3566") {
    return std::make_unique<Rk3566Hardware>(config);
  }
  if (config.board() == "rpi5") {
    return std::make_unique<Rpi5Hardware>(config);
  }
#endif
  // Unknown board string, or building on a non-Linux dev machine: fall back
  // to the mock backend rather than failing outright.
  return std::make_unique<MockHardware>(config);
}

}  // namespace eb::hardware
