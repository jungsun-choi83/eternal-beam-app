#include "hardware/sensor_event.h"

namespace eb::hardware {

const char *ToAnimationName(ActionEvent action) {
  switch (action) {
    case ActionEvent::Idle:
      return "idle";
    case ActionEvent::Touch:
      return "touch";
    case ActionEvent::Voice:
      return "voice";
    case ActionEvent::Nfc:
      return "nfc";
  }
  return "idle";
}

}  // namespace eb::hardware
