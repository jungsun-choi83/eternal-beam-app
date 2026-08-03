# Cross-compilation toolchain for the RK3566 custom board (ARM Cortex-A55,
# aarch64, mainline Linux kernel).
#
# Usage:
#   cmake -B build-rk3566 \
#         -DCMAKE_TOOLCHAIN_FILE=cmake/rk3566.toolchain.cmake \
#         -DETERNALBEAM_TARGET_BOARD=rk3566
#   cmake --build build-rk3566
#
# Override the compiler prefix/sysroot via environment variables if your SDK
# uses a different triplet or lives in a non-default location:
#   RK3566_TOOLCHAIN_PREFIX (default: aarch64-linux-gnu-)
#   RK3566_SYSROOT          (default: unset -> uses host sysroot)

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

if(NOT DEFINED ENV{RK3566_TOOLCHAIN_PREFIX})
  set(_eb_toolchain_prefix "aarch64-linux-gnu-")
else()
  set(_eb_toolchain_prefix "$ENV{RK3566_TOOLCHAIN_PREFIX}")
endif()

set(CMAKE_C_COMPILER   "${_eb_toolchain_prefix}gcc")
set(CMAKE_CXX_COMPILER "${_eb_toolchain_prefix}g++")

if(DEFINED ENV{RK3566_SYSROOT})
  set(CMAKE_SYSROOT "$ENV{RK3566_SYSROOT}")
  set(CMAKE_FIND_ROOT_PATH "$ENV{RK3566_SYSROOT}")
endif()

# Only look for libraries/headers inside the target sysroot; still allow
# CMake itself and host build tools (protoc-like code generators, etc.) to
# resolve from the host.
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

set(ETERNALBEAM_TARGET_BOARD "rk3566" CACHE STRING "Target board identifier baked into the binary")
