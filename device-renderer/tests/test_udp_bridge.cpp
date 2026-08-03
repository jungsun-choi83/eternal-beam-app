#include <catch2/catch_test_macros.hpp>

#include "hardware/sensor_event.h"
#include "udp_bridge_impl.h"

using eb::hardware::ActionEvent;
using eb::hardware::ParseUdpSensorEvent;

// These fixtures are copied verbatim from the *unchanged* Python senders
// (python/pi_sensors_to_unity_udp.py, voice_to_unity.py, eternal_beam_pi.py,
// s23_bridge_simple.py) — if one of these stops parsing, the C++ side has
// drifted from what production Python actually sends, not the other way
// around.
TEST_CASE("ParseUdpSensorEvent maps touch/voice/nfc_tagged/idle", "[udp_bridge]") {
  const auto touch = ParseUdpSensorEvent(R"({"event":"touch","distance_mm":32})");
  REQUIRE(touch.has_value());
  CHECK(touch->action == ActionEvent::Touch);
  CHECK(touch->payload == "32");

  const auto voice = ParseUdpSensorEvent(R"({"event":"voice","source":"inmp441","rms":123})");
  REQUIRE(voice.has_value());
  CHECK(voice->action == ActionEvent::Voice);
  CHECK(voice->payload == "123");

  const auto nfc = ParseUdpSensorEvent(R"({"event":"nfc_tagged","uid":"A1B2C3D4"})");
  REQUIRE(nfc.has_value());
  CHECK(nfc->action == ActionEvent::Nfc);
  CHECK(nfc->payload == "A1B2C3D4");

  const auto nfc_theme_only = ParseUdpSensorEvent(R"({"event":"nfc_tagged","theme_id":"forest","uid":"AA"})");
  REQUIRE(nfc_theme_only.has_value());
  CHECK(nfc_theme_only->payload == "AA");  // uid preferred over theme_id when both present

  const auto idle = ParseUdpSensorEvent(R"({"event":"idle","source":"pi_reset"})");
  REQUIRE(idle.has_value());
  CHECK(idle->action == ActionEvent::Idle);
}

TEST_CASE("ParseUdpSensorEvent ignores proximity/informational events by design", "[udp_bridge]") {
  CHECK_FALSE(ParseUdpSensorEvent(R"({"event":"approach","distance_mm":240})").has_value());
  CHECK_FALSE(ParseUdpSensorEvent(R"({"event":"nfc_match","source":"pi_reset"})").has_value());
  CHECK_FALSE(ParseUdpSensorEvent(R"({"event":"demo_forest","theme_id":"fresh_forest"})").has_value());
}

TEST_CASE("ParseUdpSensorEvent is robust to malformed input", "[udp_bridge]") {
  CHECK_FALSE(ParseUdpSensorEvent("not json").has_value());
  CHECK_FALSE(ParseUdpSensorEvent("{}").has_value());
  CHECK_FALSE(ParseUdpSensorEvent("").has_value());
}
