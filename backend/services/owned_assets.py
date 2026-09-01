"""
생성 자산 **영구 소유 원장** (Phase 6).

    owned_generated_assets   무엇을 샀는가  (추가만 한다)
    generated_motions        지금 무엇을 재생하는가 (포인터)

── 왜 나누는가 ──────────────────────────────────────────────────────────────
generated_motions 는 unique (user, pet, place, action) 이고 승격이 **upsert** 한다.
그래서 같은 행동을 두 번 만들면 두 번째가 첫 번째를 덮어쓴다 — 고객이 각각 값을
낸 자산인데 하나만 남는다.

포인터로서는 그 동작이 맞다(기기는 한 번에 하나를 재생한다). 틀린 것은 그 표를
**소유의 근거**로 쓴 것이다. 그래서 소유만 따로 뗀다.

── 유일성은 "무엇이 만들었는가"에 건다 ──────────────────────────────────────
(user, pet, product_key) 에 걸면 이 모듈의 존재 이유가 사라진다. 대신
source_job_id 에 건다: 생성 작업 하나가 자산 하나를 만든다. 웹훅 재전송은 막히고,
새 작업은 언제나 새 자산이 된다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE_PURCHASE = "purchase"
SOURCE_LEGACY = "legacy_migration"
SOURCE_FREE = "free"


def _table() -> str:
    return os.getenv("OWNED_ASSETS_TABLE", "owned_generated_assets")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


@dataclass
class OwnedAsset:
    user_id: str
    pet_id: str
    product_key: str
    video_url: str
    asset_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scene_id: Optional[str] = None
    object_path: Optional[str] = None
    bucket: Optional[str] = None
    credits_spent: int = 0
    ledger_id: Optional[str] = None
    source: str = SOURCE_PURCHASE
    source_job_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def as_row(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "user_id": self.user_id,
            "pet_id": self.pet_id,
            "product_key": self.product_key,
            "scene_id": self.scene_id,
            "video_url": self.video_url,
            "object_path": self.object_path,
            "bucket": self.bucket,
            "credits_spent": self.credits_spent,
            "ledger_id": self.ledger_id,
            "source": self.source,
            "source_job_id": self.source_job_id,
            "created_at": self.created_at.isoformat(),
        }


#: 인메모리 저장소 (HYBRID_USE_SUPABASE=0 전용). **삽입 순서를 유지한다** —
#: 버전이 여럿일 때 "몇 번째"가 의미를 가지므로 순서가 곧 데이터다.
_MOCK: list[OwnedAsset] = []
_MOCK_JOBS: set[str] = set()


def __reset_for_tests() -> None:
    _MOCK.clear()
    _MOCK_JOBS.clear()


def product_key_for_action(action_id: str) -> str:
    """
    행동 id → 상품 키. premium_purchase._product_key 와 **같은 규약**이다.

    아이들 이벤트는 idle:, 그 밖은 action:. 두 곳이 갈라지면 카탈로그·원장·소유가
    서로 다른 문자열로 같은 것을 가리키게 된다.
    """
    from ..scenarios.pet_scenarios import IDLE_EVENTS

    a = (action_id or "").strip().upper()
    # BREATHING 은 IDLE_EVENTS 밖이지만(무료 기본 모션) 성격은 아이들이다.
    if a in IDLE_EVENTS or a == "BREATHING":
        return f"idle:{a}"
    return f"action:{a}"


async def record(asset: OwnedAsset) -> Optional[OwnedAsset]:
    """
    소유 자산 한 줄. 이미 같은 작업으로 기록됐으면 None (재전송).

    ⚠️ **덮어쓰지 않는다.** 이 함수에 upsert 가 없는 것이 이 모듈의 계약이다.
    """
    if asset.source != SOURCE_PURCHASE and asset.credits_spent:
        # 스키마 제약과 같은 규칙을 여기서도 본다 — 여기서 걸리면 스택 트레이스가
        # 호출부를 가리킨다.
        raise ValueError(f"{asset.source} 자산에 과금을 기록할 수 없다")

    # ⚠️ 가드를 **_record_promoted_motion 과 똑같이** 맞춘다: DB 를 쓰기로 했고
    # 클라이언트도 있을 때만 DB 에 쓰고, 그 밖에는 인메모리다.
    #
    # 지갑(Phase 1)처럼 "클라이언트 없음"을 오류로 올리지 않는 이유: 소유 자산과
    # 재생 포인터는 **같은 승격 안에서 함께** 기록된다. 한쪽만 더 엄격하면
    # 포인터는 생겼는데 소유는 없는 상태가 만들어질 수 있고, 그게 정확히 이
    # 표가 없애려는 상태다. 두 기록의 가용성은 같아야 한다.
    #
    # DB 를 쓸 수 있는데 쓰기가 **실패**하면 그때는 올린다 — 아래 except 참고.
    sb = _supabase() if _use_db() else None
    if sb is None:
        if asset.source_job_id and asset.source_job_id in _MOCK_JOBS:
            return None
        if asset.source_job_id:
            _MOCK_JOBS.add(asset.source_job_id)
        _MOCK.append(asset)
        return asset

    try:
        sb.table(_table()).insert(asset.as_row()).execute()
    except Exception as e:
        msg = f"{e}".lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            # 같은 생성 작업이 두 번 승격됐다 — 웹훅 재전송이다. 자산은 이미 있다.
            logger.info("소유 자산 재전송 무시 (job=%s)", asset.source_job_id)
            return None
        # ⚠️ 여기서 조용히 넘어가면 고객이 값을 낸 자산이 소유 목록에 없게 된다.
        logger.exception(
            "소유 자산 기록 실패 — user=%s pet=%s product=%s",
            asset.user_id, asset.pet_id, asset.product_key,
        )
        raise
    return asset


async def list_for_pet(user_id: str, pet_id: str) -> list[OwnedAsset]:
    """이 펫으로 가진 자산 전부 (최신순). 같은 상품의 여러 버전이 그대로 나온다."""
    uid, pid = (user_id or "").strip(), (pet_id or "").strip()
    if not uid or not pid:
        return []

    sb = _supabase() if _use_db() else None
    if sb is None:
        rows = [a for a in _MOCK if a.user_id == uid and a.pet_id == pid and a.active]
        return sorted(rows, key=lambda a: a.created_at, reverse=True)

    try:
        r = (
            sb.table(_table())
            .select("*")
            .eq("user_id", uid)
            .eq("pet_id", pid)
            .is_("revoked_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        logger.exception("소유 자산 조회 실패 (user=%s pet=%s)", uid, pid)
        raise
    return [_from_row(row) for row in (getattr(r, "data", None) or [])]


async def count_for_product(user_id: str, pet_id: str, product_key: str) -> int:
    """
    이 상품을 몇 개 갖고 있는가.

    0 이 "살 수 없다"를 뜻하지 않는다는 점이 중요하다 — 몇 개를 갖고 있든 또 살 수
    있다. 이 값은 라이브러리 표시용이지 게이트가 아니다.
    """
    return sum(1 for a in await list_for_pet(user_id, pet_id) if a.product_key == product_key)


def _from_row(row: dict[str, Any]) -> OwnedAsset:
    created = row.get("created_at")
    return OwnedAsset(
        asset_id=str(row.get("asset_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        product_key=str(row.get("product_key") or ""),
        scene_id=(row.get("scene_id") or None),
        video_url=str(row.get("video_url") or ""),
        object_path=(row.get("object_path") or None),
        bucket=(row.get("bucket") or None),
        credits_spent=int(row.get("credits_spent") or 0),
        ledger_id=(str(row["ledger_id"]) if row.get("ledger_id") else None),
        source=str(row.get("source") or SOURCE_PURCHASE),
        source_job_id=(row.get("source_job_id") or None),
        created_at=(
            datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if created
            else datetime.utcnow()
        ),
        revoked_at=None,
    )
