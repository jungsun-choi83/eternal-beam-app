"""
클린 플레이트 (Clean Plate) — 하류 생성 입력에서 배경/그림자 잔재를 끊는다.

── 왜 필요한가 (감사 결과) ─────────────────────────────────────────────────
Phase 4 정본과 Phase 5 키프레임은 **raw**(프로바이더 원본)를 그대로 하류에
먹였다. raw 에는 모델이 그려 넣은 접지(contact)/투영(cast) 그림자와 벽→바닥
그라디언트가 남아 있고, 그 픽셀이 키프레임 → 모션 → packed-alpha 매트까지
그대로 전파됐다 (발밑 얼룩 / 회색 바닥 슬래브). 누끼(cutout)는 이미 있었지만
**보조 자산**이었을 뿐 생성 입력이 아니었다.

── 계약 ────────────────────────────────────────────────────────────────────
  * 클린 플레이트 = 기존 누끼의 펫 전경 + **고정 중립 배경**(단일 평탄색).
  * 출력은 불투명 RGB PNG 다. 프로바이더가 알파를 이해하지 못해도 배경은
    계약된 중립색이고, video_anchor 의 테두리 중앙값 패딩과 정확히 이어진다.
  * raw 는 파괴하지 않는다 — 생성 증거이자 QA/계보의 근거로 남는다.
    플레이트는 그 **파생물**이다 (DERIVED / GENERATED 로 대장 기록).
  * 버전드: CLEAN_PLATE_VERSION 이 바뀌면 다른 파생물이다.
  * 신원/형태를 건드리지 않는다 — 리샘플·리터치·침식 없음. 합성뿐이다.
    (알파 자체의 그림자 제거는 vitmatte_service.suppress_cast_shadow_alpha
     가 담당한다 — 여기서는 이미 만들어진 알파를 믿는다.)
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
from types import SimpleNamespace
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

CLEAN_PLATE_VERSION = "clean-plate-v1"

#: 대장 기록용 파생/생성 종류. raw/cutout 과 절대 같은 이름을 쓰지 않는다.
GENERATED_KIND_CANONICAL_PLATE = "canonical_plate"
GENERATED_KIND_KEYFRAME_PLATE = "keyframe_plate"

#: 기본 중립 배경 — 프롬프트의 "plain solid neutral light-gray" 와 같은 톤.
#: 정본이 배경을 고른 계보(pet_background)는 그 값을 명시적으로 넘긴다 — 이
#: 기본값/env 는 결정이 저장되지 않은 옛 정본에만 쓰인다.
_DEFAULT_BACKGROUND_RGB = (200, 200, 200)

#: 알파가 "있다"고 볼 최소값(0~255) — vitmatte_service 와 같은 기준.
_ALPHA_PRESENCE = 16

#: 이보다 전경이 적으면 누끼가 망가진 것이다 — 플레이트를 만들지 않는다.
MIN_PLATE_ALPHA_FRACTION = float(os.getenv("CLEAN_PLATE_MIN_ALPHA_FRACTION", "0.01"))


class CleanPlateError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no")


def plate_enabled() -> bool:
    """플레이트 파생 자체를 켜는가 (끄면 기존 raw 흐름으로 되돌아간다)."""
    return _flag("CLEAN_PLATE_ENABLED", "1")


def plate_required() -> bool:
    """플레이트를 못 만들면 **멈추는가**. 기본 예 — raw 로 조용히 새지 않는다."""
    return _flag("CLEAN_PLATE_REQUIRED", "1")


def background_rgb() -> tuple[int, int, int]:
    raw = (os.getenv("CLEAN_PLATE_BG_RGB") or "").strip()
    if raw:
        try:
            parts = [int(p) for p in raw.split(",")]
            if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                return (parts[0], parts[1], parts[2])
        except ValueError:
            pass
        logger.warning("CLEAN_PLATE_BG_RGB 값이 잘못됐습니다 (%r) — 기본값을 쓴다.", raw)
    return _DEFAULT_BACKGROUND_RGB


def plate_object_path(source_object_path: str) -> str:
    """raw/cutout 객체 경로 → 같은 폴더의 `_plate.png`. 결정론적이다."""
    path = (source_object_path or "").strip()
    if not path:
        raise CleanPlateError("PLATE_PATH_INVALID", "플레이트 경로의 원본 경로가 비었습니다.")
    for suffix in ("_raw.png", "_cutout.png"):
        if path.endswith(suffix):
            return path[: -len(suffix)] + "_plate.png"
    stem = path.rsplit(".", 1)[0]
    return f"{stem}_plate.png"


def build_clean_plate(
    cutout_png: bytes, background: Optional[tuple[int, int, int]] = None
) -> tuple[bytes, dict[str, Any]]:
    """
    누끼 RGBA → (불투명 RGB PNG, 메타).

    background: 이 계보의 정본이 고른 배경색 (pet_background). None 이면
    전역 기본(background_rgb())이다.

    out = α·I + (1−α)·B. 알파가 0 인 곳(그림자가 이미 빠진 곳)은 정확히 B 다.
    픽셀 위치·해상도는 그대로다 — 기하 변형 없음.
    """
    from PIL import Image

    if not cutout_png:
        raise CleanPlateError("PLATE_SOURCE_EMPTY", "누끼 바이트가 비었습니다.")
    try:
        with Image.open(io.BytesIO(cutout_png)) as im:
            mode = im.mode
            rgba = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    except CleanPlateError:
        raise
    except Exception as exc:  # noqa: BLE001 — 디코드 실패는 명시 코드로 바꾼다
        raise CleanPlateError("PLATE_SOURCE_UNREADABLE", f"누끼를 읽지 못했습니다: {exc}") from exc

    if mode not in ("RGBA", "LA", "PA", "P"):
        # 알파가 없는 이미지는 "이미 배경이 구워진" 것이다 — 플레이트가 아니다.
        raise CleanPlateError(
            "PLATE_SOURCE_NOT_RGBA",
            f"누끼에 알파 채널이 없습니다 (mode={mode}) — 플레이트를 만들 수 없습니다.",
        )

    alpha_u8 = rgba[:, :, 3]
    coverage = float((alpha_u8 > _ALPHA_PRESENCE).mean())
    if coverage < MIN_PLATE_ALPHA_FRACTION:
        raise CleanPlateError(
            "PLATE_SOURCE_ALPHA_EMPTY",
            f"누끼 전경이 {coverage:.2%} 뿐입니다 (최소 {MIN_PLATE_ALPHA_FRACTION:.0%}).",
        )

    bg = tuple(int(v) for v in background) if background is not None else background_rgb()
    a = (alpha_u8.astype(np.float32) / 255.0)[:, :, None]
    fg = rgba[:, :, :3].astype(np.float32)
    bg_arr = np.array(bg, dtype=np.float32)[None, None, :]
    out = np.clip(fg * a + bg_arr * (1.0 - a) + 0.5, 0.0, 255.0).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(out, mode="RGB").save(buf, format="PNG")
    plate_bytes = buf.getvalue()

    meta = {
        "plate_version": CLEAN_PLATE_VERSION,
        "background_rgb": list(bg),
        "alpha_coverage": round(coverage, 5),
        "size": [int(rgba.shape[1]), int(rgba.shape[0])],
        "source_mode": mode,
        "source_sha256": hashlib.sha256(cutout_png).hexdigest(),
    }
    return plate_bytes, meta


def _obj(bucket: Optional[str], path: Optional[str]) -> SimpleNamespace:
    return SimpleNamespace(
        bucket=bucket or "", object_path=path or "", mime_type="image/png"
    )


async def ensure_plate(
    *,
    user_id: str,
    content_id: str,
    derived_kind: str,
    fetch_bytes: Callable[[Any], Optional[bytes]],
    raw_object_path: Optional[str] = None,
    cutout_bucket: Optional[str] = None,
    cutout_object_path: Optional[str] = None,
    cutout_bytes: Optional[bytes] = None,
    plate_bucket: Optional[str] = None,
    plate_object_path_hint: Optional[str] = None,
    provenance: Optional[dict[str, Any]] = None,
    background: Optional[tuple[int, int, int]] = None,
) -> Optional[SimpleNamespace]:
    """
    플레이트를 **해결**한다: 이미 있으면 읽고, 없으면 누끼에서 만들어 올린다.

    빌더(정본/키프레임)는 생성 시점에 플레이트를 함께 저장하므로 여기서는
    보통 첫 분기로 끝난다. 두 번째 분기는 플레이트 이전에 만들어진 **레거시
    후보**의 지연 백필이다 — 프로바이더를 다시 부르지 않고 기존 누끼만 쓴다.

    cutout_bytes: 호출자가 같은 누끼를 이미 다른 목적으로 내려받아 갖고
    있으면 넘긴다 — 두 번째 분기가 같은 객체를 다시 다운로드하지 않는다.
    안 주면(기본) 기존처럼 fetch_bytes 로 직접 내려받는다 — 동작 불변.

    Returns: SimpleNamespace(bytes, bucket, object_path, meta, created) 또는
             None (누끼조차 없어 만들 수 없음 — 호출자가 정책을 정한다).
    Raises:  CleanPlateError — 누끼는 있으나 플레이트로 쓸 수 없는 경우.
    """
    from . import supabase_assets

    if plate_object_path_hint:
        data = fetch_bytes(_obj(plate_bucket, plate_object_path_hint))
        if data:
            return SimpleNamespace(
                bytes=data,
                bucket=plate_bucket or supabase_assets.BUCKET,
                object_path=plate_object_path_hint,
                meta={"plate_version": CLEAN_PLATE_VERSION, "reused": True},
                created=False,
            )
        logger.warning("클린 플레이트 객체를 읽지 못했습니다 (%s) — 누끼에서 재생성.", plate_object_path_hint)

    if not cutout_object_path:
        return None
    cut_bytes = cutout_bytes if cutout_bytes is not None else fetch_bytes(_obj(cutout_bucket, cutout_object_path))
    if not cut_bytes:
        return None

    plate_bytes, meta = build_clean_plate(cut_bytes, background)
    path = plate_object_path_hint or plate_object_path(raw_object_path or cutout_object_path)
    await supabase_assets.upload_asset_to_storage(path, plate_bytes, "image/png")

    # 근거 없는 파생물로 생성하지 않는다 — 대장 기록 실패는 전파한다
    # (video_anchor.ensure_video_anchor 와 같은 계약).
    from . import pet_reference_service

    await pet_reference_service.record_derived(
        user_id=user_id,
        content_id=content_id,
        object_path=path,
        derived_kind=derived_kind,
        mime_type="image/png",
        diagnostics={"clean_plate": meta, **(provenance or {})},
    )

    return SimpleNamespace(
        bytes=plate_bytes,
        bucket=supabase_assets.BUCKET,
        object_path=path,
        meta=meta,
        created=True,
    )
