#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "app/app_controller.h"
#include "app/asset_manager.h"
#include "hardware/hardware_config.h"
#include "hardware/hardware_factory.h"
#include "renderer/renderer_factory.h"

#if defined(ETERNALBEAM_WITH_CURL)
#include "app/device_sync_client.h"
#endif

#if defined(ETERNALBEAM_WITH_DRM_GL)
#include "display/drm_gl_display.h"
#endif

#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
#include "hardware/vl53l0x_touch_thread.h"
#endif

namespace {

std::atomic<bool> g_running{true};

void HandleSignal(int /*signum*/) { g_running.store(false); }

std::string EnvOr(const char *name, const std::string &fallback) {
  const char *value = std::getenv(name);
  return (value != nullptr && *value != '\0') ? std::string(value) : fallback;
}

/// Optional debug aid: dumps AppController::LastFrame() to a .ppm file
/// every couple of seconds when ETERNALBEAM_DUMP_FRAME_PPM=<path> is set —
/// lets you visually confirm the active IPetRenderer is actually drawing
/// something without a physical display attached (or on builds without
/// ETERNALBEAM_WITH_DRM_GL). See README.md "디스플레이 출력" for the real,
/// on-panel path. Not the production display path — just `xdg-open
/// frame.ppm` (or convert to PNG) to look.
void MaybeDumpFramePpm(const eb::renderer::FrameBuffer &frame, const std::string &path) {
  std::FILE *f = std::fopen(path.c_str(), "wb");
  if (f == nullptr) {
    return;
  }
  std::fprintf(f, "P6\n%d %d\n255\n", frame.width, frame.height);
  const int stride = frame.effectiveStride();
  for (int y = 0; y < frame.height; ++y) {
    const std::uint8_t *row = frame.pixels + static_cast<std::ptrdiff_t>(y) * stride;
    for (int x = 0; x < frame.width; ++x) {
      std::fputc(row[x * 4 + 0], f);
      std::fputc(row[x * 4 + 1], f);
      std::fputc(row[x * 4 + 2], f);
    }
  }
  std::fclose(f);
}

}  // namespace

int main() {
  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  eb::hardware::HardwareConfig hw_config;
  try {
    hw_config = eb::hardware::HardwareConfig::Load();
  } catch (const std::exception &e) {
    std::fprintf(stderr, "[main] hardware_config.yaml 로드 실패: %s\n", e.what());
    return 1;
  }
  std::fprintf(stderr, "[main] board = %s (%s)\n", hw_config.board().c_str(), hw_config.label().c_str());

  // ETERNALBEAM_HARDWARE 는 "이 기기가 센서를 어떻게 받는가"만 바꾼다 — GPIO/I2C
  // 직접 폴링(rk3566/rpi5)이든 기존 Python UDP 브릿지를 그대로 받는
  // udp_bridge든, AppController/렌더러는 전혀 알 필요가 없다. 보통은
  // hardware_config.yaml의 active_board (또는 HARDWARE_BOARD 환경변수)로
  // 고르면 충분하다.
  const auto hardware = eb::hardware::CreateHardware(hw_config);
  if (!hardware->Initialize()) {
    std::fprintf(stderr, "[main] 하드웨어 초기화 실패 — 센서 없이 idle 애니메이션만으로 계속 진행합니다\n");
  }

  // ETERNALBEAM_RENDERER_BACKEND is now only a dev/testing override — normally
  // AppController::Start() picks SpineRenderer vs VideoLayerRenderer itself,
  // per (pet_id, place_id), from the server's asset_type + what's actually on
  // disk (see renderer::CreateRendererForAssetDir). Leaving this env var
  // unset (or "auto") is the production default.
  const auto forced_renderer_backend =
      eb::renderer::ParseRendererBackend(EnvOr("ETERNALBEAM_RENDERER_BACKEND", "auto"));

  std::unique_ptr<eb::app::IAssetSyncClient> sync_client;
#if defined(ETERNALBEAM_WITH_CURL)
  {
    const std::string user_id = EnvOr("ETERNALBEAM_USER_ID", "");
    if (!user_id.empty()) {
      sync_client = std::make_unique<eb::app::HttpDeviceSyncClient>(hw_config.backend_base_url(), user_id);
    } else {
      std::fprintf(stderr,
                    "[main] ETERNALBEAM_USER_ID 미설정 — 서버 동기화 비활성화, 로컬에 미리 준비된 애셋만 사용\n");
    }
  }
#endif

  eb::app::AssetManager assets(hw_config.assets_root(), std::move(sync_client));
  if (!assets.LoadManifest()) {
    std::fprintf(stderr, "[main] %s/manifest.json 로드 실패\n", hw_config.assets_root().c_str());
  }

  eb::app::AppConfig app_config;
  app_config.pet_id = EnvOr("ETERNALBEAM_PET_ID", "demo_pet");
  app_config.place_id = EnvOr("ETERNALBEAM_PLACE_ID", "snow_forest");
  app_config.forced_renderer_backend = forced_renderer_backend;

#if defined(ETERNALBEAM_WITH_DRM_GL)
  // Bring the real display up *before* constructing AppController — its
  // FrameBuffer is sized from app_config.render_width/height at construction
  // time (see AppController's ctor), so it has to match whatever mode
  // DrmGlDisplay actually modeset onto, not the 720x1280 placeholder default.
  eb::display::DrmGlDisplay display;
  const bool display_disabled = EnvOr("ETERNALBEAM_DISABLE_DISPLAY", "0") == "1";
  const bool display_ready = !display_disabled && display.Initialize();
  if (display_disabled) {
    std::fprintf(stderr, "[main] ETERNALBEAM_DISABLE_DISPLAY=1 — DRM/KMS 출력 비활성화 (headless)\n");
  } else if (!display_ready) {
    std::fprintf(stderr, "[main] DRM/KMS 디스플레이 초기화 실패 — headless로 계속 진행합니다 "
                          "(ETERNALBEAM_DUMP_FRAME_PPM로 프레임 확인 가능)\n");
  } else {
    app_config.render_width = display.width();
    app_config.render_height = display.height();
  }
#endif

  eb::app::AppController controller(*hardware, assets, app_config);
  if (!controller.Start()) {
    std::fprintf(stderr,
                 "[main] 시작 실패 — %s/%s/ 에 (skeleton.json+skeleton.atlas 또는 video_manifest.json) 가 "
                 "있는지 확인하세요\n",
                 hw_config.assets_root().c_str(), app_config.pet_id.c_str());
    return 1;
  }

#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
  // Opt-in — see Vl53l0xTouchThread's class doc comment: this bypasses
  // HardwareInterface::Poll() entirely, so only enable it on a board where
  // nothing else is already emitting ActionEvent::Touch from the same
  // physical VL53L0X (today that's every board — LinuxCommonHardware's own
  // VL53L0X path is still an unimplemented stub — but that could change).
  std::unique_ptr<eb::hardware::Vl53l0xTouchThread> vl53l0x_touch_thread;
  if (EnvOr("ETERNALBEAM_VL53L0X_TOUCH_THREAD", "0") == "1" && controller.Renderer() != nullptr) {
    vl53l0x_touch_thread = std::make_unique<eb::hardware::Vl53l0xTouchThread>(
        *controller.Renderer(), eb::hardware::Vl53l0xTouchThreadConfig::FromHardwareConfig(hw_config));
    if (!vl53l0x_touch_thread->Start()) {
      std::fprintf(stderr, "[main] VL53L0X 터치 스레드 시작 실패 — 계속 진행합니다 (Poll() 경로만 사용)\n");
      vl53l0x_touch_thread.reset();
    }
  }
#endif

  const std::string dump_ppm = EnvOr("ETERNALBEAM_DUMP_FRAME_PPM", "");

  constexpr double kTargetFps = 30.0;
  const std::chrono::duration<double> frame_duration{1.0 / kTargetFps};

  auto last_tick = std::chrono::steady_clock::now();
  auto last_dump = last_tick;
  while (g_running.load()) {
    const auto now = std::chrono::steady_clock::now();
    const float delta_seconds = std::chrono::duration<float>(now - last_tick).count();
    last_tick = now;

#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
    // Tick() calls into renderer_->render()/playAction() from this (main)
    // thread — sharing Vl53l0xTouchThread's mutex here is what makes that
    // safe to run concurrently with its own background playAction() calls
    // into the exact same IPetRenderer instance (see that class's
    // thread-safety note).
    if (vl53l0x_touch_thread) {
      std::lock_guard<std::mutex> lock(vl53l0x_touch_thread->renderer_mutex());
      controller.Tick(delta_seconds);
    } else {
      controller.Tick(delta_seconds);
    }
#else
    controller.Tick(delta_seconds);
#endif

#if defined(ETERNALBEAM_WITH_DRM_GL)
    if (display_ready) {
      // Present() blocks until the panel's vblank confirms the page-flip
      // (see drm_gl_display.cpp), which is what actually paces this loop to
      // the display's real refresh rate — the sleep_for() below still runs
      // afterwards, but shrinks to ~0 once Present() itself is the
      // bottleneck. Not compositing a separate background-video plane yet
      // (see DrmGlDisplay's class doc comment) — this shows exactly what
      // AppController's active IPetRenderer drew, same as the PPM dump did.
      display.Present(controller.LastFrame());
    }
#endif

    if (!dump_ppm.empty() && std::chrono::duration<double>(now - last_dump).count() >= 2.0) {
      MaybeDumpFramePpm(controller.LastFrame(), dump_ppm);
      last_dump = now;
    }

    std::this_thread::sleep_for(frame_duration);
  }

#if defined(ETERNALBEAM_WITH_DRM_GL)
  display.Shutdown();
#endif
#if defined(ETERNALBEAM_HAS_LINUX_HARDWARE)
  if (vl53l0x_touch_thread) {
    vl53l0x_touch_thread->Stop();
  }
#endif
  hardware->Shutdown();
  std::fprintf(stderr, "[main] 종료합니다\n");
  return 0;
}
