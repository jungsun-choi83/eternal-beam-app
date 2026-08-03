include(FetchContent)
FetchContent_Declare(
  catch2
  GIT_REPOSITORY https://github.com/catchorg/Catch2.git
  GIT_TAG        v3.6.0
)
FetchContent_MakeAvailable(catch2)

add_executable(eb_tests
  test_hardware_config.cpp
  test_asset_manager.cpp
  test_udp_bridge.cpp
  test_renderer_factory.cpp
)

target_link_libraries(eb_tests
  PRIVATE
    eb_hardware
    eb_renderer
    eb_app
    Catch2::Catch2WithMain
)

# ParseUdpSensorEvent lives in src/hardware/udp_bridge_impl.h, which is
# intentionally not under include/ (it's an implementation detail of one
# HardwareInterface backend) — tests reach in directly rather than
# promoting it to the public API surface just for testability.
target_include_directories(eb_tests PRIVATE ${CMAKE_SOURCE_DIR}/src/hardware)

target_compile_definitions(eb_tests PRIVATE
  ETERNALBEAM_TEST_CONFIG_PATH="${CMAKE_SOURCE_DIR}/config/hardware_config.yaml"
)

eb_apply_warnings(eb_tests)

include(${catch2_SOURCE_DIR}/extras/Catch.cmake)
catch_discover_tests(eb_tests)
