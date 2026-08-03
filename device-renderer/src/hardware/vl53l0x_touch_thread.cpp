#include "hardware/vl53l0x_touch_thread.h"

// Linux-only (see src/hardware/CMakeLists.txt — only added to the build
// under ETERNALBEAM_HAS_LINUX_HARDWARE) — same raw I2C_RDWR ioctl pattern
// linux_common_hardware.cpp uses, kept self-contained here on purpose so
// this class can be dropped into a project (or swapped out) without pulling
// in the rest of LinuxCommonHardware.

#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>

#include "hardware/hardware_config.h"

namespace eb::hardware {

namespace {

// VL53L0X register map (ST UM2039 / API), the handful of addresses needed
// for a single-shot ranging read without the full calibration sequence.
constexpr std::uint8_t kRegSysRangeStart = 0x00;
constexpr std::uint8_t kRegResultRangeStatus = 0x14;
constexpr std::uint8_t kRegResultRangeMm = kRegResultRangeStatus + 10;  // 0x1E

// Factory-default (untuned) timing budget is ~30ms per single-shot
// ranging cycle — see class doc comment for why this thread sleeps a fixed
// duration here instead of polling the "measurement ready" interrupt bit.
constexpr int kRangingConversionDelayMs = 30;

bool WriteRegister8(int fd, std::uint8_t addr7, std::uint8_t reg, std::uint8_t value) {
  if (fd < 0) {
    return false;
  }
  std::uint8_t payload[2] = {reg, value};
  i2c_msg msg{};
  msg.addr = addr7;
  msg.flags = 0;
  msg.len = sizeof(payload);
  msg.buf = payload;

  i2c_rdwr_ioctl_data ioctl_data{};
  ioctl_data.msgs = &msg;
  ioctl_data.nmsgs = 1;
  return ::ioctl(fd, I2C_RDWR, &ioctl_data) >= 0;
}

bool ReadRegisterBytes(int fd, std::uint8_t addr7, std::uint8_t reg, std::uint8_t *out, std::size_t len) {
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
  msgs[1].len = static_cast<std::uint16_t>(len);
  msgs[1].buf = out;

  i2c_rdwr_ioctl_data ioctl_data{};
  ioctl_data.msgs = msgs;
  ioctl_data.nmsgs = 2;
  return ::ioctl(fd, I2C_RDWR, &ioctl_data) >= 0;
}

}  // namespace

Vl53l0xTouchThreadConfig Vl53l0xTouchThreadConfig::FromHardwareConfig(const HardwareConfig &config) {
  Vl53l0xTouchThreadConfig out;
  out.i2c_device_path = "/dev/i2c-" + std::to_string(config.i2c_bus());
  out.sensor_address = static_cast<std::uint8_t>(config.vl53l0x_address());
  out.touch_min_mm = static_cast<int>(config.touch_min_mm());
  out.touch_max_mm = static_cast<int>(config.touch_max_mm());
  // distance_poll_sec() is tuned for LinuxCommonHardware::Poll()'s
  // once-per-render-tick cadence; floor it at 20ms so a very small/zero
  // config value can't spin this thread's I2C bus at an unreasonable rate.
  const int configured_ms = static_cast<int>(config.distance_poll_sec() * 1000.0);
  out.poll_interval_ms = configured_ms > 20 ? configured_ms : 20;
  return out;
}

Vl53l0xTouchThread::Vl53l0xTouchThread(eb::renderer::IPetRenderer &renderer, Vl53l0xTouchThreadConfig config)
    : renderer_(renderer), config_(std::move(config)) {}

Vl53l0xTouchThread::~Vl53l0xTouchThread() { Stop(); }

bool Vl53l0xTouchThread::Start() {
  if (running_.load(std::memory_order_relaxed)) {
    return true;  // Idempotent.
  }
  if (config_.i2c_device_path.empty()) {
    std::fprintf(stderr, "[hardware:vl53l0x_touch] i2c_device_path 미설정 — 시작하지 않습니다\n");
    return false;
  }

  i2c_fd_ = ::open(config_.i2c_device_path.c_str(), O_RDWR);
  if (i2c_fd_ < 0) {
    std::fprintf(stderr, "[hardware:vl53l0x_touch] %s 오픈 실패: %s\n", config_.i2c_device_path.c_str(),
                 std::strerror(errno));
    return false;
  }

  running_.store(true, std::memory_order_relaxed);
  thread_ = std::thread(&Vl53l0xTouchThread::Run, this);
  std::fprintf(stderr,
               "[hardware:vl53l0x_touch] 시작 — %s addr=0x%02x range=[%d,%d]mm poll=%dms debounce=%dms\n",
               config_.i2c_device_path.c_str(), config_.sensor_address, config_.touch_min_mm,
               config_.touch_max_mm, config_.poll_interval_ms, config_.debounce_ms);
  return true;
}

void Vl53l0xTouchThread::Stop() {
  running_.store(false, std::memory_order_relaxed);
  if (thread_.joinable()) {
    thread_.join();
  }
  if (i2c_fd_ >= 0) {
    ::close(i2c_fd_);
    i2c_fd_ = -1;
  }
}

std::optional<int> Vl53l0xTouchThread::LastDistanceMm() const {
  std::lock_guard<std::mutex> lock(last_distance_mutex_);
  return last_distance_mm_;
}

std::optional<int> Vl53l0xTouchThread::ReadDistanceMm() {
  if (i2c_fd_ < 0) {
    return std::nullopt;
  }
  if (!WriteRegister8(i2c_fd_, config_.sensor_address, kRegSysRangeStart, 0x01)) {
    return std::nullopt;  // 센서 무응답 (연결 안 됨/전원 문제 등) — 조용히 스킵하고 다음 폴에 재시도.
  }

  std::this_thread::sleep_for(std::chrono::milliseconds(kRangingConversionDelayMs));

  std::uint8_t raw[2] = {0, 0};
  if (!ReadRegisterBytes(i2c_fd_, config_.sensor_address, kRegResultRangeMm, raw, sizeof(raw))) {
    return std::nullopt;
  }
  return (static_cast<int>(raw[0]) << 8) | static_cast<int>(raw[1]);
}

void Vl53l0xTouchThread::Run() {
  // Started far enough in the past that the very first in-range reading is
  // never suppressed by the debounce window below.
  auto last_touch_time = std::chrono::steady_clock::now() - std::chrono::hours(1);

  while (running_.load(std::memory_order_relaxed)) {
    const std::optional<int> distance_mm = ReadDistanceMm();
    if (distance_mm) {
      {
        std::lock_guard<std::mutex> lock(last_distance_mutex_);
        last_distance_mm_ = distance_mm;
      }

      const bool in_touch_band = *distance_mm >= config_.touch_min_mm && *distance_mm <= config_.touch_max_mm;
      const auto now = std::chrono::steady_clock::now();
      const auto ms_since_last_touch =
          std::chrono::duration_cast<std::chrono::milliseconds>(now - last_touch_time).count();

      if (in_touch_band && ms_since_last_touch >= config_.debounce_ms) {
        last_touch_time = now;
        std::lock_guard<std::mutex> lock(renderer_mutex_);
        renderer_.playAction("touch", config_.loop_action);
      }
    }

    // ReadDistanceMm() above already spent kRangingConversionDelayMs
    // sleeping for the ranging conversion — only sleep the remainder of the
    // configured poll interval so the *total* period per cycle matches
    // poll_interval_ms, not poll_interval_ms + conversion delay.
    const int remaining_ms = config_.poll_interval_ms - kRangingConversionDelayMs;
    if (remaining_ms > 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(remaining_ms));
    }
  }
}

}  // namespace eb::hardware
