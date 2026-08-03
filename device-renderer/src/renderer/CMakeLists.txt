# eb_renderer — everything behind IPetRenderer. spine_renderer.cpp /
# video_layer_renderer.cpp only build (and only need to compile) when their
# respective backend flag is ON.

set(EB_RENDERER_SOURCES
  asset_type.cpp
  renderer_factory.cpp
  stub_renderer.cpp
)

if(ETERNALBEAM_WITH_SPINE)
  list(APPEND EB_RENDERER_SOURCES spine_renderer.cpp)
endif()

if(ETERNALBEAM_WITH_FFMPEG)
  list(APPEND EB_RENDERER_SOURCES video_layer_renderer.cpp)
endif()

add_library(eb_renderer STATIC ${EB_RENDERER_SOURCES})

target_include_directories(eb_renderer
  PUBLIC ${CMAKE_SOURCE_DIR}/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(eb_renderer PUBLIC nlohmann_json::nlohmann_json)

if(ETERNALBEAM_WITH_SPINE)
  target_compile_definitions(eb_renderer PUBLIC ETERNALBEAM_WITH_SPINE)
  target_link_libraries(eb_renderer PUBLIC spine-cpp)
endif()

if(ETERNALBEAM_WITH_FFMPEG)
  target_compile_definitions(eb_renderer PUBLIC ETERNALBEAM_WITH_FFMPEG)
  target_link_libraries(eb_renderer PRIVATE PkgConfig::FFMPEG)
endif()

eb_apply_warnings(eb_renderer)
