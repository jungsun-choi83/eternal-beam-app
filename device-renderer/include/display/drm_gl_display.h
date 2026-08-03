#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "renderer/pet_renderer.h"

namespace eb::display {

struct DrmGlDisplayConfig {
  /// KMS device node. RK3566's VOP (video output processor) is normally the
  /// only KMS device, so this is almost always "/dev/dri/card0" — but some
  /// vendor BSPs (or boards with a discrete GPU/second display controller)
  /// expose more than one node, in which case override this. Use
  /// `drm_info` (libdrm-tests) or plain `ls /dev/dri` on the target to check.
  std::string drm_device_path = "/dev/dri/card0";

  /// Which connector to drive. 0 (default) = auto-pick the first *connected*
  /// connector, preferring HDMI-A over DSI/eDP/DPI over anything else — see
  /// PickConnector() in the .cpp for the exact preference order. Set this to
  /// a specific `drmModeConnector::connector_id` (see `drmModeGetResources`)
  /// only if a board has more than one connected display and you need to
  /// pick a non-default one.
  std::uint32_t connector_id = 0;

  /// EGL/GLES debug aid: logs every DRM/GBM/EGL/GL call's return value at
  /// each Initialize() step. Leave off in production — RK3566's kernel log
  /// buffer fills up fast at 30-60 Hz if this were left on for Present().
  bool verbose_init_logging = true;
};

/// Owns the entire KMS → GBM → EGL/GLES pipeline needed to push
/// `IPetRenderer::render()`'s CPU-side `FrameBuffer` (RGBA8) onto a real
/// HDMI/DSI panel on RK3566 (or any other DRM/KMS + Mesa-GBM-capable Linux
/// board) — no X11, no Wayland, no desktop compositor. This is the
/// "디스플레이 출력" extension point tracked in device-renderer/README.md
/// and the device-renderer-architecture canvas.
///
/// Usage (see main.cpp):
/// ```cpp
/// eb::display::DrmGlDisplay display;
/// if (!display.Initialize()) { /* fall back to headless (PPM dump) */ }
/// // ... size AppConfig::render_width/height from display.width()/height() ...
/// while (running) {
///   controller.Tick(delta_seconds);
///   display.Present(controller.LastFrame());  // uploads + swaps + page-flips
/// }
/// display.Shutdown();
/// ```
///
/// Scope: this drives exactly one KMS plane with exactly one texture (the
/// pet layer's FrameBuffer) — it does not (yet) composite a separate
/// background video plane underneath it. Multi-plane/multi-layer
/// compositing (matching Unity's HologramController Z-ordering) is tracked
/// as follow-up work; see the "확장 지점" note in README.md.
///
/// Not compiled unless ETERNALBEAM_WITH_DRM_GL=ON (see CMakeLists.txt) —
/// requires libdrm, libgbm, libEGL and libGLESv2 on the target sysroot.
class DrmGlDisplay {
 public:
  DrmGlDisplay();
  ~DrmGlDisplay();

  DrmGlDisplay(const DrmGlDisplay &) = delete;
  DrmGlDisplay &operator=(const DrmGlDisplay &) = delete;

  /// Runs the full bring-up sequence documented in the .cpp file, step by
  /// step: (1) open the DRM node + pick connector/encoder/CRTC/mode,
  /// (2) create the GBM device + scanout-capable surface sized to that
  /// mode, (3) create the EGL display/context/window-surface over that GBM
  /// surface, (4) compile the passthrough-textured-quad GLES program and
  /// allocate the streaming texture, (5) modeset the CRTC onto a first
  /// (blank) buffer so the panel is actively scanning out before the first
  /// real Present(). Returns false (with a stderr diagnostic naming which
  /// step failed) if anything along the way doesn't succeed — callers
  /// should treat that as "no physical display available" and fall back to
  /// a headless path (e.g. ETERNALBEAM_DUMP_FRAME_PPM), not abort.
  bool Initialize(const DrmGlDisplayConfig &config = {});

  /// Releases every handle acquired by Initialize(), in reverse order.
  /// Safe to call even if Initialize() failed partway through, or was never
  /// called.
  void Shutdown();

  /// Uploads `frame.pixels` into the streaming GLES texture, draws the
  /// fullscreen quad, calls eglSwapBuffers(), locks the next GBM front
  /// buffer, and drmModePageFlip()s it onto the CRTC — blocking until the
  /// previous flip's vblank event confirms the swap, which is what paces
  /// this to the panel's real refresh rate (no manual sleep needed once
  /// this is wired into the main loop). Returns false on any step failure;
  /// the caller may keep calling Present() again on the next tick — most
  /// failure modes here (e.g. a transient page-flip EBUSY) are recoverable.
  ///
  /// `frame.width`/`frame.height` do not need to match width()/height() —
  /// mismatched frames are still uploaded and drawn (GL scales the quad to
  /// the full viewport), but for pixel-perfect output size AppConfig's
  /// render_width/render_height from width()/height() after Initialize().
  bool Present(const eb::renderer::FrameBuffer &frame);

  /// Modeset resolution in pixels, resolved during Initialize() from the
  /// connector's chosen mode. 0 before a successful Initialize().
  int width() const { return width_; }
  int height() const { return height_; }

 private:
  bool InitGbm(const DrmGlDisplayConfig &config);
  bool InitEgl(const DrmGlDisplayConfig &config);
  bool InitGl(const DrmGlDisplayConfig &config);

  struct Impl;
  std::unique_ptr<Impl> impl_;

  int width_ = 0;
  int height_ = 0;
};

}  // namespace eb::display
