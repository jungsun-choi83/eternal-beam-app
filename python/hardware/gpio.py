"""Linux 표준 GPIO 접근 (libgpiod, /dev/gpiochipN) — RPi.GPIO/sysfs GPIO 대체.

python-gpiod v2.x(신규, 공식 순수 파이썬 바인딩) API를 우선 사용하고,
구버전 v1.x 바인딩(gpiod.Chip/get_line)이 설치된 배포판도 지원합니다.
gpiod 패키지가 없거나 라인이 비활성화(enabled: false)면 open_line() 은
None 을 반환하므로, 호출부는 GPIO 없이도(하드웨어 미조립 상태) 항상 동작해야 합니다.
"""

from __future__ import annotations

import logging
from typing import Optional

from .config import GpioLineConfig, HardwareConfig, load_hardware_config

log = logging.getLogger("hardware.gpio")

try:
    import gpiod  # type: ignore

    _GPIOD_V2 = hasattr(gpiod, "request_lines")
except ImportError:  # pragma: no cover
    gpiod = None  # type: ignore
    _GPIOD_V2 = False


class GpioUnavailable(RuntimeError):
    """gpiod 미설치, 라인 offset 미설정, 또는 라인 비활성화."""


class GpioLine:
    """단일 GPIO 라인 — 출력(on/off) 또는 입력(읽기) 한 줄을 다룸."""

    def __init__(self, chip_path: str, cfg: GpioLineConfig):
        if gpiod is None:
            raise GpioUnavailable(
                "gpiod 패키지가 없습니다: pip install gpiod (libgpiod2 시스템 패키지도 필요)"
            )
        if cfg.offset < 0:
            raise GpioUnavailable(f"GPIO 라인 '{cfg.name}' offset 미설정 — hardware_config.yaml 확인")

        self.cfg = cfg
        self._chip_path = chip_path
        self._request = None
        self._chip = None
        self._line = None
        self._open()

    def _open(self) -> None:
        if _GPIOD_V2:
            from gpiod.line import Direction, Edge, LineSettings

            direction = Direction.OUTPUT if self.cfg.direction == "out" else Direction.INPUT
            edge = {
                "rising": Edge.RISING,
                "falling": Edge.FALLING,
                "both": Edge.BOTH,
            }.get(self.cfg.edge or "", Edge.NONE)
            settings = LineSettings(
                direction=direction,
                edge_detection=edge if direction == Direction.INPUT else Edge.NONE,
                active_low=self.cfg.active_low,
            )
            self._request = gpiod.request_lines(
                self._chip_path,
                consumer="eternal-beam",
                config={self.cfg.offset: settings},
            )
        else:  # gpiod v1.x (Chip / get_line 스타일 바인딩)
            self._chip = gpiod.Chip(self._chip_path)
            self._line = self._chip.get_line(self.cfg.offset)
            request_type = (
                gpiod.LINE_REQ_DIR_OUT if self.cfg.direction == "out" else gpiod.LINE_REQ_DIR_IN
            )
            flags = gpiod.LINE_REQ_FLAG_ACTIVE_LOW if self.cfg.active_low else 0
            self._line.request(consumer="eternal-beam", type=request_type, flags=flags)

    def set_value(self, on: bool) -> None:
        if self.cfg.direction != "out":
            raise RuntimeError(f"'{self.cfg.name}' 은 입력 라인입니다.")
        if _GPIOD_V2:
            from gpiod.line import Value

            self._request.set_value(self.cfg.offset, Value.ACTIVE if on else Value.INACTIVE)
        else:
            self._line.set_value(1 if on else 0)

    def get_value(self) -> bool:
        if _GPIOD_V2:
            from gpiod.line import Value

            return self._request.get_value(self.cfg.offset) == Value.ACTIVE
        return bool(self._line.get_value())

    def close(self) -> None:
        try:
            if _GPIOD_V2 and self._request is not None:
                self._request.release()
            elif self._line is not None:
                self._line.release()
        except Exception:
            pass
        finally:
            self._request = None
            self._line = None
            self._chip = None

    def __enter__(self) -> "GpioLine":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_line(name: str, cfg: HardwareConfig | None = None) -> Optional[GpioLine]:
    """hardware_config.yaml 의 gpio.lines.<name> 으로 라인을 연다.

    비활성화(enabled: false)이거나 gpiod/하드웨어가 없으면 None 을 반환한다.
    호출부는 반드시 None 을 허용해서, GPIO 없이도(개발 PC, 미조립 보드) 동작해야 한다.
    """
    cfg = cfg or load_hardware_config()
    line_cfg = cfg.gpio_line(name)
    if not line_cfg.enabled:
        return None

    chip = cfg.gpio_chip
    chip_path = chip if chip.startswith("/") else f"/dev/{chip}"
    try:
        return GpioLine(chip_path, line_cfg)
    except GpioUnavailable as e:
        log.warning("[GPIO] '%s' 비활성화: %s", name, e)
        return None
