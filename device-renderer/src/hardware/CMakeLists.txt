# eb_hardware — everything behind HardwareInterface. Only linux_common_hardware
# / rk3566_impl / rpi5_impl may `#include <gpiod.h>` or `<linux/i2c-dev.h>`;
# udp_bridge_impl is intentionally cross-platform (plain BSD/Winsock sockets)
# so it also builds on the Windows/macOS dev machine.

set(EB_HARDWARE_SOURCES
  hardware_config.cpp
  hardware_factory.cpp
  sensor_event.cpp
  mock_impl.cpp
  udp_bridge_impl.cpp
)

if(ETERNALBEAM_HAS_LINUX_HARDWARE)
  list(APPEND EB_HARDWARE_SOURCES
    linux_common_hardware.cpp
    rk3566_impl.cpp
    rpi5_impl.cpp
    vl53l0x_touch_thread.cpp
  )
endif()

add_library(eb_hardware STATIC ${EB_HARDWARE_SOURCES})

target_include_directories(eb_hardware
  PUBLIC ${CMAKE_SOURCE_DIR}/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(eb_hardware PUBLIC ${EB_YAML_CPP_TARGET} nlohmann_json::nlohmann_json)

if(WIN32)
  target_link_libraries(eb_hardware PRIVATE ws2_32)
endif()

if(ETERNALBEAM_HAS_LINUX_HARDWARE)
  target_compile_definitions(eb_hardware PUBLIC ETERNALBEAM_HAS_LINUX_HARDWARE)
  if(GPIOD_FOUND)
    target_include_directories(eb_hardware PRIVATE ${GPIOD_INCLUDE_DIRS})
    target_link_libraries(eb_hardware PRIVATE ${GPIOD_LIBRARIES})
  else()
    # Falls back to -lgpiod so a manual toolchain (no pkg-config data) still
    # links, as long as libgpiod.so is on the sysroot's library search path.
    target_link_libraries(eb_hardware PRIVATE gpiod)
  endif()
endif()

eb_apply_warnings(eb_hardware)
