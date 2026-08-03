"""Linux 표준 I2C 접근 (smbus2, /dev/i2c-N) — RPi 전용 Blinka(board/busio) 대체.

adafruit-circuitpython-vl53l0x / adafruit-circuitpython-pn532 같은 드라이버는
내부적으로 adafruit_bus_device.I2CDevice 를 쓰는데, 이 클래스는 busio.I2C 와
동일한 메서드(try_lock/unlock/writeto/readfrom_into/scan)만 있으면 동작합니다.
CircuitPython(Blinka)이 아니어도 되므로, 여기서는 smbus2(표준 Linux i2c-dev,
ioctl I2C_RDWR)로 같은 인터페이스를 구현해 기존 Adafruit 드라이버를 그대로
재사용합니다 — RPi5든 RK3566이든 /dev/i2c-<bus> 만 다르면 됩니다.
"""

from __future__ import annotations

import threading

from .config import HardwareConfig, load_hardware_config

# smbus2는 사용하는 시점(LinuxI2CBus 생성)에만 import — hardware_config.yaml 만 읽는
# 코드(예: PC 개발 환경, 아직 패키지 미설치 상태)는 이 모듈을 import 해도 에러가 나지 않는다.
try:
    import smbus2

    _SMBUS2_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover
    smbus2 = None  # type: ignore
    _SMBUS2_IMPORT_ERROR = exc


class LinuxI2CBus:
    """busio.I2C 호환 shim — /dev/i2c-<bus_number> (표준 Linux i2c-dev)."""

    def __init__(self, bus_number: int):
        if smbus2 is None:
            raise RuntimeError(
                "smbus2가 필요합니다: pip install smbus2 (requirements-pi.txt 참고)"
            ) from _SMBUS2_IMPORT_ERROR
        self.bus_number = bus_number
        self._bus = smbus2.SMBus(bus_number)
        self._lock = threading.Lock()

    def try_lock(self) -> bool:
        self._lock.acquire(blocking=True)
        return True

    def unlock(self) -> None:
        if self._lock.locked():
            self._lock.release()

    def readfrom(self, address: int, nbytes: int) -> bytes:
        buf = bytearray(nbytes)
        self.readfrom_into(address, buf)
        return bytes(buf)

    def scan(self) -> list[int]:
        found: list[int] = []
        for addr in range(0x03, 0x78):
            try:
                self._bus.write_quick(addr)
                found.append(addr)
            except OSError:
                continue
        return found

    def writeto(self, address: int, buffer, *, start: int = 0, end: int | None = None) -> None:
        stop = end if end is not None else len(buffer)
        msg = smbus2.i2c_msg.write(address, bytes(buffer[start:stop]))
        self._bus.i2c_rdwr(msg)

    def readfrom_into(self, address: int, buffer, *, start: int = 0, end: int | None = None) -> None:
        stop = end if end is not None else len(buffer)
        length = stop - start
        msg = smbus2.i2c_msg.read(address, length)
        self._bus.i2c_rdwr(msg)
        buffer[start:stop] = bytes(msg)

    def writeto_then_readfrom(
        self,
        address: int,
        buffer_out,
        buffer_in,
        *,
        out_start: int = 0,
        out_end: int | None = None,
        in_start: int = 0,
        in_end: int | None = None,
    ) -> None:
        out_stop = out_end if out_end is not None else len(buffer_out)
        in_stop = in_end if in_end is not None else len(buffer_in)
        in_len = in_stop - in_start
        write_msg = smbus2.i2c_msg.write(address, bytes(buffer_out[out_start:out_stop]))
        read_msg = smbus2.i2c_msg.read(address, in_len)
        self._bus.i2c_rdwr(write_msg, read_msg)
        buffer_in[in_start:in_stop] = bytes(read_msg)

    def deinit(self) -> None:
        try:
            self._bus.close()
        except Exception:
            pass


_bus_lock = threading.Lock()
_bus_singleton: LinuxI2CBus | None = None


def get_i2c_bus(cfg: HardwareConfig | None = None) -> LinuxI2CBus:
    """hardware_config.yaml 의 i2c.bus 번호로 연 공유 인스턴스를 반환 (VL53L0X/PN532 공유 버스)."""
    global _bus_singleton
    with _bus_lock:
        if _bus_singleton is None:
            cfg = cfg or load_hardware_config()
            _bus_singleton = LinuxI2CBus(cfg.i2c_bus)
        return _bus_singleton


def reset_i2c_bus() -> None:
    """버스를 닫고 캐시를 지움 — 다음 get_i2c_bus() 호출 시 새로 연다 (재시도/테스트용)."""
    global _bus_singleton
    with _bus_lock:
        if _bus_singleton is not None:
            _bus_singleton.deinit()
        _bus_singleton = None
