"""
판매자/운영(Seller/Ops) 측 Shaker 관리 — **소유 모델의 판매자 쪽**.

    ETERNAL BEAM 이 소유: Shaker 앱 · /shaker · API · 펫 조회 · 영상 접근 ·
                          보안/공유 토큰 · **QR 생성** · 운영 도구
    사용자가 소유:        자기 펫 프로필 · 펫 콘텐츠 · 생성된 경험 ·
                          물리 편지/메모리 카드 · **그 펫으로 가는 개인 QR 링크**

즉 QR 을 **만드는 주체**는 판매자이고, 만들어진 링크가 가리키는 펫과 그 링크
자체는 고객의 것이다. 이 모듈은 그 "만드는 쪽"의 최소 기능만 담는다.

── 이 모듈이 하지 않는 것 ───────────────────────────────────────────────────
펫을 만들지 않는다. **canonical petId 는 이미 존재한다** — 고객이 사진을 올려
생성 파이프라인을 돌린 결과다. 운영은 그것을 **찾을** 뿐 새로 만들지 않는다.
QR·편지·메모리 박스·웹앱·기기가 전부 같은 petId 를 가리키는 것이 요구사항이고,
그 보장은 "여기에 펫 생성 경로가 없다"로 성립한다.

생성하지 않는다. 프로바이더를 호출하지 않는다. 과금하지 않는다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException

from ..auth import AuthedUser, require_user

logger = logging.getLogger(__name__)


class OpsError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── 인가 ──────────────────────────────────────────────────────────────────────

_OPS_ENV = "SHAKER_OPS_USER_IDS"


def ops_user_ids() -> set[str]:
    """
    운영 권한을 가진 Eternal Beam user_id 목록 (쉼표 구분).

    **설정하지 않으면 아무도 운영자가 아니다.** 공유 시크릿 하나로 여는 방식을
    쓰지 않은 이유: 그러면 "누가 이 QR 을 만들었는가"가 남지 않는다. 물리 제품에
    인쇄되어 나가는 링크라 감사 추적이 필요하고, 기존 JWT 인증을 그대로 쓰면
    user_id 가 로그에 남는다.
    """
    raw = (os.getenv(_OPS_ENV) or "").strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def is_ops_user(user_id: str | None) -> bool:
    uid = (user_id or "").strip().lower()
    return bool(uid) and uid in ops_user_ids()


async def require_ops(user: AuthedUser = Depends(require_user)) -> AuthedUser:
    """
    운영자 전용 의존성. 인증 **위에** 얹는다 — 익명 운영자는 없다.

    fail closed: 환경변수가 없으면 전원 403 이다. 운영 도구가 안 열리는 것은
    즉시 눈에 띄고 되돌리기 쉽지만, 실수로 열리면 남의 펫으로 QR 을 찍을 수 있다.
    """
    if not is_ops_user(user.user_id):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "OPS_FORBIDDEN",
                "message": "운영 권한이 없습니다.",
            },
        )
    return user


# ── 펫 찾기 · 소유자 확인 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PetSummary:
    """운영 화면이 보는 펫 한 건. 고객 개인정보는 최소한만 담는다."""

    pet_id: str
    #: 소유 고객. 운영자는 이것을 봐야 올바른 펫인지 확인할 수 있다.
    owner_user_id: str
    #: 이 펫에 승격된 canonical 모션 개수 (경험이 실제로 있는지 가늠).
    ready_count: int
    source: str
    created_at: Optional[str] = None


@dataclass(frozen=True)
class PetSearchResult:
    pets: list[PetSummary]
    degraded: bool = False
    registry_available: bool = True


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


def _motions_table() -> str:
    return os.getenv("GENERATED_MOTIONS_TABLE", "generated_motions")


async def search_pets(
    query: str | None = None, *, limit: int = 200, include_legacy: bool = False
) -> PetSearchResult:
    """
    운영자가 고객 펫을 찾는다. pet_id / user_id / content_id 부분 일치.

    ── 출처 (Phase 13.2 에서 고침) ────────────────────────────────────────────
    **1순위: pets 레지스트리.** BREATHING 만 있는 무료 펫도 여기 있다.

    예전에는 generated_motions 하나만 봤는데, 그건 **프리미엄 모션이 승격됐을
    때만** 채워지는 테이블이다. 그래서 무료 펫이 운영 콘솔에 아예 나타나지 않았고,
    QR 제품의 주 고객(기기 없는 무료 사용자)이 생산 파이프라인에 들어올 수 없었다.

    2순위(과도기): generated_motions — 레지스트리 이전에 만들어진 프리미엄 펫.
    백필이 끝나면 이 폴백은 걷어낼 수 있다.
    """
    q = (query or "").strip().lower()

    # 기본 목록의 유일한 출처는 레지스트리다. 최신 등록 순서를 보존한 채 필터한
    # 다음에 limit 을 적용하므로 UUID 문자열 순서가 운영 우선순위를 정하지 않는다.
    registry_rows = []
    registry_available = True
    try:
        from . import pet_registry

        registry_rows = await pet_registry.search(q or None, limit=2000)
    except Exception as e:
        registry_available = False
        logger.exception("펫 레지스트리 검색 실패")
        if not include_legacy:
            raise OpsError(
                "PET_REGISTRY_UNAVAILABLE", "등록 펫 목록을 조회하지 못했습니다.", status=503
            ) from e

    def _created(row) -> str:
        return str(row.created_at or "")

    registry_rows.sort(key=_created, reverse=True)
    registry_pets = [
        PetSummary(
            pet_id=row.pet_id,
            owner_user_id=row.user_id,
            ready_count=0,
            source="REGISTRY",
            created_at=row.created_at,
        )
        for row in registry_rows
    ]

    if not include_legacy:
        return PetSearchResult(
            pets=registry_pets[: max(1, min(limit, 200))],
            registry_available=True,
        )

    rows: list[tuple[str, str]] = []  # (user_id, pet_id)

    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_motions_table())
                .select("user_id, pet_id")
                .limit(2000)
                .execute()
            )
            rows = [
                (str(d.get("user_id") or ""), str(d.get("pet_id") or ""))
                for d in (getattr(r, "data", None) or [])
            ]
        except Exception as e:
            logger.exception("운영 펫 검색 실패")
            raise OpsError("PET_SEARCH_UNAVAILABLE", "펫을 조회하지 못했습니다.", status=503) from e
    else:
        from . import generated_motions_service as motions_svc

        rows = [(m.user_id, m.pet_id) for m in motions_svc._MOCK_MOTIONS.values()]

    # legacy opt-in: 레지스트리 행은 REGISTRY 로 유지하고, 레지스트리에 없는
    # generated_motions 펫만 LEGACY 로 덧붙인다.
    registry_keys = {(p.owner_user_id, p.pet_id) for p in registry_pets}
    counts: dict[tuple[str, str], int] = {}
    for uid, pid in rows:
        if not uid or not pid:
            continue
        if q and q not in pid.lower() and q not in uid.lower():
            continue
        key = (uid, pid)
        if key not in registry_keys:
            counts[key] = counts.get(key, 0) + 1

    legacy_pets = [
        PetSummary(pet_id=pid, owner_user_id=uid, ready_count=n, source="LEGACY")
        for (uid, pid), n in counts.items()
    ]
    legacy_pets.sort(key=lambda p: (p.pet_id, p.owner_user_id))
    combined = registry_pets + legacy_pets
    return PetSearchResult(
        pets=combined[: max(1, min(limit, 200))],
        degraded=not registry_available,
        registry_available=registry_available,
    )


async def resolve_pet_owner(pet_id: str) -> str:
    """
    이 petId 의 소유 고객. 없으면 예외.

    **운영자가 소유자를 입력하지 않는 것이 요점이다.** 손으로 넣게 하면 오타 하나로
    남의 펫에 QR 이 붙고, 그 QR 은 이미 인쇄된 뒤다. 서버가 canonical 바인딩에서
    직접 읽는다.

    1순위는 pets 레지스트리(무료 펫 포함), 2순위는 generated_motions(레거시)다.
    """
    pid = (pet_id or "").strip()
    if not pid:
        raise OpsError("PET_REQUIRED", "pet_id 가 필요합니다.")

    try:
        from . import pet_registry

        owner = await pet_registry.owner_of(pid)
        if owner:
            return owner
    except Exception:  # noqa: BLE001 — 레지스트리 장애 시 레거시로 내려간다
        logger.warning("펫 레지스트리 소유자 조회 실패 — 레거시 경로 시도 (pet=%s)", pid)

    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_motions_table())
                .select("user_id")
                .eq("pet_id", pid)
                .limit(1)
                .execute()
            )
            data = getattr(r, "data", None) or []
            if data and data[0].get("user_id"):
                return str(data[0]["user_id"])
        except Exception as e:
            logger.exception("운영 소유자 조회 실패 (pet=%s)", pid)
            raise OpsError(
                "PET_OWNER_UNAVAILABLE", "펫 소유자를 확인하지 못했습니다.", status=503
            ) from e
    else:
        from . import generated_motions_service as motions_svc

        for m in motions_svc._MOCK_MOTIONS.values():
            if m.pet_id == pid:
                return m.user_id

    raise OpsError(
        "PET_NOT_FOUND",
        "이 pet_id 로 만들어진 펫 경험을 찾을 수 없습니다.",
        status=404,
    )


# ── BREATHING 위치 찾기 ───────────────────────────────────────────────────────


def content_id_from_pet_id(pet_id: str) -> Optional[str]:
    """
    petId → content_id.

    프론트의 불변식이다: `derivePetIdFromContent(cid) = "pet_" + cid`
    (src/lib/pet-identity.ts). 그래서 접두사를 떼면 content_id 가 나온다.
    이 규칙을 따르지 않는 pet_id(크레딧 세션이 서버에서 발급한 값)는 None 이다.
    """
    pid = (pet_id or "").strip()
    if not pid.startswith("pet_") or len(pid) <= 4:
        return None
    return pid[4:]


@dataclass(frozen=True)
class BreathingLocation:
    bucket: str
    object_path: str


def derive_breathing_location(user_id: str, pet_id: str) -> Optional[BreathingLocation]:
    """
    BREATHING 의 스토리지 위치를 **규약에서** 유도한다.

    generate.py 가 `{user_id}/{content_id}/idle_loop.mp4` 로 올린다. 운영자는
    고객의 브라우저 세션(pipeline.idle_video_url)을 볼 수 없으므로, 서버가 알 수
    있는 유일한 경로가 이 규약이다.

    **유도가 곧 존재 증명은 아니다.** 확인은 호출부가 서명을 시도해서 한다 —
    없는 객체에는 서명이 만들어지지 않으므로, 서명 성공이 존재 확인을 겸한다.
    별도의 목록 조회 API 를 두지 않는 이유가 이것이다.
    """
    uid = (user_id or "").strip()
    cid = content_id_from_pet_id(pet_id)
    if not uid or not cid:
        return None
    from .asset_url_refresh import default_bucket

    return BreathingLocation(bucket=default_bucket(), object_path=f"{uid}/{cid}/idle_loop.mp4")


async def locate_breathing(user_id: str, pet_id: str) -> Optional[tuple[BreathingLocation, str]]:
    """
    (위치, 지금 유효한 URL) — 찾지 못하면 None.

    ── 출처 순서 (Phase 13.2 후속 수정) ──────────────────────────────────────
    **1순위: pets 레지스트리에 저장된 실제 위치.** 앱이 BREATHING 완료 직후
    등록하며 저장한 (breathing_bucket, breathing_object_path) 다. 이것이 정본이다.

    2순위: `{user_id}/{content_id}/idle_loop.mp4` 규약 유도.

    ── 왜 레지스트리를 먼저 봐야 하는가 ─────────────────────────────────────
    레지스트리의 **소유자는 canonical user_id**(인증 토큰)인데, **객체 경로는
    생성 시점 신원**을 접두사로 갖는다. 둘이 다를 수 있다(알려진 한계 #8).
    그때 규약만 유도하면 존재하지 않는 경로가 나와 서명이 실패하고, 운영은
    "BREATHING 이 아직 생성되지 않았다"는 409 를 받는다 — 이미 생성된 펫을
    다시 만들라는 뜻이 되고, canonical petId 가 갈라지는 입구가 된다.

    서명이 성공하면 객체가 실제로 있다는 뜻이다. 두 후보 모두 실패하면 규약 밖에
    저장된 펫이므로 운영자가 URL 을 직접 넘겨야 한다.

    **조회만 한다** — 생성하지 않고, 없는 자산을 만들지 않는다.
    """
    from .asset_url_refresh import StorageObject, default_bucket, sign_object

    candidates: list[BreathingLocation] = []

    try:
        from . import pet_registry

        pet = await pet_registry.get(pet_id)
        if pet and pet.breathing_object_path:
            candidates.append(
                BreathingLocation(
                    bucket=(pet.breathing_bucket or "").strip() or default_bucket(),
                    object_path=pet.breathing_object_path,
                )
            )
    except Exception:  # noqa: BLE001 — 레지스트리 장애가 규약 폴백을 막지 않는다
        logger.warning("펫 레지스트리 BREATHING 조회 실패 — 규약 유도로 내려간다 (pet=%s)", pet_id)

    derived = derive_breathing_location(user_id, pet_id)
    if derived and derived not in candidates:
        candidates.append(derived)

    for loc in candidates:
        signed = sign_object(StorageObject(bucket=loc.bucket, path=loc.object_path))
        if signed:
            return (loc, signed)
    return None


# ── 사진 카드용 원본 (Phase 17) ───────────────────────────────────────────────
#
# MEMORY BOX 의 사진 카드(85×55mm)가 쓸 **정본 펫 이미지**를 찾는다.
#
# 왜 필요한가: 자동 완결(order_finalization)은 사람의 개입 없이 생산 준비까지
# 간다. 그런데 사진만은 어디서 오는지 아무도 정하지 않아 photo_image_url 이
# None 으로 남았고, MEMORY BOX 는 **패키지 ZIP 자체가 만들어지지 않았다**
# (구성 파일 하나가 실패하면 ZIP 을 만들지 않는 것이 의도된 규칙이다).
#
# BREATHING 과 **같은 방식**으로 찾는다 — 규약 경로에 서명을 시도하고, 성공하면
# 그것이 존재 증명이다. 새 목록 API 를 두지 않는 이유도 같다.

#: 우선순위. 앞의 것이 먼저 시도된다.
#:
#: 원본 사진을 먼저 보는 이유: 고객이 **실제로 찍은 그 사진**이고, 카드는
#: 액자가 아니라 사진 그 자체여야 한다. 누끼(cutout)는 배경이 없어 흰 카드 위에서
#: 떠 보이므로 차선이다 — 다만 원본이 없는 예전 펫도 있으므로 폴백으로 남긴다.
#:
#: ⚠️ 순서를 바꾸면 **인쇄되어 배송되는 그림이 바뀐다.** 제품 결정이지 구현
#:    세부가 아니다. 운영이 개별 주문을 덮어쓸 수 있는 경로는 따로 있다
#:    (production_package.attach_photo).
PHOTO_CANDIDATE_PATHS = (
    "{uid}/{cid}/background_source/original.jpg",
    "{uid}/{cid}/cutout.png",
    "{uid}/{cid}/cutout_vitmatte.png",
    "{uid}/{cid}/dog_only_nobg.png",
    "{uid}/{cid}/luma_keyframe.jpg",
)


async def locate_pet_photo(user_id: str, pet_id: str) -> Optional[str]:
    """
    사진 카드에 쓸 정본 이미지의 **지금 유효한 URL** — 찾지 못하면 None.

    **조회만 한다.** 이미지를 만들지 않고, 생성 파이프라인을 부르지 않으며,
    없는 자산을 만들어 내지 않는다.

    None 을 돌려주는 것은 실패가 아니라 사실이다. 규약 밖에 저장된 펫이면
    운영자가 URL 을 직접 넣는다(attach_photo). 여기서 예외를 던지면 사진 하나
    때문에 결제된 주문의 생산 준비 전체가 멈춘다.
    """
    from .asset_url_refresh import StorageObject, default_bucket, sign_object

    uid = (user_id or "").strip()
    cid = content_id_from_pet_id(pet_id)
    if not uid or not cid:
        return None

    bucket = default_bucket()
    for template in PHOTO_CANDIDATE_PATHS:
        path = template.format(uid=uid, cid=cid)
        signed = sign_object(StorageObject(bucket=bucket, path=path))
        if signed:
            logger.info("사진 카드 원본 확정 — pet=%s path=%s", pet_id, path)
            return signed

    logger.warning(
        "사진 카드 원본을 규약 경로에서 찾지 못했다 — pet=%s. "
        "운영 콘솔에서 이미지를 직접 지정해야 한다.",
        pet_id,
    )
    return None
