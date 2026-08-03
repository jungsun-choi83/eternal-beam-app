#include "hardware/hardware_config.h"

#include <cstdlib>
#include <stdexcept>

#include <yaml-cpp/yaml.h>

namespace eb::hardware {

namespace {

std::optional<std::string> GetEnv(const char *name) {
  const char *value = std::getenv(name);
  if (value == nullptr || std::string(value).empty()) {
    return std::nullopt;
  }
  return std::string(value);
}

std::string DefaultConfigPath() {
  // Relative to the process working directory, which build/run scripts are
  // expected to set to the device-renderer/ project root.
  return "config/hardware_config.yaml";
}

/// Recursively merges `override` on top of `base` (override wins), matching
/// python/hardware/config.py::_deep_merge exactly.
YAML::Node DeepMerge(const YAML::Node &base, const YAML::Node &override) {
  YAML::Node result = YAML::Clone(base);
  if (!override || !override.IsMap()) {
    return result;
  }
  for (const auto &kv : override) {
    const std::string key = kv.first.as<std::string>();
    if (result[key] && result[key].IsMap() && kv.second.IsMap()) {
      result[key] = DeepMerge(result[key], kv.second);
    } else {
      result[key] = kv.second;
    }
  }
  return result;
}

YAML::Node NavigatePath(const YAML::Node &root, const std::vector<std::string> &path) {
  YAML::Node node = root;
  for (const auto &key : path) {
    if (!node || !node.IsMap() || !node[key]) {
      return YAML::Node();
    }
    node = node[key];
  }
  return node;
}

/// yaml-cpp's built-in scalar->int conversion doesn't reliably handle
/// 0x-prefixed hex literals (used for I2C addresses in hardware_config.yaml),
/// so integers are always parsed through the scalar string with base 0
/// (auto-detects 0x/0/decimal) instead of trusting Node::as<int>() directly.
int ParseIntFlexible(const YAML::Node &node, int fallback) {
  if (!node || node.IsNull() || !node.IsScalar()) {
    return fallback;
  }
  try {
    return static_cast<int>(std::stol(node.as<std::string>(), nullptr, 0));
  } catch (...) {
    return fallback;
  }
}

}  // namespace

struct HardwareConfig::Impl {
  YAML::Node raw;
};

HardwareConfig HardwareConfig::Load(const std::optional<std::string> &path) {
  const std::string config_path =
      path.value_or(GetEnv("HARDWARE_CONFIG").value_or(DefaultConfigPath()));

  YAML::Node doc;
  try {
    doc = YAML::LoadFile(config_path);
  } catch (const YAML::BadFile &) {
    throw std::runtime_error(
        "hardware_config.yaml을 찾을 수 없습니다: " + config_path +
        " (HARDWARE_CONFIG 환경변수로 경로를 지정할 수 있습니다)");
  } catch (const YAML::ParserException &e) {
    throw std::runtime_error("hardware_config.yaml 파싱 실패 (" + config_path +
                              "): " + e.what());
  }

  const YAML::Node boards = doc["boards"];
  const std::string board = GetEnv("HARDWARE_BOARD").value_or(
      doc["active_board"] ? doc["active_board"].as<std::string>() : std::string());
  if (board.empty()) {
    throw std::runtime_error(config_path + ": active_board가 비어 있습니다.");
  }
  if (!boards || !boards[board]) {
    std::string available;
    if (boards) {
      for (const auto &kv : boards) {
        available += kv.first.as<std::string>() + " ";
      }
    }
    throw std::runtime_error(config_path + ": board '" + board +
                              "'가 boards: 아래 없습니다. 사용 가능: " + available);
  }

  const YAML::Node common = doc["common"];
  const YAML::Node merged =
      common ? DeepMerge(common, boards[board]) : YAML::Clone(boards[board]);

  HardwareConfig cfg;
  cfg.board_ = board;
  cfg.label_ = boards[board]["label"] ? boards[board]["label"].as<std::string>() : board;
  cfg.config_path_ = config_path;
  cfg.impl_ = std::make_shared<Impl>();
  cfg.impl_->raw = merged;
  return cfg;
}

int HardwareConfig::i2c_bus() const {
  if (auto env = GetEnv("HARDWARE_I2C_BUS")) {
    return std::stoi(*env);
  }
  return GetInt({"i2c", "bus"}, 1);
}

int HardwareConfig::vl53l0x_address() const { return GetInt({"i2c", "vl53l0x_address"}, 0x29); }

int HardwareConfig::pn532_address() const { return GetInt({"i2c", "pn532_address"}, 0x24); }

std::string HardwareConfig::gpio_chip() const {
  if (auto env = GetEnv("HARDWARE_GPIO_CHIP")) {
    return *env;
  }
  return GetString({"gpio", "chip"}, "gpiochip0");
}

GpioLineConfig HardwareConfig::gpio_line(const std::string &name) const {
  GpioLineConfig line;
  line.name = name;

  const YAML::Node node = NavigatePath(impl_->raw, {"gpio", "lines", name});
  if (!node || !node.IsMap()) {
    return line;  // Defaults to disabled — matches python's graceful fallback.
  }

  line.enabled = node["enabled"] ? node["enabled"].as<bool>() : false;
  line.offset = ParseIntFlexible(node["offset"], -1);
  line.direction = node["direction"] ? node["direction"].as<std::string>() : "out";
  line.active_low = node["active_low"] ? node["active_low"].as<bool>() : false;
  if (node["edge"] && !node["edge"].IsNull()) {
    line.edge = node["edge"].as<std::string>();
  }
  return line;
}

std::string HardwareConfig::alsa_card() const {
  if (auto env = GetEnv("VOICE_ALSA_CARD")) {
    return *env;
  }
  return GetString({"audio", "alsa_card"}, "0");
}

std::map<std::string, std::string> HardwareConfig::display_env() const {
  std::map<std::string, std::string> out;
  const YAML::Node node = NavigatePath(impl_->raw, {"display", "env"});
  if (node && node.IsMap()) {
    for (const auto &kv : node) {
      out[kv.first.as<std::string>()] = kv.second.as<std::string>();
    }
  }
  return out;
}

double HardwareConfig::touch_min_mm() const { return GetDouble({"distance", "touch_min_mm"}, 28.0); }
double HardwareConfig::touch_max_mm() const { return GetDouble({"distance", "touch_max_mm"}, 40.0); }
double HardwareConfig::approach_threshold_mm() const {
  return GetDouble({"distance", "approach_threshold_mm"}, 300.0);
}
double HardwareConfig::distance_poll_sec() const { return GetDouble({"distance", "poll_sec"}, 0.05); }

double HardwareConfig::nfc_poll_sec() const { return GetDouble({"nfc", "poll_sec"}, 0.15); }
double HardwareConfig::nfc_debounce_sec() const { return GetDouble({"nfc", "debounce_sec"}, 1.5); }

double HardwareConfig::voice_rms_threshold() const {
  return GetDouble({"voice", "rms_threshold"}, 1200.0);
}
double HardwareConfig::voice_hold_ms() const { return GetDouble({"voice", "hold_ms"}, 350.0); }
double HardwareConfig::voice_cooldown_sec() const { return GetDouble({"voice", "cooldown_sec"}, 3.0); }

std::string HardwareConfig::assets_root() const { return GetString({"assets", "root"}, "assets"); }
std::string HardwareConfig::assets_sync_endpoint() const {
  return GetString({"assets", "sync_endpoint"}, "/v1/device/sync");
}

std::string HardwareConfig::udp_bind_host() const {
  if (auto env = GetEnv("UDP_BIND_HOST")) {
    return *env;
  }
  return GetString({"network", "udp_bind_host"}, "0.0.0.0");
}

int HardwareConfig::udp_port() const {
  if (auto env = GetEnv("UDP_PORT")) {
    return std::stoi(*env);
  }
  return GetInt({"network", "udp_port"}, 5005);
}

std::string HardwareConfig::backend_base_url() const {
  if (auto env = GetEnv("ETERNALBEAM_BACKEND_URL")) {
    return *env;
  }
  return GetString({"network", "backend_base_url"}, "http://127.0.0.1:8000");
}

std::string HardwareConfig::GetString(const std::vector<std::string> &path,
                                       const std::string &fallback) const {
  const YAML::Node node = NavigatePath(impl_->raw, path);
  if (!node || node.IsNull() || !node.IsScalar()) {
    return fallback;
  }
  try {
    return node.as<std::string>();
  } catch (...) {
    return fallback;
  }
}

double HardwareConfig::GetDouble(const std::vector<std::string> &path, double fallback) const {
  const YAML::Node node = NavigatePath(impl_->raw, path);
  if (!node || node.IsNull() || !node.IsScalar()) {
    return fallback;
  }
  try {
    return std::stod(node.as<std::string>());
  } catch (...) {
    return fallback;
  }
}

int HardwareConfig::GetInt(const std::vector<std::string> &path, int fallback) const {
  return ParseIntFlexible(NavigatePath(impl_->raw, path), fallback);
}

bool HardwareConfig::GetBool(const std::vector<std::string> &path, bool fallback) const {
  const YAML::Node node = NavigatePath(impl_->raw, path);
  if (!node || node.IsNull() || !node.IsScalar()) {
    return fallback;
  }
  try {
    return node.as<bool>();
  } catch (...) {
    return fallback;
  }
}

}  // namespace eb::hardware
