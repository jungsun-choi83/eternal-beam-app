#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "app/asset_manager.h"
#include "hardware/hardware_interface.h"
#include "hardware/sensor_event.h"
#include "renderer/renderer_factory.h"

namespace eb::app {

struct AppConfig {
  std::string pet_id;
  std::string place_id;
  /// Fall back to the looping "idle" animation this many seconds after the
  /// last transient (touch/voice/nfc) event, mirroring how HologramController
  /// implicitly had no timeout at all today (Unity just kept looping
  /// whichever clip PythonBridge last selected) — this is a deliberate
  /// improvement, not a straight port.
  double idle_return_after_sec = 6.0;

  /// Shared FrameBuffer dimensions handed to whichever IPetRenderer is
  /// active — replaces the old IRenderer::Initialize(width, height); the
  /// renderer never allocates or resizes this buffer itself.
  int render_width = 720;
  int render_height = 1280;

  /// Bypasses the asset_type/on-disk auto-selection in Start() (see
  /// renderer::CreateRendererForAssetDir) when set to anything other than
  /// kAuto — wired from ETERNALBEAM_RENDERER_BACKEND in main.cpp, mainly
  /// for local dev/testing (e.g. force kStub on a machine with neither
  /// Spine nor FFmpeg vendored in).
  eb::renderer::RendererBackend forced_renderer_backend = eb::renderer::RendererBackend::kAuto;
};

/// Wires hardware sensor events to renderer action selection — the C++
/// equivalent of python/eternal_beam_pi.py's event routing combined with
/// Unity's PythonBridge.OnPetVideoUrlReceived(). Touch/Voice/Nfc are
/// transient reactions; Idle is the resting loop everything falls back to.
///
/// Deliberately unaware of *which* HardwareInterface (direct GPIO/I2C vs.
/// UdpBridgeHardware) it's wired to. It used to take a fixed IPetRenderer&
/// chosen once at process startup; it now *owns* the renderer instead and
/// picks it in Start() via renderer::CreateRendererForAssetDir(), once the
/// synced asset directory (and the server's declared asset_type) are
/// actually known — see that function's doc comment for the full
/// Spine-vs-Video decision/exception order.
class AppController {
 public:
  AppController(eb::hardware::HardwareInterface &hardware, AssetManager &assets, AppConfig config);

  /// Resolves the pet/place asset directory via AssetManager, picks the
  /// matching IPetRenderer (see class doc comment), loads it, and starts
  /// the idle action. Returns false if no assets and no matching renderer
  /// could be resolved at all (not even the logging StubRenderer skips
  /// this — CreateRendererForAssetDir only returns Stub when *something*
  /// is wrong, e.g. no assets whatsoever for this pet/place).
  bool Start();

  /// Call once per frame with the elapsed time since the previous call.
  void Tick(float delta_seconds);

  /// The frame most recently drawn by IPetRenderer::render() — main.cpp hands this to the
  /// display/compositor path (DRM/KMS framebuffer, GL texture upload, PPM
  /// dump for headless testing, ...), which is intentionally left as an
  /// extension point outside this class (see README.md).
  const eb::renderer::FrameBuffer &LastFrame() const { return frame_buffer_; }

  /// The concrete IPetRenderer Start() resolved (SpineRenderer/
  /// VideoLayerRenderer/StubRenderer — see CreateRendererForAssetDir()).
  /// nullptr until Start() has run. Exists so main.cpp can wire optional,
  /// out-of-band renderer drivers (e.g. Vl53l0xTouchThread) into the exact
  /// same instance AppController itself drives from Tick() — see that
  /// class's thread-safety note about sharing renderer_mutex() once you do.
  eb::renderer::IPetRenderer *Renderer() const { return renderer_.get(); }

 private:
  void OnSensorEvent(const eb::hardware::SensorEvent &event);
  void TriggerAction(eb::hardware::ActionEvent action, bool loop);

  eb::hardware::HardwareInterface &hardware_;
  AssetManager &assets_;
  AppConfig config_;

  std::unique_ptr<eb::renderer::IPetRenderer> renderer_;

  std::vector<std::uint8_t> frame_pixels_;
  eb::renderer::FrameBuffer frame_buffer_;

  eb::hardware::ActionEvent current_action_ = eb::hardware::ActionEvent::Idle;
  double seconds_since_last_event_ = 0.0;
};

}  // namespace eb::app
