#pragma once

#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <string>

namespace eb::app {

struct AssetEntry {
  std::string version;
};

/// Abstracts "how do we get newer content onto this device" away from
/// AssetManager — and away from *which renderer backend* is active.
/// DownloadInto() just has to populate `destination_dir` with whatever
/// files the active IPetRenderer::loadAsset() expects to find there
/// (skeleton.json+.atlas for SpineRenderer, video_manifest.json for
/// VideoLayerRenderer — see assets/README.md); AssetManager never inspects the
/// contents itself. The default NoopAssetSyncClient assumes assets already
/// exist locally (e.g. pre-loaded at manufacturing/provisioning time);
/// HttpDeviceSyncClient (see include/app/device_sync_client.h) is the real
/// implementation used for VideoLayerRenderer today, wrapping the existing
/// `GET /v1/device/sync` endpoint unchanged.
class IAssetSyncClient {
 public:
  virtual ~IAssetSyncClient() = default;

  /// Remote version string/hash for (pet_id, place_id), or std::nullopt if
  /// the server has nothing for that combination (mirrors the existing
  /// /v1/device/sync 404 semantics for "not generated yet").
  virtual std::optional<std::string> FetchRemoteVersion(const std::string &pet_id,
                                                         const std::string &place_id) = 0;

  /// Downloads/writes whatever the active renderer backend needs for
  /// (pet_id, place_id) into `destination_dir`. Returns true on success.
  virtual bool DownloadInto(const std::string &pet_id, const std::string &place_id,
                            const std::filesystem::path &destination_dir) = 0;
};

/// Always available; used when no sync client is configured (e.g. assets
/// were provisioned onto the device out of band). EnsureLocalAssets() then
/// only ever serves what's already on disk.
class NoopAssetSyncClient : public IAssetSyncClient {
 public:
  std::optional<std::string> FetchRemoteVersion(const std::string &pet_id,
                                                 const std::string &place_id) override;
  bool DownloadInto(const std::string &pet_id, const std::string &place_id,
                    const std::filesystem::path &destination_dir) override;
};

/// Standardizes where downloaded content lives on disk (assets/, see
/// assets/README.md) and hands the resulting directory to whichever
/// IPetRenderer is active — this class (like HardwareInterface) is one of
/// the two seams the whole "swap the renderer without touching anything
/// else" architecture relies on: it is completely renderer-agnostic, it
/// only manages directories and a version manifest.
class AssetManager {
 public:
  explicit AssetManager(std::filesystem::path assets_root,
                        std::unique_ptr<IAssetSyncClient> sync_client = nullptr);

  /// Loads assets_root/manifest.json, creating an empty one if missing.
  /// Returns false only on an actual I/O or parse error (a missing file is
  /// not an error — it just means no assets have been synced yet).
  bool LoadManifest();

  /// Ensures assets_root/<pet_id>/<place_id>/ is present and up to date:
  ///   1. If a sync client is configured, ask it for the remote version.
  ///   2. Download into a temp dir and atomically replace the local copy
  ///      when missing locally or when the remote version differs.
  ///   3. Persist the new version into manifest.json.
  /// Returns the on-disk directory ready for IPetRenderer::loadAsset(), or
  /// std::nullopt if nothing is available either locally or remotely.
  std::optional<std::filesystem::path> EnsureLocalAssets(const std::string &pet_id, const std::string &place_id);

  const std::filesystem::path &assets_root() const { return assets_root_; }

 private:
  std::string ManifestKey(const std::string &pet_id, const std::string &place_id) const;
  std::filesystem::path EntryDir(const std::string &pet_id, const std::string &place_id) const;
  bool SaveManifest() const;
  bool DownloadAndInstall(const std::string &pet_id, const std::string &place_id,
                          const std::string &remote_version, const std::filesystem::path &entry_dir,
                          const std::string &manifest_key);

  std::filesystem::path assets_root_;
  std::unique_ptr<IAssetSyncClient> sync_client_;
  std::map<std::string, AssetEntry> manifest_;
};

}  // namespace eb::app
