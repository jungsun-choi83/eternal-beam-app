#pragma once

#include <filesystem>
#include <memory>
#include <string>

#include "renderer/asset_type.h"
#include "renderer/pet_renderer.h"

namespace eb::renderer {

/// Which concrete IPetRenderer implementation to instantiate. `kAuto` picks
/// the best available at compile time (Spine if ETERNALBEAM_WITH_SPINE,
/// else Video if ETERNALBEAM_WITH_FFMPEG, else the logging Stub) — see
/// renderer_factory.cpp. main.cpp resolves this from the
/// ETERNALBEAM_RENDERER_BACKEND env var (see hardware_config.yaml comment).
enum class RendererBackend {
  kAuto,
  kSpine,
  kVideo,
  kStub,
};

RendererBackend ParseRendererBackend(const std::string &value, RendererBackend fallback = RendererBackend::kAuto);

/// Returns the requested IPetRenderer implementation, falling back to the
/// StubRenderer (logs every call, draws nothing) whenever the requested
/// backend wasn't compiled in — so the hardware-event -> action-selection
/// pipeline (AppController) always has *something* to drive, even on a dev
/// machine with neither Spine nor FFmpeg vendored in.
std::unique_ptr<IPetRenderer> CreateRenderer(RendererBackend backend = RendererBackend::kAuto);

/// Content-aware factory: combines what the server *declared* for this
/// asset directory (`declared_type` — see ReadDeclaredAssetType() in
/// asset_type.h, populated from the sync response's `asset_type` field)
/// with what is *actually present on disk* at `asset_dir` to decide between
/// SpineRenderer and VideoLayerRenderer. This is the seam
/// AppController::Start() calls once EnsureLocalAssets() has resolved
/// `asset_dir` — i.e. after a sync, not at process startup — so a
/// pet/place that just got rigged mid-deployment picks up SpineRenderer on
/// its very next sync without a binary restart or env var change.
///
/// `forced_backend`: pass anything other than kAuto (e.g. from
/// ETERNALBEAM_RENDERER_BACKEND, for local dev/testing) to bypass the
/// asset_type/on-disk logic entirely and defer straight to CreateRenderer()
/// — matches the previous, purely-env-var-driven behavior.
///
/// Decision order when forced_backend == kAuto (the "exception 처리"
/// requested: local rigging data always wins if it's actually present):
///   1. `asset_dir/skeleton.json` AND `asset_dir/skeleton.atlas` both exist
///      -> SpineRenderer, regardless of declared_type. Handles the case
///      where a device already has rigging cached locally before the
///      server's asset_type flag (or a slow CDN) catches up.
///   2. Else if declared_type == kSpine (server says "spine" but no rig
///      files were actually found locally — e.g. still mid-download, or
///      the rigging pipeline hasn't produced files for this content yet):
///      logs a warning and falls through to step 3 rather than handing
///      SpineRenderer an asset dir it can't load.
///   3. `asset_dir/video_manifest.json` exists -> VideoLayerRenderer.
///   4. Nothing usable found -> StubRenderer, with an error log (mirrors
///      CreateRenderer()'s existing "backend not compiled in" fallback).
std::unique_ptr<IPetRenderer> CreateRendererForAssetDir(const std::filesystem::path &asset_dir,
                                                         AssetType declared_type = AssetType::kUnknown,
                                                         RendererBackend forced_backend = RendererBackend::kAuto);

}  // namespace eb::renderer
