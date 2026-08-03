#pragma once

// This header is only compiled on Linux targets (see src/hardware/CMakeLists.txt) —
// it is the shared implementation both rk3566_impl.cpp and rpi5_impl.cpp build
// on top of, using nothing but standard Linux kernel interfaces:
//   - I2C:  /dev/i2c-N via <linux/i2c-dev.h> ioctl (no vendor SDK)
//   - GPIO: libgpiod (https://libgpiod.readthedocs.io) — chip name + line
//           offset only, so a board that renumbers offsets just changes
//           hardware_config.yaml, never this code.
//
// Board .cpp files subclass LinuxCommonHardware and override only the parts
// that are genuinely SoC-specific (constructor board name, and — once the
// real renderer needs it — display bring-up / DRM-KMS connector selection).

#include <cstdint>
#include <optional>
#include <string>

#include "hardware/hardware_config.h"
#include "hardware/hardware_interface.h"

namespace eb::hardware {

class LinuxCommonHardware : public HardwareInterface {
 public:
  explicit LinuxCommonHardware(HardwareConfig config, std::string board_name);
  ~LinuxCommonHardware() override;

  bool Initialize() override;
  void Shutdown() override;
  void Poll() override;
  void SetStatusLed(bool on) override;
  std::string BoardName() const override { return board_name_; }

 protected:
  const HardwareConfig &config() const { return config_; }

  /// Hook for board-specific bring-up that runs after the common I2C/GPIO
  /// setup succeeds. Default is a no-op; override in a board subclass for
  /// things like DRM/KMS display init that differ per SoC.
  virtual bool OnInitialize() { return true; }
  virtual void OnShutdown() {}

 private:
  bool OpenI2c();
  void CloseI2c();
  bool OpenStatusLed();
  void CloseStatusLed();

  /// Best-effort "is anything answering at this address" probe (like
  /// `i2cdetect -y <bus>`), used only for the startup diagnostic log —
  /// never for gating functionality.
  bool ProbeI2cAddress(uint8_t addr7) const;

  /// Reads the VL53L0X ranging result, in millimeters. Returns std::nullopt
  /// if the sensor isn't answering.
  ///
  /// NOTE: this only issues the read of the final range register — it does
  /// NOT perform the full ST VL53L0X calibration/bring-up sequence (SPAD
  /// mapping, timing budget, ref calibration) that
  /// adafruit-circuitpython-vl53l0x performs on start-up. Porting that
  /// sequence from the Adafruit driver is tracked as follow-up work; until
  /// then this will typically return stale/zero readings on real hardware.
  std::optional<int> ReadDistanceMm();

  /// Reads a queued NFC UID from the PN532, if any tag is present.
  /// NOTE: same caveat as ReadDistanceMm() — the PN532 host-controller
  /// handshake (SAM configuration, passive-target polling) needs to be
  /// ported from adafruit-circuitpython-pn532 before this returns real UIDs.
  std::optional<std::string> ReadNfcUid();

  HardwareConfig config_;
  std::string board_name_;

  int i2c_fd_ = -1;
  void *gpio_chip_ = nullptr;  // gpiod_chip*, opaque here to avoid leaking <gpiod.h> into this header
  void *status_led_line_ = nullptr;  // gpiod_line*

  double last_touch_emit_sec_ = 0.0;
  double last_nfc_emit_sec_ = 0.0;
};

}  // namespace eb::hardware
