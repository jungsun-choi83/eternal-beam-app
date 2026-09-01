"""
디지털 상품 카탈로그 — **크레딧 가격의 유일한 권위** (Phase 3).

    theme:aurora           THEME    5
    theme:sunset           THEME    4
    idle:BLINKING          IDLE     3
    action:COME_CLOSER     ACTION   2
    theme:custom_photo_bg  AI_BG    8

── 원칙 ─────────────────────────────────────────────────────────────────────
**가격은 카테고리가 아니라 상품이 정한다.** product_type 은 분류일 뿐 값에
관여하지 않는다. Aurora 5 · Sunset 4 · Limited 8 이 동시에 성립해야 한다.

이것이 대체하는 것:
    THEME_PRICE_<KEY>_KRW    테마마다 환경변수를 하나씩 늘려야 했다
    IDLE_BUNDLE_CREDITS      **카테고리 전체**가 한 값
    ACTION_EVENT_CREDITS     **카테고리 전체**가 한 값
    themes.ts 의 "$2.99"     브라우저 번들에 박힌 가격

── 없는 상품은 무료가 아니라 **판매 불가**다 ────────────────────────────────
theme_catalog.price_krw() 의 규칙을 그대로 가져온다. 가격 미설정을 0 으로
떨어뜨리면 설정 누락이 곧 전량 무료 배포가 된다. 무료 상품은 credit_price=0 인
행을 **명시적으로** 갖는다.

── 조회 실패는 "무료"도 "없음"도 아니다 ─────────────────────────────────────
카탈로그를 읽지 못하면 CatalogUnavailableError 를 던진다. 0 으로 떨어뜨리면
장애 중에 전 상품이 공짜가 되고, "없음"으로 떨어뜨리면 산 사람이 못 쓰게 된다.
둘 다 조용히 잘못되는 쪽이라, 시끄럽게 실패하는 편이 낫다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

TYPE_THEME = "THEME"
TYPE_IDLE = "IDLE"
TYPE_ACTION = "ACTION"
TYPE_AI_BG = "AI_BG"

ALL_TYPES: frozenset[str] = frozenset({TYPE_THEME, TYPE_IDLE, TYPE_ACTION, TYPE_AI_BG})

#: 상품 키 접두사 규약. 도메인 식별자는 **이미 있는 것을 그대로** 쓴다 —
#: 새로 만들면 카탈로그와 소유권 테이블이 조인되지 않는다.
PREFIX_THEME = "theme:"
PREFIX_IDLE = "idle:"
PREFIX_ACTION = "action:"

#: IDLE_BUNDLE 구매가 가리키는 상품 키. 번들도 하나의 상품이다.
KEY_IDLE_BUNDLE = "idle:BUNDLE"


class CatalogUnavailableError(Exception):
    """카탈로그를 읽지 못했다. **가격을 추측하지 않는다.**"""

    def __init__(self, message: str = "상품 카탈로그를 불러오지 못했습니다."):
        super().__init__(message)
        self.message = message
        self.code = "CATALOG_UNAVAILABLE"
        self.status = 503


@dataclass(frozen=True)
class DigitalProduct:
    product_key: str
    product_type: str
    credit_price: int
    display_name: Optional[str] = None
    active: bool = True

    @property
    def free(self) -> bool:
        """0 = **명시적으로** 무료. 가격이 없는 것과 다르다."""
        return self.credit_price == 0

    @property
    def purchasable(self) -> bool:
        return self.active and self.credit_price > 0


# ── 키 규약 ──────────────────────────────────────────────────────────────────


def theme_key(theme: str) -> str:
    return f"{PREFIX_THEME}{(theme or '').strip().lower()}"


def idle_key(event_id: str) -> str:
    return f"{PREFIX_IDLE}{(event_id or '').strip().upper()}"


def action_key(action_id: str) -> str:
    return f"{PREFIX_ACTION}{(action_id or '').strip().upper()}"


# ── 저장소 ───────────────────────────────────────────────────────────────────


def _table() -> str:
    return os.getenv("DIGITAL_PRODUCTS_TABLE", "digital_products")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: 인메모리 카탈로그 (HYBRID_USE_SUPABASE=0 전용).
#:
#: 마이그레이션의 시드와 **같은 값**이어야 한다 — 목업과 SQL 이 갈라지면 그 차이는
#: 프로덕션에서만 드러난다. test_product_catalog.py 가 두 목록의 일치를 강제한다.
_SEED: tuple[tuple[str, str, int, str], ...] = (
    ("theme:fresh_forest", TYPE_THEME, 0, "Fresh Forest"),
    ("theme:beach", TYPE_THEME, 0, "Beach"),
    ("theme:snow_forest", TYPE_THEME, 0, "Snow Forest"),
    ("theme:celestial", TYPE_THEME, 0, "Celestial"),
    ("theme:golden_meadow", TYPE_THEME, 0, "Golden Meadow"),
    ("theme:starlight", TYPE_THEME, 0, "Starlight"),
    ("idle:BREATHING", TYPE_IDLE, 0, "Breathing"),
    ("idle:BLINKING", TYPE_IDLE, 1, "Blinking"),
    ("idle:EAR_TWITCHING", TYPE_IDLE, 1, "Ear Twitching"),
    ("idle:HEAD_TILTING", TYPE_IDLE, 1, "Head Tilting"),
    ("idle:TAIL_WAGGING", TYPE_IDLE, 1, "Tail Wagging"),
    ("idle:BUNDLE", TYPE_IDLE, 1, "Idle Motion Bundle"),
    ("action:COME_CLOSER", TYPE_ACTION, 1, "Come Closer"),
)

_MOCK: dict[str, DigitalProduct] = {}


def _mock_catalog() -> dict[str, DigitalProduct]:
    if not _MOCK:
        for key, kind, price, name in _SEED:
            _MOCK[key] = DigitalProduct(key, kind, price, name)
    return _MOCK


def __reset_for_tests() -> None:
    _MOCK.clear()


def set_price_for_tests(product_key: str, credit_price: int, product_type: str = TYPE_IDLE) -> None:
    """
    테스트에서 상품 가격을 바꾼다.

    **가격이 상품마다 다를 수 있다**는 성질을 테스트가 실제로 확인하려면, 값을
    바꿀 수단이 있어야 한다. 목업 카탈로그에만 작용한다.
    """
    cat = _mock_catalog()
    prior = cat.get(product_key)
    cat[product_key] = DigitalProduct(
        product_key=product_key,
        product_type=(prior.product_type if prior else product_type),
        credit_price=credit_price,
        display_name=(prior.display_name if prior else None),
        active=(prior.active if prior else True),
    )


def _row_to_product(row: dict) -> DigitalProduct:
    return DigitalProduct(
        product_key=str(row.get("product_key") or ""),
        product_type=str(row.get("product_type") or ""),
        credit_price=int(row.get("credit_price") or 0),
        display_name=(row.get("display_name") or None),
        active=bool(row.get("active", True)),
    )


async def get_product(product_key: str) -> Optional[DigitalProduct]:
    """
    상품 하나. **없으면 None = 판매 불가** (무료가 아니다).

    Raises:
        CatalogUnavailableError: 카탈로그를 읽지 못함 — 가격을 추측하지 않는다.
    """
    key = (product_key or "").strip()
    if not key:
        return None

    if not _use_db():
        p = _mock_catalog().get(key)
        return p if (p and p.active) else None

    try:
        sb = _supabase()
    except Exception as e:
        raise CatalogUnavailableError() from e
    if not sb:
        raise CatalogUnavailableError("Supabase 가 설정되지 않았습니다.")

    try:
        r = (
            sb.table(_table())
            .select("product_key, product_type, credit_price, display_name, active")
            .eq("product_key", key)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.exception("상품 카탈로그 조회 실패 (product_key=%s)", key)
        raise CatalogUnavailableError() from e

    rows = getattr(r, "data", None) or []
    if not rows:
        return None
    p = _row_to_product(rows[0])
    return p if p.active else None


async def credit_price(product_key: str) -> Optional[int]:
    """이 상품의 크레딧 가격. **None = 판매 불가** (0 과 다르다)."""
    p = await get_product(product_key)
    return p.credit_price if p else None


async def require_price(product_key: str) -> int:
    """
    가격을 반드시 얻는다. 없으면 거절한다.

    과금 경로 전용이다: 가격을 모르는 채로 차감하면 얼마를 받아야 하는지 모른 채
    돈을 받는 것이다.
    """
    price = await credit_price(product_key)
    if price is None:
        raise CatalogUnavailableError(
            f"판매하지 않는 상품입니다: {product_key}"
        )
    return price


async def list_products(product_type: Optional[str] = None) -> list[DigitalProduct]:
    """활성 상품 목록. 화면 카탈로그가 쓴다."""
    if not _use_db():
        out = [p for p in _mock_catalog().values() if p.active]
        if product_type:
            out = [p for p in out if p.product_type == product_type]
        return sorted(out, key=lambda p: p.product_key)

    try:
        sb = _supabase()
    except Exception as e:
        raise CatalogUnavailableError() from e
    if not sb:
        raise CatalogUnavailableError("Supabase 가 설정되지 않았습니다.")

    try:
        q = (
            sb.table(_table())
            .select("product_key, product_type, credit_price, display_name, active")
            .eq("active", True)
        )
        if product_type:
            q = q.eq("product_type", product_type)
        r = q.execute()
    except Exception as e:
        logger.exception("상품 카탈로그 목록 조회 실패")
        raise CatalogUnavailableError() from e

    return sorted(
        (_row_to_product(row) for row in (getattr(r, "data", None) or [])),
        key=lambda p: p.product_key,
    )
