#pragma once

#include "renderer/pet_renderer.h"

namespace eb::renderer {

/// No-draw placeholder renderer. Logs every call to stderr instead of
/// drawing anything, so AppController's hardware-event -> action-selection
/// wiring can be exercised (including on the Windows/macOS dev machine)
/// before Spine-CPP or FFmpeg is vendored in / neither backend was
/// requested.
class StubRenderer : public IPetRenderer {
 public:
  bool loadAsset(const std::string &path) override;
  void playAction(const std::string &action_name, bool loop) override;
  void render(FrameBuffer &frame_buffer) override;
  void setDepth(float z) override;
  float depth() const override { return depth_; }

 private:
  std::string current_action_;
  float depth_ = 0.0f;
  double elapsed_seconds_ = 0.0;
};

}  // namespace eb::renderer
