#include <cstdlib>

#include <catch2/catch_test_macros.hpp>

#include "hardware/hardware_config.h"

using eb::hardware::HardwareConfig;

namespace {

#if defined(_WIN32)
void SetEnvVar(const char *name, const char *value) { _putenv_s(name, value); }
void UnsetEnvVar(const char *name) { _putenv_s(name, ""); }
#else
void SetEnvVar(const char *name, const char *value) { setenv(name, value, /*overwrite=*/1); }
void UnsetEnvVar(const char *name) { unsetenv(name); }
#endif

/// Ensures HARDWARE_BOARD env overrides from one test never leak into the
/// next.
struct EnvGuard {
  explicit EnvGuard(const char *name) : name_(name) {}
  ~EnvGuard() { UnsetEnvVar(name_); }
  const char *name_;
};

}  // namespace

TEST_CASE("HardwareConfig loads the default (rpi5) board", "[hardware_config]") {
  EnvGuard guard("HARDWARE_BOARD");
  UnsetEnvVar("HARDWARE_BOARD");

  const HardwareConfig cfg = HardwareConfig::Load(ETERNALBEAM_TEST_CONFIG_PATH);

  CHECK(cfg.board() == "rpi5");
  CHECK(cfg.i2c_bus() == 1);
  CHECK(cfg.gpio_chip() == "gpiochip0");
  CHECK(cfg.vl53l0x_address() == 0x29);
  CHECK(cfg.pn532_address() == 0x24);
}

TEST_CASE("HARDWARE_BOARD env var overrides active_board", "[hardware_config]") {
  EnvGuard guard("HARDWARE_BOARD");
  SetEnvVar("HARDWARE_BOARD", "rk3566");

  const HardwareConfig cfg = HardwareConfig::Load(ETERNALBEAM_TEST_CONFIG_PATH);

  CHECK(cfg.board() == "rk3566");
  CHECK(cfg.i2c_bus() == 3);
  CHECK(cfg.gpio_chip() == "gpiochip1");
  // common.* values are still merged in regardless of which board is active.
  CHECK(cfg.vl53l0x_address() == 0x29);
}

TEST_CASE("gpio_line() returns a disabled default when the line is unknown", "[hardware_config]") {
  EnvGuard guard("HARDWARE_BOARD");
  UnsetEnvVar("HARDWARE_BOARD");

  const HardwareConfig cfg = HardwareConfig::Load(ETERNALBEAM_TEST_CONFIG_PATH);
  const auto line = cfg.gpio_line("does_not_exist");

  CHECK(line.enabled == false);
  CHECK(line.offset == -1);

  const auto status_led = cfg.gpio_line("status_led");
  CHECK(status_led.enabled == false);  // disabled by default in config/hardware_config.yaml
  CHECK(status_led.offset == 17);
}
