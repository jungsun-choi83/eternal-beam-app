#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "hardware/hardware_config.h"
#include "hardware/hardware_interface.h"

namespace eb::hardware {

/// Parses one line of the *unchanged* JSON-over-UDP protocol the Python
/// sensor bridges already speak (python/pi_sensors_to_unity_udp.py,
/// voice_to_unity.py, eternal_beam_pi.py, s23_bridge_simple.py) — e.g.
/// {"event":"touch","distance_mm":32}, {"event":"nfc_tagged","uid":".."},
/// {"event":"voice","rms":123}, {"event":"idle","source":"pi_reset"}.
/// Returns std::nullopt for lines that don't map to one of the four
/// ActionEvents AppController understands (e.g. "approach"/"nfc_match" are
/// proximity/informational pre-signals with no dedicated action — Unity's
/// UDPReceiver used to treat "approach" as a "near" hint with no equivalent
/// today; skipping it here is deliberate, not an oversight). Exposed as a
/// free function (no socket I/O) purely so it's unit-testable — see
/// tests/test_udp_bridge.cpp.
std::optional<SensorEvent> ParseUdpSensorEvent(const std::string &line);

/// HardwareInterface implementation that receives sensor events over UDP
/// instead of polling GPIO/I2C directly — the C++ equivalent of Unity's
/// UDPReceiver + PythonBridge, preserving the existing Python-side sensor
/// bridges and their wire format completely unchanged. Selected via
/// `active_board: udp_bridge` (or HARDWARE_BOARD=udp_bridge) in
/// hardware_config.yaml — see hardware_factory.cpp.
///
/// Cross-platform (Winsock2 on Windows, BSD sockets elsewhere) since it has
/// no board-specific GPIO/I2C dependency, unlike Rk3566Hardware/Rpi5Hardware
/// — this also means it's the one HardwareInterface backend that's
/// meaningfully testable on the Windows/macOS dev machine against the real
/// Python sender.
class UdpBridgeHardware : public HardwareInterface {
 public:
  explicit UdpBridgeHardware(HardwareConfig config);
  ~UdpBridgeHardware() override;

  bool Initialize() override;
  void Shutdown() override;
  void Poll() override;
  void SetStatusLed(bool on) override;
  std::string BoardName() const override { return "udp_bridge"; }

 private:
  HardwareConfig config_;
  std::string bind_host_;
  int port_ = 5005;
  bool status_led_on_ = false;

  // Opaque so this header never needs <winsock2.h>/<sys/socket.h>; on
  // platforms without a socket implementation Initialize() logs and returns
  // false, leaving Poll() a no-op (same degraded-mode contract as every
  // other HardwareInterface implementation).
  std::intptr_t socket_handle_ = -1;
};

}  // namespace eb::hardware
