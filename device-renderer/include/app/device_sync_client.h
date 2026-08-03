#pragma once

#include <string>

#include "app/asset_manager.h"

namespace eb::app {

/// IAssetSyncClient backed by the *existing, unchanged* backend endpoint
/// `GET /v1/device/sync?user_id=&place_id=&pet_id=` (backend/routers/device_v1.py)
/// — the same call Unity used to make before PythonBridge.OnPetVideoUrlReceived
/// handed the results to VideoLayer. DownloadInto() doesn't fetch video
/// bytes (VideoLayerRenderer streams by URL, same as Unity's
/// VideoPlayer.url did) — it just writes what the endpoint returned as
/// assets/<pet_id>/<place_id>/video_manifest.json, plus sync_meta.json
/// capturing the response's `asset_type` field (see renderer/asset_type.h)
/// so CreateRendererForAssetDir() can pick SpineRenderer vs
/// VideoLayerRenderer without a second network call. Older backend
/// responses without `asset_type` are treated as "video" (see
/// kDefaultAssetTypeField in the .cpp) — every response before this field
/// existed only ever carried Luma-generated video URLs.
///
/// Only compiled when ETERNALBEAM_WITH_CURL=ON (see CMakeLists.txt).
class HttpDeviceSyncClient : public IAssetSyncClient {
 public:
  /// `base_url` e.g. "https://api.eternalbeam.example" (no trailing slash).
  /// `user_id` is fixed per client instance because the endpoint requires
  /// it but IAssetSyncClient's contract (shared with the Spine-asset sync
  /// path) only carries pet_id/place_id — a physical device is provisioned
  /// for one account, so this matches how ETERNALBEAM_PET_ID/PLACE_ID are
  /// already resolved once at startup in main.cpp.
  HttpDeviceSyncClient(std::string base_url, std::string user_id);

  std::optional<std::string> FetchRemoteVersion(const std::string &pet_id, const std::string &place_id) override;
  bool DownloadInto(const std::string &pet_id, const std::string &place_id,
                     const std::filesystem::path &destination_dir) override;

 private:
  /// Performs the GET call once; returns the raw JSON body, or std::nullopt
  /// on any transport error / non-2xx status (404 = "no motions charged
  /// yet" is expected and not logged as an error, matching device_v1.py's
  /// documented 404 semantics).
  std::optional<std::string> FetchSyncJson(const std::string &pet_id, const std::string &place_id);

  std::string base_url_;
  std::string user_id_;
};

}  // namespace eb::app
