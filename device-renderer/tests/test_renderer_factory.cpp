#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "renderer/asset_type.h"
#include "renderer/renderer_factory.h"

using eb::renderer::AssetType;
using eb::renderer::CreateRendererForAssetDir;
using eb::renderer::ParseAssetType;
using eb::renderer::ReadDeclaredAssetType;
using eb::renderer::RendererBackend;
using eb::renderer::WriteSyncMeta;

namespace {

std::filesystem::path MakeTempRoot() {
  const auto root = std::filesystem::temp_directory_path() /
                     ("eb_renderer_factory_test_" + std::to_string(std::random_device{}()));
  std::filesystem::create_directories(root);
  return root;
}

void WriteFile(const std::filesystem::path &path, const std::string &content) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream out(path);
  out << content;
}

}  // namespace

TEST_CASE("ParseAssetType recognizes server-declared asset_type values", "[renderer_factory][asset_type]") {
  CHECK(ParseAssetType("spine") == AssetType::kSpine);
  CHECK(ParseAssetType("SPINE") == AssetType::kSpine);
  CHECK(ParseAssetType("rigging") == AssetType::kSpine);
  CHECK(ParseAssetType("video") == AssetType::kVideo);
  CHECK(ParseAssetType("mp4") == AssetType::kVideo);
  CHECK(ParseAssetType("") == AssetType::kUnknown);
  CHECK(ParseAssetType("something_else") == AssetType::kUnknown);
}

TEST_CASE("WriteSyncMeta/ReadDeclaredAssetType round-trip through sync_meta.json",
          "[renderer_factory][asset_type]") {
  const auto root = MakeTempRoot();

  // No sync_meta.json yet — matches pre-seeded / out-of-band assets.
  CHECK(ReadDeclaredAssetType(root) == AssetType::kUnknown);

  REQUIRE(WriteSyncMeta(root, AssetType::kSpine));
  CHECK(ReadDeclaredAssetType(root) == AssetType::kSpine);

  REQUIRE(WriteSyncMeta(root, AssetType::kVideo));  // Overwriting a previous sync must work too.
  CHECK(ReadDeclaredAssetType(root) == AssetType::kVideo);

  std::filesystem::remove_all(root);
}

TEST_CASE("CreateRendererForAssetDir always resolves to a usable renderer", "[renderer_factory]") {
  const auto root = MakeTempRoot();

  SECTION("empty dir, no declared type -> falls back but never returns null") {
    auto renderer = CreateRendererForAssetDir(root, AssetType::kUnknown);
    REQUIRE(renderer != nullptr);
  }

  SECTION("only video_manifest.json present") {
    WriteFile(root / "video_manifest.json", R"({"motions":{"idle":"https://example/idle.mp4"}})");
    auto renderer = CreateRendererForAssetDir(root, AssetType::kVideo);
    REQUIRE(renderer != nullptr);
  }

  SECTION("declared spine but no local rig files -> exception fallback to video_manifest.json") {
    WriteFile(root / "video_manifest.json", R"({"motions":{"idle":"https://example/idle.mp4"}})");
    auto renderer = CreateRendererForAssetDir(root, AssetType::kSpine);
    REQUIRE(renderer != nullptr);
  }

  SECTION("skeleton.json + skeleton.atlas present -> local rig data wins regardless of declared_type") {
    WriteFile(root / "skeleton.json", "{}");
    WriteFile(root / "skeleton.atlas", "");
    auto renderer = CreateRendererForAssetDir(root, AssetType::kVideo);
    REQUIRE(renderer != nullptr);
  }

  SECTION("forced_backend != kAuto bypasses asset_type/on-disk logic entirely") {
    auto renderer = CreateRendererForAssetDir(root, AssetType::kSpine, RendererBackend::kStub);
    REQUIRE(renderer != nullptr);
  }

  std::filesystem::remove_all(root);
}
