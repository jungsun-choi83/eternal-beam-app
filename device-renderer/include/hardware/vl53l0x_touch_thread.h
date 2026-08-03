#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

#include "renderer/pet_renderer.h"

namespace eb::hardware {

class HardwareConfig;

struct Vl53l0xTouchThreadConfig {
  /// e.g. "/dev/i2c-1" — see HardwareConfig::i2c_bus(). Left empty by
  /// default so a missing-config mistake fails loudly in Start() instead of
  /// silently opening the wrong bus.
  std::string i2c_device_path;

  /// 7-bit I2C address. 0x29 is the VL53L0X factory-default (see
  /// HardwareConfig::vl53l0x_address(), which is where FromHardwareConfig()
  /// below pulls it from).
  std::uint8_t sensor_address = 0x29;

  /// Readings within [touch_min_mm, touch_max_mm] count as a touch — mirrors
  /// HardwareConfig::touch_min_mm()/touch_max_mm() (the same band
  /// LinuxCommonHardware::Poll() would use for the same sensor).
  int touch_min_mm = 0;
  int touch_max_mm = 60;

  /// How often the loop re-triggers ranging. This thread is intentionally
  /// decoupled from AppController's 30 FPS render tick — a proximity sensor
  /// can be, and usually should be, sampled at its own cadence.
  int poll_interval_ms = 50;

  /// Minimum time between two playAction("touch") calls, so a hand held
  /// steady in range for a second doesn't retrigger the animation 20x.
  int debounce_ms = 400;

  /// Forwarded as playAction()'s `loop` argument — false (the default)
  /// matches AppController's convention of touch/voice/nfc being one-shot
  /// reactions that AppController would otherwise fall back to "idle" from
  /// after idle_return_after_sec. This thread does *not* do that fallback
  /// itself (see class doc comment) — pass true here if you want the touch
  /// animation to hold/loop indefinitely instead.
  bool loop_action = false;

  /// Convenience: fills every field above from the same hardware_config.yaml
  /// values LinuxCommonHardware itself would use for this sensor, so this
  /// thread and the "normal" HardwareInterface path can never disagree on
  /// what counts as a touch even if only one of them is actually wired up.
  static Vl53l0xTouchThreadConfig FromHardwareConfig(const HardwareConfig &config);
};

/// Dedicated background thread that polls a VL53L0X proximity sensor over
/// I2C on its own cadence and calls `IPetRenderer::playAction("touch", ...)`
/// directly the instant a reading falls inside the configured distance
/// band — the lowest-latency path from "hand near the display" to "the pet
/// reacts", bypassing HardwareInterface::Poll() / AppController::
/// OnSensorEvent() / TriggerAction() entirely.
///
/// Ranging sequence per poll (see .cpp): write SYSRANGE_START=0x01 to kick
/// off a single-shot ranging cycle, sleep for the sensor's default (factory
/// timing-budget) conversion time, then read the 2-byte range result. This
/// deliberately skips the SPAD-mapping/timing-budget/reference calibration
/// sequence real ST drivers perform at boot (same caveat already noted on
/// LinuxCommonHardware::ReadDistanceMm() — porting that is tracked
/// separately) — readings are usable but not to full datasheet accuracy.
///
/// Use *one* touch source per physical sensor: either this thread, or
/// LinuxCommonHardware's own (currently stubbed) VL53L0X polling inside
/// HardwareInterface::Poll() — not both, or the same hand near the sensor
/// could fire playAction("touch") twice through two independent paths.
///
/// Thread-safety: spine-cpp's AnimationState/Skeleton (and most other
/// IPetRenderer backends) are not internally thread-safe. This class takes
/// renderer_mutex() for the full duration of every playAction() call it
/// makes. If `renderer` is *also* driven from another thread (most
/// commonly: AppController::Tick()'s render()/playAction() calls on
/// main.cpp's main loop thread), wrap those calls in
/// `std::lock_guard<std::mutex> lock(touch_thread.renderer_mutex());` too —
/// see main.cpp for the reference wiring.
class Vl53l0xTouchThread {
 public:
  Vl53l0xTouchThread(eb::renderer::IPetRenderer &renderer, Vl53l0xTouchThreadConfig config);
  ~Vl53l0xTouchThread();

  Vl53l0xTouchThread(const Vl53l0xTouchThread &) = delete;
  Vl53l0xTouchThread &operator=(const Vl53l0xTouchThread &) = delete;

  /// Opens the configured I2C device and starts the polling thread. Returns
  /// false (without starting a thread) if the I2C device couldn't be
  /// opened — safe to still destruct/Stop() normally in that case. Calling
  /// Start() again while already running is a no-op that returns true.
  bool Start();

  /// Signals the loop to exit, joins the thread, and closes the I2C device.
  /// Safe to call more than once, or if Start() was never called / failed.
  void Stop();

  /// Guards every call this thread makes into `renderer_` — see class doc
  /// comment for why anything else driving the same renderer needs to share
  /// this lock too.
  std::mutex &renderer_mutex() { return renderer_mutex_; }

  /// Most recent successful ranging result, in millimeters — for
  /// diagnostics/tests. std::nullopt before the first successful reading
  /// (or if the sensor has never answered).
  std::optional<int> LastDistanceMm() const;

 private:
  void Run();
  std::optional<int> ReadDistanceMm();

  eb::renderer::IPetRenderer &renderer_;
  Vl53l0xTouchThreadConfig config_;

  int i2c_fd_ = -1;
  std::thread thread_;
  std::atomic<bool> running_{false};
  std::mutex renderer_mutex_;

  mutable std::mutex last_distance_mutex_;
  std::optional<int> last_distance_mm_;
};

}  // namespace eb::hardware
