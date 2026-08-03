# eb_display — DrmGlDisplay, the only place in this project allowed to touch
# libdrm/GBM/EGL/GLES directly. Only added to the build when
# ETERNALBEAM_WITH_DRM_GL=ON (see top-level CMakeLists.txt), which itself
# requires CMAKE_SYSTEM_NAME=Linux — this whole subdirectory is unreachable
# from a Windows/macOS dev-machine configure.

add_library(eb_display STATIC drm_gl_display.cpp)

target_include_directories(eb_display
  PUBLIC ${CMAKE_SOURCE_DIR}/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
)

# eb_renderer only for renderer/pet_renderer.h's FrameBuffer type — no link
# dependency on eb_hardware/eb_app, keeping DrmGlDisplay a leaf module main.cpp
# can wire in independently of which IPetRenderer backend is active.
target_link_libraries(eb_display
  PUBLIC
    eb_renderer
    PkgConfig::DRM_GL
)

eb_apply_warnings(eb_display)
