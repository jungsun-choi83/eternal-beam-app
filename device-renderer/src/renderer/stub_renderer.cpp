#include "stub_renderer.h"

#include <cstdio>

namespace eb::renderer {

bool StubRenderer::loadAsset(const std::string &path) {
  std::fprintf(stderr, "[renderer:stub] loadAsset(%s)\n", path.c_str());
  return true;
}

void StubRenderer::playAction(const std::string &action_name, bool loop) {
  if (action_name == current_action_) {
    return;
  }
  current_action_ = action_name;
  elapsed_seconds_ = 0.0;
  std::fprintf(stderr, "[renderer:stub] playAction(%s, loop=%s)\n", action_name.c_str(),
               loop ? "true" : "false");
}

void StubRenderer::render(FrameBuffer &frame_buffer) {
  // Intentionally no-op drawing — nothing to draw without a real backend.
  // Still honors the "clear stale pixels" contract so a backend swap never
  // leaves a previous layer's last frame on screen.
  frame_buffer.clear();
}

void StubRenderer::setDepth(float z) {
  depth_ = z;
  std::fprintf(stderr, "[renderer:stub] setDepth(%.3f)\n", z);
}

}  // namespace eb::renderer
