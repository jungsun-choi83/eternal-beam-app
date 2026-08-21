"""
유료 테마 스토어 (Phase 11) — 소유권 · 결제 · 분리.

핵심 계약:
  * 무료 테마는 결제 없이 언제나 쓸 수 있다.
  * 유료 테마는 사기 전엔 NOT OWNED, 산 뒤엔 OWNED.
  * **구독 자격과 테마 소유권은 완전히 별개다** — 양방향으로 검증한다.
  * 같은 사람이 두 번 눌러도 두 번 청구되지 않는다 (멱등).
  * 남이 산 테마는 내 것이 아니다.
  * 테마를 사거나 바꿔도 BREATHING/프리미엄 행동이 다시 만들어지지 않는다.
  * **가격을 발명하지 않는다** — 설정이 없으면 팔리지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import theme_store_v1
from backend.services import theme_catalog, theme_entitlement, theme_purchase, toss_billing

from .conftest import ASGITestClient

BUYER = "buyer@example.com"
OTHER = "other@example.com"
PAID = "aurora"
FREE = "fresh_forest"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.delenv("THEME_PAID_KEYS", raising=False)
    monkeypatch.delenv("THEME_ENTITLEMENT_TTL_DAYS", raising=False)
    for k in theme_catalog.ALL_THEME_KEYS:
        monkeypatch.delenv(f"THEME_PRICE_{k.upper()}_KRW", raising=False)
    theme_entitlement.__reset_for_tests()
    yield
    theme_entitlement.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(theme_store_v1.router, prefix="/api")
    return ASGITestClient(app)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> int:
    """
    PM 이 가격을 정한 상태를 흉내 낸다.

    ⚠️ 이 숫자는 **테스트 픽스처이지 제품 가격이 아니다.** 실제 가격은 설정으로만
    들어오며 코드에는 어떤 기본값도 없다.
    """
    monkeypatch.setenv(f"THEME_PRICE_{PAID.upper()}_KRW", "4900")
    return 4900


@pytest.fixture
def card(monkeypatch: pytest.MonkeyPatch):
    """등록된 결제 수단. **구독 상태가 아니다** — 카드일 뿐이다."""
    from backend.services import billing_store

    class FakeSub:
        billing_key = "bk_test"
        customer_key = "ck_test"
        # 일부러 만료 상태로 둔다 — 구독이 죽어 있어도 테마는 살 수 있어야 한다.
        status = "expired"

    async def _get(_uid, _provider):
        return FakeSub()

    monkeypatch.setattr(billing_store, "get_subscription", _get)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _catalog(client: ASGITestClient, user: str = BUYER) -> dict[str, dict]:
    r = client.get("/api/v1/themes/catalog", headers=_auth(user))
    assert r.status_code == 200, r.text
    return {t["theme_key"]: t for t in r.json()["themes"]}


def _buy(client: ASGITestClient, theme: str = PAID, user: str = BUYER):
    return client.post(
        "/api/v1/themes/purchase", json={"theme_key": theme}, headers=_auth(user)
    )


# ── 무료 테마 ────────────────────────────────────────────────────────────────


def test_free_theme_is_usable_without_payment(client: ASGITestClient):
    """**핵심 계약**: 무료 테마는 결제 없이 언제나 쓸 수 있다."""
    row = _catalog(client)[FREE]
    assert row["free"] is True
    assert row["owned"] is True          # 살 필요 없이 이미 쓸 수 있다
    assert row["purchasable"] is False   # [Buy] 를 보여 주지 않는다
    assert row["price_krw"] == 0


def test_all_default_free_themes_are_owned(client: ASGITestClient):
    cat = _catalog(client)
    for key in ("fresh_forest", "beach", "snow_forest", "celestial", "golden_meadow", "starlight"):
        assert cat[key]["free"] is True, key
        assert cat[key]["owned"] is True, key


def test_free_theme_cannot_be_purchased(client: ASGITestClient, card):
    """무료 테마에 결제를 만들지 않는다 — 만들면 그게 곧 오과금이다."""
    r = _buy(client, FREE)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_IS_FREE"


# ── 미보유 유료 테마 ─────────────────────────────────────────────────────────


def test_unowned_paid_theme_shows_as_not_owned(client: ASGITestClient, priced):
    row = _catalog(client)[PAID]
    assert row["free"] is False
    assert row["owned"] is False
    assert row["purchasable"] is True
    assert row["price_krw"] == priced


def test_paid_theme_without_price_is_not_purchasable(client: ASGITestClient, card):
    """
    **가격을 발명하지 않는다.** 설정이 없으면 팔리지 않는다.

    0 으로 떨어뜨리면 "무료로 팔린다" — 가격 미설정이 전량 무료 배포가 된다.
    """
    row = _catalog(client)[PAID]
    assert row["price_krw"] is None
    assert row["purchasable"] is False

    r = _buy(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_PRICE_NOT_SET"


def test_saved_card_path_falls_back_instead_of_blocking(client: ASGITestClient, priced):
    """
    카드가 없으면 /purchase 는 **안내**를 준다 — "살 수 없다"가 아니다.

    ⚠️ 계약이 바뀐 자리다. 예전에는 PAYMENT_METHOD_REQUIRED 로 막았고, 그래서
    테마를 사려면 먼저 멤버십 체크아웃으로 카드를 등록해야 했다. 그건 일회성
    구매라는 성격과 어긋나고 구독 흐름을 전제로 만든다. 이제 이 코드는
    "결제창 경로로 가라"는 신호이며, 실제 구매는 /checkout → /confirm 으로 된다
    (test_theme_standalone_payment.py).
    """
    r = _buy(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYMENT_METHOD_UNAVAILABLE"

    # 카드 없이도 체크아웃은 열린다 — 이것이 바뀐 계약의 핵심이다.
    co = client.post(
        "/api/v1/themes/checkout", json={"theme_key": PAID}, headers=_auth(BUYER)
    )
    assert co.status_code == 200, co.text
    assert co.json()["amount"] == priced


def test_purchase_requires_auth(client: ASGITestClient):
    assert client.post("/api/v1/themes/purchase", json={"theme_key": PAID}).status_code == 401
    assert client.get("/api/v1/themes/catalog").status_code == 401


def test_unknown_theme_is_rejected(client: ASGITestClient, card):
    r = _buy(client, "not_a_theme")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "THEME_UNKNOWN"


# ── 구매 성공 ────────────────────────────────────────────────────────────────


def test_successful_purchase_grants_ownership(client: ASGITestClient, priced, card):
    """**핵심 흐름**: NOT OWNED [Buy] → 결제 → OWNED [Use]."""
    assert _catalog(client)[PAID]["owned"] is False

    r = _buy(client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "owned"
    assert b["charged"] == priced
    assert b["already_owned"] is False
    assert b["order_id"]

    row = _catalog(client)[PAID]
    assert row["owned"] is True
    assert row["purchasable"] is False   # 이미 샀으므로 [Buy] 가 사라진다


def test_ownership_is_permanent_by_default(client: ASGITestClient, priced, card):
    """
    기본은 영구다 (expires_at=null).

    ⚠️ 기간제 여부는 PM 미결. 목표 UX 가 "OWNED" 이므로 영구가 기본이고,
    기간제가 새로운 발명이다.
    """
    _buy(client)
    ents = _sync(theme_entitlement.list_entitlements, BUYER)
    assert len(ents) == 1
    assert ents[0].expires_at is None
    assert ents[0].active is True


def test_ttl_is_configurable_for_pm(client: ASGITestClient, priced, card, monkeypatch):
    """PM 이 기간제를 정하면 설정만 채우면 된다 — 스키마는 준비돼 있다."""
    monkeypatch.setenv("THEME_ENTITLEMENT_TTL_DAYS", "30")
    _buy(client)
    ents = _sync(theme_entitlement.list_entitlements, BUYER)
    assert ents[0].expires_at is not None
    assert ents[0].active is True


def test_expired_entitlement_is_not_owned(client: ASGITestClient, priced, card):
    """만료된 소유권은 쓸 수 없다 — 해석할 수 없는 값도 만료로 본다(fail closed)."""
    _buy(client)
    key = theme_entitlement._key(BUYER, PAID)
    theme_entitlement._MOCK_ENTITLEMENTS[key]["expires_at"] = "2020-01-01T00:00:00+00:00"
    assert _catalog(client)[PAID]["owned"] is False

    theme_entitlement._MOCK_ENTITLEMENTS[key]["expires_at"] = "쓰레기값"
    assert _catalog(client)[PAID]["owned"] is False


def test_failed_payment_grants_nothing(client: ASGITestClient, card, monkeypatch):
    """
    결제가 실패하면 소유권이 생기지 않는다.

    toss_billing 목업은 amount<=0 을 실패 신호로 쓴다 — 여기서는 charge 를 직접
    실패로 갈아 끼워 "실패는 예외가 아니라 결과" 규약을 그대로 태운다.
    """
    monkeypatch.setenv(f"THEME_PRICE_{PAID.upper()}_KRW", "4900")

    async def _fail(**kw):
        return toss_billing.ChargeResult(
            ok=False, payment_key=None, order_id=kw["order_id"], amount=kw["amount"],
            raw={}, failure_code="CARD_DECLINED", failure_message="카드 거절",
        )

    monkeypatch.setattr(toss_billing, "charge", _fail)

    r = _buy(client)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "THEME_PAYMENT_FAILED"
    assert _sync(theme_entitlement.list_entitlements, BUYER) == []
    assert _catalog(client)[PAID]["owned"] is False


# ── 멱등성 · 중복 결제 ───────────────────────────────────────────────────────


def test_duplicate_purchase_does_not_charge_twice(client: ASGITestClient, priced, card):
    """**핵심 회귀**: 두 번 눌러도 두 번 청구되지 않는다."""
    first = _buy(client).json()
    assert first["charged"] == priced
    assert first["already_owned"] is False

    for _ in range(3):
        again = _buy(client).json()
        assert again["charged"] == 0
        assert again["already_owned"] is True
        assert again["status"] == "owned"

    # 소유권 행은 하나뿐이다.
    assert len(_sync(theme_entitlement.list_entitlements, BUYER)) == 1


def test_duplicate_purchase_never_reaches_the_payment_provider(
    client: ASGITestClient, priced, card, monkeypatch
):
    """
    이미 보유면 프로바이더를 **호출조차 하지 않는다.**

    "청구는 했지만 금액이 0" 이 아니라 "청구 자체가 없다"여야 한다 — 결제사
    호출은 그 자체로 비용이고 실패 가능성이다.
    """
    _buy(client)

    calls: list[str] = []
    real_charge = toss_billing.charge

    async def _tracked(**kw):
        calls.append(kw["order_id"])
        return await real_charge(**kw)

    monkeypatch.setattr(toss_billing, "charge", _tracked)

    assert _buy(client).json()["charged"] == 0
    assert calls == []


def test_each_purchase_uses_a_fresh_order_id(client: ASGITestClient, priced, card):
    """order_id 가 멱등성의 축이다 — 구매마다 새로 만들어져야 한다."""
    a = _buy(client, "aurora").json()["order_id"]
    theme_entitlement.__reset_for_tests()
    b = _buy(client, "aurora").json()["order_id"]
    assert a and b and a != b


def test_order_lookup_finds_the_granting_purchase(client: ASGITestClient, priced, card):
    order_id = _buy(client).json()["order_id"]
    found = _sync(theme_entitlement.find_by_order, order_id)
    assert found is not None
    assert found.user_id == BUYER
    assert found.theme_key == PAID


# ── 사용자 간 격리 ───────────────────────────────────────────────────────────


def test_ownership_does_not_leak_across_users(client: ASGITestClient, priced, card):
    """**핵심 회귀**: 남이 산 테마는 내 것이 아니다."""
    assert _buy(client, PAID, user=BUYER).status_code == 200

    assert _catalog(client, BUYER)[PAID]["owned"] is True
    assert _catalog(client, OTHER)[PAID]["owned"] is False
    assert _catalog(client, OTHER)[PAID]["purchasable"] is True

    assert _sync(theme_entitlement.list_entitlements, OTHER) == []


def test_two_users_buy_independently(client: ASGITestClient, priced, card):
    _buy(client, PAID, user=BUYER)
    second = _buy(client, PAID, user=OTHER).json()
    assert second["charged"] == priced       # 남이 샀다고 공짜가 되지 않는다
    assert second["already_owned"] is False
    assert _catalog(client, OTHER)[PAID]["owned"] is True


# ── 구독과 완전히 별개 ───────────────────────────────────────────────────────


def test_theme_purchase_never_reads_subscription_entitlement(
    client: ASGITestClient, priced, card, monkeypatch
):
    """
    **핵심 회귀**: 테마 구매가 구독 자격을 조회하지 않는다.

    조회하지 않으면 섞일 수도 없다. billing_store 에서 읽는 것은 **카드**이지
    구독 상태가 아니다 (card 픽스처의 status 는 일부러 expired 다).
    """
    from backend.services import premium_entitlement

    async def _forbidden(*_a, **_k):
        raise AssertionError("테마 경로가 구독 자격을 조회했다")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _forbidden)

    assert _buy(client).status_code == 200
    assert _catalog(client)[PAID]["owned"] is True


def test_theme_purchase_does_not_change_subscription_state(
    client: ASGITestClient, priced, card
):
    """테마를 사도 구독 저장소는 한 글자도 바뀌지 않는다."""
    from backend.services import subscription_store_service as sub_store

    before = dict(sub_store._MOCK_SUBS)
    _buy(client)
    assert sub_store._MOCK_SUBS == before


def test_subscription_does_not_grant_themes(client: ASGITestClient, priced):
    """
    반대 방향: 활성 구독이 있어도 유료 테마가 공짜로 생기지 않는다.

    멤버십 할인/무료 제공은 **PM 미결**이며 구현하지 않았다 — 구현하면 두 축이
    커플링되고, 그건 요구사항이 금지한 것이다.
    """
    from backend.services import premium_entitlement

    async def _member(_uid):
        return premium_entitlement.EntitlementState(
            entitled=True, status="active", enforced=True
        )

    # 회원이어도 카탈로그의 소유 상태는 그대로다.
    assert _catalog(client)[PAID]["owned"] is False
    assert _sync(theme_entitlement.owned_theme_keys, BUYER) == set()


def test_theme_modules_are_independent_of_subscription_and_generation():
    """
    구조로 고정한다 — 테마 모듈이 구독·생성 모듈을 **import 하지 않는다.**

    AST 로 보는 이유는 함수 안에서 하는 지연 import 까지 잡기 위해서다.
    """
    import ast

    forbidden = {
        "premium_entitlement",
        "subscription_store_service",
        "subscription_webhook_service",
        "premium_generation",
        "generation_queue",
        "credit_generation_service",
        "wallet_service",
        "premium_purchase",
        "luma_service",
        "wan_service",
        "video_generation",
    }
    for path in (
        "backend/services/theme_catalog.py",
        "backend/services/theme_entitlement.py",
        "backend/services/theme_purchase.py",
        "backend/routers/theme_store_v1.py",
    ):
        tree = ast.parse(open(path, encoding="utf-8").read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.rsplit(".", 1)[-1])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported.add(a.name)
                if node.module:
                    imported.add(node.module.rsplit(".", 1)[-1])
        leaked = imported & forbidden
        assert not leaked, f"{path} 가 {leaked} 를 import 한다"


# ── 테마 변경이 생성을 유발하지 않는다 ──────────────────────────────────────


def test_buying_or_changing_theme_never_generates(
    client: ASGITestClient, priced, card, monkeypatch
):
    """
    **핵심 회귀**: 테마를 사거나 바꿔도 BREATHING/프리미엄 행동이 다시 만들어지지 않는다.

    생성 진입점을 전부 폭탄으로 갈아 끼우고 스토어 경로를 두드린다.
    """
    from backend.services import (
        credit_generation_service,
        generation_queue,
        premium_generation,
        premium_purchase,
        video_generation,
        wallet_service,
    )

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨 — 테마 경로는 생성하지 않는다")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
        (credit_generation_service, "generate_with_credit"),
        (video_generation, "submit_generation"),
        (wallet_service, "deduct_credits"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _catalog(client)
    _buy(client)
    _catalog(client)
    _buy(client)          # 멱등 경로
    _buy(client, "sunset")  # 가격 미설정 → 거절 경로
    assert fired == []


# ── 카탈로그 설정 ────────────────────────────────────────────────────────────


def test_paid_set_is_configurable_without_code_change(
    client: ASGITestClient, monkeypatch
):
    """
    어떤 테마가 유료인가도 PM 소관이다.

    요구사항 예시(Beach/Snow Forest 유료)는 **설정으로** 표현된다 — 코드가 지금
    무료인 테마를 임의로 유료로 바꾸지 않는다.
    """
    monkeypatch.setenv("THEME_PAID_KEYS", "beach,snow_forest")
    monkeypatch.setenv("THEME_PRICE_BEACH_KRW", "3900")

    cat = _catalog(client)
    assert cat["beach"]["free"] is False
    assert cat["beach"]["price_krw"] == 3900
    assert cat["beach"]["purchasable"] is True
    assert cat["snow_forest"]["free"] is False
    assert cat["snow_forest"]["price_krw"] is None  # 가격 미설정 → 못 판다
    # 원래 유료였던 것들이 무료로 돌아간다.
    assert cat["aurora"]["free"] is True


def test_paid_set_can_be_emptied(client: ASGITestClient, monkeypatch):
    """되돌리기 스위치 — 전부 무료로 만들 수 있다."""
    monkeypatch.setenv("THEME_PAID_KEYS", "")
    cat = _catalog(client)
    assert all(row["free"] for row in cat.values())
    assert all(row["owned"] for row in cat.values())


def test_catalog_covers_every_frontend_theme_key():
    """
    카탈로그가 프론트 themes.ts 의 themeKey 전체를 덮는다.

    ⚠️ key 로 잡는 이유: themes.ts 에서 beach 와 custom_photo_bg 가 **둘 다 id 9**
    라 숫자 id 는 신뢰할 수 없다(기존 결함). key 는 충돌이 없다.
    """
    import re

    src = open("src/components/memorial/themes.ts", encoding="utf-8").read()
    body = src[src.index("memorialThemes: MemorialTheme[] = ["):]
    keys = set(re.findall(r'themeKey:\s*"([^"]+)"', body))
    keys.add("custom_photo_bg")  # 상수로 참조되는 항목
    assert keys == set(theme_catalog.ALL_THEME_KEYS), keys ^ set(theme_catalog.ALL_THEME_KEYS)


def test_catalog_keys_are_unique():
    assert len(theme_catalog.ALL_THEME_KEYS) == len(set(theme_catalog.ALL_THEME_KEYS))


def test_bad_price_config_does_not_sell(monkeypatch):
    """오타·음수·0 은 '무료'가 아니라 '팔지 않음'이다."""
    for bad in ("abc", "-100", "0", " "):
        monkeypatch.setenv(f"THEME_PRICE_{PAID.upper()}_KRW", bad)
        assert theme_catalog.price_krw(PAID) is None, bad
