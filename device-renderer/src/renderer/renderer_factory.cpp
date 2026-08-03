#include "renderer/renderer_factory.h"

#include <algorithm>
#include <cctype>
#include <cstdio>

#include "stub_renderer.h"

#if defined(ETERNALBEAM_WITH_SPINE)
#include "spine_renderer.h"
#endif

#if defined(ETERNALBEAM_WITH_FFMPEG)
#include "video_layer_renderer.h"
#endif

namespace eb::renderer {

namespace {

std::string ToLowerCopy(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  return s;
}

}  // namespace

RendererBackend ParseRendererBackend(const std::string &value, RendererBackend fallback) {
  const std::string v = ToLowerCopy(value);
  if (v == "spine") return RendererBackend::kSpine;
  if (v == "video") return RendererBackend::kVideo;
  if (v == "stub") return RendererBackend::kStub;
  if (v == "auto" || v.empty()) return RendererBackend::kAuto;
  std::fprintf(stderr, "[renderer:factory] 알 수 없는 backend '%s' — auto로 대체\n", value.c_str());
  return fallback;
}

std::unique_ptr<IPetRenderer> CreateRenderer(RendererBackend backend) {
  if (backend == RendererBackend::kAuto) {
#if defined(ETERNALBEAM_WITH_SPINE)
    backend = RendererBackend::kSpine;
#elif defined(ETERNALBEAM_WITH_FFMPEG)
    backend = RendererBackend::kVideo;
#else
    backend = RendererBackend::kStub;
#endif
  }

  switch (backend) {
    case RendererBackend::kSpine:
#if defined(ETERNALBEAM_WITH_SPINE)
      return std::make_unique<SpineRenderer>();
#else
      std::fprintf(stderr,
                    "[renderer:factory] ETERNALBEAM_RENDERER_BACKEND=spine 이지만 "
                    "ETERNALBEAM_WITH_SPINE=OFF로 빌드됨 — stub으로 대체\n");
      return std::make_unique<StubRenderer>();
#endif
    case RendererBackend::kVideo:
#if defined(ETERNALBEAM_WITH_FFMPEG)
      return std::make_unique<VideoLayerRenderer>();
#else
      std::fprintf(stderr,
                    "[renderer:factory] ETERNALBEAM_RENDERER_BACKEND=video 이지만 "
                    "ETERNALBEAM_WITH_FFMPEG=OFF로 빌드됨 — stub으로 대체\n");
      return std::make_unique<StubRenderer>();
#endif
    case RendererBackend::kStub:
    case RendererBackend::kAuto:
    default:
      return std::make_unique<StubRenderer>();
  }
}

namespace {

bool HasSpineFiles(const std::filesystem::path &asset_dir) {
  std::error_code ec;
  return std::filesystem::exists(asset_dir / "skeleton.json", ec) &&
         std::filesystem::exists(asset_dir / "skeleton.atlas", ec);
}

bool HasVideoManifest(const std::filesystem::path &asset_dir) {
  std::error_code ec;
  return std::filesystem::exists(asset_dir / "video_manifest.json", ec);
}

}  // namespace

std::unique_ptr<IPetRenderer> CreateRendererForAssetDir(const std::filesystem::path &asset_dir,
                                                         AssetType declared_type, RendererBackend forced_backend) {
  if (forced_backend != RendererBackend::kAuto) {
    std::fprintf(stderr, "[renderer:factory] ETERNALBEAM_RENDERER_BACKEND=%s 로 강제 지정됨 — "
                          "asset_type(%s)/디스크 상태를 무시합니다\n",
                 forced_backend == RendererBackend::kSpine ? "spine"
                 : forced_backend == RendererBackend::kVideo ? "video"
                                                              : "stub",
                 ToString(declared_type));
    return CreateRenderer(forced_backend);
  }

  // 1. 로컬에 실제 리깅 데이터가 있으면 서버가 뭐라고 했든 Spine이 우선입니다.
  if (HasSpineFiles(asset_dir)) {
    std::fprintf(stderr, "[renderer:factory] %s 에 skeleton.json+.atlas 존재 -> SpineRenderer\n",
                 asset_dir.string().c_str());
    return CreateRenderer(RendererBackend::kSpine);
  }

  // 2. 서버는 spine이라 했지만 로컬에 리깅 파일이 없는 예외 상황 — video로 안전하게 폴백.
  if (declared_type == AssetType::kSpine) {
    std::fprintf(stderr,
                 "[renderer:factory] 서버가 asset_type=spine 이라 했지만 %s 에 리깅 파일이 없습니다 "
                 "— video_manifest.json 폴백을 시도합니다\n",
                 asset_dir.string().c_str());
  }

  // 3. declared_type이 kVideo/kUnknown이거나 위 예외 폴백 케이스 — video_manifest.json으로 판단.
  if (HasVideoManifest(asset_dir)) {
    return CreateRenderer(RendererBackend::kVideo);
  }

  std::fprintf(stderr,
               "[renderer:factory] %s 에 skeleton.json+.atlas 도 video_manifest.json 도 없습니다 "
               "(declared_type=%s) — StubRenderer로 대체\n",
               asset_dir.string().c_str(), ToString(declared_type));
  return CreateRenderer(RendererBackend::kStub);
}

}  // namespace eb::renderer
