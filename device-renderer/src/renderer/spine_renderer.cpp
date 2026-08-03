#include "spine_renderer.h"

#include <cstdio>
#include <filesystem>

// NOTE: spine-cpp's public API has shifted slightly across major versions
// (e.g. Skeleton::updateWorldTransform() gained a spine::Physics parameter
// in 4.2). This file targets the 4.0/4.1-era signatures used at the time of
// writing; if the version you vendor under libs/ differs, the compiler
// errors here will point at exactly what changed.

namespace eb::renderer {

namespace {

/// "Idle -> Action(touch/voice/nfc)"가 자연스럽게 겹쳐지도록(crossfade) 설정하는
/// 기본 블렌딩 시간입니다. AnimationState::setAnimation()(playAction()이 매 호출마다
/// 부름)이 이 값을 내부적으로 참고해 이전 트랙과 새 애니메이션을 자동으로 믹스하므로,
/// playAction() 쪽 코드는 별도 타이머 없이 그대로 두면 됩니다.
constexpr float kIdleActionCrossfadeSec = 0.5f;

/// idle<->action 양방향 믹스를 등록합니다(복귀 시에도 동일하게 부드럽게) — 두 애니메이션
/// 이름 중 하나라도 이 스켈레톤에 없으면 조용히 스킵합니다(리그마다 touch/voice/nfc가
/// 전부 있으리라는 보장이 없으므로 에러로 취급하지 않습니다).
void RegisterIdleActionCrossfade(spine::SkeletonData &skeleton_data, spine::AnimationStateData &mix_data,
                                  const char *idle_name, const char *action_name, float duration_sec) {
  spine::Animation *idle_anim = skeleton_data.findAnimation(idle_name);
  spine::Animation *action_anim = skeleton_data.findAnimation(action_name);
  if (idle_anim == nullptr || action_anim == nullptr) {
    return;
  }
  mix_data.setMix(idle_anim, action_anim, duration_sec);
  mix_data.setMix(action_anim, idle_anim, duration_sec);
}

}  // namespace

void GlTextureLoader::load(spine::AtlasPage &page, const spine::String &path) {
  // TODO: decode `path` (PNG) and upload it via glTexImage2D, then store the
  // resulting GLuint somewhere retrievable from `page` (consult the
  // AtlasPage fields in your vendored spine-cpp version — this is
  // intentionally left unimplemented since it depends on how this project
  // manages its GL/EGL context, which is board/display-stack specific).
  std::fprintf(stderr, "[renderer:spine] TextureLoader::load(%s) — GL upload not implemented\n",
               path.buffer());
}

void GlTextureLoader::unload(void *texture) {
  (void)texture;
  // TODO: glDeleteTextures for the handle stored by load() above.
}

SpineRenderer::SpineRenderer() = default;
SpineRenderer::~SpineRenderer() {
  animation_state_.reset();
  skeleton_.reset();
  animation_state_data_.reset();
  skeleton_data_.reset();
  atlas_.reset();
}

bool SpineRenderer::loadAsset(const std::string &path) {
  const std::filesystem::path dir(path);
  const std::filesystem::path skeleton_json_path = dir / "skeleton.json";
  const std::filesystem::path atlas_path = dir / "skeleton.atlas";

  atlas_ = std::make_unique<spine::Atlas>(atlas_path.string().c_str(), &texture_loader_);
  if (atlas_->getPages().size() == 0) {
    std::fprintf(stderr, "[renderer:spine] atlas load 실패: %s\n", atlas_path.string().c_str());
    return false;
  }

  spine::SkeletonJson json(atlas_.get());
  spine::SkeletonData *raw_data = json.readSkeletonDataFile(skeleton_json_path.string().c_str());
  if (raw_data == nullptr) {
    std::fprintf(stderr, "[renderer:spine] skeleton load 실패 (%s): %s\n",
                 skeleton_json_path.string().c_str(), json.getError().buffer());
    return false;
  }
  skeleton_data_.reset(raw_data);

  animation_state_data_ = std::make_unique<spine::AnimationStateData>(skeleton_data_.get());
  // 요청하신 "idle -> action 0.5초 크로스페이드" 초기화: idle과 touch/voice/nfc 사이를
  // 양방향으로 0.5초 믹스로 등록하고, 그 외 조합(예: touch -> voice)은 setDefaultMix()의
  // 같은 기본값을 씁니다. sensor_event.h::ToAnimationName()이 정의하는 4개 고정 액션
  // 이름("idle"/"touch"/"voice"/"nfc")과 정확히 맞춰뒀습니다.
  animation_state_data_->setDefaultMix(kIdleActionCrossfadeSec);
  for (const char *action_name : {"touch", "voice", "nfc"}) {
    RegisterIdleActionCrossfade(*skeleton_data_, *animation_state_data_, "idle", action_name,
                                 kIdleActionCrossfadeSec);
  }

  skeleton_ = std::make_unique<spine::Skeleton>(skeleton_data_.get());
  animation_state_ = std::make_unique<spine::AnimationState>(animation_state_data_.get());

  skeleton_->setToSetupPose();

  // Best-effort auto-skin: assets/<pet_id>/<place_id>/ — try the pet_id
  // segment as a skin name so a shared template rig can carry multiple
  // pets' texture regions without every caller having to know about
  // setSkin() explicitly. Silently keeps the default skin if there's no
  // match (most rigs only have one skin while this is being rolled out).
  const std::filesystem::path pet_dir = dir.parent_path();
  if (!pet_dir.empty()) {
    setSkin(pet_dir.filename().string());
  }

  has_last_render_time_ = false;
  return true;
}

bool SpineRenderer::setSkin(const std::string &skin_name) {
  if (!skeleton_) {
    return false;
  }
  if (!skeleton_->setSkin(skin_name.c_str())) {
    return false;
  }
  skeleton_->setSlotsToSetupPose();
  return true;
}

void SpineRenderer::playAction(const std::string &action_name, bool loop) {
  if (!animation_state_ || !skeleton_data_) {
    return;
  }
  spine::Animation *animation = skeleton_data_->findAnimation(action_name.c_str());
  if (animation == nullptr) {
    std::fprintf(stderr, "[renderer:spine] animation '%s'를 찾을 수 없습니다\n", action_name.c_str());
    return;
  }
  animation_state_->setAnimation(0, animation, loop);
}

void SpineRenderer::updateAnimation(float delta_seconds) {
  if (!animation_state_ || !skeleton_) {
    return;
  }
  animation_state_->update(delta_seconds);
  animation_state_->apply(*skeleton_);
  skeleton_->update(delta_seconds);
  skeleton_->updateWorldTransform();
}

void SpineRenderer::drawIntoFrameBuffer(FrameBuffer &frame_buffer) {
  if (!skeleton_) {
    return;
  }
  // TODO: walk skeleton_->getDrawOrder(), batch each spine::RegionAttachment
  // / spine::MeshAttachment's world vertices + UVs, rasterize into
  // frame_buffer.pixels (or, once a GL/EGL context exists on the target,
  // draw with a shader that reproduces
  // EternalBeam/Assets/Shaders/PetShader.shader's premultiplied-alpha blend
  // + rim-light + sharpen logic and read the result back into
  // frame_buffer). This is the direct C++ port target for that shader (see
  // the migration analysis canvas for the full mapping). setDepth()'s value
  // is available via depth() for depth-based rim/scale cues once this is
  // implemented.
  (void)frame_buffer;
}

void SpineRenderer::render(FrameBuffer &frame_buffer) {
  const auto now = std::chrono::steady_clock::now();
  float delta_seconds = 0.0f;
  if (has_last_render_time_) {
    delta_seconds = std::chrono::duration<float>(now - last_render_time_).count();
  }
  last_render_time_ = now;
  has_last_render_time_ = true;

  updateAnimation(delta_seconds);
  frame_buffer.clear();
  drawIntoFrameBuffer(frame_buffer);
}

void SpineRenderer::setDepth(float z) { depth_ = z; }

}  // namespace eb::renderer
