cmake_minimum_required(VERSION 3.16)
project(EternalBeamDevice LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin)
list(APPEND CMAKE_MODULE_PATH ${CMAKE_SOURCE_DIR}/cmake)
include(CompilerWarnings)

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
# Which physical board this binary targets. This only selects the default
# hardware_config.yaml lookup and a compile-time string baked into the
# binary for logging — actual GPIO/I2C addressing always comes from the
# YAML config, so switching boards should never require touching this file.
set(ETERNALBEAM_TARGET_BOARD "auto" CACHE STRING
    "Target board identifier: auto | rpi5 | rk3566 | mock")

option(ETERNALBEAM_WITH_SPINE
       "Build the real Spine-CPP renderer (requires libs/ to be vendored, see libs/spine-cpp/README.md)"
       OFF)
option(ETERNALBEAM_WITH_FFMPEG
       "Build VideoLayerRenderer (the C++ port of Unity's VideoLayer.cs), which decodes per-action MP4s via FFmpeg"
       OFF)
option(ETERNALBEAM_WITH_CURL
       "Build HttpDeviceSyncClient, which calls the existing GET /v1/device/sync endpoint via libcurl"
       OFF)
option(ETERNALBEAM_WITH_DRM_GL
       "Build DrmGlDisplay, which pushes the composited FrameBuffer to a real HDMI/DSI panel via libdrm+GBM+EGL/GLES (no X11/Wayland). Linux + Mesa-capable GPU driver only"
       OFF)
option(ETERNALBEAM_BUILD_TESTS "Build the tests/ suite" OFF)
option(ETERNALBEAM_FETCH_YAML_CPP
       "Fetch yaml-cpp via FetchContent when it isn't found on the system"
       ON)

message(STATUS "EternalBeamDevice: target board = ${ETERNALBEAM_TARGET_BOARD}")
message(STATUS "EternalBeamDevice: Spine-CPP renderer (skeletal) = ${ETERNALBEAM_WITH_SPINE}")
message(STATUS "EternalBeamDevice: VideoLayerRenderer (FFmpeg)   = ${ETERNALBEAM_WITH_FFMPEG}")
message(STATUS "EternalBeamDevice: HTTP device-sync client (curl) = ${ETERNALBEAM_WITH_CURL}")
message(STATUS "EternalBeamDevice: DRM/KMS+EGL display output      = ${ETERNALBEAM_WITH_DRM_GL}")

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
find_package(Threads REQUIRED)

find_package(yaml-cpp QUIET CONFIG)
if(NOT yaml-cpp_FOUND AND ETERNALBEAM_FETCH_YAML_CPP)
  include(FetchContent)
  set(YAML_CPP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
  set(YAML_CPP_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
  set(YAML_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(
    yaml-cpp
    GIT_REPOSITORY https://github.com/jbeder/yaml-cpp.git
    GIT_TAG        0.8.0
  )
  FetchContent_MakeAvailable(yaml-cpp)
elseif(NOT yaml-cpp_FOUND)
  message(FATAL_ERROR
    "yaml-cpp not found and ETERNALBEAM_FETCH_YAML_CPP=OFF. "
    "Install yaml-cpp on the sysroot or re-enable fetching.")
endif()

# yaml-cpp >= 0.7 exports the `yaml-cpp::yaml-cpp` ALIAS target; older
# releases (and some FetchContent-built trees) only define the bare
# `yaml-cpp` target. Resolve once here so every subdirectory can link
# against a single, always-correct ${EB_YAML_CPP_TARGET} name.
if(TARGET yaml-cpp::yaml-cpp)
  set(EB_YAML_CPP_TARGET yaml-cpp::yaml-cpp)
else()
  set(EB_YAML_CPP_TARGET yaml-cpp)
endif()

# assets/manifest.json is small and fully owned by AssetManager, but it's
# still real JSON parsed/written from untrusted-ish disk state — use
# nlohmann/json (header-only, MIT) instead of a hand-rolled parser.
find_package(nlohmann_json QUIET CONFIG)
if(NOT nlohmann_json_FOUND)
  include(FetchContent)
  FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
  )
  set(JSON_BuildTests OFF CACHE INTERNAL "")
  FetchContent_MakeAvailable(nlohmann_json)
endif()

# Real Linux hardware backends (rk3566_impl.cpp / rpi5_impl.cpp) only build
# on Linux targets; other platforms (Windows/macOS dev machines) fall back
# to the mock backend so the rest of the app is still buildable/testable.
set(ETERNALBEAM_HAS_LINUX_HARDWARE OFF)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(ETERNALBEAM_HAS_LINUX_HARDWARE ON)
  find_package(PkgConfig)
  if(PkgConfig_FOUND)
    pkg_check_modules(GPIOD libgpiod)
  endif()
  if(NOT GPIOD_FOUND)
    message(WARNING
      "libgpiod not found via pkg-config — rk3566_impl/rpi5_impl will still "
      "compile but GPIO calls will fail at runtime until libgpiod-dev is "
      "installed on the target sysroot.")
  endif()
endif()

if(ETERNALBEAM_WITH_SPINE)
  if(NOT DEFINED SPINE_CPP_SOURCE_DIR)
    message(FATAL_ERROR
      "ETERNALBEAM_WITH_SPINE=ON requires -DSPINE_CPP_SOURCE_DIR=<path to "
      "vendored spine-cpp/spine-cpp>. See libs/spine-cpp/README.md.")
  endif()
  add_subdirectory(${SPINE_CPP_SOURCE_DIR} spine-cpp-build)
endif()

# VideoLayerRenderer (src/renderer/video_layer_renderer.cpp) — needs libav{format,codec,util}
# + libswscale. These ship as system dev packages on every target board
# (apt install ffmpeg libavformat-dev libavcodec-dev libswscale-dev
# libavutil-dev) — not vendored via FetchContent since building FFmpeg from
# source is its own multi-hour undertaking outside this project's scope.
if(ETERNALBEAM_WITH_FFMPEG)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET
    libavformat
    libavcodec
    libswscale
    libavutil
  )
endif()

# HttpDeviceSyncClient (src/app/device_sync_client.cpp) — same
# GET /v1/device/sync endpoint Unity already calls; libcurl is the standard
# choice for HTTPS support without vendoring a TLS stack ourselves.
if(ETERNALBEAM_WITH_CURL)
  find_package(CURL REQUIRED)
endif()

# DrmGlDisplay (src/display/drm_gl_display.cpp) — talks directly to
# libdrm (KMS modesetting), libgbm (scanout-capable render targets) and
# EGL/GLESv2 (drawing the FrameBuffer as a textured quad). Requires a
# Mesa-GBM-capable GPU driver on the target sysroot (Panfrost/lima for
# Mali, or the vendor's own libmali.so if it implements EGL_KHR_platform_gbm)
# — apt install libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev.
if(ETERNALBEAM_WITH_DRM_GL)
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "ETERNALBEAM_WITH_DRM_GL=ON requires a Linux target (DRM/KMS is Linux-only).")
  endif()
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(DRM_GL REQUIRED IMPORTED_TARGET
    libdrm
    gbm
    egl
    glesv2
  )
endif()

# ---------------------------------------------------------------------------
# Subprojects
# ---------------------------------------------------------------------------
add_subdirectory(src/hardware)
add_subdirectory(src/renderer)
add_subdirectory(src/app)
if(ETERNALBEAM_WITH_DRM_GL)
  add_subdirectory(src/display)
endif()

add_executable(eternal_beam_device src/main.cpp)
target_link_libraries(eternal_beam_device
  PRIVATE
    eb_hardware
    eb_renderer
    eb_app
    Threads::Threads
)
if(ETERNALBEAM_WITH_DRM_GL)
  target_link_libraries(eternal_beam_device PRIVATE eb_display)
  target_compile_definitions(eternal_beam_device PRIVATE ETERNALBEAM_WITH_DRM_GL)
endif()
eb_apply_warnings(eternal_beam_device)

install(TARGETS eternal_beam_device RUNTIME DESTINATION bin)
install(DIRECTORY assets/ DESTINATION share/eternal-beam-device/assets
        FILES_MATCHING PATTERN "README.md" PATTERN ".gitkeep")

if(ETERNALBEAM_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
