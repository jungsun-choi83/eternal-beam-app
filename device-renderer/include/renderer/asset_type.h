#pragma once

#include <filesystem>
#include <string>

namespace eb::renderer {

/// Content-type hint carried by the *server's* sync response (new
/// `asset_type` field on `GET /v1/device/sync` — see
/// backend/models/hybrid_business.py::DeviceSyncResponse). This describes
/// what kind of content the backend prepared for a given (pet_id,
/// place_id); it is deliberately a separate concept from RendererBackend
/// (which describes what this *binary* was compiled to support) — the two
/// get reconciled in CreateRendererForAssetDir() (see renderer_factory.h).
enum class AssetType {
  kUnknown,  // Server didn't send asset_type (older backend), or the value wasn't recognized.
  kSpine,
  kVideo,
};

AssetType ParseAssetType(const std::string &value);
const char *ToString(AssetType type);

/// Persists the server-declared asset_type alongside downloaded content so
/// CreateRendererForAssetDir() can read it back without re-hitting the
/// network on every restart. IAssetSyncClient implementations (see
/// HttpDeviceSyncClient::DownloadInto) call this once per successful sync.
/// Safe to call repeatedly; overwrites any previous value for `asset_dir`.
bool WriteSyncMeta(const std::filesystem::path &asset_dir, AssetType asset_type);

/// Reads back whatever WriteSyncMeta() last wrote for `asset_dir`. Returns
/// kUnknown if sync_meta.json is missing/unreadable — this is the normal
/// case for assets that were pre-seeded out-of-band (manufacturing time,
/// manual test fixtures, ...) rather than synced via DeviceSyncClient.
AssetType ReadDeclaredAssetType(const std::filesystem::path &asset_dir);

}  // namespace eb::renderer
