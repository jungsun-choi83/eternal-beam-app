"""
Phase 7A — Phase 6 BREATHING publication into the current product contract.

This module never generates, uploads, copies, or mutates a Phase 6 asset. It validates an
immutable ``pet_motion_versions`` result and its selected ``pet_motion_candidates`` row,
verifies that the stored object still exists, then projects that object into ``pets``.

``pet_motion_versions`` / ``pet_motion_candidates`` remain the source of truth. ``pets`` is
only the compatibility pointer consumed by the existing browser, Shaker, and operations code.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import asset_url_refresh, motion_video_service, pet_registry

BREATHING = "BREATHING"


class MotionPublicationError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class BreathingPublication:
    publication_id: str
    motion_version_id: str
    selected_candidate_id: str
    pet_id: str
    content_id: Optional[str]
    breathing_bucket: str
    breathing_object_path: str
    idle_video_url: str
    background_baked: bool = False
    #: Phase 7F — 발행 객체의 명시적 전달 포맷. 'packed_alpha' 또는 None(레거시).
    delivery_format: Optional[str] = None
    published_at: Optional[str] = None
    deduplicated: bool = False


@dataclass(frozen=True)
class PublishedBreathing:
    """Phase 7F 하이드레이션 — 현재 발행된 BREATHING 을 브라우저 계약으로 해석한 것."""

    pet_id: str
    motion_id: str
    breathing_bucket: str
    breathing_object_path: str
    #: 매번 새로 서명한다 — 저장된 서명 URL 은 절대 그대로 내보내지 않는다.
    url: str
    background_baked: bool
    motion_version_id: Optional[str] = None
    delivery_format: Optional[str] = None
    publication_id: Optional[str] = None
    content_id: Optional[str] = None


# Supabase 없이도 동일한 멱등 계약을 검증하기 위한 테스트/로컬 저장소.
_MOCK_PUBLICATIONS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_PUBLICATIONS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


async def _load_version(motion_version_id: str) -> dict[str, Any]:
    row: Optional[dict[str, Any]] = None
    client = motion_video_service._supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(motion_video_service._versions_table())
                .select("*")
                .eq("id", motion_version_id)
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            row = rows[0] if rows else None
        except Exception as exc:
            raise MotionPublicationError(
                "PUBLICATION_UNAVAILABLE", "Phase 6 버전을 확인하지 못했습니다.", status=503
            ) from exc
    else:
        row = next(
            (r for r in motion_video_service._MOCK_VERSIONS if str(r.get("id")) == motion_version_id),
            None,
        )
    if not row:
        raise MotionPublicationError(
            "MOTION_VERSION_NOT_FOUND", "Phase 6 BREATHING 버전을 찾을 수 없습니다.", status=404
        )
    return row


async def _load_selected_candidate(
    *, version: dict[str, Any], user_id: str, pet_id: str
) -> dict[str, Any]:
    selected_id = str(version.get("selected_candidate_id") or "").strip()
    if not selected_id:
        raise MotionPublicationError(
            "SELECTED_CANDIDATE_MISSING", "선택된 Phase 6 후보가 없습니다.", status=409
        )

    candidate: Optional[dict[str, Any]] = None
    client = motion_video_service._supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(motion_video_service._candidates_table())
                .select("*")
                .eq("id", selected_id)
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            candidate = rows[0] if rows else None
        except Exception as exc:
            raise MotionPublicationError(
                "PUBLICATION_UNAVAILABLE", "선택 후보를 확인하지 못했습니다.", status=503
            ) from exc
    else:
        candidate = next(
            (
                c
                for c in motion_video_service._MOCK_CANDIDATES
                if str(c.get("id")) == selected_id
            ),
            None,
        )
    if not candidate:
        raise MotionPublicationError(
            "SELECTED_CANDIDATE_MISSING", "선택된 Phase 6 후보 행을 찾을 수 없습니다.", status=409
        )
    if (
        str(candidate.get("motion_version_id") or "") != str(version["id"])
        or str(candidate.get("user_id") or "") != user_id
        or str(candidate.get("pet_id") or "") != pet_id
        or str(candidate.get("motion_id") or "").upper() != BREATHING
        or candidate.get("selected") is not True
    ):
        raise MotionPublicationError(
            "SELECTED_CANDIDATE_INVALID", "선택 후보가 이 BREATHING 버전에 속하지 않습니다.", status=409
        )
    if str(candidate.get("decision") or "").upper() != "PASS":
        raise MotionPublicationError(
            "CANDIDATE_NOT_PASS", "QA PASS 후보만 제품에 발행할 수 있습니다.", status=409
        )
    return candidate


def delivery_format_for(candidate: Optional[dict[str, Any]], object_path: str) -> Optional[str]:
    """
    발행/하이드레이션 객체의 전달 포맷 판정 (Phase 7F).

    후보의 명시 컬럼이 1순위다 — 단, 그 컬럼은 derived_video_path 의 포맷 선언
    이므로 실제 발행 경로가 그 파생물일 때만 유효하다. 명시 값이 없으면 포장
    규칙의 파일명(`_packed.mp4`)으로 보수적으로 추정하고, 그 외에는 None
    (레거시 — 브라우저가 기존 규칙으로 재생)이다.
    """
    path = (object_path or "").strip()
    if candidate:
        fmt = str(candidate.get("delivery_format") or "").strip().lower()
        derived = str(candidate.get("derived_video_path") or "").strip()
        if fmt and derived and derived == path:
            return fmt
    if path.split("?")[0].endswith("_packed.mp4"):
        return "packed_alpha"
    return None


def _asset_location(candidate: dict[str, Any]) -> asset_url_refresh.StorageObject:
    # Phase 7A 는 새 파생물을 만들지 않는다. 이미 파생 자산이 있으면 그것을 우선하고,
    # 현재 Phase 6 의 일반적인 결과인 raw 자산으로 폴백한다.
    path = str(candidate.get("derived_video_path") or candidate.get("raw_video_path") or "").strip()
    bucket = str(candidate.get("raw_bucket") or asset_url_refresh.default_bucket()).strip()
    if not path or not bucket:
        raise MotionPublicationError(
            "CANDIDATE_ASSET_MISSING", "선택 후보에 발행 가능한 스토리지 경로가 없습니다.", status=409
        )
    return asset_url_refresh.StorageObject(bucket=bucket, path=path)


def _rpc_error(exc: Exception) -> MotionPublicationError:
    raw = str(exc)
    mapping = (
        ("PET_NOT_OWNED", "PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", 403),
        ("MOTION_VERSION_NOT_FOUND", "MOTION_VERSION_NOT_FOUND", "Phase 6 버전이 없습니다.", 404),
        ("MOTION_NOT_PUBLISHABLE", "MOTION_NOT_PUBLISHABLE", "완료된 모션만 발행할 수 있습니다.", 409),
        ("SELECTED_CANDIDATE_MISSING", "SELECTED_CANDIDATE_MISSING", "선택 후보가 없습니다.", 409),
        ("CANDIDATE_NOT_PASS", "CANDIDATE_NOT_PASS", "QA PASS 후보만 발행할 수 있습니다.", 409),
        ("STALE_MOTION_VERSION", "STALE_MOTION_VERSION", "현재 버전보다 오래된 모션은 발행할 수 없습니다.", 409),
        ("PUBLICATION_CONFLICT", "PUBLICATION_CONFLICT", "기존 발행 기록과 입력이 다릅니다.", 409),
    )
    for marker, code, message, status in mapping:
        if marker in raw:
            return MotionPublicationError(code, message, status=status)
    return MotionPublicationError(
        "PUBLICATION_UNAVAILABLE", "BREATHING 발행을 완료하지 못했습니다.", status=503
    )


async def _publish_projection(
    *,
    user_id: str,
    pet_id: str,
    version: dict[str, Any],
    candidate: dict[str, Any],
    asset: asset_url_refresh.StorageObject,
) -> dict[str, Any]:
    client = motion_video_service._supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.rpc(
                    "publish_phase6_breathing",
                    {
                        "p_user_id": user_id,
                        "p_pet_id": pet_id,
                        "p_motion_version_id": str(version["id"]),
                        "p_selected_candidate_id": str(candidate["id"]),
                        "p_bucket": asset.bucket,
                        "p_object_path": asset.path,
                    },
                )
                .execute()
            )
            data = getattr(result, "data", None) or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict) or not data.get("publication_id"):
                raise RuntimeError("publish_phase6_breathing returned no publication")
            return data
        except MotionPublicationError:
            raise
        except Exception as exc:  # PostgREST exposes the PL/pgSQL marker in the exception text.
            raise _rpc_error(exc) from exc

    existing = next(
        (p for p in _MOCK_PUBLICATIONS if p["motion_version_id"] == str(version["id"])), None
    )
    if existing:
        same = (
            existing["selected_candidate_id"] == str(candidate["id"])
            and existing["user_id"] == user_id
            and existing["pet_id"] == pet_id
            and existing["bucket"] == asset.bucket
            and existing["object_path"] == asset.path
        )
        if not same:
            raise MotionPublicationError(
                "PUBLICATION_CONFLICT", "기존 발행 기록과 입력이 다릅니다.", status=409
            )
        return {**existing, "deduplicated": True}

    pet = await pet_registry.get(pet_id)
    if pet and pet.user_id != user_id:
        raise MotionPublicationError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)

    current = [p for p in _MOCK_PUBLICATIONS if p["pet_id"] == pet_id]
    if current and max(int(p["motion_version"]) for p in current) > int(version.get("version") or 0):
        raise MotionPublicationError(
            "STALE_MOTION_VERSION", "현재 버전보다 오래된 모션은 발행할 수 없습니다.", status=409
        )

    publication = {
        "publication_id": str(uuid.uuid4()),
        "motion_version_id": str(version["id"]),
        "selected_candidate_id": str(candidate["id"]),
        "user_id": user_id,
        "pet_id": pet_id,
        "motion_version": int(version.get("version") or 0),
        "bucket": asset.bucket,
        "object_path": asset.path,
        "published_at": _now_iso(),
        "deduplicated": False,
    }
    _MOCK_PUBLICATIONS.append(publication)

    old = pet_registry._MOCK_PETS.get(pet_id) or {}
    pet_registry._MOCK_PETS[pet_id] = {
        **old,
        "pet_id": pet_id,
        "user_id": user_id,
        "content_id": old.get("content_id") or pet_registry.content_id_of(pet_id),
        "breathing_bucket": asset.bucket,
        "breathing_object_path": asset.path,
        "breathing_motion_version_id": str(version["id"]),
        "source": old.get("source") or pet_registry.SOURCE_APP,
        "background_baked": False,
        "created_at": old.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }
    return publication


async def publish_breathing(
    *,
    user_id: str,
    pet_id: str,
    motion_version_id: str,
    sign_fn: Optional[Callable[[asset_url_refresh.StorageObject], Optional[str]]] = None,
) -> BreathingPublication:
    """Validate and publish one existing Phase 6 BREATHING QA PASS asset."""
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    version_id = (motion_version_id or "").strip()
    if not uid or not pid or not version_id:
        raise MotionPublicationError(
            "PUBLICATION_INVALID", "user_id, pet_id, motion_version_id 가 필요합니다."
        )

    version = await _load_version(version_id)
    if str(version.get("user_id") or "") != uid or str(version.get("pet_id") or "") != pid:
        raise MotionPublicationError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)
    if str(version.get("motion_id") or "").upper() != BREATHING:
        raise MotionPublicationError(
            "BREATHING_REQUIRED", "Phase 7A 는 BREATHING 모션만 발행합니다.", status=409
        )

    status = str(version.get("status") or "").lower()
    if status != motion_video_service.STATUS_COMPLETE:
        code = {
            motion_video_service.STATUS_REVIEW: "MOTION_REVIEW_NOT_PUBLISHABLE",
            motion_video_service.STATUS_FAILED: "MOTION_FAILED_NOT_PUBLISHABLE",
        }.get(status, "MOTION_NOT_COMPLETE")
        raise MotionPublicationError(code, "완료된 QA PASS 모션만 발행할 수 있습니다.", status=409)

    candidate = await _load_selected_candidate(version=version, user_id=uid, pet_id=pid)
    asset = _asset_location(candidate)
    signed_url = (sign_fn or asset_url_refresh.sign_object)(asset)
    if not signed_url:
        raise MotionPublicationError(
            "CANDIDATE_ASSET_NOT_FOUND",
            "선택된 Phase 6 BREATHING 스토리지 객체를 확인할 수 없습니다.",
            status=409,
        )

    published = await _publish_projection(
        user_id=uid, pet_id=pid, version=version, candidate=candidate, asset=asset
    )
    return BreathingPublication(
        publication_id=str(published["publication_id"]),
        motion_version_id=str(version["id"]),
        selected_candidate_id=str(candidate["id"]),
        pet_id=pid,
        content_id=pet_registry.content_id_of(pid),
        breathing_bucket=asset.bucket,
        breathing_object_path=asset.path,
        # Existing StoredPipeline and IdleLoopVideo consume this field directly.
        idle_video_url=str(signed_url),
        background_baked=False,
        delivery_format=delivery_format_for(candidate, asset.path),
        published_at=(str(published["published_at"]) if published.get("published_at") else None),
        deduplicated=bool(published.get("deduplicated")),
    )


# ══════════════════════════════════════════════════════════════════════════
# Phase 7F — 하이드레이션 (읽기 전용)
# ══════════════════════════════════════════════════════════════════════════


async def _breathing_version_id_of(pet_id: str) -> Optional[str]:
    """pets.breathing_motion_version_id — pet_registry 계약을 건드리지 않는 별도 조회."""
    if not _use_db():
        row = pet_registry._MOCK_PETS.get(pet_id) or {}
        value = str(row.get("breathing_motion_version_id") or "").strip()
        return value or None
    client = motion_video_service._supabase()
    if not client:
        return None
    try:
        result = (
            client.table("pets")
            .select("breathing_motion_version_id")
            .eq("pet_id", pet_id)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        value = str((rows[0] if rows else {}).get("breathing_motion_version_id") or "").strip()
        return value or None
    except Exception:
        # 컬럼/행이 없는 구세대 DB — 레거시 포인터로만 응답한다.
        return None


async def _publication_id_for_version(motion_version_id: str) -> Optional[str]:
    if not _use_db():
        row = next(
            (
                p
                for p in _MOCK_PUBLICATIONS
                if str(p.get("motion_version_id")) == motion_version_id
            ),
            None,
        )
        return str(row["publication_id"]) if row else None
    client = motion_video_service._supabase()
    if not client:
        return None
    try:
        result = (
            client.table("pet_motion_publications")
            .select("id")
            .eq("motion_version_id", motion_version_id)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        return str(rows[0]["id"]) if rows else None
    except Exception:
        return None


async def get_published_breathing(
    *,
    user_id: str,
    pet_id: str,
    sign_fn: Optional[Callable[[asset_url_refresh.StorageObject], Optional[str]]] = None,
) -> PublishedBreathing:
    """
    현재 발행된 BREATHING 포인터를 **지금 유효한** 서명 URL 로 해석한다.

    읽기 전용이다 — 생성·발행·포장을 하지 않는다. pets 의 breathing_* 가 단일
    근거이므로, Phase 7A 발행물뿐 아니라 레거시(Luma) 포인터도 그대로 서빙된다
    (그 경우 delivery_format=None, background_baked 는 pets 행 값).
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise MotionPublicationError("PUBLICATION_INVALID", "user_id 와 pet_id 가 필요합니다.")

    pet = await pet_registry.get(pid)
    if not pet:
        raise MotionPublicationError("PET_NOT_FOUND", "등록된 펫이 없습니다.", status=404)
    if pet.user_id != uid:
        raise MotionPublicationError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)

    path = (pet.breathing_object_path or "").strip()
    if not path:
        raise MotionPublicationError(
            "BREATHING_NOT_PUBLISHED", "발행된 BREATHING 이 없습니다.", status=404
        )
    bucket = (pet.breathing_bucket or asset_url_refresh.default_bucket()).strip()
    asset = asset_url_refresh.StorageObject(bucket=bucket, path=path)
    signed_url = (sign_fn or asset_url_refresh.sign_object)(asset)
    if not signed_url:
        raise MotionPublicationError(
            "PUBLISHED_ASSET_UNAVAILABLE",
            "발행된 BREATHING 스토리지 객체를 확인할 수 없습니다.",
            status=409,
        )

    version_id = await _breathing_version_id_of(pid)
    candidate: Optional[dict[str, Any]] = None
    publication_id: Optional[str] = None
    if version_id:
        try:
            version = await _load_version(version_id)
            candidate = await _load_selected_candidate(version=version, user_id=uid, pet_id=pid)
        except MotionPublicationError:
            # 포인터는 살아 있는데 계보 행을 못 찾는 경우 — 포맷은 경로로 추정한다.
            candidate = None
        publication_id = await _publication_id_for_version(version_id)

    return PublishedBreathing(
        pet_id=pid,
        motion_id=BREATHING,
        breathing_bucket=bucket,
        breathing_object_path=path,
        url=str(signed_url),
        background_baked=bool(pet.background_baked),
        motion_version_id=version_id,
        delivery_format=delivery_format_for(candidate, path),
        publication_id=publication_id,
        content_id=pet.content_id or pet_registry.content_id_of(pid),
    )
