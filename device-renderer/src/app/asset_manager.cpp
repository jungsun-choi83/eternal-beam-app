#include "app/asset_manager.h"

#include <cstdio>
#include <fstream>
#include <utility>

#include <nlohmann/json.hpp>

namespace eb::app {

std::optional<std::string> NoopAssetSyncClient::FetchRemoteVersion(const std::string & /*pet_id*/,
                                                                    const std::string & /*place_id*/) {
  return std::nullopt;
}

bool NoopAssetSyncClient::DownloadInto(const std::string & /*pet_id*/, const std::string & /*place_id*/,
                                       const std::filesystem::path & /*destination_dir*/) {
  return false;
}

AssetManager::AssetManager(std::filesystem::path assets_root, std::unique_ptr<IAssetSyncClient> sync_client)
    : assets_root_(std::move(assets_root)), sync_client_(std::move(sync_client)) {}

std::string AssetManager::ManifestKey(const std::string &pet_id, const std::string &place_id) const {
  return pet_id + "::" + place_id;
}

std::filesystem::path AssetManager::EntryDir(const std::string &pet_id, const std::string &place_id) const {
  return assets_root_ / pet_id / place_id;
}

bool AssetManager::LoadManifest() {
  std::error_code ec;
  std::filesystem::create_directories(assets_root_, ec);

  const std::filesystem::path manifest_path = assets_root_ / "manifest.json";
  manifest_.clear();
  if (!std::filesystem::exists(manifest_path)) {
    return true;  // Nothing synced yet — not an error.
  }

  std::ifstream in(manifest_path);
  if (!in) {
    std::fprintf(stderr, "[assets] manifest.json 열기 실패: %s\n", manifest_path.string().c_str());
    return false;
  }

  nlohmann::json doc;
  try {
    in >> doc;
  } catch (const nlohmann::json::parse_error &e) {
    std::fprintf(stderr, "[assets] manifest.json 파싱 실패: %s\n", e.what());
    return false;
  }

  if (doc.contains("entries") && doc["entries"].is_object()) {
    for (const auto &[key, value] : doc["entries"].items()) {
      AssetEntry entry;
      entry.version = value.value("version", "");
      manifest_[key] = entry;
    }
  }
  return true;
}

bool AssetManager::SaveManifest() const {
  nlohmann::json entries = nlohmann::json::object();
  for (const auto &[key, entry] : manifest_) {
    entries[key] = {{"version", entry.version}};
  }

  nlohmann::json doc;
  doc["schema_version"] = 1;
  doc["entries"] = entries;

  const std::filesystem::path manifest_path = assets_root_ / "manifest.json";
  std::ofstream out(manifest_path);
  if (!out) {
    std::fprintf(stderr, "[assets] manifest.json 쓰기 실패: %s\n", manifest_path.string().c_str());
    return false;
  }
  out << doc.dump(2);
  return true;
}

bool AssetManager::DownloadAndInstall(const std::string &pet_id, const std::string &place_id,
                                      const std::string &remote_version,
                                      const std::filesystem::path &entry_dir,
                                      const std::string &manifest_key) {
  if (!sync_client_) {
    return false;
  }

  const std::filesystem::path tmp_dir = assets_root_ / (".tmp_" + manifest_key);
  std::error_code ec;
  std::filesystem::remove_all(tmp_dir, ec);
  std::filesystem::create_directories(tmp_dir, ec);

  if (!sync_client_->DownloadInto(pet_id, place_id, tmp_dir)) {
    std::filesystem::remove_all(tmp_dir, ec);
    return false;
  }

  // Swap the fully-downloaded temp dir into place atomically-ish (rename),
  // rather than downloading directly into entry_dir, so a crash mid-download
  // never leaves a half-written asset set the renderer might try to load.
  std::filesystem::remove_all(entry_dir, ec);
  std::filesystem::create_directories(entry_dir.parent_path(), ec);
  std::filesystem::rename(tmp_dir, entry_dir, ec);
  if (ec) {
    std::fprintf(stderr, "[assets] %s 설치 실패: %s\n", manifest_key.c_str(), ec.message().c_str());
    std::filesystem::remove_all(tmp_dir, ec);
    return false;
  }

  AssetEntry entry;
  entry.version = remote_version;
  manifest_[manifest_key] = entry;
  SaveManifest();
  return true;
}

std::optional<std::filesystem::path> AssetManager::EnsureLocalAssets(const std::string &pet_id,
                                                                       const std::string &place_id) {
  const std::string key = ManifestKey(pet_id, place_id);
  const std::filesystem::path entry_dir = EntryDir(pet_id, place_id);

  auto entry_dir_populated = [&]() {
    std::error_code ec;
    return std::filesystem::exists(entry_dir, ec) && !std::filesystem::is_empty(entry_dir, ec);
  };

  auto find_local = [&]() -> const AssetEntry * {
    auto it = manifest_.find(key);
    if (it == manifest_.end()) {
      return nullptr;
    }
    if (!entry_dir_populated()) {
      return nullptr;
    }
    return &it->second;
  };

  std::optional<std::string> remote_version;
  if (sync_client_) {
    remote_version = sync_client_->FetchRemoteVersion(pet_id, place_id);
  }

  const AssetEntry *local = find_local();

  // Files may already be present on disk (provisioned out-of-band, e.g. at
  // manufacturing time, or dropped in manually for local testing) even
  // though manifest.json doesn't know about them yet — adopt the directory
  // as-is so EnsureLocalAssets() doesn't require a sync client for
  // pre-seeded assets. Renderer-agnostic: we don't care *what's* in there
  // (skeleton.json+atlas vs video_manifest.json), only that it's non-empty.
  if (local == nullptr && entry_dir_populated()) {
    AssetEntry entry;
    entry.version = "local";
    manifest_[key] = entry;
    SaveManifest();
    local = &manifest_[key];
  }

  const bool stale = local != nullptr && remote_version.has_value() && local->version != *remote_version;

  if (sync_client_ && remote_version.has_value() && (local == nullptr || stale)) {
    if (DownloadAndInstall(pet_id, place_id, *remote_version, entry_dir, key)) {
      local = find_local();
    }
  }

  if (local == nullptr) {
    std::fprintf(stderr, "[assets] %s 에 대한 로컬/원격 애셋을 찾을 수 없습니다\n", key.c_str());
    return std::nullopt;
  }

  return entry_dir;
}

}  // namespace eb::app
