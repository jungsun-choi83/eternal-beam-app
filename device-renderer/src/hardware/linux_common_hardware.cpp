#include "linux_common_hardware.h"

#include <fcntl.h>
#include <gpiod.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>

namespace eb::hardware {

namespace {

/// Combined write(reg)+read(len) transaction via I2C_RDWR — the same
/// pattern python/hardware/i2c_bus.py::LinuxI2CBus builds on top of smbus2's
/// i2c_msg, just issued directly against the kernel ioctl here.
bool I2cReadRegister(int fd, uint8_t addr7, uint8_t reg, uint8_t *out, size_t len) {
  if (fd < 0) {
    return false;
  }
  i2c_msg msgs[2];
  msgs[0].addr = addr7;
  msgs[0].flags = 0;
  msgs[0].len = 1;
  msgs[0].buf = &reg;

  msgs[1].addr = addr7;
  msgs[1].flags = I2C_M_RD;
  msgs[1].len = static_cast<uint16_t>(len);
  msgs[1].buf = out;

  i2c_rdwr_ioctl_data payload;
  payload.msgs = msgs;
  payload.nmsgs = 2;
  return ::ioctl(fd, I2C_RDWR, &payload) >= 0;
}

double NowSeconds() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

}  // namespace

LinuxCommonHardware::LinuxCommonHardware(HardwareConfig config, std::string board_name)
    : config_(std::move(config)), board_name_(std::move(board_name)) {}

LinuxCommonHardware::~LinuxCommonHardware() { Shutdown(); }

bool LinuxCommonHardware::Initialize() {
  const bool i2c_ok = OpenI2c();
  if (!i2c_ok) {
    std::fprintf(stderr, "[hardware:%s] I2C 버스 열기 실패 (bus=%d) — 센서 없이 계속 진행합니다\n",
                 board_name_.c_str(), config_.i2c_bus());
  } else {
    const bool vl53_present = ProbeI2cAddress(static_cast<uint8_t>(config_.vl53l0x_address()));
    const bool pn532_present = ProbeI2cAddress(static_cast<uint8_t>(config_.pn532_address()));
    std::fprintf(stderr, "[hardware:%s] I2C bus %d 오픈 완료 — VL53L0X(0x%02x): %s, PN532(0x%02x): %s\n",
                 board_name_.c_str(), config_.i2c_bus(), config_.vl53l0x_address(),
                 vl53_present ? "응답함" : "무응답", config_.pn532_address(),
                 pn532_present ? "응답함" : "무응답");
  }

  // status_led is opt-in (enabled: false by default in hardware_config.yaml)
  // — failing to open it is expected on most boards and never fatal.
  OpenStatusLed();

  return OnInitialize();
}

void LinuxCommonHardware::Shutdown() {
  OnShutdown();
  CloseStatusLed();
  CloseI2c();
}

void LinuxCommonHardware::Poll() {
  const double now = NowSeconds();

  if (auto distance_mm = ReadDistanceMm()) {
    const bool in_touch_band =
        *distance_mm >= config_.touch_min_mm() && *distance_mm <= config_.touch_max_mm();
    // Debounce at 4x the configured poll interval so a single noisy sample
    // can't retrigger the animation every frame.
    if (in_touch_band && now - last_touch_emit_sec_ > config_.distance_poll_sec() * 4.0) {
      last_touch_emit_sec_ = now;
      Emit(SensorEvent{ActionEvent::Touch, ""});
    }
  }

  if (auto uid = ReadNfcUid()) {
    if (now - last_nfc_emit_sec_ > config_.nfc_debounce_sec()) {
      last_nfc_emit_sec_ = now;
      Emit(SensorEvent{ActionEvent::Nfc, *uid});
    }
  }

  // Voice (mic RMS) detection needs a dedicated ALSA capture thread — see
  // python/voice_to_unity.py for the reference implementation this should
  // be ported from. Not wired into this synchronous Poll() yet.
}

void LinuxCommonHardware::SetStatusLed(bool on) {
  if (!status_led_line_) {
    return;
  }
  const GpioLineConfig line_cfg = config_.gpio_line("status_led");
  bool physical_high = on;
  if (line_cfg.active_low) {
    physical_high = !on;
  }
  gpiod_line_set_value(static_cast<gpiod_line *>(status_led_line_), physical_high ? 1 : 0);
}

bool LinuxCommonHardware::OpenI2c() {
  const std::string path = "/dev/i2c-" + std::to_string(config_.i2c_bus());
  i2c_fd_ = ::open(path.c_str(), O_RDWR);
  return i2c_fd_ >= 0;
}

void LinuxCommonHardware::CloseI2c() {
  if (i2c_fd_ >= 0) {
    ::close(i2c_fd_);
    i2c_fd_ = -1;
  }
}

bool LinuxCommonHardware::OpenStatusLed() {
  const GpioLineConfig line_cfg = config_.gpio_line("status_led");
  if (!line_cfg.enabled || line_cfg.offset < 0) {
    return false;
  }

  gpiod_chip *chip = gpiod_chip_open_by_name(config_.gpio_chip().c_str());
  if (chip == nullptr) {
    std::fprintf(stderr, "[hardware:%s] GPIO 칩 열기 실패: %s\n", board_name_.c_str(),
                 config_.gpio_chip().c_str());
    return false;
  }

  gpiod_line *line = gpiod_chip_get_line(chip, static_cast<unsigned int>(line_cfg.offset));
  if (line == nullptr) {
    gpiod_chip_close(chip);
    return false;
  }

  const int default_value = line_cfg.active_low ? 1 : 0;
  if (gpiod_line_request_output(line, "eternal-beam-device", default_value) != 0) {
    gpiod_chip_close(chip);
    return false;
  }

  gpio_chip_ = chip;
  status_led_line_ = line;
  return true;
}

void LinuxCommonHardware::CloseStatusLed() {
  if (status_led_line_ != nullptr) {
    gpiod_line_release(static_cast<gpiod_line *>(status_led_line_));
    status_led_line_ = nullptr;
  }
  if (gpio_chip_ != nullptr) {
    gpiod_chip_close(static_cast<gpiod_chip *>(gpio_chip_));
    gpio_chip_ = nullptr;
  }
}

bool LinuxCommonHardware::ProbeI2cAddress(uint8_t addr7) const {
  uint8_t scratch = 0;
  return I2cReadRegister(i2c_fd_, addr7, /*reg=*/0x00, &scratch, 1);
}

std::optional<int> LinuxCommonHardware::ReadDistanceMm() {
  if (i2c_fd_ < 0) {
    return std::nullopt;
  }
  // TODO(sensor-bringup): port the VL53L0X calibration/ranging sequence from
  // adafruit-circuitpython-vl53l0x (SPAD mapping, timing budget, ref
  // calibration) before relying on this in production. Until then no touch
  // events are emitted from real hardware.
  return std::nullopt;
}

std::optional<std::string> LinuxCommonHardware::ReadNfcUid() {
  if (i2c_fd_ < 0) {
    return std::nullopt;
  }
  // TODO(sensor-bringup): port the PN532 SAM-configuration + passive-target
  // polling handshake from adafruit-circuitpython-pn532 before relying on
  // this in production. Until then no NFC events are emitted from real
  // hardware.
  return std::nullopt;
}

}  // namespace eb::hardware
