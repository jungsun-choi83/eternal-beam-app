"""
읽기 전용 라이브러리 집계 (Phase 11) — "내가 실제로 가진 모션 전체".

    무료 기본(BREATHING)   pet_motion_publications  (motion_id='BREATHING')
    구매/멤버십 생성 모션    owned_generated_assets   (append-only 소유 원장)

두 표를 합쳐 하나의 목록으로 낸다. **생성하지 않는다. 발행하지 않는다.**
호출마다 스토리지 서명을 새로 하는 것 말고는 아무것도 쓰지 않는다.

── 왜 pet_motion_versions/candidates 를 직접 읽지 않는가 ─────────────────────
그 두 표는 QA 진행 중(REVIEW/FAIL 포함) 상태까지 담는 생성 정본이다. 여기서
읽으면 발행되지 않은 시도가 라이브러리 카드로 새어 나갈 위험이 생긴다.
pet_motion_publications 와 owned_generated_assets 는 **이미 커밋된 결과만**
담고 있으므로, 그 둘만 읽는 것 자체가 "REVIEW/미발행은 절대 노출하지 않는다"는
계약을 코드 구조로 강제한다.

── generated_motions 는 소유 근거가 아니라 재생 폴백이다 ──────────────────────
그 표는 "지금 재생 중인 것"의 포인터이고 upsert 로 과거 버전을 덮어쓴다.
소유 이력이 아니다 — owned_assets.py 의 이유가 그대로 여기에도 적용된다.
소유 행(owned_generated_assets)은 추가만 되므로, 모션이 재생성되면 옛 행의
bucket/object_path 는 더 이상 없는 객체를 가리킬 수 있다. 그 경우에만 이 펫의
현재 포인터(같은 user·pet 으로 좁힌 generated_motions)에서 packed_alpha 재생
자산을 빌려 온다. 소유/출처 메타데이터는 그래도 소유 행에서만 나오고, 포인터에
행이 있다는 이유로 카드를 만들지는 않는다. 어느 표도 쓰지 않는다.

── 한 모션 = 카드 하나 ───────────────────────────────────────────────────────
같은 pet·motion_id 의 소유 행이 여럿(재생성 이력)이어도 그리드에는 하나만 낸다:
가장 최근의 **재생 가능한** 소유 행을 고르고, 하나도 서명되지 않으면 가장 최근
행에 포인터 폴백을 시도한다. 나머지 행은 지우지 않고 provenance 에 남긴다.

── 멤버십 만료 ───────────────────────────────────────────────────────────────
구독 상태는 여기서 절대 접근을 좁히지 않는다. 이미 owned_generated_assets 에
있는 자산은 구독이 만료돼도 계속 나오고 계속 재생된다 — 이 모듈이 새로 만드는
것은 없으므로 "새 생성만 막힌다"는 규칙과 자연히 맞는다. 아직 한 번도 만들지
않은 멤버십 상품은 애초에 owned_generated_assets 에 행이 없으므로, 여기서
따로 걸러낼 필요 없이 목록에 나타나지 않는다 — 오퍼일 뿐 라이브러리 기억이
아니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from . import asset_url_refresh
from . import generated_motions_service
from . import motion_publication_service
from . import owned_assets
from . import pet_registry
from . import premium_entitlement
from . import premium_purchase
from . import product_catalog

logger = logging.getLogger(__name__)

BREATHING = motion_publication_service.BREATHING
DELIVERY_PACKED_ALPHA = "packed_alpha"

PLAYBACK_SOURCE_OWNED = "owned_generated_assets"
PLAYBACK_SOURCE_POINTER = "generated_motions"

TYPE_INCLUDED = "included"
TYPE_CREDIT_PURCHASE = "credit_purchase"
TYPE_MEMBERSHIP = "membership"
TYPE_OWNED = "owned"

ACCESS_PLAYABLE = "playable"
ACCESS_LOCKED = "locked"  # noqa: F401 — access.state 계약의 일부. 아직 만들지 않은 것은
# 카드로 노출하지 않으므로(오퍼일 뿐) 현재 경로에서는 나오지 않지만, 응답 계약이
# 세 값을 약속하므로 상수는 남겨 둔다.
ACCESS_UNAVAILABLE = "unavailable"


class LibraryError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class LibraryOwnership:
    type: str
    permanent: bool = True


@dataclass(frozen=True)
class LibraryAccess:
    state: str


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    pet_id: str
    motion_id: str
    display_name: Optional[str]
    publication_id: Optional[str]
    motion_version_id: Optional[str]
    version: Optional[int]
    url: Optional[str]
    delivery_format: Optional[str]
    background_baked: bool
    generated_at: Optional[str]
    published_at: Optional[str]
    ownership: LibraryOwnership
    access: LibraryAccess
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LibraryResult:
    pet_id: str
    motions: list[LibraryEntry]
    subscription_status: Optional[str]
    entitled: bool


async def assert_pet_owned(user_id: str, pet_id: str) -> None:
    """
    이 펫이 이 사용자의 것인가.

    BREATHING 만 있는 펫은 ``pets``(pet_registry) 에 등록돼 있고, 프리미엄만 있는
    펫은 그 표에 없을 수 있다. 그래서 두 검사를 **둘 다** 한다 — 하나는 등록된
    펫을 엄격히 지키고, 다른 하나(TOFU)는 프리미엄 원장으로 같은 것을 지킨다.
    어느 한쪽이라도 "남의 것"이라고 하면 거절한다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise LibraryError("PET_REQUIRED", "user_id 와 pet_id 가 필요합니다.", status=400)

    try:
        registered = await pet_registry.get(pid)
    except pet_registry.PetRegistryError as e:
        raise LibraryError(e.code, e.message, status=e.status) from e
    if registered and registered.user_id != uid:
        raise LibraryError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)

    try:
        await premium_purchase.assert_pet_owned(uid, pid)
    except premium_purchase.PurchaseError as e:
        raise LibraryError(e.code, e.message, status=e.status) from e


def _resolve_access(
    *, video_url: Optional[str], bucket: Optional[str], object_path: Optional[str]
) -> tuple[Optional[str], str]:
    """
    저장된 위치 → (재생 URL, access.state). 호출마다 새로 서명한다.

    스토리지 경로를 알아볼 수 없으면(외부 CDN·레거시 URL) 원본을 그대로
    playable 로 통과시킨다 — asset_url_refresh 의 원칙과 같다. 경로는 아는데
    서명이 실패하면(객체가 없거나 접근 불가) unavailable — 소유는 살아 있지만
    지금은 재생할 수 없다는 뜻을 정직하게 낸다.
    """
    obj = asset_url_refresh.StorageObject(bucket=bucket, path=object_path) if (bucket and object_path) else None
    if obj is None:
        obj = asset_url_refresh.parse_storage_object(video_url)
    if obj is None:
        return (video_url or None), (ACCESS_PLAYABLE if video_url else ACCESS_UNAVAILABLE)
    signed = asset_url_refresh.sign_object(obj)
    if signed:
        return signed, ACCESS_PLAYABLE
    return None, ACCESS_UNAVAILABLE


def _motion_id_from_product_key(product_key: str) -> str:
    key = (product_key or "").strip()
    return key.split(":", 1)[1].upper() if ":" in key else key.upper()


def _display_name(catalog: dict[str, product_catalog.DigitalProduct], product_key: str, fallback: str) -> str:
    product = catalog.get(product_key)
    if product and product.display_name:
        return product.display_name
    return fallback


async def _breathing_entries(
    *,
    user_id: str,
    pet_id: str,
    pet: Optional[pet_registry.RegisteredPet],
    catalog: dict[str, product_catalog.DigitalProduct],
) -> list[LibraryEntry]:
    rows = await motion_publication_service.list_breathing_publications(user_id, pet_id)
    display_name = _display_name(catalog, product_catalog.idle_key(BREATHING), "Breathing")
    background_baked = bool(pet.background_baked) if pet else False

    entries: list[LibraryEntry] = []
    for row in rows:
        publication_id = str(row.get("id") or row.get("publication_id") or "") or None
        motion_version_id = str(row.get("motion_version_id") or "") or None
        version_raw = row.get("motion_version")
        bucket = str(row.get("bucket") or "").strip() or asset_url_refresh.default_bucket()
        object_path = str(row.get("object_path") or "").strip()
        published_at = row.get("published_at")

        url, access_state = _resolve_access(video_url=None, bucket=bucket, object_path=object_path)
        entries.append(
            LibraryEntry(
                id=f"publication:{publication_id or motion_version_id or object_path}",
                pet_id=pet_id,
                motion_id=BREATHING,
                display_name=display_name,
                publication_id=publication_id,
                motion_version_id=motion_version_id,
                version=int(version_raw) if version_raw is not None else None,
                url=url,
                delivery_format=motion_publication_service.delivery_format_for(None, object_path),
                background_baked=background_baked,
                generated_at=None,
                published_at=str(published_at) if published_at else None,
                ownership=LibraryOwnership(type=TYPE_INCLUDED, permanent=True),
                access=LibraryAccess(state=access_state),
                provenance={
                    "source": "pet_motion_publications",
                    "selected_candidate_id": row.get("selected_candidate_id"),
                },
            )
        )
    return entries


def _ownership_type_for(source: str) -> str:
    if source == owned_assets.SOURCE_PURCHASE:
        return TYPE_CREDIT_PURCHASE
    if source == owned_assets.SOURCE_FREE:
        return TYPE_MEMBERSHIP
    return TYPE_OWNED


async def _current_pointers(user_id: str, pet_id: str) -> dict[str, str]:
    """
    이 사용자·펫의 현재 재생 포인터 {ACTION: video_url}. **읽기 전용.**

    generated_motions_service.list_motions_for_pet 은 user_id 와 pet_id 를 함께
    조건으로 건다 — 같은 pet_id 라도 다른 사용자의 포인터는 여기 들어오지 않는다.
    조회가 실패하면 빈 dict 다: 폴백이 없을 뿐, 라이브러리 자체는 계속 나간다.
    """
    try:
        motions = await generated_motions_service.list_motions_for_pet(user_id, pet_id)
    except Exception:
        logger.exception("현재 포인터 조회 실패 — 폴백 없이 진행 (user=%s pet=%s)", user_id, pet_id)
        return {}
    pointers: dict[str, str] = {}
    for m in motions:
        if m.user_id != user_id or m.pet_id != pet_id:
            continue
        action = (m.action_id or "").strip().upper()
        if action and m.video_url:
            pointers[action] = m.video_url
    return pointers


def _resolve_pointer_playback(pointer_url: Optional[str]) -> Optional[str]:
    """
    현재 포인터 → 새 서명 URL. packed_alpha 파생물이 아니면 None.

    폴백은 발행 계약(packed_alpha)을 만족하는 객체에만 허용한다 — 원본 프로바이더
    영상으로는 절대 내려가지 않는다. 스토리지 객체로 파싱되지 않는 URL 은
    재서명도 포맷 확인도 할 수 없으므로 폴백 후보가 아니다.
    """
    obj = asset_url_refresh.parse_storage_object(pointer_url)
    if obj is None:
        return None
    if motion_publication_service.delivery_format_for(None, obj.path) != DELIVERY_PACKED_ALPHA:
        return None
    return asset_url_refresh.sign_object(obj) or None


@dataclass(frozen=True)
class _OwnedPlayback:
    asset: owned_assets.OwnedAsset
    url: Optional[str]
    access_state: str
    delivery_format: Optional[str]
    playback_source: str


def _owned_playback(a: owned_assets.OwnedAsset) -> _OwnedPlayback:
    url, access_state = _resolve_access(video_url=a.video_url, bucket=a.bucket, object_path=a.object_path)
    lineage = dict(a.lineage or {})
    return _OwnedPlayback(
        asset=a,
        url=url,
        access_state=access_state,
        delivery_format=(lineage.get("delivery_format") or None)
        or motion_publication_service.delivery_format_for(None, a.object_path or ""),
        playback_source=PLAYBACK_SOURCE_OWNED,
    )


def _select_playback(
    rows: list[owned_assets.OwnedAsset], pointer_url: Optional[str]
) -> _OwnedPlayback:
    """
    같은 모션의 소유 행들(최신순) → 카드 하나의 재생 해석.

        1) 자기 자산이 서명되는 가장 최근 행            → playable (소유 행 자산)
        2) 없으면 가장 최근 행 + 현재 포인터(packed)   → playable (포인터 자산)
        3) 그것도 없으면 가장 최근 행                   → unavailable
    """
    newest: Optional[_OwnedPlayback] = None
    for a in rows:
        pb = _owned_playback(a)
        if pb.access_state == ACCESS_PLAYABLE:
            return pb
        newest = newest or pb
    assert newest is not None
    fallback_url = _resolve_pointer_playback(pointer_url)
    if fallback_url:
        return _OwnedPlayback(
            asset=newest.asset,
            url=fallback_url,
            access_state=ACCESS_PLAYABLE,
            delivery_format=DELIVERY_PACKED_ALPHA,
            playback_source=PLAYBACK_SOURCE_POINTER,
        )
    return newest


async def _owned_entries(
    *, user_id: str, pet_id: str, catalog: dict[str, product_catalog.DigitalProduct]
) -> list[LibraryEntry]:
    assets = await owned_assets.list_for_pet(user_id, pet_id)
    if not assets:
        return []
    pointers = await _current_pointers(user_id, pet_id)

    # list_for_pet 은 최신순 — 그룹 안 순서가 곧 "가장 최근"의 정의다.
    grouped: dict[str, list[owned_assets.OwnedAsset]] = {}
    for a in assets:
        grouped.setdefault(_motion_id_from_product_key(a.product_key), []).append(a)

    entries: list[LibraryEntry] = []
    for motion_id, rows in grouped.items():
        pb = _select_playback(rows, pointers.get(motion_id))
        a = pb.asset
        fallback_name = motion_id.replace("_", " ").title()
        lineage = dict(a.lineage or {})
        history = [r.asset_id for r in rows if r.asset_id != a.asset_id]

        entries.append(
            LibraryEntry(
                id=f"asset:{a.asset_id}",
                pet_id=pet_id,
                motion_id=motion_id,
                display_name=_display_name(catalog, a.product_key, fallback_name),
                # Phase 7H 자산만 발행 계보를 갖는다(레거시/구독 이행 이전 자산은 없음) —
                # 없는 값을 추측해 채우지 않는다.
                publication_id=(lineage.get("publication_id") or None),
                motion_version_id=(lineage.get("pet_motion_version_id") or None),
                # 발행 표를 다시 조인하지 않는 한 버전 번호는 정직하게 알 수 없다.
                version=None,
                url=pb.url,
                delivery_format=pb.delivery_format,
                background_baked=False,
                generated_at=a.created_at.isoformat() if a.created_at else None,
                # pet_motion_publications 를 다시 조인하지 않는 한 발행 시각은 모른다 —
                # 자산 생성 시각(generated_at)만 정직하게 낸다.
                published_at=None,
                ownership=LibraryOwnership(type=_ownership_type_for(a.source), permanent=True),
                access=LibraryAccess(state=pb.access_state),
                provenance={
                    "source": PLAYBACK_SOURCE_OWNED,
                    "asset_id": a.asset_id,
                    "product_key": a.product_key,
                    "source_kind": a.source,
                    "credits_spent": a.credits_spent,
                    "ledger_id": a.ledger_id,
                    "playback_source": pb.playback_source,
                    **({"lineage": lineage} if lineage else {}),
                    **({"superseded_asset_ids": history} if history else {}),
                },
            )
        )
    return entries


def _sort_key(entry: LibraryEntry) -> str:
    return entry.published_at or entry.generated_at or ""


async def get_library(*, user_id: str, pet_id: str) -> LibraryResult:
    """
    이 펫의 라이브러리 전체. **읽기 전용** — 아무것도 생성·발행하지 않는다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    await assert_pet_owned(uid, pid)

    pet = await pet_registry.get(pid)

    try:
        catalog = {p.product_key: p for p in await product_catalog.list_products()}
    except product_catalog.CatalogUnavailableError:
        # 카탈로그 장애가 발견을 막아선 안 된다 — premium_v1.get_premium_assets 와 같은 원칙.
        catalog = {}

    try:
        entitlement = await premium_entitlement.get_entitlement(uid)
    except premium_entitlement.EntitlementUnavailableError:
        entitlement = premium_entitlement.EntitlementState(
            entitled=False, status=None, enforced=premium_entitlement.subscription_required()
        )

    entries = await _breathing_entries(user_id=uid, pet_id=pid, pet=pet, catalog=catalog)
    entries += await _owned_entries(user_id=uid, pet_id=pid, catalog=catalog)
    entries.sort(key=_sort_key, reverse=True)

    return LibraryResult(
        pet_id=pid,
        motions=entries,
        subscription_status=entitlement.status,
        entitled=entitlement.entitled,
    )
