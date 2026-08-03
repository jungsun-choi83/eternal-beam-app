#pragma once

#include <chrono>
#include <functional>
#include <string>

namespace eb::hardware {

/// Mirrors the four fixed actions used throughout the backend
/// (backend/scenarios/pet_scenarios.py: IDLE/TOUCH/VOICE/NFC) and the
/// existing Python UDP bridge (python/eternal_beam_pi.py). Keeping the same
/// four names end-to-end means the animation-selection logic barely changes
/// when the trigger source moves from UDP packets to in-process polling.
enum class ActionEvent {
  Idle,
  Touch,
  Voice,
  Nfc,
};

/// Converts an ActionEvent to the lowercase animation name Spine assets are
/// expected to expose (see assets/README.md and pet_scenarios.py ACTIONS).
const char *ToAnimationName(ActionEvent action);

struct SensorEvent {
  ActionEvent action;
  /// Extra context for the event — e.g. the NFC UID / slot id for
  /// ActionEvent::Nfc, empty for the others.
  std::string payload;
  std::chrono::steady_clock::time_point timestamp{std::chrono::steady_clock::now()};
};

using SensorEventCallback = std::function<void(const SensorEvent &)>;

}  // namespace eb::hardware
