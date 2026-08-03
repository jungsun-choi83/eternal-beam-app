#include "rpi5_impl.h"

#include <cstdio>
#include <utility>

namespace eb::hardware {

Rpi5Hardware::Rpi5Hardware(HardwareConfig config)
    : LinuxCommonHardware(std::move(config), "rpi5") {}

bool Rpi5Hardware::OnInitialize() {
  // Pi 5 routes GPIO through the RP1 southbridge, which the kernel already
  // maps transparently to gpiochip0 — nothing extra to bring up here beyond
  // what LinuxCommonHardware::Initialize() already does.
  std::fprintf(stderr, "[hardware:rpi5] board bring-up 완료\n");
  return true;
}

void Rpi5Hardware::OnShutdown() {}

}  // namespace eb::hardware
