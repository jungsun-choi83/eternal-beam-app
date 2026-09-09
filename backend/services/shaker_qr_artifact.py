"""
Shaker QR 산출물 보관 (Phase 13.1) — **토큰은 여전히 해시만 저장한다.**

── 무엇을 푸는가 ───────────────────────────────────────────────────────────
Phase 10 은 공유 토큰을 sha256 으로만 저장한다. 옳은 결정이지만 운영에 실질적
문제를 만들었다: 발급 탭을 닫으면 **같은 QR 을 다시 뽑을 수 없다.** 남는 경로는
재발급뿐이고, 그건 새 토큰 → 이미 인쇄된 QR 무효화 → 재인쇄를 뜻한다.

여기서는 토큰이 아니라 **렌더된 산출물**을 보관한다:

    share_id ─┬─ token_hash   (Phase 10, 그대로)
              ├─ qr.svg       (인쇄용 정본)
              └─ qr.png       (화면 미리보기, 선택)

재다운로드는 이 파일을 그대로 내보낸다 — 토큰을 복원하지 않고, 새 공유를 만들지
않으며, **이미 인쇄된 QR 이 그대로 유효하다.**

⚠️ 솔직하게: QR 은 디코딩 가능하다. 이 산출물을 읽을 수 있는 사람은 스캔해서
   URL 을 얻을 수 있다 — 인쇄된 카드를 가진 사람과 같은 수준이다. 보호는 암호가
   아니라 **접근 제어**(운영 allowlist 전용 경로)로 한다. 그래도 원문 토큰
   컬럼은 만들지 않는다: 덤프에서 문자열로 전량 긁어 가는 것과 이미지를 한 장씩
   디코딩하는 것은 다른 난이도다.

이 모듈은 생성·구독·결제 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class QrArtifactError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("SHAKER_QR_ARTIFACTS_TABLE", "shaker_qr_artifacts")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_ARTIFACTS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_ARTIFACTS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QrArtifact:
    share_id: str
    token_hash: str
    pet_id: str
    qr_svg: str
    qr_png: Optional[bytes] = None
    target_host: Optional[str] = None
    purpose: Optional[str] = None
    created_at: Optional[str] = None


_SELECT = (
    "share_id, token_hash, pet_id, qr_svg, qr_png_base64, target_host, purpose, created_at"
)


def _to_artifact(row: dict[str, Any]) -> QrArtifact:
    png_b64 = row.get("qr_png_base64")
    png: Optional[bytes] = None
    if png_b64:
        try:
            png = base64.b64decode(png_b64)
        except Exception:
            png = None
    return QrArtifact(
        share_id=str(row.get("share_id") or ""),
        token_hash=str(row.get("token_hash") or ""),
        pet_id=str(row.get("pet_id") or ""),
        qr_svg=str(row.get("qr_svg") or ""),
        qr_png=png,
        target_host=(row.get("target_host") or None),
        purpose=(row.get("purpose") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


async def store(
    *,
    share_id: str,
    token_hash: str,
    pet_id: str,
    share_url: str,
    purpose: str | None = None,
) -> QrArtifact:
    """
    공유 하나의 QR 산출물을 렌더해 보관한다. **발급 직후 한 번**만 부른다.

    share_url 은 **저장하지 않는다** — 인자로 받아 렌더에만 쓰고, 남는 것은
    QR 이미지와 호스트뿐이다. 그래서 원문 토큰이 문자열로 남지 않는다.

    이미 있으면 **덮어쓰지 않는다.** 산출물이 바뀌면 이미 인쇄된 QR 과 달라질 수
    있고, 그건 이 기능이 막으려던 바로 그 상황이다.
    """
    sid = (share_id or "").strip()
    if not sid:
        raise QrArtifactError("SHARE_ID_REQUIRED", "share_id 가 필요합니다.")

    existing = await get(sid)
    if existing:
        return existing

    from . import qr_service

    try:
        # ⚠️ **기본 파라미터를 쓴다.** 즉석 렌더(/ops/qr)와 같은 값이어야
        # "인쇄한 QR"과 "다시 받은 QR"이 같은 파일이 된다. 여기서 scale 을 다르게
        # 주면 두 경로가 다른 바이트를 내고, 그건 이 기능이 막으려던 상황이다.
        svg = qr_service.render_qr(share_url, kind="svg", filename_hint=pet_id)
        png = qr_service.render_qr(share_url, kind="png", filename_hint=pet_id)
    except qr_service.QrError as e:
        raise QrArtifactError(e.code, e.message, status=e.status) from e

    row: dict[str, Any] = {
        "share_id": sid,
        "token_hash": token_hash,
        "pet_id": pet_id,
        "qr_svg": svg.data.decode("utf-8"),
        "qr_png_base64": base64.b64encode(png.data).decode("ascii"),
        "target_host": qr_service.target_host(share_url),
        "purpose": (purpose or "").strip().upper() or None,
        "created_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).upsert(row, on_conflict="share_id").execute()
        except Exception as e:
            # 산출물 저장 실패가 공유 발급을 되돌리지는 않는다 — 링크는 이미
            # 유효하다. 다만 재다운로드가 불가능해지므로 크게 남긴다.
            logger.error("QR 산출물 저장 실패 — share=%s (재다운로드 불가)", sid)
            raise QrArtifactError(
                "QR_ARTIFACT_STORE_UNAVAILABLE",
                "QR 산출물을 저장하지 못했습니다.",
                status=503,
            ) from e
    else:
        _MOCK_ARTIFACTS[sid] = row

    return _to_artifact(row)


async def get(share_id: str) -> Optional[QrArtifact]:
    sid = (share_id or "").strip()
    if not sid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("share_id", sid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_artifact(data[0]) if data else None
        except Exception as e:
            logger.exception("QR 산출물 조회 실패 (share=%s)", sid)
            raise QrArtifactError(
                "QR_ARTIFACT_STORE_UNAVAILABLE",
                "QR 산출물을 확인하지 못했습니다.",
                status=503,
            ) from e

    row = _MOCK_ARTIFACTS.get(sid)
    return _to_artifact(row) if row else None


async def require(share_id: str) -> QrArtifact:
    a = await get(share_id)
    if not a:
        raise QrArtifactError(
            "QR_ARTIFACT_NOT_FOUND",
            (
                "이 공유의 QR 산출물이 없습니다. Phase 13.1 이전에 발급된 링크이거나 "
                "저장에 실패했습니다 — 인쇄가 아직이라면 재발급하세요."
            ),
            status=404,
        )
    return a
