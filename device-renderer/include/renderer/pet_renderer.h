#pragma once

#include <cstdint>
#include <cstring>
#include <string>

namespace eb::renderer {

/// Raw RGBA8 pixel buffer every IPetRenderer implementation draws into.
/// Decouples "how one layer is drawn" (Spine skeletal draw calls, video
/// frame decode, ...) from "how layers get composited and pushed to the
/// screen" — the direct successor of Unity's per-layer RenderTexture, minus
/// the GPU/Unity dependency. Allocated and sized by the caller (see
/// AppController); implementations must draw into it, never resize it.
struct FrameBuffer {
  std::uint8_t *pixels = nullptr;  // RGBA8, top-left origin, row-major, premultiplied alpha
  int width = 0;
  int height = 0;
  int stride_bytes = 0;  // 0 => tightly packed (width * 4)

  int effectiveStride() const { return stride_bytes > 0 ? stride_bytes : width * 4; }

  /// Fills every pixel with transparent black — implementations should call
  /// this at the top of render() before drawing so stale pixels from a
  /// previously-active layer never bleed through after a backend swap.
  void clear() const {
    if (pixels == nullptr || width <= 0 || height <= 0) {
      return;
    }
    const int stride = effectiveStride();
    for (int row = 0; row < height; ++row) {
      std::memset(pixels + static_cast<std::size_t>(row) * stride, 0,
                  static_cast<std::size_t>(width) * 4);
    }
  }
};

/// Rendering boundary every "pet layer" implements. This is the direct
/// successor of Unity's VideoLayer component + PetShader material: the app
/// (AppController, and the UDP sensor pipeline that feeds it — see
/// hardware/hardware_interface.h) only ever calls through this interface,
/// so SpineRenderer (new, skeletal) and VideoLayerRenderer (ported,
/// per-action MP4 clips) are drop-in replacements for each other:
///
///   IPetRenderer* currentRenderer = useSpine ? (IPetRenderer*)new SpineRenderer()
///                                             : (IPetRenderer*)new VideoLayerRenderer();
///   currentRenderer->render(frame);  // main loop doesn't know or care which one it is
///
/// (renderer_factory.h's CreateRenderer() is the real, RAII-safe equivalent
/// of the `new` calls above — see its doc comment.)
class IPetRenderer {
 public:
  virtual ~IPetRenderer() = default;

  /// Loads whatever this backend needs from `path` (always a directory —
  /// see AssetManager::EnsureLocalAssets):
  ///   - SpineRenderer expects <path>/skeleton.json + <path>/skeleton.atlas
  ///   - VideoLayerRenderer expects <path>/video_manifest.json (action_id -> URL)
  /// Returns false if nothing usable was found for this backend at `path`.
  virtual bool loadAsset(const std::string &path) = 0;

  /// Starts playing the named action ("idle", "touch", "voice", "nfc" — see
  /// hardware/sensor_event.h::ToAnimationName). SpineRenderer maps this to a
  /// Spine animation name; VideoLayerRenderer maps it to a per-action clip URL.
  /// `loop`: true keeps replaying indefinitely (idle always does); false
  /// plays once and holds the last frame until the next playAction() call.
  virtual void playAction(const std::string &action_name, bool loop) = 0;

  /// Advances animation/video decode based on wall-clock time elapsed since
  /// the previous call, and draws the current frame into `frame_buffer`
  /// (must already be allocated/sized by the caller). Called once per tick
  /// from AppController::Tick(), matching Unity's per-frame Update/render.
  virtual void render(FrameBuffer &frame_buffer) = 0;

  /// Z-depth for multi-layer compositing (background / subject / foreground
  /// glow), mirroring HologramController's per-layer Z ordering. Lower
  /// values are drawn first (further back) by the compositor. Implementations
  /// only need to remember the value; ordering is the compositor's job.
  virtual void setDepth(float z) = 0;

  virtual float depth() const = 0;
};

}  // namespace eb::renderer
