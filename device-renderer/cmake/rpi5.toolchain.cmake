# Cross-compilation toolchain for Raspberry Pi 5 (ARM Cortex-A76, aarch64).
# Mirrors rk3566.toolchain.cmake — only the env var prefix differs — so both
# boards share the exact same CMake project and source tree.
#
# Usage:
#   cmake -B build-rpi5 \
#         -DCMAKE_TOOLCHAIN_FILE=cmake/rpi5.toolchain.cmake \
#         -DETERNALBEAM_TARGET_BOARD=rpi5
#   cmake --build build-rpi5
#
#   RPI5_TOOLCHAIN_PREFIX (default: aarch64-linux-gnu-)
#   RPI5_SYSROOT          (default: unset -> uses host sysroot)

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

if(NOT DEFINED ENV{RPI5_TOOLCHAIN_PREFIX})
  set(_eb_toolchain_prefix "aarch64-linux-gnu-")
else()
  set(_eb_toolchain_prefix "$ENV{RPI5_TOOLCHAIN_PREFIX}")
endif()

set(CMAKE_C_COMPILER   "${_eb_toolchain_prefix}gcc")
set(CMAKE_CXX_COMPILER "${_eb_toolchain_prefix}g++")

if(DEFINED ENV{RPI5_SYSROOT})
  set(CMAKE_SYSROOT "$ENV{RPI5_SYSROOT}")
  set(CMAKE_FIND_ROOT_PATH "$ENV{RPI5_SYSROOT}")
endif()

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

set(ETERNALBEAM_TARGET_BOARD "rpi5" CACHE STRING "Target board identifier baked into the binary")
