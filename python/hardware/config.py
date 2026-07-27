"""hardware_config.yaml 로더 — 보드별 GPIO/I2C/오디오/네트워크 설정.

환경변수:
  HARDWARE_CONFIG   설정 파일 경로 (기본: python/hardware_config.yaml)
  HARDWARE_BOARD    active_board override (예: rk3566)
  HARDWARE_I2C_BUS  i2c.bus override (예: 3)
  HARDWARE_GPIO_CHIP  gpio.chip override (예: gpiochip1)
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "PyYAML이 필요합니다: pip install pyyaml (requirements-pi.txt 참고)"
    ) from exc

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "hardware_config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """override 가 base 를 덮어씀. 둘 다 dict인 키는 재귀적으로 병합."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class GpioLineConfig:
    """hardware_config.yaml 의 gpio.lines.<name> 항목."""

    name: str
    enabled: bool = False
    offset: int = -1
    direction: str = "out"  # "in" | "out"
    active_low: bool = False
    edge: str | None = None  # None | "rising" | "falling" | "both"


@dataclass
class HardwareConfig:
    board: str
    label: str
    config_path: Path
    raw: dict[str, Any] = field(repr=False)

    # --- I2C ---
    @property
    def i2c_bus(self) -> int:
        return int(os.getenv("HARDWARE_I2C_BUS", self.raw.get("i2c", {}).get("bus", 1)))

    @property
    def vl53l0x_address(self) -> int:
        return int(self.raw.get("i2c", {}).get("vl53l0x_address", 0x29))

    @property
    def pn532_address(self) -> int:
        return int(self.raw.get("i2c", {}).get("pn532_address", 0x24))

    # --- GPIO ---
    @property
    def gpio_chip(self) -> str:
        return os.getenv("HARDWARE_GPIO_CHIP", self.raw.get("gpio", {}).get("chip", "gpiochip0"))

    def gpio_line(self, name: str) -> GpioLineConfig:
        raw_line = self.raw.get("gpio", {}).get("lines", {}).get(name, {}) or {}
        return GpioLineConfig(
            name=name,
            enabled=bool(raw_line.get("enabled", False)),
            offset=int(raw_line.get("offset", -1)),
            direction=str(raw_line.get("direction", "out")),
            active_low=bool(raw_line.get("active_low", False)),
            edge=raw_line.get("edge"),
        )

    # --- Audio ---
    @property
    def alsa_card(self) -> str:
        return os.getenv("VOICE_ALSA_CARD", str(self.raw.get("audio", {}).get("alsa_card", "0")))

    # --- Display (mpv/omxplayer 실행 환경) ---
    @property
    def display_env(self) -> dict[str, str]:
        return dict(self.raw.get("display", {}).get("env", {}) or {})

    # --- 나머지 공통 설정(distance/nfc/voice/network/player)은 dotted-path 로 조회 ---
    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


_cached_config: HardwareConfig | None = None


def load_hardware_config(path: str | Path | None = None, *, force_reload: bool = False) -> HardwareConfig:
    """hardware_config.yaml 을 읽고 active_board 설정을 병합해서 반환 (기본 경로는 캐시됨)."""
    global _cached_config
    if _cached_config is not None and not force_reload and path is None:
        return _cached_config

    config_path = Path(path or os.getenv("HARDWARE_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise FileNotFoundError(
            f"hardware_config.yaml 을 찾을 수 없습니다: {config_path}\n"
            "  python/hardware_config.yaml 이 있는지 확인하거나 "
            "HARDWARE_CONFIG 환경변수로 경로를 지정하세요."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}

    boards: dict[str, Any] = doc.get("boards", {}) or {}
    board = os.getenv("HARDWARE_BOARD", str(doc.get("active_board", "")).strip()).strip()
    if not board:
        raise ValueError(f"{config_path}: active_board 가 비어 있습니다.")
    if board not in boards:
        raise ValueError(
            f"{config_path}: board '{board}' 가 boards: 아래 없습니다. "
            f"사용 가능: {list(boards)}"
        )

    common: dict[str, Any] = doc.get("common", {}) or {}
    merged = _deep_merge(common, boards[board])

    cfg = HardwareConfig(
        board=board,
        label=str(boards[board].get("label", board)),
        config_path=config_path,
        raw=merged,
    )
    if path is None:
        _cached_config = cfg
    return cfg
