#include "mock_impl.h"

#include <cstdio>
#include <utility>

namespace eb::hardware {

MockHardware::MockHardware(HardwareConfig config) : config_(std::move(config)) {}

bool MockHardware::Initialize() {
  std::fprintf(stderr, "[hardware:mock] 실제 센서 없이 동작 중 — SimulateEvent()로 이벤트를 주입하세요\n");
  return true;
}

void MockHardware::Shutdown() {}

void MockHardware::Poll() {
  // Intentionally empty — see class comment.
}

void MockHardware::SetStatusLed(bool on) {
  if (on != status_led_on_) {
    status_led_on_ = on;
    std::fprintf(stderr, "[hardware:mock] status_led -> %s\n", on ? "ON" : "OFF");
  }
}

void MockHardware::SimulateEvent(ActionEvent action, std::string payload) {
  Emit(SensorEvent{action, std::move(payload)});
}

}  // namespace eb::hardware
