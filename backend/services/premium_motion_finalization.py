"""
Phase 7H — 프리미엄 모션 원자적 이행 확정 (finalization).

Phase 7A(BREATHING → pets 포인터)의 일반화다. BREATHING 이 아닌 상용 모션의
QA PASS + packed 파생물을 **제품 계약**으로 투영한다:

    Phase 6 selected PASS candidate (packed-alpha, Phase 7F)
      → pet_motion_publications   발행 원장 (버전당 1회, 멱등 앵커)
      → 예약 확정 + owned_generated_assets  (기존 commit_for_asset 그대로)
      → generated_motions 현재 포인터        (기존 재생/디바이스 계약 그대로)

── 돈의 규칙 ────────────────────────────────────────────────────────────────
확정(commit)은 이 함수 안에서, 소유 기록 **직전**에만 일어난다 — 기존
generation_credits.commit_for_asset 의 계약을 그대로 쓴다. PASS 가 아니거나
packed 파생물이 없으면 어떤 쓰기도 하지 않는다: 과금이 확정됐는데 소유/발행이
없는 상태는 만들 수 없다.

── 멱등성 ───────────────────────────────────────────────────────────────────
재시도(웹훅류 재전송, 워커 재시작)는 안전하다:
  * 발행: motion_version_id unique — 있으면 재사용
  * 확정: credit_reservation.commit 은 여러 번 불려도 안전
  * 소유: source_job_id = "phase7:{run_id}" 로 중복 무시
  * 포인터: (user,pet,place,action) upsert

Phase 6(pet_motion_versions/candidates)이 정본이고, 여기 기록들은 상거래/재생
투영이다. QA 결정은 절대 바꾸지 않는다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import (
    asset_url_refresh,
    generated_motions_service,
    generation_credits,
    motion_delivery_service,
    supabase_assets,
)

logger = logging.getLogger(__name__)

#: 기존 상용 모션만 — 새 모션(PET_HEAD, RUN …)은 카탈로그에 명시적으로 추가되기
#: 전까지 이행 대상이 아니다 (기술적으로 생성 가능해도 판매되지 않는다).
#: 포장 대상 목록(motion_delivery_service.PACKAGEABLE_MOTIONS)에서 무료 기본
#: 모션(BREATHING)을 뺀 것과 정확히 같다 — 두 목록이 갈라지면 포장은 되는데
#: 이행이 안 되는(또는 그 반대) 모션이 생긴다.
PREMIUM_MOTIONS: tuple[str, ...] = tuple(
    m for m in motion_delivery_service.PACKAGEABLE_MOTIONS if m != "BREATHING"
)

#: 발행/재생 포인터가 저장하는 서명 URL 수명 — 레거시 업로드 서명(7일)과 동일.
#: 읽기 경로(Shaker 등)는 어차피 경로를 파싱해 재서명한다.
_POINTER_URL_TTL = 604800


class PremiumFinalizationError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class PremiumFinalization:
    publication_id: str
    motion_id: str
    motion_version_id: str
    selected_candidate_id: str
    pet_id: str
    video_url: str
    bucket: str
    object_path: str
    delivery_format: str
    owned_source_job_id: str
    deduplicated: bool = False


# HYBRID_USE_SUPABASE=0 — 발행 원장 mock (BREATHING 발행 mock 과 분리:
# 그쪽은 pets 포인터 이동까지 흉내내지만 프리미엄 발행은 포인터가 다르다).
_MOCK_PUBLICATIONS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_PUBLICATIONS.clear()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sign_pointer_url(bucket: str, path: str) -> Optional[str]:
    """포인터에 저장할 7일 서명 URL — 레거시 stored_url 과 같은 수명 계약."""
    client = supabase_assets.get_client() if _use_db() else None
    if client:
        try:
            res = client.storage.from_(bucket).create_signed_url(path, _POINTER_URL_TTL)
            if isinstance(res, dict):
                for k in ("signedURL", "signedUrl", "signed_url", "url"):
                    v = res.get(k)
                    if isinstance(v, str) and v:
                        return v
                data = res.get("data")
                if isinstance(data, dict):
                    for k in ("signedURL", "signedUrl", "signed_url", "url"):
                        v = data.get(k)
                        if isinstance(v, str) and v:
                            return v
        except Exception:
            logger.exception("프리미엄 포인터 서명 실패 (bucket=%s path=%s)", bucket, path)
            return None
        return None
    # mock 모드 — 포장 mock 스토어에 있는 객체만 인정한다.
    if path in motion_delivery_service._MOCK_DELIVERY_OBJECTS:
        return f"mock://{bucket}/{path}"
    return asset_url_refresh.sign_object(asset_url_refresh.StorageObject(bucket=bucket, path=path))


async def _ensure_publication(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    motion_version_id: str,
    motion_version: int,
    candidate_id: str,
    bucket: str,
    object_path: str,
) -> tuple[str, bool]:
    """발행 원장 1행 (버전당 1회). 반환: (publication_id, 재사용 여부)."""
    client = None
    if _use_db():
        from . import motion_video_service

        client = motion_video_service._supabase()
    if client:
        try:
            existing = (
                client.table("pet_motion_publications")
                .select("id,selected_candidate_id,object_path")
                .eq("motion_version_id", motion_version_id)
                .limit(1)
                .execute()
            )
            rows = getattr(existing, "data", None) or []
            if rows:
                row = rows[0]
                if (
                    str(row.get("selected_candidate_id")) != candidate_id
                    or str(row.get("object_path")) != object_path
                ):
                    raise PremiumFinalizationError(
                        "PUBLICATION_CONFLICT", "기존 발행 기록과 입력이 다릅니다.", status=409
                    )
                return str(row["id"]), True
            inserted = (
                client.table("pet_motion_publications")
                .insert(
                    {
                        "motion_version_id": motion_version_id,
                        "selected_candidate_id": candidate_id,
                        "user_id": user_id,
                        "pet_id": pet_id,
                        "motion_id": motion_id,
                        "motion_version": int(motion_version or 1),
                        "bucket": bucket,
                        "object_path": object_path,
                        "background_baked": False,
                    }
                )
                .execute()
            )
            rows = getattr(inserted, "data", None) or []
            if rows:
                return str(rows[0]["id"]), False
            raise RuntimeError("publication insert returned no row")
        except PremiumFinalizationError:
            raise
        except Exception as exc:
            # unique 경합 — 동시 확정이 먼저 썼다. 다시 읽어 재사용한다.
            try:
                retry = (
                    client.table("pet_motion_publications")
                    .select("id")
                    .eq("motion_version_id", motion_version_id)
                    .limit(1)
                    .execute()
                )
                rows = getattr(retry, "data", None) or []
                if rows:
                    return str(rows[0]["id"]), True
            except Exception:
                pass
            raise PremiumFinalizationError(
                "PUBLICATION_UNAVAILABLE", f"발행 원장을 기록하지 못했습니다: {exc}", status=503
            ) from exc

    existing = next(
        (p for p in _MOCK_PUBLICATIONS if p["motion_version_id"] == motion_version_id), None
    )
    if existing:
        if (
            existing["selected_candidate_id"] != candidate_id
            or existing["object_path"] != object_path
        ):
            raise PremiumFinalizationError(
                "PUBLICATION_CONFLICT", "기존 발행 기록과 입력이 다릅니다.", status=409
            )
        return str(existing["id"]), True
    row = {
        "id": str(uuid.uuid4()),
        "motion_version_id": motion_version_id,
        "selected_candidate_id": candidate_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "motion_id": motion_id,
        "motion_version": int(motion_version or 1),
        "bucket": bucket,
        "object_path": object_path,
        "background_baked": False,
        "published_at": _now_iso(),
    }
    _MOCK_PUBLICATIONS.append(row)
    return str(row["id"]), False


async def finalize_premium_motion(
    *,
    run_id: str,
    user_id: str,
    pet_id: str,
    motion_id: str,
    motion_version_id: str,
    motion_version: int,
    candidate_id: str,
    product_key: Optional[str] = None,
    reservation_ledger_id: Optional[str] = None,
    credits_reserved: int = 0,
    sign_fn: Optional[Callable[[str, str], Optional[str]]] = None,
) -> PremiumFinalization:
    """
    QA PASS + packed 파생물 → 발행 + 예약 확정 + 소유 + 현재 포인터.

    검증이 전부 통과하기 전에는 아무것도 쓰지 않는다. 어느 단계에서 실패해도
    재호출이 안전하다(모든 쓰기가 멱등 앵커를 가진다).
    """
    mid = (motion_id or "").strip().upper()
    if mid not in PREMIUM_MOTIONS:
        raise PremiumFinalizationError(
            "MOTION_NOT_COMMERCIAL", f"{mid} 는 프리미엄 이행 대상이 아닙니다.", status=409
        )

    # ── 검증: 후보가 이 실행/펫의 것이고, PASS 이며, packed 파생물이 있다 ──
    try:
        candidate = await motion_delivery_service._load_candidate(candidate_id)
    except motion_delivery_service.MotionDeliveryError as exc:
        raise PremiumFinalizationError(exc.code, exc.message, status=exc.status) from exc
    if (
        str(candidate.get("motion_version_id") or "") != motion_version_id
        or str(candidate.get("user_id") or "") != user_id
        or str(candidate.get("pet_id") or "") != pet_id
        or str(candidate.get("motion_id") or "").upper() != mid
    ):
        raise PremiumFinalizationError(
            "CANDIDATE_MISMATCH", "후보가 이 실행/펫/모션에 속하지 않습니다.", status=409
        )
    if str(candidate.get("decision") or "").upper() != "PASS":
        raise PremiumFinalizationError(
            "CANDIDATE_NOT_PASS", "QA PASS 후보만 이행할 수 있습니다.", status=409
        )
    derived = str(candidate.get("derived_video_path") or "").strip()
    fmt = motion_delivery_service.candidate_delivery_format(candidate)
    if not derived or fmt != motion_delivery_service.DELIVERY_PACKED_ALPHA:
        raise PremiumFinalizationError(
            "DELIVERY_NOT_PACKAGED",
            "packed-alpha 파생물이 없습니다 — 이행 전에 포장(Phase 7F)이 필요합니다.",
            status=409,
        )
    bucket = str(candidate.get("raw_bucket") or asset_url_refresh.default_bucket()).strip()

    signed = (sign_fn or _sign_pointer_url)(bucket, derived)
    if not signed:
        raise PremiumFinalizationError(
            "DELIVERY_ASSET_UNAVAILABLE", "포장된 스토리지 객체를 확인할 수 없습니다.", status=409
        )

    # ── 1) 발행 원장 (멱등 앵커) ───────────────────────────────────────────
    publication_id, deduplicated = await _ensure_publication(
        user_id=user_id,
        pet_id=pet_id,
        motion_id=mid,
        motion_version_id=motion_version_id,
        motion_version=motion_version,
        candidate_id=candidate_id,
        bucket=bucket,
        object_path=derived,
    )

    lineage = {
        "generation_run_id": run_id,
        "pet_motion_version_id": motion_version_id,
        "selected_candidate_id": candidate_id,
        "publication_id": publication_id,
        "delivery_bucket": bucket,
        "delivery_object_path": derived,
        "delivery_format": motion_delivery_service.DELIVERY_PACKED_ALPHA,
        "product_key": product_key,
        "reservation_ledger_id": reservation_ledger_id,
    }

    # ── 2) 예약 확정 + 소유 기록 — 기존 계약 그대로 (확정이 먼저) ──────────
    owned_source_job_id = f"phase7:{run_id}"
    try:
        await generation_credits.commit_for_asset(
            reservation_ledger_id=reservation_ledger_id,
            credits=int(credits_reserved or 0),
            user_id=user_id,
            pet_id=pet_id,
            action_id=mid,
            video_url=str(signed),
            object_path=derived,
            bucket=bucket,
            source_job_id=owned_source_job_id,
            lineage=lineage,
        )
    except PremiumFinalizationError:
        raise
    except Exception as exc:
        raise PremiumFinalizationError(
            "FINALIZATION_COMMIT_FAILED",
            f"예약 확정/소유 기록에 실패했습니다: {exc}",
            status=503,
        ) from exc

    # ── 3) 현재 포인터 — 기존 재생/디바이스가 읽는 유일한 표 ───────────────
    await generated_motions_service.record_pointer(
        user_id=user_id,
        pet_id=pet_id,
        place_id=generated_motions_service.THEME_INDEPENDENT_PLACE_ID,
        action_id=mid,
        video_url=str(signed),
    )

    return PremiumFinalization(
        publication_id=publication_id,
        motion_id=mid,
        motion_version_id=motion_version_id,
        selected_candidate_id=candidate_id,
        pet_id=pet_id,
        video_url=str(signed),
        bucket=bucket,
        object_path=derived,
        delivery_format=motion_delivery_service.DELIVERY_PACKED_ALPHA,
        owned_source_job_id=owned_source_job_id,
        deduplicated=deduplicated,
    )
