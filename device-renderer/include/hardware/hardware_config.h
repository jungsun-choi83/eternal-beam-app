#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace eb::hardware {

/// Mirrors python/hardware/config.py::GpioLineConfig — one entry under
/// common.gpio.lines.<name> in hardware_config.yaml.
struct GpioLineConfig {
  std::string name;
  bool enabled = false;
  int offset = -1;
  std::string direction = "out";  // "in" | "out"
  bool active_low = false;
  std::optional<std::string> edge;  // "rising" | "falling" | "both"
};

/// Loaded, board-merged view of hardware_config.yaml. This is the C++
/// equivalent of python/hardware/config.py::HardwareConfig — same fields,
/// same env var override names, same active_board + boards/common merge
/// semantics, so both stacks read the exact same mental model even though
/// they load independent YAML files during the transition period.
class HardwareConfig {
 public:
  /// Loads and merges hardware_config.yaml.
  ///
  /// Resolution order for the config file path:
  ///   1. `path`, if given
  ///   2. HARDWARE_CONFIG environment variable
  ///   3. <repo>/device-renderer/config/hardware_config.yaml
  ///
  /// Resolution order for the active board:
  ///   1. HARDWARE_BOARD environment variable
  ///   2. `active_board:` key in the YAML file
  ///
  /// Throws std::runtime_error with a descriptive message on any failure —
  /// callers (main.cpp) are expected to catch it and fail fast, mirroring
  /// python/hardware/config.py::load_hardware_config().
  static HardwareConfig Load(const std::optional<std::string> &path = std::nullopt);

  const std::string &board() const { return board_; }
  const std::string &label() const { return label_; }
  const std::string &config_path() const { return config_path_; }

  int i2c_bus() const;
  int vl53l0x_address() const;
  int pn532_address() const;

  std::string gpio_chip() const;
  GpioLineConfig gpio_line(const std::string &name) const;

  std::string alsa_card() const;
  std::map<std::string, std::string> display_env() const;

  double touch_min_mm() const;
  double touch_max_mm() const;
  double approach_threshold_mm() const;
  double distance_poll_sec() const;

  double nfc_poll_sec() const;
  double nfc_debounce_sec() const;

  double voice_rms_threshold() const;
  double voice_hold_ms() const;
  double voice_cooldown_sec() const;

  std::string assets_root() const;
  std::string assets_sync_endpoint() const;

  /// UDP bridge (see src/hardware/udp_bridge_impl.h) — mirrors
  /// python/hardware_config.yaml's network.udp_host/udp_port, but here it's
  /// the *bind* address/port this process listens on (the Python side is
  /// the sender, unchanged), not the address it sends to.
  std::string udp_bind_host() const;
  int udp_port() const;

  /// Base URL for the backend this device talks to (see
  /// src/app/device_sync_client.h) — same GET /v1/device/sync endpoint
  /// Unity used to call, now called from C++ instead.
  std::string backend_base_url() const;

  /// Generic dotted-path lookup for anything not exposed as a typed
  /// accessor above, matching python's HardwareConfig.get(*path, default=).
  /// Example: cfg.GetString({"player", "binary"}, "auto").
  std::string GetString(const std::vector<std::string> &path, const std::string &fallback) const;
  double GetDouble(const std::vector<std::string> &path, double fallback) const;
  int GetInt(const std::vector<std::string> &path, int fallback) const;
  bool GetBool(const std::vector<std::string> &path, bool fallback) const;

 private:
  struct Impl;
  std::shared_ptr<Impl> impl_;

  std::string board_;
  std::string label_;
  std::string config_path_;
};

}  // namespace eb::hardware
