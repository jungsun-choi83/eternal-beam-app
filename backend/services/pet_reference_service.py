"""
펫 레퍼런스 대장 (Durable Pet Identity Intake, Phase 1).

── 무엇을 하는가 ───────────────────────────────────────────────────────────
사용자가 준 **원본 사진**을 스토리지에 영구 보존하고, 펫당 여러 장의
레퍼런스를 append-only 로 기록한다(pet_reference_images). 이후 신원
파이프라인(멀티뷰 → 정본 펫 이미지 → 액션 키프레임)의 출발점이다.

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


_SELECT = (
    "id, pet_id, content_id, user_id, role, source, derived_kind, parent_reference_id, "
    "bucket, object_path, original_filename, mime_type, width, height, bytes_size, "
    "content_hash, view_label, acceptance_state, rejection_code, version, created_at, "
    "detection, person_detected, diagnostics"
)


def _to_ref(row: dict[str, Any], *, recorded: bool = True, deduplicated: bool = False) -> PetReference:
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
    """
    cutouts = [
        r
        for r in refs
        if r.role == ROLE_DERIVED and (r.derived_kind or "").startswith("cutout")
    ]
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
    for c in refs:
        if (
            c.role == ROLE_DERIVED
            and (c.derived_kind or "").startswith("cutout")
            and str(c.parent_reference_id or "") == oid
            and c.acceptance_state == STATE_ACCEPTED
            and c.recorded
        ):
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
    for r in refs:
        if r.role != ROLE_ORIGINAL or r.acceptance_state != STATE_ACCEPTED or not r.id:
            continue
        rid = str(r.id)
        cut = pairing.get(rid)
        out[rid] = {
            "cutout_reference_id": (str(cut.id) if cut and cut.id else None),
            "strict": bool(cut and cut.parent_reference_id and str(cut.parent_reference_id) == rid),
        }
    return out


def intake_readiness(
    refs: list[PetReference],
) -> tuple[bool, Optional[PetReference], Optional[PetReference]]:
    """Return the accepted original/cutout pair that makes Phase 1 ready."""
    originals = [
        r
        for r in refs
        if r.role == ROLE_ORIGINAL
        and r.acceptance_state == STATE_ACCEPTED
        and r.recorded
        and r.id
    ]
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
    for r in rows:
        if r.get("role") == ROLE_ORIGINAL and r.get("content_hash") == content_hash:
            return r
    return None


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
        return _to_ref(existing, deduplicated=True)

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
            return _to_ref(dup, deduplicated=True)
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
