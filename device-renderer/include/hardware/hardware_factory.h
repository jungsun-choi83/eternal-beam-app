#pragma once

#include <memory>

#include "hardware/hardware_config.h"
#include "hardware/hardware_interface.h"

namespace eb::hardware {

/// Single place that maps `HardwareConfig::board()` to a concrete
/// HardwareInterface implementation. Adding a new board is a two-step
/// change: implement HardwareInterface in a new src/hardware/<board>_impl.cpp
/// file, then add one branch here.
///
/// On non-Linux platforms (Windows/macOS dev machines) this always returns
/// the mock backend regardless of the requested board, so the rest of the
/// app remains buildable and testable without real sensors attached.
std::unique_ptr<HardwareInterface> CreateHardware(const HardwareConfig &config);

}  // namespace eb::hardware
