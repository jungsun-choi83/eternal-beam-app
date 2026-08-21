"""
누끼 파이프라인 테스트 공용 픽스처.

무거운 모델(SAM2 / ViTMatte / YOLO)은 기본 유닛 테스트에서 전부 목업한다 —
가중치 없이 CI/로컬에서 그대로 돌아가야 하기 때문. 실제 가중치를 쓰는 통합
테스트는 `-m integration` 으로 따로 실행한다(README 참고).
"""

from __future__ import annotations

import io
import os
import sys

import anyio
import httpx
import numpy as np
import pytest
from PIL import Image

# 저장소 루트를 import 경로에 넣어 `backend.*` 로 임포트할 수 있게 한다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: 실제 모델 가중치가 필요한 테스트 (기본 실행에서 제외)",
    )


@pytest.fixture(autouse=True)
def _clean_cutout_env(monkeypatch: pytest.MonkeyPatch):
    """임계값 관련 환경변수가 로컬 .env 에서 새어 들어오지 않도록 초기화."""
    for key in (
        "CUTOUT_MIN_MASK_AREA_FRACTION",
        "CUTOUT_MAX_MASK_AREA_FRACTION",
        "CUTOUT_RECTANGLE_FILL_RATIO",
        "CUTOUT_REJECT_RECTANGLE_LIKE",
        "CUTOUT_DEBUG_ENABLED",
        "PET_PREPROCESS_ALLOW_FULLFRAME_FALLBACK",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


class ASGITestClient:
    """
    최소한의 동기 테스트 클라이언트.

    starlette 0.36 의 TestClient 는 httpx.Client(app=...) 를 호출하는데, httpx
    0.28 에서 그 인자가 제거돼 TypeError 가 난다. 의존성 버전을 건드리지 않고
    httpx.ASGITransport 를 직접 쓰는 편이 안전하다.
    """

    def __init__(self, app, *, base_url: str = "http://testserver"):
        self.app = app
        self.base_url = base_url
        self.uploads: list[str] = []
        self.calls: dict[str, int] = {}

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async def _go() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url=self.base_url
            ) as client:
                return await client.request(method, url, **kwargs)

        return anyio.run(_go)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> httpx.Response:
        return self.request("PUT", url, **kwargs)


def follow_shaker_asset(client: "ASGITestClient", url: str | None) -> str:
    """
    Shaker 재생 URL 의 **실제 대상**.

    공개 응답은 기본적으로 `/api/v1/shaker/asset?...` 프록시 경로를 싣는다 —
    스토리지 객체 경로에 고객 이메일이 들어 있어 그대로 노출할 수 없기 때문이다.
    테스트는 302 Location 을 따라가 최종 URL 을 본다. 프록시를 끈 설정에서는
    값이 이미 최종 URL 이므로 그대로 돌려준다.
    """
    v = (url or "").strip()
    if not v.startswith("/api/v1/shaker/asset"):
        return v
    return client.get(v).headers.get("location", "")


def make_rgb_image(width: int = 128, height: int = 96, color=(120, 90, 60)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def make_jpeg_bytes(width: int = 128, height: int = 96) -> bytes:
    buf = io.BytesIO()
    make_rgb_image(width, height).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def make_rgba_png_bytes(alpha_fraction: float, width: int = 100, height: int = 100) -> bytes:
    """alpha_fraction 비율만큼 불투명한 RGBA PNG."""
    rgb = np.full((height, width, 3), 128, dtype=np.uint8)
    alpha = np.zeros((height, width), dtype=np.uint8)
    rows = int(round(height * alpha_fraction))
    if rows > 0:
        alpha[:rows, :] = 255
    rgba = np.dstack([rgb, alpha])
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def blob_mask(
    height: int,
    width: int,
    *,
    area_fraction: float,
    rectangle: bool = False,
) -> np.ndarray:
    """
    0/255 이진 마스크 생성.

    rectangle=True  → 꽉 찬 직사각형 (bbox fill ratio ≈ 1.0)
    rectangle=False → 타원 (bbox fill ratio ≈ pi/4 ≈ 0.785)
    """
    mask = np.zeros((height, width), dtype=np.uint8)
    target = area_fraction * height * width
    if target <= 0:
        return mask

    if rectangle:
        # 전체 너비 × 비례 높이 밴드 → 면적은 정확히 area_fraction, fill ratio = 1.0
        rows = max(1, min(height, int(round(height * area_fraction))))
        y0 = (height - rows) // 2
        mask[y0 : y0 + rows, :] = 255
        return mask

    # 타원 면적 = pi * a * b  → 정사각 비율로 반지름 산출
    radius = np.sqrt(target / np.pi)
    ry = min(radius, height / 2.0 - 1)
    rx = min(radius, width / 2.0 - 1)
    yy, xx = np.mgrid[0:height, 0:width]
    cy, cx = height / 2.0, width / 2.0
    inside = ((yy - cy) / max(ry, 1e-6)) ** 2 + ((xx - cx) / max(rx, 1e-6)) ** 2 <= 1.0
    mask[inside] = 255
    return mask


class FakeBoxes:
    """ultralytics Boxes 의 최소 인터페이스 (cls / conf / xyxy)."""

    def __init__(self, entries: list[tuple[int, float, tuple[float, float, float, float]]]):
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def cls(self):
        return [_Scalar(e[0]) for e in self._entries]

    @property
    def conf(self):
        return [_Scalar(e[1]) for e in self._entries]

    @property
    def xyxy(self):
        return [_Tensorish(np.array(e[2], dtype=np.float32)) for e in self._entries]


class _Scalar(float):
    def item(self):  # pragma: no cover - 편의용
        return float(self)


class _Tensorish:
    def __init__(self, arr: np.ndarray):
        self._arr = arr

    def cpu(self):
        return self

    def numpy(self):
        return self._arr

    def __iter__(self):
        return iter(self._arr)


class FakeResult:
    def __init__(self, boxes: FakeBoxes | None):
        self.boxes = boxes


class FakeYolo:
    """predict() 호출 인자를 기록하는 목업 YOLO."""

    names = {16: "dog", 15: "cat"}

    def __init__(self, entries=None):
        self.entries = entries if entries is not None else []
        self.calls: list[dict] = []

    def predict(self, source=None, **kwargs):
        self.calls.append({"source": source, **kwargs})
        return [FakeResult(FakeBoxes(self.entries) if self.entries else FakeBoxes([]))]
