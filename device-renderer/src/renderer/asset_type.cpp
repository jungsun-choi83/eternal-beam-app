#include "renderer/asset_type.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>

#include <nlohmann/json.hpp>

namespace eb::renderer {

namespace {

std::string ToLowerCopy(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  return s;
}

}  // namespace

AssetType ParseAssetType(const std::string &value) {
  const std::string v = ToLowerCopy(value);
  if (v == "spine" || v == "skeleton" || v == "rig" || v == "rigging") return AssetType::kSpine;
  if (v == "video" || v == "mp4" || v == "clip") return AssetType::kVideo;
  return AssetType::kUnknown;
}

const char *ToString(AssetType type) {
  switch (type) {
    case AssetType::kSpine:
      return "spine";
    case AssetType::kVideo:
      return "video";
    case AssetType::kUnknown:
    default:
      return "unknown";
  }
}

bool WriteSyncMeta(const std::filesystem::path &asset_dir, AssetType asset_type) {
  std::error_code ec;
  std::filesystem::create_directories(asset_dir, ec);

  nlohmann::json doc;
  doc["schema_version"] = 1;
  doc["asset_type"] = ToString(asset_type);

  std::ofstream out(asset_dir / "sync_meta.json");
  if (!out) {
    std::fprintf(stderr, "[renderer:asset_type] sync_meta.json 쓰기 실패: %s\n", asset_dir.string().c_str());
    return false;
  }
  out << doc.dump(2);
  return true;
}

AssetType ReadDeclaredAssetType(const std::filesystem::path &asset_dir) {
  std::ifstream in(asset_dir / "sync_meta.json");
  if (!in) {
    return AssetType::kUnknown;  // Pre-seeded / out-of-band assets never wrote this file — expected.
  }

  nlohmann::json doc;
  try {
    in >> doc;
  } catch (const nlohmann::json::parse_error &e) {
    std::fprintf(stderr, "[renderer:asset_type] sync_meta.json 파싱 실패: %s\n", e.what());
    return AssetType::kUnknown;
  }
  return ParseAssetType(doc.value("asset_type", ""));
}

}  // namespace eb::renderer
