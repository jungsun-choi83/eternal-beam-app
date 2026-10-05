"""
펫 레퍼런스 대장 (Durable Pet Identity Intake, Phase 1).

── 무엇을 하는가 ───────────────────────────────────────────────────────────
사용자가 준 **원본 사진**을 스토리지에 영구 보존하고, 펫당 여러 장의
레퍼런스를 기록한다(pet_reference_images). 이후 신원 파이프라인(멀티뷰 →
정본 펫 이미지 → 액션 키프레임)의 출발점이다.

행은 지우지 않고, 바이트·경로·계보 열은 고치지 않는다. 갱신되는 열은
acceptance_state / rejection_code 둘뿐이다 — 사용자가 사진을 빼거나 바꾸면
그 원본과 누끼가 rejected 로 물러나고, 같은 바이트가 다시 오면 되살아난다.

── 원본 vs 파생 ────────────────────────────────────────────────────────────
role='original' 은 사용자 제공 증거다. 저장 경로에 콘텐츠 해시가 들어가므로
같은 바이트는 같은 객체로 수렴하고, 다른 바이트가 기존 원본을 덮어쓸 수 없다.
role='derived'(누끼 등)는 **이미 올라간 객체를 가리키기만** 한다 — 여기서
파생물을 다시 업로드하지 않으므로 파생 기록이 원본을 훼손할 방법이 없다.

── 소유권 ─────────────────────────────────────────────────────────────────
pet_registry 와 같은 최초 사용 시 귀속(TOFU)이다. pets 레지스트리에 등록된
펫이면 그 소유자가 정본이고, 등록 전이면 먼저 레퍼런스를 만든 신원이 소유한다.
다른 신원의 접근은 거절한다.

── 하지 않는 것 ────────────────────────────────────────────────────────────
생성하지 않는다. 뷰/포즈/가림 라벨을 추측하지 않는다 — 파이프라인이 실제로
아는 값(YOLO 검출, ViTMatte 진단)만 기록하고 나머지는 unknown 으로 남긴다.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROLE_ORIGINAL = "original"
ROLE_DERIVED = "derived"
#: 합성(생성) 자산 — Phase 4 정본 펫 등. **절대 original 이 되지 않는다**:
#: 역사적 증거(original)와 분석 보조(derived)와 구분되는 제3의 부류이며,
#: 신원 분석/레퍼런스 세트는 original 만 근거로 삼는다.
ROLE_GENERATED = "generated"

SOURCE_APP = "app"
SOURCE_OPS = "ops"
SOURCE_PIPELINE = "pipeline"

VIEW_UNKNOWN = "UNKNOWN"

STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"

#: 사용자가 UI 에서 사진을 빼거나 바꿔서 물러난 원본(과 그 누끼)의 거절 코드.
#: 이 코드로 거절된 행만 같은 바이트의 재등록으로 되살아난다 — 다른 이유의
#: 거절은 재업로드로 풀리지 않는다.
REJECTION_SUPERSEDED_BY_USER = "SUPERSEDED_BY_USER"
#: 같은 원본의 누끼가 다른 바이트로 **교체**되어 물러난 누끼. 원본이 되살아날 때
#: 함께 되살아나지 않는다 (그 자리는 교체한 누끼의 것이다) — 같은 누끼 바이트가
#: 다시 올 때만 되살아난다.
REJECTION_CUTOUT_REPLACED = "CUTOUT_REPLACED_BY_USER"

#: 한 펫(=한 content_id)에 붙일 수 있는 **서로 다른** 원본 장수. 멀티 레퍼런스
#: 인테이크는 1~3장을 같은 펫에 쌓는다 — 같은 바이트의 재시도는 여전히 멱등이라
#: 이 상한을 소모하지 않는다.
MAX_ORIGINALS_PER_PET = 3

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


class PetReferenceError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PET_REFERENCE_IMAGES_TABLE", "pet_reference_images")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


def _bucket() -> str:
    from . import supabase_assets

    return supabase_assets.BUCKET


#: 테스트/스토리지 없는 환경용 인메모리 대장. pet_registry._MOCK_PETS 와 같은 역할.
_MOCK_REFS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_REFS.clear()
    _INPUT_GATES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pet_id_for_content(content_id: str) -> str:
    """content_id → canonical petId. 프론트 규약(pet-identity.ts)과 같은 규칙."""
    return f"pet_{(content_id or '').strip()}"


@dataclass(frozen=True)
class PetReference:
    id: Optional[str]
    pet_id: str
    content_id: str
    user_id: str
    role: str
    source: str
    bucket: str
    object_path: str
    version: int
    derived_kind: Optional[str] = None
    parent_reference_id: Optional[str] = None
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    bytes_size: Optional[int] = None
    content_hash: Optional[str] = None
    view_label: str = VIEW_UNKNOWN
    acceptance_state: str = STATE_ACCEPTED
    rejection_code: Optional[str] = None
    created_at: Optional[str] = None
    detection: Optional[dict[str, Any]] = None
    person_detected: Optional[bool] = None
    diagnostics: Optional[dict[str, Any]] = None
    #: 행이 실제로 대장에 기록됐는가. False 는 "바이트는 안전하게 저장됐지만
    #: 행 삽입이 실패했다"는 뜻이다 — 호출자가 정직하게 보고할 수 있게 남긴다.
    recorded: bool = True
    #: 이번 호출이 기존 행을 돌려준 것인가 (멱등 재시도).
    deduplicated: bool = False
    #: 이번 호출이 사용자가 뺐던(SUPERSEDED_BY_USER) 행을 되살렸는가.
    reactivated: bool = False


_SELECT = (
    "id, pet_id, content_id, user_id, role, source, derived_kind, parent_reference_id, "
    "bucket, object_path, original_filename, mime_type, width, height, bytes_size, "
    "content_hash, view_label, acceptance_state, rejection_code, version, created_at, "
    "detection, person_detected, diagnostics"
)


def _to_ref(
    row: dict[str, Any],
    *,
    recorded: bool = True,
    deduplicated: bool = False,
    reactivated: bool = False,
) -> PetReference:
    return PetReference(
        id=(str(row["id"]) if row.get("id") else None),
        pet_id=str(row.get("pet_id") or ""),
        content_id=str(row.get("content_id") or ""),
        user_id=str(row.get("user_id") or ""),
        role=str(row.get("role") or ROLE_ORIGINAL),
        source=str(row.get("source") or SOURCE_APP),
        derived_kind=(row.get("derived_kind") or None),
        parent_reference_id=(str(row["parent_reference_id"]) if row.get("parent_reference_id") else None),
        bucket=str(row.get("bucket") or ""),
        object_path=str(row.get("object_path") or ""),
        original_filename=(row.get("original_filename") or None),
        mime_type=(row.get("mime_type") or None),
        width=row.get("width"),
        height=row.get("height"),
        bytes_size=row.get("bytes_size"),
        content_hash=(row.get("content_hash") or None),
        view_label=str(row.get("view_label") or VIEW_UNKNOWN),
        acceptance_state=str(row.get("acceptance_state") or STATE_ACCEPTED),
        rejection_code=(row.get("rejection_code") or None),
        version=int(row.get("version") or 1),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        detection=(row.get("detection") or None),
        person_detected=row.get("person_detected"),
        diagnostics=(row.get("diagnostics") or None),
        recorded=recorded,
        deduplicated=deduplicated,
        reactivated=reactivated,
    )


def _image_dimensions(data: bytes) -> tuple[Optional[int], Optional[int]]:
    """치수를 읽지 못해도 인테이크를 막지 않는다 — 원본 보존이 우선이다."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None, None


def _ext_for_mime(mime_type: Optional[str]) -> str:
    return _EXT_BY_MIME.get((mime_type or "").strip().lower(), ".bin")


def original_object_path(user_id: str, content_id: str, content_hash: str, mime_type: Optional[str]) -> str:
    """
    원본의 객체 경로. 해시가 경로에 들어가므로:
      * 같은 바이트 재업로드 → 같은 객체 (upsert 무해)
      * 다른 바이트 → 다른 객체 (기존 원본을 덮어쓸 수 없다)
    """
    return f"{user_id}/{content_id}/references/original_{content_hash[:16]}{_ext_for_mime(mime_type)}"


# ── 조회 ────────────────────────────────────────────────────────────────────


async def _rows_for_pet(pet_id: str) -> list[dict[str, Any]]:
    pid = (pet_id or "").strip()
    if not pid:
        return []

    if _use_db() and _supabase():
        try:
            # supabase-py 는 동기 클라이언트다. 이벤트 루프를 막지 않고 스레드로
            # 넘겨야 여러 사진(=여러 요청)의 대장 조회가 실제로 동시에 진행된다.
            def _select():
                return (
                    _supabase()
                    .table(_table())
                    .select(_SELECT)
                    .eq("pet_id", pid)
                    .order("created_at", desc=False)
                    .execute()
                )

            r = await asyncio.to_thread(_select)
            return getattr(r, "data", None) or []
        except Exception as e:
            # pet_registry.get 과 같은 이유로 "없음"으로 답하지 않는다 — 조회 실패를
            # 빈 대장으로 보고하면 소유권 귀속(TOFU)이 우회된다.
            logger.exception("펫 레퍼런스 조회 실패 (pet=%s)", pid)
            raise PetReferenceError(
                "PET_REFERENCES_UNAVAILABLE", "레퍼런스를 확인하지 못했습니다.", status=503
            ) from e

    return [r for r in _MOCK_REFS if r.get("pet_id") == pid]


async def _assert_pet_accessible(user_id: str, pet_id: str) -> list[dict[str, Any]]:
    """
    소유권 확인 + 기존 행 반환.

    1) pets 레지스트리에 등록돼 있으면 그 소유자가 정본이다.
    2) 등록 전이면 기존 레퍼런스 행의 신원이 소유자다 (TOFU).
    """
    from . import pet_registry

    try:
        pet = await pet_registry.get(pet_id)
    except pet_registry.PetRegistryError:
        # 레지스트리를 못 읽는다고 인테이크까지 막지 않는다 — 아래의 레퍼런스
        # 행 기반 검사가 여전히 남의 펫 접근을 거절한다.
        pet = None
    if pet and pet.user_id != user_id:
        raise PetReferenceError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)

    rows = await _rows_for_pet(pet_id)
    if not pet:
        owners = {str(r.get("user_id") or "") for r in rows}
        if owners and user_id not in owners:
            raise PetReferenceError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)
    return rows


async def list_references(*, user_id: str, pet_id: str) -> list[PetReference]:
    """소유권이 확인된 호출자에게 해당 펫의 레퍼런스 전체를 돌려준다."""
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetReferenceError("PET_REFERENCE_INVALID", "user_id 와 pet_id 가 필요합니다.")
    rows = await _assert_pet_accessible(uid, pid)
    return [_to_ref(r) for r in rows]


def pair_cutouts(refs: list[PetReference]) -> dict[str, Optional[PetReference]]:
    """
    원본 레퍼런스 id → 짝지어진 누끼(파생) 레퍼런스.

    parent_reference_id 로 명시적으로 연결된 누끼가 정본이다.

    ── content_id 폴백이 **모호하지 않을 때만** 남는 이유 ────────────────────
    단일 사진 온보딩(Phase 1 훅)은 parent 링크 없이 콘텐츠당 누끼 하나를 남긴다.
    그 레거시 짝짓기는 그대로 살린다 — 단, **그 content_id 의 원본이 정확히
    하나일 때만**이다. 멀티 레퍼런스(한 펫에 원본 2~3장)에서는 모든 원본이 같은
    content_id 를 공유하므로, parent 없는 누끼 하나가 세 원본 전부에 붙어
    "어느 원본의 누끼인지"를 잃는다. 그 경우 폴백을 쓰지 않고 엄격한 부모 링크만
    인정한다. 짝이 없으면 None.

    accepted 가 아닌 누끼는 짝이 되지 않는다. 반면 "원본이 하나인가"는 거절된
    원본까지 **전부** 센다 — 사진을 바꾼 펫에서 물러난 원본의 부모 없는 누끼가
    새 원본에 흘러가면 안 된다.
    """
    cutouts = active_cutouts(refs)
    originals = [r for r in refs if r.role == ROLE_ORIGINAL and r.id]

    originals_per_content: dict[str, int] = {}
    for r in originals:
        originals_per_content[r.content_id] = originals_per_content.get(r.content_id, 0) + 1

    by_parent: dict[str, PetReference] = {}
    by_content: dict[str, PetReference] = {}
    for c in cutouts:
        if c.parent_reference_id:
            by_parent.setdefault(str(c.parent_reference_id), c)
            continue
        # 부모가 없는 누끼만 콘텐츠 수준 폴백 후보다 — 다른 원본에 이미 묶인
        # 누끼가 제3의 원본에 흘러가는 일은 없다.
        by_content.setdefault(c.content_id, c)

    out: dict[str, Optional[PetReference]] = {}
    for r in originals:
        fallback = (
            by_content.get(r.content_id)
            if originals_per_content.get(r.content_id, 0) == 1
            else None
        )
        out[str(r.id)] = by_parent.get(str(r.id)) or fallback
    return out


def strict_cutout_for_original(
    refs: list[PetReference], original_id: Optional[str]
) -> Optional[PetReference]:
    """
    이 원본에 **엄격하게 연결된** 누끼만 돌려준다 (parent_reference_id 일치).

    멀티 레퍼런스 인테이크가 "원본 N ↔ 누끼 N" 을 장마다 확인할 때 쓴다 —
    pair_cutouts 의 레거시 폴백조차 타지 않는다.
    """
    oid = str(original_id or "").strip()
    if not oid:
        return None
    for c in active_cutouts(refs):
        if str(c.parent_reference_id or "") == oid and c.recorded:
            return c
    return None


def strict_lineage_map(refs: list[PetReference]) -> dict[str, dict[str, Any]]:
    """
    accepted original id → 그 원본이 **실제로 소비하는** 누끼의 신원.

    ── 왜 프로필 재사용 키에 이것이 들어가야 하는가 ──────────────────────────
    신원/형태 프로필의 멱등 판정은 원본 집합(source_reference_ids)과 분석기
    버전만 봤다. 그런데 누끼는 원본과 **다른 시점에** 붙는다: 한 장의 누끼
    단계가 실패한 뒤 재시도로 나중에 붙으면 원본 집합은 그대로다. 그래서
    "입력이 안 바뀌었다"로 판정돼, 그 원본은 세그멘테이션이 생긴 뒤에도
    프로필에 영원히 기여하지 못했다.

    여기서 돌려주는 (원본 → 누끼 id + 엄격 계보 여부) 사상이 그 차이를 드러낸다.
    pair_cutouts 와 같은 짝짓기를 쓴다 — 빌더가 실제로 읽는 것과 같은 값이라야
    재사용 판정이 빌드 결과와 어긋나지 않는다.
    """
    pairing = pair_cutouts(refs)
    out: dict[str, dict[str, Any]] = {}
    for r in active_originals(refs):
        if not r.id:
            continue
        rid = str(r.id)
        cut = pairing.get(rid)
        out[rid] = {
            "cutout_reference_id": (str(cut.id) if cut and cut.id else None),
            "strict": bool(cut and cut.parent_reference_id and str(cut.parent_reference_id) == rid),
        }
    return out


def active_originals(refs: list[PetReference]) -> list[PetReference]:
    """
    지금 이 펫의 증거로 **살아 있는** 원본 (accepted).

    원본을 세거나 소비하는 모든 곳(신원/형태 프로필, 레퍼런스 세트, 인테이크
    준비 판정, 장수 상한)이 이 한 곳을 거친다 — 사용자가 뺀 사진(rejected)이
    어느 한 소비자에게만 남아 있는 일이 없도록.
    """
    return [r for r in refs if r.role == ROLE_ORIGINAL and r.acceptance_state == STATE_ACCEPTED]


def active_cutouts(refs: list[PetReference]) -> list[PetReference]:
    """accepted 상태의 누끼(파생)만. 물러난 원본의 누끼는 여기서 빠진다."""
    return [
        r
        for r in refs
        if r.role == ROLE_DERIVED
        and (r.derived_kind or "").startswith("cutout")
        and r.acceptance_state == STATE_ACCEPTED
    ]


def intake_readiness(
    refs: list[PetReference],
) -> tuple[bool, Optional[PetReference], Optional[PetReference]]:
    """Return the accepted original/cutout pair that makes Phase 1 ready."""
    originals = [r for r in active_originals(refs) if r.recorded and r.id]
    paired = pair_cutouts(refs)
    for original in originals:
        cutout = paired.get(str(original.id))
        if (
            cutout
            and cutout.role == ROLE_DERIVED
            and cutout.acceptance_state == STATE_ACCEPTED
            and cutout.recorded
            and cutout.parent_reference_id == original.id
        ):
            return True, original, cutout
    return False, originals[0] if originals else None, None


# ── 기록 ────────────────────────────────────────────────────────────────────


def _next_version(rows: list[dict[str, Any]], role: str) -> int:
    versions = [int(r.get("version") or 0) for r in rows if r.get("role") == role]
    return (max(versions) + 1) if versions else 1


async def _insert_row(row: dict[str, Any]) -> tuple[bool, Optional[Exception]]:
    """(성공 여부, 오류). 유니크 충돌은 호출자가 재조회로 판별한다."""
    if _use_db() and _supabase():
        try:
            await asyncio.to_thread(lambda: _supabase().table(_table()).insert(row).execute())
            return True, None
        except Exception as e:  # noqa: BLE001 — 충돌/장애 판별은 호출자가 한다
            return False, e

    # 인메모리 경로에서도 유니크 인덱스와 같은 규칙을 흉내 낸다.
    for r in _MOCK_REFS:
        if r["pet_id"] != row["pet_id"]:
            continue
        if (
            row["role"] == ROLE_ORIGINAL
            and r["role"] == ROLE_ORIGINAL
            and row.get("content_hash")
            and r.get("content_hash") == row.get("content_hash")
        ):
            return False, PetReferenceError("DUPLICATE", "duplicate original hash")
        if (
            row["role"] in (ROLE_DERIVED, ROLE_GENERATED)
            and r["role"] == row["role"]
            and r["object_path"] == row["object_path"]
        ):
            return False, PetReferenceError("DUPLICATE", "duplicate non-original object")
        if r["role"] == row["role"] and int(r.get("version") or 0) == int(row["version"]):
            return False, PetReferenceError("DUPLICATE", "duplicate version")
    _MOCK_REFS.append(dict(row))
    return True, None


def _find_existing_original(rows: list[dict[str, Any]], content_hash: str) -> Optional[dict[str, Any]]:
    # 상태를 가리지 않는다 — 유니크 인덱스(pet_id, content_hash)도 상태를 보지
    # 않으므로, 거절된 행이 있으면 같은 해시의 새 행은 어차피 삽입될 수 없다.
    for r in rows:
        if r.get("role") == ROLE_ORIGINAL and r.get("content_hash") == content_hash:
            return r
    return None


def _is_superseded(row: dict[str, Any]) -> bool:
    return (
        row.get("acceptance_state") == STATE_REJECTED
        and row.get("rejection_code") == REJECTION_SUPERSEDED_BY_USER
    )


def _linked_derived_rows(rows: list[dict[str, Any]], original_ids: set[str]) -> list[dict[str, Any]]:
    """parent_reference_id 로 이 원본들에 묶인 파생 행 (누끼 등)."""
    return [
        r
        for r in rows
        if r.get("role") == ROLE_DERIVED and str(r.get("parent_reference_id") or "") in original_ids
    ]


async def _update_acceptance_rows(pet_id: str, ids: list[str], patch: dict[str, Any]) -> None:
    """상태 열만 바꾸는 **한 번의 UPDATE 문** (인메모리 경로는 같은 효과)."""
    if _use_db() and _supabase():
        await asyncio.to_thread(
            lambda: _supabase()
            .table(_table())
            .update(patch)
            .eq("pet_id", pet_id)
            .in_("id", ids)
            .execute()
        )
        return

    for r in _MOCK_REFS:
        if r.get("pet_id") == pet_id and str(r.get("id") or "") in ids:
            r.update(patch)


async def _set_acceptance(
    pet_id: str, ids: list[str], *, state: str, rejection_code: Optional[str]
) -> list[dict[str, Any]]:
    """
    여러 행의 acceptance_state 를 **한 번의 UPDATE 문**으로 바꾸고, 다시 읽어
    실제로 바뀌었는지 확인한 뒤 최신 행들을 돌려준다.

    원본과 그 누끼가 같은 문장 안에서 함께 뒤집히므로 "원본만 물러나고 누끼는
    남은" 중간 상태가 커밋되지 않는다.

    ── 왜 다시 읽는가 ──────────────────────────────────────────────────────
    UPDATE 는 0행을 바꾸고도 오류 없이 돌아올 수 있다 (예: anon 키 + RLS 정책이
    쓰기를 조용히 걸러낼 때). 그걸 성공으로 보고하면 물러났어야 할 사진이 계속
    신원에 기여한다. 바뀌지 않은 행이 하나라도 있으면 예외로 올린다.
    """
    wanted = [i for i in dict.fromkeys(str(i) for i in ids if i)]
    if not wanted:
        return await _rows_for_pet(pet_id)
    patch = {"acceptance_state": state, "rejection_code": rejection_code}

    try:
        await _update_acceptance_rows(pet_id, wanted, patch)
    except Exception as e:
        logger.exception("펫 레퍼런스 상태 변경 실패 (pet=%s state=%s)", pet_id, state)
        raise PetReferenceError(
            "PET_REFERENCES_UNAVAILABLE", "레퍼런스 상태를 바꾸지 못했습니다.", status=503
        ) from e

    rows = await _rows_for_pet(pet_id)
    by_id = {str(r.get("id") or ""): r for r in rows}
    unchanged = [
        i
        for i in wanted
        if i not in by_id
        or by_id[i].get("acceptance_state") != state
        or (by_id[i].get("rejection_code") or None) != rejection_code
    ]
    if unchanged:
        logger.error(
            "펫 레퍼런스 상태 변경이 적용되지 않았다 — UPDATE 가 %d/%d 행을 바꾸지 못했다 "
            "(pet=%s state=%s ids=%s). 쓰기 권한(service role / RLS)을 확인할 것.",
            len(unchanged),
            len(wanted),
            pet_id,
            state,
            unchanged,
        )
        raise PetReferenceError(
            "PET_REFERENCE_UPDATE_NOT_APPLIED",
            "레퍼런스 상태 변경이 저장되지 않았습니다.",
            status=503,
        )
    return rows


async def _existing_original_ref(
    pet_id: str, rows: list[dict[str, Any]], existing: dict[str, Any]
) -> PetReference:
    """
    같은 바이트의 기존 원본 행을 돌려준다. 사용자가 뺐던(SUPERSEDED_BY_USER)
    행이면 연결된 누끼와 함께 accepted 로 되살린다 — 새 행을 만들지 않으므로
    유니크 인덱스와 누끼 경로(cutout_{hash16}.png)가 그대로 맞물린다.
    """
    if not _is_superseded(existing):
        return _to_ref(existing, deduplicated=True)

    oid = str(existing.get("id") or "")
    ids = [oid] + [
        str(r.get("id") or "") for r in _linked_derived_rows(rows, {oid}) if _is_superseded(r)
    ]
    await _set_acceptance(pet_id, ids, state=STATE_ACCEPTED, rejection_code=None)
    revived = {**existing, "acceptance_state": STATE_ACCEPTED, "rejection_code": None}
    return _to_ref(revived, deduplicated=True, reactivated=True)


# ── 사용자가 뺀 사진의 퇴장 / 동기화 ─────────────────────────────────────────


async def _reject_originals(pet_id: str, rows: list[dict[str, Any]], original_ids: set[str]) -> list[str]:
    """accepted 인 원본들과 그에 묶인 파생 행을 함께 거절한다. 바뀐 id 목록을 돌려준다."""
    targets = [
        str(r.get("id") or "")
        for r in rows
        if r.get("role") == ROLE_ORIGINAL
        and str(r.get("id") or "") in original_ids
        and r.get("acceptance_state") == STATE_ACCEPTED
    ]
    # 원본이 이미 물러났더라도, 뒤늦게 붙은 accepted 누끼는 마저 거절한다 (멱등).
    targets += [
        str(r.get("id") or "")
        for r in _linked_derived_rows(rows, original_ids)
        if r.get("acceptance_state") == STATE_ACCEPTED
    ]
    await _set_acceptance(
        pet_id, targets, state=STATE_REJECTED, rejection_code=REJECTION_SUPERSEDED_BY_USER
    )
    return targets


# ── 펫 입력 잠금 (생성이 시작된 뒤에는 사진·누끼를 바꿀 수 없다) ─────────────


class _PetInputGate:
    """
    펫 하나의 입력 변경(다수 동시 허용)과 생성 실행 만들기(단독)를 가르는 문.

    변경 요청은 shared() 안에서 "잠겼는가 확인 → 쓰기"를 하고, 실행 생성은
    exclusive() 안에서 "인테이크 검증 → 실행 삽입"을 한다. 그래서 변경이 확인과
    쓰기 사이에 실행 생성에 추월당하거나, 실행 생성이 검증과 삽입 사이에 변경에
    추월당하지 않는다. 같은 펫의 사진 여러 장은 여전히 동시에 올라간다.

    **프로세스 안에서만** 유효하다. API 가 여러 프로세스/인스턴스로 늘어나면
    서로 다른 프로세스의 변경과 실행 생성은 이 문으로 직렬화되지 않는다.
    """

    def __init__(self) -> None:
        self._shared = 0
        self._exclusive = False
        self._waiters: list[asyncio.Future] = []

    async def _wait(self) -> None:
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)

    def _wake(self) -> None:
        for fut in list(self._waiters):
            if not fut.done():
                fut.get_loop().call_soon_threadsafe(
                    lambda f=fut: f.done() or f.set_result(None)
                )

    @asynccontextmanager
    async def shared(self):
        while self._exclusive:
            await self._wait()
        self._shared += 1
        try:
            yield
        finally:
            self._shared -= 1
            self._wake()

    @asynccontextmanager
    async def exclusive(self):
        while self._exclusive or self._shared:
            await self._wait()
        self._exclusive = True
        try:
            yield
        finally:
            self._exclusive = False
            self._wake()


_INPUT_GATES: dict[str, _PetInputGate] = {}


def pet_input_gate(pet_id: str) -> _PetInputGate:
    pid = (pet_id or "").strip()
    gate = _INPUT_GATES.get(pid)
    if gate is None:
        gate = _PetInputGate()
        _INPUT_GATES[pid] = gate
    return gate


async def pet_inputs_locked(pet_id: str, refs: Optional[list[PetReference]] = None) -> bool:
    """
    이 펫의 사진·누끼가 잠겼는가. **잠금의 유일한 정의다** — 업로드, 누끼 교체,
    동기화, 거절이 모두 이 함수 하나를 본다.

    잠김 = 다음 중 하나:
      (1) 대장에 생성 자산(role='generated': 정본/키프레임/모션)이 하나라도 있다
      (2) FAILED/CANCELLED 가 아닌 생성 실행이 하나라도 있다
    FAILED/CANCELLED 실행만 있는 펫은 생성 자산이 없을 때에만 풀려 있다.

    판정할 수 없으면(대장·실행 조회 실패) 예외(503)다 — 모르는 것을 "안 잠김"
    으로 답하지 않는다. refs 를 주면 그 대장을 쓰고, 없으면 여기서 읽는다.
    """
    pid = (pet_id or "").strip()
    if not pid:
        return False
    ledger = refs if refs is not None else [_to_ref(r) for r in await _rows_for_pet(pid)]
    if any(r.role == ROLE_GENERATED for r in ledger):
        return True
    from . import pet_generation_run_service

    try:
        return await pet_generation_run_service.pet_has_locking_run(pid)
    except pet_generation_run_service.PetGenerationRunError as e:
        raise PetReferenceError(e.code, e.message, status=e.status) from e


async def assert_pet_inputs_unlocked(
    pet_id: str, refs: Optional[list[PetReference]] = None
) -> None:
    """잠긴 펫이면 409 PHASE1_LOCKED. 판정 불가는 503 (pet_inputs_locked 참고)."""
    if await pet_inputs_locked(pet_id, refs):
        raise PetReferenceError(
            "PHASE1_LOCKED",
            "생성이 시작된 뒤에는 사진과 누끼를 바꿀 수 없습니다.",
            status=409,
        )


async def supersede_cutouts(*, user_id: str, pet_id: str, reference_ids: list[str]) -> None:
    """
    누끼(파생) 행을 CUTOUT_REPLACED_BY_USER 로 물린다 — 같은 원본에 다른 누끼가
    들어설 자리를 비운다. 행과 객체는 그대로 남으므로 과거 계보는 계속 조회된다.
    원본에는 쓸 수 없다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    wanted = {str(i) for i in (reference_ids or []) if i}
    if not uid or not pid:
        raise PetReferenceError("PET_REFERENCE_INVALID", "user_id 와 pet_id 가 필요합니다.")
    rows = await _assert_pet_accessible(uid, pid)
    targets = [r for r in rows if str(r.get("id") or "") in wanted]
    if len(targets) != len(wanted) or any(r.get("role") != ROLE_DERIVED for r in targets):
        raise PetReferenceError(
            "PET_REFERENCE_NOT_FOUND", "교체할 누끼 레퍼런스가 없습니다.", status=404
        )
    await _set_acceptance(
        pid,
        [str(r["id"]) for r in targets if r.get("acceptance_state") == STATE_ACCEPTED],
        state=STATE_REJECTED,
        rejection_code=REJECTION_CUTOUT_REPLACED,
    )


async def reject_original(*, user_id: str, pet_id: str, reference_id: str) -> list[PetReference]:
    """
    원본 한 장과 그 누끼를 SUPERSEDED_BY_USER 로 거절한다. **멱등하다.**
    잠긴 펫(pet_inputs_locked)이면 409 PHASE1_LOCKED 다.

    행을 지우지 않는다 — 과거 세트/정본/키프레임이 핀으로 잡은 id 는 계속
    조회된다(list_references 는 모든 상태를 돌려준다). 새 빌드만 이 원본을
    더 이상 보지 않는다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    rid = (reference_id or "").strip()
    if not uid or not pid or not rid:
        raise PetReferenceError(
            "PET_REFERENCE_INVALID", "user_id, pet_id, reference_id 가 필요합니다."
        )
    async with pet_input_gate(pid).shared():
        rows = await _assert_pet_accessible(uid, pid)
        await assert_pet_inputs_unlocked(pid, [_to_ref(r) for r in rows])
        target = next((r for r in rows if str(r.get("id") or "") == rid), None)
        if not target or target.get("role") != ROLE_ORIGINAL:
            raise PetReferenceError(
                "PET_REFERENCE_NOT_FOUND", "거절할 원본 레퍼런스가 없습니다.", status=404
            )
        # 다른 이유로 이미 거절된 원본의 코드를 덮어쓰지 않는다.
        if target.get("acceptance_state") == STATE_REJECTED and not _is_superseded(target):
            return [_to_ref(r) for r in rows]
        await _reject_originals(pid, rows, {rid})
        return [_to_ref(r) for r in await _rows_for_pet(pid)]


@dataclass(frozen=True)
class OriginalSyncResult:
    #: 동기화 후 살아 있는 원본 (accepted).
    active: list[PetReference]
    #: 이번 호출이 거절한 원본 id.
    rejected_original_ids: list[str]
    #: UI 에는 있지만 대장에 accepted 원본이 없는 해시 (업로드 실패/미도착).
    missing_hashes: list[str]


async def sync_active_originals(
    *, user_id: str, pet_id: str, content_hashes: list[str]
) -> OriginalSyncResult:
    """
    대장을 사용자의 **현재 사진 집합**에 맞춘다.

    content_hashes 는 지금 UI 에 있는 모든 사진의 sha256 이다. 그 목록에 없는
    accepted 원본은 누끼와 함께 거절되고, 목록에 있는 원본은 절대 거절되지
    않는다. id 가 아니라 해시로 받는 이유: 이번 패스에서 업로드가 실패한
    사진이라도 UI 에 남아 있는 한, 이전에 accepted 된 행은 살아 있어야 한다.

    ── 알려진 한계: 읽기와 쓰기 사이의 창 ───────────────────────────────────
    "어느 행을 물릴지"는 먼저 읽어서 정하고, 그 id 들을 한 번의 UPDATE 로 물린 뒤
    다시 읽어 확인한다. 읽기와 UPDATE 는 한 트랜잭션이 아니다. 그 사이에 삽입된
    원본/누끼는 이번 동기화가 보지 못한다 — 목록에 있는 사진을 잘못 물리는 일은
    없고(물릴 대상은 해시로 정해진다), 놓친 행은 다음 동기화가 거둔다.
    클라이언트는 펫당 한 번에 한 패스만 돌려 이 창을 피한다
    (src/lib/reference-sync.ts). 원자적 SQL 함수는 실제 Supabase 통합 테스트가
    생긴 뒤에 다시 검토한다.

    여기서 되살리지는 않는다 — 재활성화는 바이트가 다시 올라올 때
    (record_original) 일어난다. 목록이 비면 거절한다: 빈 목록은 "사진 없음"이
    아니라 클라이언트 오류일 가능성이 높고, 전부 물리면 되돌릴 근거가 없다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetReferenceError("PET_REFERENCE_INVALID", "user_id 와 pet_id 가 필요합니다.")
    keep = {str(h or "").strip().lower() for h in (content_hashes or [])}
    keep.discard("")
    if not keep:
        raise PetReferenceError(
            "PET_REFERENCE_SYNC_EMPTY", "현재 사진의 해시가 최소 1개 필요합니다."
        )

    async with pet_input_gate(pid).shared():
        rows = await _assert_pet_accessible(uid, pid)
        # 바꿀 것이 없더라도 잠긴 펫에는 PHASE1_LOCKED 로 답한다 — 클라이언트가
        # "이 펫은 잠겼다"를 이 응답으로 안다.
        await assert_pet_inputs_unlocked(pid, [_to_ref(r) for r in rows])
        stale = {
            str(r.get("id") or "")
            for r in rows
            if r.get("role") == ROLE_ORIGINAL
            and r.get("acceptance_state") == STATE_ACCEPTED
            and str(r.get("content_hash") or "").lower() not in keep
        }
        stale.discard("")
        if stale:
            await _reject_originals(pid, rows, stale)
            rows = await _rows_for_pet(pid)

    active = active_originals([_to_ref(r) for r in rows])
    active_hashes = {str(r.content_hash or "").lower() for r in active}
    return OriginalSyncResult(
        active=active,
        rejected_original_ids=sorted(stale),
        missing_hashes=sorted(keep - active_hashes),
    )


async def record_original(
    *,
    user_id: str,
    content_id: str,
    data: bytes,
    mime_type: Optional[str] = None,
    original_filename: Optional[str] = None,
    source: str = SOURCE_APP,
    view_label: str = VIEW_UNKNOWN,
    detection: Optional[dict[str, Any]] = None,
    person_detected: Optional[bool] = None,
    diagnostics: Optional[dict[str, Any]] = None,
    acceptance_state: str = STATE_ACCEPTED,
    rejection_code: Optional[str] = None,
) -> PetReference:
    """
    원본을 스토리지에 영구 저장하고 대장에 기록한다. **바이트 기준으로 멱등하다.**

    같은 펫에 같은 바이트가 다시 들어오면 기존 행을 그대로 돌려준다(새 버전을
    만들지 않는다). 스토리지 업로드 실패는 예외로 올린다 — 원본이 durable 하지
    않은데 성공처럼 보이면 안 된다. 행 삽입 실패는 recorded=False 로 보고한다
    (바이트는 이미 안전하다).
    """
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    if not uid or not cid:
        raise PetReferenceError("PET_REFERENCE_INVALID", "user_id 와 content_id 가 필요합니다.")
    if not data:
        raise PetReferenceError("PET_REFERENCE_EMPTY", "이미지 데이터가 비어 있습니다.")

    pid = pet_id_for_content(cid)
    rows = await _assert_pet_accessible(uid, pid)

    content_hash = hashlib.sha256(data).hexdigest()
    existing = _find_existing_original(rows, content_hash)
    if existing:
        return await _existing_original_ref(pid, rows, existing)

    path = original_object_path(uid, cid, content_hash, mime_type)

    from . import supabase_assets

    # 업로드가 곧 durable 보장이다. 실패는 그대로 올린다.
    await supabase_assets.upload_asset_to_storage(path, data, mime_type or "application/octet-stream")

    width, height = _image_dimensions(data)
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "content_id": cid,
        "user_id": uid,
        "role": ROLE_ORIGINAL,
        "source": source,
        "derived_kind": None,
        "parent_reference_id": None,
        "bucket": _bucket(),
        "object_path": path,
        "original_filename": (original_filename or None),
        "mime_type": (mime_type or None),
        "width": width,
        "height": height,
        "bytes_size": len(data),
        "content_hash": content_hash,
        "view_label": view_label or VIEW_UNKNOWN,
        "acceptance_state": acceptance_state,
        "rejection_code": rejection_code,
        "detection": detection,
        "person_detected": person_detected,
        "diagnostics": diagnostics,
        "version": _next_version(rows, ROLE_ORIGINAL),
        "created_at": _now_iso(),
    }

    # 버전 경쟁은 재시도로 푼다. 해시 충돌(같은 바이트 동시 삽입)은 기존 행 반환.
    for _ in range(3):
        ok, err = await _insert_row(row)
        if ok:
            return _to_ref(row)
        again = await _rows_for_pet(pid)
        dup = _find_existing_original(again, content_hash)
        if dup:
            return await _existing_original_ref(pid, again, dup)
        row["version"] = _next_version(again, ROLE_ORIGINAL)
        last_err = err

    logger.error("원본 레퍼런스 행 기록 실패 (pet=%s): %s", pid, last_err)
    return _to_ref(row, recorded=False)


async def record_derived(
    *,
    user_id: str,
    content_id: str,
    object_path: str,
    derived_kind: str,
    bucket: Optional[str] = None,
    source: str = SOURCE_PIPELINE,
    parent_reference_id: Optional[str] = None,
    mime_type: Optional[str] = None,
    diagnostics: Optional[dict[str, Any]] = None,
    detection: Optional[dict[str, Any]] = None,
    person_detected: Optional[bool] = None,
) -> PetReference:
    """
    **이미 저장된** 파생 객체(누끼 등)를 대장에 기록한다. 여기서는 아무것도
    업로드하지 않는다 — 파생 기록이 원본 객체를 건드릴 방법 자체가 없다.
    같은 (pet, object_path) 는 한 번만 기록된다.
    """
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    path = (object_path or "").strip()
    kind = (derived_kind or "").strip()
    if not uid or not cid or not path or not kind:
        raise PetReferenceError(
            "PET_REFERENCE_INVALID",
            "user_id, content_id, object_path, derived_kind 가 필요합니다.",
        )

    pid = pet_id_for_content(cid)
    rows = await _assert_pet_accessible(uid, pid)

    if parent_reference_id:
        parent = next((r for r in rows if str(r.get("id") or "") == parent_reference_id), None)
        if (
            not parent
            or parent.get("role") != ROLE_ORIGINAL
            or parent.get("user_id") != uid
            or parent.get("content_id") != cid
        ):
            raise PetReferenceError(
                "PET_REFERENCE_PARENT_INVALID",
                "파생 레퍼런스의 원본 연결이 유효하지 않습니다.",
                status=409,
            )

    for r in rows:
        if r.get("role") == ROLE_DERIVED and r.get("object_path") == path:
            existing_parent = str(r.get("parent_reference_id") or "")
            if parent_reference_id and existing_parent != parent_reference_id:
                raise PetReferenceError(
                    "PET_REFERENCE_PARENT_CONFLICT",
                    "이미 다른 원본에 연결된 파생 레퍼런스입니다.",
                    status=409,
                )
            # 원본은 살아 있는데 누끼만 물러난 채 남은 경우(부분 실패, 또는 교체로
            # 물러났던 누끼 바이트가 다시 온 경우)를 여기서 푼다. 원본이 여전히
            # 물러나 있으면 누끼도 그대로 둔다.
            owner = next(
                (o for o in rows if str(o.get("id") or "") == existing_parent), None
            )
            if (
                r.get("acceptance_state") == STATE_REJECTED
                and r.get("rejection_code")
                in (REJECTION_SUPERSEDED_BY_USER, REJECTION_CUTOUT_REPLACED)
                and owner
                and owner.get("acceptance_state") == STATE_ACCEPTED
            ):
                await _set_acceptance(
                    pid, [str(r.get("id") or "")], state=STATE_ACCEPTED, rejection_code=None
                )
                revived = {**r, "acceptance_state": STATE_ACCEPTED, "rejection_code": None}
                return _to_ref(revived, deduplicated=True, reactivated=True)
            return _to_ref(r, deduplicated=True)

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "content_id": cid,
        "user_id": uid,
        "role": ROLE_DERIVED,
        "source": source,
        "derived_kind": kind,
        "parent_reference_id": parent_reference_id,
        "bucket": (bucket or _bucket()),
        "object_path": path,
        "original_filename": None,
        "mime_type": (mime_type or None),
        "width": None,
        "height": None,
        "bytes_size": None,
        "content_hash": None,
        "view_label": VIEW_UNKNOWN,
        "acceptance_state": STATE_ACCEPTED,
        "rejection_code": None,
        "detection": detection,
        "person_detected": person_detected,
        "diagnostics": diagnostics,
        "version": _next_version(rows, ROLE_DERIVED),
        "created_at": _now_iso(),
    }

    for _ in range(3):
        ok, err = await _insert_row(row)
        if ok:
            return _to_ref(row)
        again = await _rows_for_pet(pid)
        for r in again:
            if r.get("role") == ROLE_DERIVED and r.get("object_path") == path:
                return _to_ref(r, deduplicated=True)
        row["version"] = _next_version(again, ROLE_DERIVED)
        last_err = err

    logger.error("파생 레퍼런스 행 기록 실패 (pet=%s path=%s): %s", pid, path, last_err)
    return _to_ref(row, recorded=False)


async def record_generated(
    *,
    user_id: str,
    content_id: str,
    object_path: str,
    generated_kind: str,
    bucket: Optional[str] = None,
    mime_type: Optional[str] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> PetReference:
    """
    **합성(생성)** 자산을 대장에 기록한다 (Phase 4 정본 펫 등).

    role='generated' 다 — original 로 기록될 방법이 없다(별도 함수, 역할 고정).
    provenance(정본 버전/후보/입력 레퍼런스 id)는 diagnostics 에 남는다.
    record_derived 처럼 업로드하지 않는다 — 이미 저장된 객체를 가리키기만 한다.
    """
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    path = (object_path or "").strip()
    kind = (generated_kind or "").strip()
    if not uid or not cid or not path or not kind:
        raise PetReferenceError(
            "PET_REFERENCE_INVALID",
            "user_id, content_id, object_path, generated_kind 가 필요합니다.",
        )

    pid = pet_id_for_content(cid)
    rows = await _assert_pet_accessible(uid, pid)

    for r in rows:
        if r.get("role") == ROLE_GENERATED and r.get("object_path") == path:
            return _to_ref(r, deduplicated=True)

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "content_id": cid,
        "user_id": uid,
        "role": ROLE_GENERATED,
        "source": SOURCE_PIPELINE,
        "derived_kind": kind,
        "parent_reference_id": None,
        "bucket": (bucket or _bucket()),
        "object_path": path,
        "original_filename": None,
        "mime_type": (mime_type or None),
        "width": None,
        "height": None,
        "bytes_size": None,
        "content_hash": None,
        "view_label": VIEW_UNKNOWN,
        "acceptance_state": STATE_ACCEPTED,
        "rejection_code": None,
        "detection": None,
        "person_detected": None,
        "diagnostics": provenance,
        "version": _next_version(rows, ROLE_GENERATED),
        "created_at": _now_iso(),
    }

    for _ in range(3):
        ok, err = await _insert_row(row)
        if ok:
            return _to_ref(row)
        again = await _rows_for_pet(pid)
        for r in again:
            if r.get("role") == ROLE_GENERATED and r.get("object_path") == path:
                return _to_ref(r, deduplicated=True)
        row["version"] = _next_version(again, ROLE_GENERATED)
        last_err = err

    logger.error("생성 레퍼런스 행 기록 실패 (pet=%s path=%s): %s", pid, path, last_err)
    return _to_ref(row, recorded=False)
