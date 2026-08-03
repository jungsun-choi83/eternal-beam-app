#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "app/asset_manager.h"

using eb::app::AssetManager;

namespace {

std::filesystem::path MakeTempRoot() {
  const auto root = std::filesystem::temp_directory_path() /
                     ("eb_asset_manager_test_" + std::to_string(std::random_device{}()));
  std::filesystem::create_directories(root);
  return root;
}

void WriteFile(const std::filesystem::path &path, const std::string &content) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream out(path);
  out << content;
}

}  // namespace

TEST_CASE("EnsureLocalAssets adopts pre-seeded Spine files without a sync client", "[asset_manager]") {
  const auto root = MakeTempRoot();
  WriteFile(root / "petA" / "placeA" / "skeleton.json", "{}");
  WriteFile(root / "petA" / "placeA" / "skeleton.atlas", "");

  AssetManager manager(root);
  REQUIRE(manager.LoadManifest());

  const auto dir = manager.EnsureLocalAssets("petA", "placeA");
  REQUIRE(dir.has_value());
  CHECK(std::filesystem::equivalent(*dir, root / "petA" / "placeA"));
  CHECK(std::filesystem::exists(*dir / "skeleton.json"));

  // The adopted entry should now be persisted, so a second manager instance
  // (simulating a process restart) finds it without re-scanning the disk.
  AssetManager reloaded(root);
  REQUIRE(reloaded.LoadManifest());
  const auto reloaded_dir = reloaded.EnsureLocalAssets("petA", "placeA");
  REQUIRE(reloaded_dir.has_value());

  std::filesystem::remove_all(root);
}

TEST_CASE("EnsureLocalAssets adopts pre-seeded VideoLayerRenderer files too (renderer-agnostic)", "[asset_manager]") {
  const auto root = MakeTempRoot();
  WriteFile(root / "petB" / "placeB" / "video_manifest.json", R"({"motions":{"idle":"https://example/idle.mp4"}})");

  AssetManager manager(root);
  REQUIRE(manager.LoadManifest());

  const auto dir = manager.EnsureLocalAssets("petB", "placeB");
  REQUIRE(dir.has_value());
  CHECK(std::filesystem::exists(*dir / "video_manifest.json"));

  std::filesystem::remove_all(root);
}

TEST_CASE("EnsureLocalAssets returns nullopt when nothing is available", "[asset_manager]") {
  const auto root = MakeTempRoot();

  AssetManager manager(root);
  REQUIRE(manager.LoadManifest());

  const auto dir = manager.EnsureLocalAssets("unknown_pet", "unknown_place");
  CHECK_FALSE(dir.has_value());

  std::filesystem::remove_all(root);
}
