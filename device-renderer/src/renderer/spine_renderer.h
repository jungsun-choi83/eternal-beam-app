#pragma once

// Only compiled when ETERNALBEAM_WITH_SPINE=ON (see CMakeLists.txt and
// libs/spine-cpp/README.md) — everything in here talks to the real
// spine-cpp runtime.

#include <chrono>
#include <memory>
#include <string>

#include <spine/spine.h>

#include "renderer/pet_renderer.h"

namespace eb::renderer {

/// Minimal spine::TextureLoader — actual OpenGL ES / software texture
/// decode is left as an explicit extension point (see LoadTexture below)
/// since it depends on the target's GL/EGL context setup, which this
/// project doesn't own.
class GlTextureLoader : public spine::TextureLoader {
 public:
  void load(spine::AtlasPage &page, const spine::String &path) override;
  void unload(void *texture) override;
};

/// IPetRenderer implementation backed by spine-cpp — the skeletal successor
/// to Unity's VideoLayer. Owns exactly one loaded skeleton at a time;
/// swapping pet/place reloads a fresh SkeletonData via loadAsset(), which is
/// cheap relative to the video-decode pipeline VideoLayerRenderer still uses
/// for content that hasn't been rigged yet.
class SpineRenderer : public IPetRenderer {
 public:
  SpineRenderer();
  ~SpineRenderer() override;

  bool loadAsset(const std::string &path) override;
  void playAction(const std::string &action_name, bool loop) override;
  void render(FrameBuffer &frame_buffer) override;
  void setDepth(float z) override;
  float depth() const override { return depth_; }

  /// Selects a skin within the currently loaded skeleton (e.g. to swap in a
  /// specific pet's texture atlas region set against a shared rig).
  /// Not part of IPetRenderer — Spine-specific, called automatically by
  /// loadAsset() using the asset directory's <pet_id> segment as a best
  /// guess, and available here for callers that want to override that.
  bool setSkin(const std::string &skin_name);

 private:
  void updateAnimation(float delta_seconds);
  void drawIntoFrameBuffer(FrameBuffer &frame_buffer);

  GlTextureLoader texture_loader_;
  std::unique_ptr<spine::Atlas> atlas_;
  std::unique_ptr<spine::SkeletonData> skeleton_data_;
  std::unique_ptr<spine::AnimationStateData> animation_state_data_;
  std::unique_ptr<spine::Skeleton> skeleton_;
  std::unique_ptr<spine::AnimationState> animation_state_;

  float depth_ = 0.0f;
  std::chrono::steady_clock::time_point last_render_time_{};
  bool has_last_render_time_ = false;
};

}  // namespace eb::renderer
