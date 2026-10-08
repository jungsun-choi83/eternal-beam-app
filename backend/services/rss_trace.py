"""
누끼 경로 **임시** RSS 추적 — 2GB Render 워커 안에서 ViTMatte 의 실제 메모리
프로파일을 재기 위한 것.

로컬(macOS)에서는 ViTMatte 디코더가 oneDNN 없이 im2col 로 돌아 메모리가 부풀어
Render(Linux, oneDNN) 수치를 대신하지 못한다. 그래서 프로덕션 경로 자체에
가벼운 측정점을 두고 로그로 읽는다. 측정만 하고 동작은 바꾸지 않는다.

  CUTOUT_RSS_TRACE   "1"(기본) | "0" — 끄면 모든 mark 가 즉시 반환한다.

로그 형식 (grep '[RSS-TRACE]'):
  [RSS-TRACE] cutout=<id> point=<name> rss_mb=<현재 RSS> maxrss_mb=<프로세스 수명 최대> <추가 필드>

RSS 읽기 순서: /proc/self/status(VmRSS, Linux) → psutil → resource.ru_maxrss(피크만).
"""

from __future__ import annotations

import contextvars
import logging
import os
import resource
import sys
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENABLED = os.getenv("CUTOUT_RSS_TRACE", "1").strip().lower() in ("1", "true", "yes")

_current: contextvars.ContextVar[Optional["Tracer"]] = contextvars.ContextVar("rss_tracer", default=None)


def read_rss_mb() -> Optional[float]:
    """현재 프로세스 RSS(MB). 알 수 없으면 None."""
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)  # kB → MB
    except OSError:
        pass
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 2**20, 1)
    except Exception:  # noqa: BLE001
        return None


def read_maxrss_mb() -> Optional[float]:
    """프로세스 수명 최대 RSS(MB). Linux 는 kB, macOS 는 bytes 단위."""
    try:
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001
        return None
    return round(raw / 2**20, 1) if sys.platform == "darwin" else round(raw / 1024.0, 1)


class _PeakSampler:
    """짧은 구간(디코더 forward)의 RSS 피크를 잡는 샘플러 스레드."""

    def __init__(self, interval_s: float = 0.002):
        self.interval = interval_s
        self.peak: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.peak = read_rss_mb()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rss-peak-sampler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            r = read_rss_mb()
            if r is not None and (self.peak is None or r > self.peak):
                self.peak = r
            time.sleep(self.interval)

    def stop(self) -> Optional[float]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        r = read_rss_mb()
        if r is not None and (self.peak is None or r > self.peak):
            self.peak = r
        return self.peak


class Tracer:
    """한 번의 누끼 호출에 대한 측정점 모음. 비활성이면 전부 no-op."""

    def __init__(self, tag: str = "cutout", enabled: Optional[bool] = None):
        self.enabled = ENABLED if enabled is None else bool(enabled)
        self.id = uuid.uuid4().hex[:8]
        self.tag = tag
        self.points: list[dict[str, Any]] = []
        self._t0 = time.monotonic()
        self._sampler: Optional[_PeakSampler] = None

    def mark(self, point: str, **fields: Any) -> None:
        if not self.enabled:
            return
        rec = {
            "point": point,
            "t_ms": round((time.monotonic() - self._t0) * 1000.0),
            "rss_mb": read_rss_mb(),
            "maxrss_mb": read_maxrss_mb(),
        }
        rec.update(fields)
        self.points.append(rec)
        extras = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info(
            "[RSS-TRACE] %s=%s point=%s rss_mb=%s maxrss_mb=%s t_ms=%s %s",
            self.tag,
            self.id,
            point,
            rec["rss_mb"],
            rec["maxrss_mb"],
            rec["t_ms"],
            extras,
        )

    def start_peak_window(self) -> None:
        if not self.enabled:
            return
        self._sampler = _PeakSampler()
        self._sampler.start()

    def end_peak_window(self) -> Optional[float]:
        if not self.enabled or self._sampler is None:
            return None
        peak = self._sampler.stop()
        self._sampler = None
        return peak

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "id": self.id, "points": list(self.points)}

    # ── 현재 컨텍스트 (하위 함수가 시그니처 변경 없이 tracer 를 찾게) ──
    def __enter__(self) -> "Tracer":
        self._token = _current.set(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._sampler is not None:
            self.end_peak_window()
        _current.reset(self._token)


def current() -> Optional[Tracer]:
    return _current.get()
