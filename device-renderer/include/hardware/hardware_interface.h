#pragma once

#include <memory>
#include <string>

#include "hardware/sensor_event.h"

namespace eb::hardware {

class HardwareConfig;

/// Abstract hardware boundary. Everything above this line (src/app,
/// src/renderer) only ever talks to HardwareInterface — never to gpiod,
/// i2c-dev, ALSA, or any other board-specific API directly.
///
/// Porting to a new board means adding one .cpp file that implements this
/// interface (see rk3566_impl.cpp for the pattern) and a matching `boards.*`
/// entry in hardware_config.yaml. No other source file should need to change.
class HardwareInterface {
 public:
  virtual ~HardwareInterface() = default;

  /// Opens GPIO lines / I2C bus / ALSA device etc. Returns false (and logs
  /// why) on failure so main() can decide whether to run in a degraded mode.
  virtual bool Initialize() = 0;

  /// Releases every OS handle opened by Initialize(). Safe to call even if
  /// Initialize() failed or was never called.
  virtual void Shutdown() = 0;

  /// Polls sensors once. Implementations should be non-blocking (or bound to
  /// a short timeout) — this is called from the main render loop, typically
  /// at 30-60 Hz. Any detected touch/voice/NFC/idle transition is reported
  /// through the callback registered via SetSensorEventCallback.
  virtual void Poll() = 0;

  /// Optional status LED — a no-op on boards where gpio.lines.status_led is
  /// disabled in hardware_config.yaml (see HardwareConfig::GpioLine).
  virtual void SetStatusLed(bool on) = 0;

  /// Board identifier as resolved from hardware_config.yaml, e.g. "rpi5",
  /// "rk3566", or "mock" on non-Linux dev machines. Used only for logging /
  /// diagnostics — never branch application logic on this string.
  virtual std::string BoardName() const = 0;

  void SetSensorEventCallback(SensorEventCallback callback) {
    callback_ = std::move(callback);
  }

 protected:
  /// Implementations call this from Poll() whenever a sensor threshold is
  /// crossed. No-op if no callback has been registered yet.
  void Emit(const SensorEvent &event) const {
    if (callback_) {
      callback_(event);
    }
  }

 private:
  SensorEventCallback callback_;
};

}  // namespace eb::hardware
