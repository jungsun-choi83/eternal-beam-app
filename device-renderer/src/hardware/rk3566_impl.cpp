#include "rk3566_impl.h"

#include <cstdio>
#include <utility>

namespace eb::hardware {

Rk3566Hardware::Rk3566Hardware(HardwareConfig config)
    : LinuxCommonHardware(std::move(config), "rk3566") {}

bool Rk3566Hardware::OnInitialize() {
  // TODO: RK3566 DRM/KMS bring-up (selecting the correct Rockchip VOP
  // plane/connector for the Pepper's Ghost panel) goes here once the
  // renderer talks to the framebuffer directly instead of through
  // X11/Wayland — see docs/RK3566_이식_가이드.md for the display env
  // variables used in the meantime.
  std::fprintf(stderr, "[hardware:rk3566] board bring-up 완료\n");
  return true;
}

void Rk3566Hardware::OnShutdown() {}

}  // namespace eb::hardware
