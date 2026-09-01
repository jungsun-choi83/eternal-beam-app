"""
디지털 상품 카탈로그의 **가격 계약** (Phase 3).

핵심 원칙 하나:

    가격은 카테고리가 아니라 **상품**이 정한다.

    theme:aurora  5   ·   theme:sunset  4   ·   theme:limited  8
    이 셋이 동시에 성립해야 한다.

여기서 고정하는 것:
  * 상품마다 독립적으로 가격을 매길 수 있다 (카테고리가 값을 강제하지 않는다)
  * 카탈로그에 없는 상품은 **무료가 아니라 판매 불가**다
  * 조회 실패는 0 도 무료도 아니다 — 시끄럽게 실패한다
  * 목업 시드가 마이그레이션 시드와 일치한다
  * 아이들 이벤트는 kind 가 ACTION: 이어도 상품 키는 idle: 이다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
from backend.services import premium_purchase, product_catalog

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261002000000_digital_products.sql"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    product_catalog.__reset_for_tests()
    yield
    product_catalog.__reset_for_tests()


# ── 상품마다 다른 가격 ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_products_in_the_same_category_can_have_different_prices():
    """
    **이 파일의 이유.** 예전에는 ACTION_EVENT_CREDITS 하나가 카테고리 전체의
    값이었다. 아이들 넷에 서로 다른 값을 매기는 것이 불가능했다.
    """
    product_catalog.set_price_for_tests("idle:BLINKING", 3)
    product_catalog.set_price_for_tests("idle:TAIL_WAGGING", 7)

    assert await product_catalog.credit_price("idle:BLINKING") == 3
    assert await product_catalog.credit_price("idle:TAIL_WAGGING") == 7
    # 나머지는 영향을 받지 않는다.
    assert await product_catalog.credit_price("idle:EAR_TWITCHING") == 1


@pytest.mark.anyio
async def test_the_category_does_not_determine_the_price():
    """같은 THEME 타입 안에서 값이 갈릴 수 있다 (Aurora 5 / Sunset 4)."""
    product_catalog.set_price_for_tests("theme:aurora", 5, product_catalog.TYPE_THEME)
    product_catalog.set_price_for_tests("theme:sunset", 4, product_catalog.TYPE_THEME)
    product_catalog.set_price_for_tests("theme:limited", 8, product_catalog.TYPE_THEME)

    prices = {
        p.product_key: p.credit_price
        for p in await product_catalog.list_products(product_catalog.TYPE_THEME)
    }
    assert prices["theme:aurora"] == 5
    assert prices["theme:sunset"] == 4
    assert prices["theme:limited"] == 8


# ── 없는 상품 ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_missing_product_is_not_free():
    """
    가격 미설정을 0 으로 떨어뜨리면 **설정 누락이 곧 전량 무료 배포**가 된다.
    theme_catalog.price_krw() 가 None 과 0 을 구분한 이유와 같다.
    """
    assert await product_catalog.credit_price("theme:does_not_exist") is None

    with pytest.raises(product_catalog.CatalogUnavailableError):
        await product_catalog.require_price("theme:does_not_exist")


@pytest.mark.anyio
async def test_zero_is_explicitly_free_and_different_from_missing():
    assert await product_catalog.credit_price("idle:BREATHING") == 0
    p = await product_catalog.get_product("idle:BREATHING")
    assert p is not None and p.free and not p.purchasable


@pytest.mark.anyio
async def test_breathing_is_free_in_the_catalog():
    """
    BREATHING 은 언제나 무료다 — 이 저장소 전체가 그 계약 위에 서 있다.
    유료 목록에 실수로 들어가면 무료 경험이 통째로 막힌다.
    """
    assert await product_catalog.credit_price("idle:BREATHING") == 0


# ── 구매 종류 → 상품 키 ──────────────────────────────────────────────────────


def test_idle_events_map_to_idle_products_even_though_the_kind_says_action():
    """
    두 이름공간이 겹치지 않는다:
        kind 의 'ACTION:'      = 한 건짜리 구매 (번들의 반대말)
        product_key 의 'action:' = 액션 상품    (아이들의 반대말)

    Behavior Library 는 아이들도 한 건씩 산다. kind 접두사를 그대로 베끼면
    카탈로그에 없는 'action:BLINKING' 을 찾게 되고 가격 조회가 실패한다.
    (실제로 처음 구현에서 그렇게 틀렸고, 테스트가 잡았다.)
    """
    for event in IDLE_EVENTS:
        kind = premium_purchase.action_kind(event)
        assert kind == f"ACTION:{event}"
        assert premium_purchase._product_key(kind) == f"idle:{event}"

    for action in PET_ACTIONS:
        kind = premium_purchase.action_kind(action)
        assert premium_purchase._product_key(kind) == f"action:{action}"

    assert premium_purchase._product_key(premium_purchase.KIND_IDLE_BUNDLE) == "idle:BUNDLE"


@pytest.mark.anyio
async def test_every_purchasable_kind_has_a_catalog_price():
    """
    레지스트리에 있는데 카탈로그에 없는 상품이 있으면, 그 상품은 **살 수 없다.**
    조용히 그렇게 되지 않도록 여기서 잡는다.
    """
    kinds = [premium_purchase.KIND_IDLE_BUNDLE]
    kinds += [premium_purchase.action_kind(a) for a in IDLE_EVENTS]
    kinds += [premium_purchase.action_kind(a) for a in PET_ACTIONS]

    for kind in kinds:
        price = await premium_purchase.credits_for_kind(kind)
        assert isinstance(price, int) and price >= 0, kind


@pytest.mark.anyio
async def test_price_changes_flow_through_to_the_purchase_path():
    """카탈로그를 바꾸면 과금액이 바뀐다 — 배포도 환경변수도 없이."""
    kind = premium_purchase.action_kind("BLINKING")
    assert await premium_purchase.credits_for_kind(kind) == 1

    product_catalog.set_price_for_tests("idle:BLINKING", 9)
    assert await premium_purchase.credits_for_kind(kind) == 9


@pytest.mark.anyio
async def test_an_unsold_product_is_rejected_not_given_away():
    product_catalog.__reset_for_tests()
    product_catalog._mock_catalog().pop("idle:BLINKING", None)

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await premium_purchase.credits_for_kind(premium_purchase.action_kind("BLINKING"))
    assert e.value.code == "PRODUCT_NOT_SOLD"


# ── 목업과 마이그레이션이 같은가 ────────────────────────────────────────────


def test_mock_seed_matches_the_migration_seed():
    """
    목업과 SQL 이 갈라지면 그 차이는 **프로덕션에서만** 드러난다 — 이 저장소가
    Phase 8 에서 이미 겪은 일이다(0크레딧 갱신이 목업에서는 통과, SQL 에서는 실패).
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("values", 1)[1].split("on conflict", 1)[0]
    rows = re.findall(r"\('([^']+)',\s*'([A-Z_]+)',\s*(\d+),", block)
    in_sql = {key: (kind, int(price)) for key, kind, price in rows}

    in_py = {
        key: (kind, price) for key, kind, price, _name in product_catalog._SEED
    }
    assert in_sql == in_py, (
        f"SQL 에만: {set(in_sql) - set(in_py)} / Python 에만: {set(in_py) - set(in_sql)}"
    )


def test_types_match_the_database_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("add constraint digital_products_type_check", 1)[1].split(";", 1)[0]
    in_sql = set(re.findall(r"'([A-Z_]+)'", block))
    assert in_sql == set(product_catalog.ALL_TYPES)


def test_key_prefix_helpers_follow_the_convention():
    assert product_catalog.theme_key("Aurora") == "theme:aurora"
    assert product_catalog.idle_key("blinking") == "idle:BLINKING"
    assert product_catalog.action_key("come_closer") == "action:COME_CLOSER"


# ── 가격은 프론트에 없다 ────────────────────────────────────────────────────


def test_the_frontend_no_longer_hardcodes_prices():
    """
    themes.ts 의 "$2.99" 는 **브라우저 번들 안의 가격**이었다. 바꾸려면 프론트를
    다시 배포해야 했고, 서버 값과 어긋나면 눌러도 거절당하는 버튼이 생겼다.
    """
    themes = (REPO / "src" / "components" / "memorial" / "themes.ts").read_text(encoding="utf-8")
    assert "price" not in themes, "themes.ts 에 가격이 남아 있다"
    assert "$2.99" not in themes


def test_category_wide_env_prices_are_gone():
    """
    IDLE_BUNDLE_CREDITS / ACTION_EVENT_CREDITS 는 **카테고리 전체**의 값이었다.
    상수 대입이 되살아나면 카탈로그가 권위라는 말이 거짓이 된다.
    """
    src = (REPO / "backend" / "services" / "premium_purchase.py").read_text(encoding="utf-8")
    assert not re.search(r"^(IDLE_BUNDLE_CREDITS|ACTION_EVENT_CREDITS)\s*=", src, re.M)
    assert 'os.getenv("IDLE_BUNDLE_CREDITS"' not in src
    assert 'os.getenv("ACTION_EVENT_CREDITS"' not in src
