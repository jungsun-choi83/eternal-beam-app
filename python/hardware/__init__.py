"""보드 하드웨어 접근 계층 — GPIO/I2C 하드코딩을 hardware_config.yaml 로 분리.

RPi 전용 CircuitPython(Blinka: board/busio)·RPi.GPIO 대신
Linux 표준 API(smbus2 = /dev/i2c-*, gpiod = /dev/gpiochip*) 를 사용해서
RK3566 등 다른 보드로 이식할 때 이 패키지만 보면 됩니다.
"""

from .config import GpioLineConfig, HardwareConfig, load_hardware_config
from .gpio import GpioLine, GpioUnavailable, open_line
from .i2c_bus import LinuxI2CBus, get_i2c_bus, reset_i2c_bus

__all__ = [
    "GpioLineConfig",
    "HardwareConfig",
    "load_hardware_config",
    "GpioLine",
    "GpioUnavailable",
    "open_line",
    "LinuxI2CBus",
    "get_i2c_bus",
    "reset_i2c_bus",
]
