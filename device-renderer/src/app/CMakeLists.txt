# eb_app — ties eb_hardware and eb_renderer together (AppController,
# AssetManager, DeviceSyncClient). Never includes gpiod/i2c-dev/spine/ffmpeg
# headers directly.

set(EB_APP_SOURCES
  app_controller.cpp
  asset_manager.cpp
)

if(ETERNALBEAM_WITH_CURL)
  list(APPEND EB_APP_SOURCES device_sync_client.cpp)
endif()

add_library(eb_app STATIC ${EB_APP_SOURCES})

target_include_directories(eb_app
  PUBLIC ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(eb_app
  PUBLIC
    eb_hardware
    eb_renderer
    nlohmann_json::nlohmann_json
)

if(ETERNALBEAM_WITH_CURL)
  target_compile_definitions(eb_app PUBLIC ETERNALBEAM_WITH_CURL)
  target_link_libraries(eb_app PRIVATE CURL::libcurl)
endif()

eb_apply_warnings(eb_app)
