#include "app/device_sync_client.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <functional>
#include <map>
#include <sstream>

#include <curl/curl.h>
#include <nlohmann/json.hpp>

#include "renderer/asset_type.h"

namespace eb::app {

namespace {

// Every /v1/device/sync response before the `asset_type` field existed only
// ever carried Luma-generated video URLs — so a missing field defaults to
// "video", not "unknown", keeping old backend deployments working exactly
// as before against this newer client.
constexpr const char *kDefaultAssetTypeField = "video";

std::string UrlEncode(CURL *curl, const std::string &value) {
  char *escaped = curl_easy_escape(curl, value.c_str(), static_cast<int>(value.size()));
  std::string out = escaped != nullptr ? escaped : value;
  curl_free(escaped);
  return out;
}

std::size_t WriteCallback(char *ptr, std::size_t size, std::size_t nmemb, void *user_data) {
  auto *out = static_cast<std::string *>(user_data);
  out->append(ptr, size * nmemb);
  return size * nmemb;
}

std::string ToLowerCopy(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  return s;
}

}  // namespace

HttpDeviceSyncClient::HttpDeviceSyncClient(std::string base_url, std::string user_id)
    : base_url_(std::move(base_url)), user_id_(std::move(user_id)) {
  if (!base_url_.empty() && base_url_.back() == '/') {
    base_url_.pop_back();
  }
}

std::optional<std::string> HttpDeviceSyncClient::FetchSyncJson(const std::string &pet_id,
                                                                const std::string &place_id) {
  CURL *curl = curl_easy_init();
  if (curl == nullptr) {
    std::fprintf(stderr, "[app:device_sync] curl_easy_init 실패\n");
    return std::nullopt;
  }

  std::ostringstream url;
  url << base_url_ << "/v1/device/sync?user_id=" << UrlEncode(curl, user_id_)
      << "&place_id=" << UrlEncode(curl, place_id);
  if (!pet_id.empty()) {
    url << "&pet_id=" << UrlEncode(curl, pet_id);
  }

  std::string response_body;
  curl_easy_setopt(curl, CURLOPT_URL, url.str().c_str());
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);

  const CURLcode res = curl_easy_perform(curl);
  long status_code = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
  curl_easy_cleanup(curl);

  if (res != CURLE_OK) {
    std::fprintf(stderr, "[app:device_sync] GET /v1/device/sync 실패: %s\n", curl_easy_strerror(res));
    return std::nullopt;
  }
  if (status_code == 404) {
    return std::nullopt;  // "이 장소에 충전된 영혼(모션)이 없습니다" — device_v1.py 문서화된 정상 케이스.
  }
  if (status_code < 200 || status_code >= 300) {
    std::fprintf(stderr, "[app:device_sync] GET /v1/device/sync -> HTTP %ld\n", status_code);
    return std::nullopt;
  }
  return response_body;
}

std::optional<std::string> HttpDeviceSyncClient::FetchRemoteVersion(const std::string &pet_id,
                                                                     const std::string &place_id) {
  const auto body = FetchSyncJson(pet_id, place_id);
  if (!body.has_value()) {
    return std::nullopt;
  }

  nlohmann::json doc;
  try {
    doc = nlohmann::json::parse(*body);
  } catch (const nlohmann::json::parse_error &e) {
    std::fprintf(stderr, "[app:device_sync] 응답 파싱 실패: %s\n", e.what());
    return std::nullopt;
  }

  // The endpoint has no explicit version/etag field today — derive a stable
  // fingerprint from the sorted (action_id, video_url) pairs so
  // AssetManager can still detect "content changed since last sync".
  // asset_type is folded in too, so a pet/place flipping from video to
  // spine (or back) counts as a content change even if action_id/video_url
  // pairs happen to stay the same during the transition.
  std::map<std::string, std::string> sorted_urls;
  if (doc.contains("motions") && doc["motions"].is_array()) {
    for (const auto &item : doc["motions"]) {
      const std::string action_id = ToLowerCopy(item.value("action_id", ""));
      const std::string video_url = item.value("video_url", "");
      if (!action_id.empty()) {
        sorted_urls[action_id] = video_url;
      }
    }
  }
  if (sorted_urls.empty()) {
    return std::nullopt;
  }

  const std::string asset_type_field = doc.value("asset_type", kDefaultAssetTypeField);

  std::string fingerprint_input = "asset_type=" + asset_type_field + ";";
  for (const auto &[action_id, url] : sorted_urls) {
    fingerprint_input += action_id + "=" + url + ";";
  }
  const std::size_t hash = std::hash<std::string>{}(fingerprint_input);

  std::ostringstream version;
  version << std::hex << hash;
  return version.str();
}

bool HttpDeviceSyncClient::DownloadInto(const std::string &pet_id, const std::string &place_id,
                                         const std::filesystem::path &destination_dir) {
  // TODO(rigging-pipeline): once the backend actually serves rigged
  // skeleton.json/.atlas URLs for asset_type=spine responses (see
  // docs/매팅_및_리깅_AI_조사.md — no auto-rigging pipeline exists yet),
  // download+write those here too, mirroring the video_manifest.json
  // handling below. Until then, an asset_type=spine response with no
  // motions[] simply fails to download anything, and
  // CreateRendererForAssetDir()'s exception-fallback path (see
  // renderer_factory.h) takes over at render time.
  const auto body = FetchSyncJson(pet_id, place_id);
  if (!body.has_value()) {
    return false;
  }

  nlohmann::json doc;
  try {
    doc = nlohmann::json::parse(*body);
  } catch (const nlohmann::json::parse_error &e) {
    std::fprintf(stderr, "[app:device_sync] 응답 파싱 실패: %s\n", e.what());
    return false;
  }

  nlohmann::json motions = nlohmann::json::object();
  if (doc.contains("motions") && doc["motions"].is_array()) {
    for (const auto &item : doc["motions"]) {
      const std::string action_id = ToLowerCopy(item.value("action_id", ""));
      const std::string video_url = item.value("video_url", "");
      if (!action_id.empty() && !video_url.empty()) {
        motions[action_id] = {{"video_url", video_url}};
      }
    }
  }
  if (motions.empty()) {
    std::fprintf(stderr, "[app:device_sync] 응답에 유효한 motions가 없습니다 (%s/%s)\n", pet_id.c_str(),
                 place_id.c_str());
    return false;
  }

  nlohmann::json manifest;
  manifest["schema_version"] = 1;
  manifest["user_id"] = doc.value("user_id", user_id_);
  manifest["pet_id"] = doc.value("pet_id", pet_id);
  manifest["place_id"] = doc.value("place_id", place_id);
  manifest["motions"] = motions;

  std::error_code ec;
  std::filesystem::create_directories(destination_dir, ec);

  std::ofstream out(destination_dir / "video_manifest.json");
  if (!out) {
    std::fprintf(stderr, "[app:device_sync] video_manifest.json 쓰기 실패: %s\n",
                 destination_dir.string().c_str());
    return false;
  }
  out << manifest.dump(2);

  // asset_type을 sync_meta.json으로 별도 저장 — CreateRendererForAssetDir()이
  // 렌더러 종류와 무관하게 이 디렉터리 하나만 보고 스켈레톤 렌더러/영상 렌더러를
  // 고를 수 있게 합니다 (video_manifest.json 스키마에 얹지 않는 이유는
  // asset_type=spine인데 아직 video_manifest.json이 없는 과도기 상태도 표현하기
  // 위함입니다 — 이 경우 renderer_factory가 "예외 처리"로 폴백합니다).
  const std::string asset_type_field = doc.value("asset_type", kDefaultAssetTypeField);
  eb::renderer::WriteSyncMeta(destination_dir, eb::renderer::ParseAssetType(asset_type_field));

  return true;
}

}  // namespace eb::app
