#include "app/app_controller.h"

#include <cstdio>
#include <utility>

#include "renderer/asset_type.h"

namespace eb::app {

using eb::hardware::ActionEvent;
using eb::hardware::SensorEvent;
using eb::hardware::ToAnimationName;
using eb::renderer::AssetType;
using eb::renderer::CreateRendererForAssetDir;

AppController::AppController(eb::hardware::HardwareInterface &hardware, AssetManager &assets, AppConfig config)
    : hardware_(hardware), assets_(assets), config_(std::move(config)) {
  hardware_.SetSensorEventCallback([this](const SensorEvent &event) { OnSensorEvent(event); });

  frame_pixels_.assign(
      static_cast<std::size_t>(config_.render_width) * static_cast<std::size_t>(config_.render_height) * 4, 0);
  frame_buffer_.pixels = frame_pixels_.data();
  frame_buffer_.width = config_.render_width;
  frame_buffer_.height = config_.render_height;
  frame_buffer_.stride_bytes = config_.render_width * 4;
}

bool AppController::Start() {
  const auto asset_dir = assets_.EnsureLocalAssets(config_.pet_id, config_.place_id);
  if (!asset_dir) {
    std::fprintf(stderr, "[app] %s/%s 애셋을 로드할 수 없어 시작하지 못했습니다\n", config_.pet_id.c_str(),
                 config_.place_id.c_str());
    return false;
  }

  // 서버가 이번 sync에서 선언한 asset_type을 읽어(HttpDeviceSyncClient::DownloadInto가
  // 기록한 sync_meta.json, 없으면 kUnknown) 실제 렌더러를 고릅니다 — 팩토리 자체의
  // 우선순위/예외 처리는 renderer::CreateRendererForAssetDir()의 문서를 참고하세요.
  const AssetType declared_type = eb::renderer::ReadDeclaredAssetType(*asset_dir);
  renderer_ = CreateRendererForAssetDir(*asset_dir, declared_type, config_.forced_renderer_backend);

  if (!renderer_->loadAsset(asset_dir->string())) {
    std::fprintf(stderr, "[app] %s 로더 실패 (declared_type=%s)\n", asset_dir->string().c_str(),
                 eb::renderer::ToString(declared_type));
    return false;
  }
  TriggerAction(ActionEvent::Idle, /*loop=*/true);
  return true;
}

void AppController::Tick(float delta_seconds) {
  hardware_.Poll();
  if (renderer_) {
    renderer_->render(frame_buffer_);
  }

  if (current_action_ != ActionEvent::Idle) {
    seconds_since_last_event_ += delta_seconds;
    if (seconds_since_last_event_ >= config_.idle_return_after_sec) {
      TriggerAction(ActionEvent::Idle, /*loop=*/true);
    }
  }
}

void AppController::OnSensorEvent(const SensorEvent &event) {
  // Touch/Voice/Nfc are one-shot reactions; Idle is the resting loop Tick()
  // falls back to automatically once idle_return_after_sec elapses.
  TriggerAction(event.action, /*loop=*/event.action == ActionEvent::Idle);
}

void AppController::TriggerAction(ActionEvent action, bool loop) {
  current_action_ = action;
  seconds_since_last_event_ = 0.0;
  if (renderer_) {
    renderer_->playAction(ToAnimationName(action), loop);
  }
}

}  // namespace eb::app
