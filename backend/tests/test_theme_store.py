"""
유료 테마 스토어 — 소유권 · 크레딧 결제 · 분리.

핵심 계약:
  * 무료 테마는 결제 없이 언제나 쓸 수 있다.
  * 유료 테마는 사기 전엔 NOT OWNED, 산 뒤엔 OWNED.
  * **구독 자격과 테마 소유권은 완전히 별개다** — 양방향으로 검증한다.
  * 같은 사람이 두 번 눌러도 두 번 청구되지 않는다 (멱등).
  * 남이 산 테마는 내 것이 아니다.
  * 테마를 사거나 바꿔도 BREATHING/프리미엄 행동이 다시 만들어지지 않는다.
  * **가격을 발명하지 않는다** — 설정이 없으면 팔리지 않는다.

── 화폐가 바뀌었다 (Phase 11) ───────────────────────────────────────────────
예전에 이 파일은 `POST /themes/purchase` (저장된 카드로 KRW 즉시 청구)를 두드렸다.
그 경로는 은퇴했고, 테마를 사는 유일한 방법은 **Beam Credit** 이다:

    POST /api/v1/themes/purchase-with-credits

계약 자체는 그대로다 — 멱등성, 사용자 간 격리, 구독과의 분리, 생성 금지는
화폐와 무관하게 지켜져야 한다. 그래서 테스트를 지우지 않고 **경로만 옮겼다.**

차감·원장·소유권의 원자성은 서비스/SQL 층에서 본다
(test_theme_credit_purchase.py · test_theme_credit_purchase_sql.py).
여기서 보는 것은 **HTTP 표면과 카탈로그**다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import theme_store_v1
from backend.services import (
    credit_ledger,
    product_catalog,
    theme_catalog,
    theme_entitlement,
    wallet_service,
)

from .conftest import ASGITestClient

BUYER = "buyer@example.com"
OTHER = "other@example.com"
PAID = "aurora"
FREE = "fresh_forest"

#: Aurora 의 크레딧 가격. 마이그레이션 20261003000000 과 같은 값이다.
CREDITS = 5


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    monkeypatch.delenv("THEME_PAID_KEYS", raising=False)
    monkeypatch.delenv("THEME_ENTITLEMENT_TTL_DAYS", raising=False)
    for k in theme_catalog.ALL_THEME_KEYS:
        monkeypatch.delenv(f"THEME_PRICE_{k.upper()}_KRW", raising=False)
    theme_entitlement.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    product_catalog.__reset_for_tests()
    product_catalog.set_price_for_tests(
        product_catalog.theme_key(PAID), CREDITS, product_catalog.TYPE_THEME
    )
    yield
    theme_entitlement.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    product_catalog.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(theme_store_v1.router, prefix="/api")
    return ASGITestClient(app)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> int:
    """
    레거시 KRW 가격이 설정된 상태 — **카탈로그 표시용이다.**

    ⚠️ 이 숫자로는 이제 아무것도 살 수 없다(주문을 만드는 경로가 없다). 카탈로그가
    여전히 price_krw 를 실어 보내므로 그 필드의 계약만 확인한다.
    """
    monkeypatch.setenv(f"THEME_PRICE_{PAID.upper()}_KRW", "4900")
    return 4900


@pytest.fixture
def funded() -> int:
    """구매자와 제3자 모두에게 넉넉한 잔액. **구독이 아니다** — 지갑일 뿐이다."""
    for user in (BUYER, OTHER):
        _sync(
            wallet_service.add_credits,
            user,
            20,
            reason=credit_ledger.REASON_CREDIT_PACK_TOPUP,
            idempotency_key=f"fund:{user}",
        )
    return 20


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _catalog(client: ASGITestClient, user: str = BUYER) -> dict[str, dict]:
    r = client.get("/api/v1/themes/catalog", headers=_auth(user))
    assert r.status_code == 200, r.text
    return {t["theme_key"]: t for t in r.json()["themes"]}


def _balance(user: str = BUYER) -> int:
    w = _sync(wallet_service.get_wallet, user, create_if_missing=True)
    return w.current_credits if w else 0


def _buy(client: ASGITestClient, theme: str = PAID, user: str = BUYER):
    return client.post(
        "/api/v1/themes/purchase-with-credits",
        json={"theme_key": theme},
        headers=_auth(user),
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


def test_free_theme_cannot_be_purchased(client: ASGITestClient, funded):
    """무료 테마에 결제를 만들지 않는다 — 만들면 그게 곧 오과금이다."""
    r = _buy(client, FREE)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_IS_FREE"
    assert _balance() == funded


# ── 미보유 유료 테마 ─────────────────────────────────────────────────────────


def test_unowned_paid_theme_shows_as_not_owned(client: ASGITestClient, priced):
    row = _catalog(client)[PAID]
    assert row["free"] is False
    assert row["owned"] is False
    assert row["purchasable"] is True
    assert row["price_krw"] == priced
    assert row["credit_price"] == CREDITS


def test_the_catalog_answers_balance_and_price_in_one_call(
    client: ASGITestClient, funded
):
    """
    화면이 "잔액 20 / 가격 5" 를 그리는 데 조회가 두 번 필요하면 그 사이에
    잔액이 바뀔 수 있고, 사용자는 자기가 못 살 것을 [Buy] 로 본다.
    """
    r = client.get("/api/v1/themes/catalog", headers=_auth(BUYER))
    body = r.json()
    assert body["credit_balance"] == funded
    assert {t["theme_key"]: t["credit_price"] for t in body["themes"]}[PAID] == CREDITS


def test_a_theme_without_a_credit_price_is_not_sold(client: ASGITestClient, funded):
    """
    **가격을 발명하지 않는다.** 카탈로그에 값이 없으면 팔리지 않는다.

    0 으로 떨어뜨리면 "무료로 팔린다" — 가격 미설정이 전량 무료 배포가 된다.
    null 과 0 은 다르다: null 은 판매 불가, 0 은 명시적 무료다.
    """
    product_catalog._mock_catalog().pop(product_catalog.theme_key(PAID), None)

    assert _catalog(client)[PAID]["credit_price"] is None

    r = _buy(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_PRODUCT_NOT_SOLD"
    assert _balance() == funded


def test_purchase_requires_auth(client: ASGITestClient):
    assert client.post(
        "/api/v1/themes/purchase-with-credits", json={"theme_key": PAID}
    ).status_code == 401
    assert client.get("/api/v1/themes/catalog").status_code == 401


def test_unknown_theme_is_rejected(client: ASGITestClient, funded):
    r = _buy(client, "not_a_theme")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "THEME_UNKNOWN"


# ── 구매 성공 ────────────────────────────────────────────────────────────────


def test_successful_purchase_grants_ownership(client: ASGITestClient, funded):
    """**핵심 흐름**: NOT OWNED [5 크레딧] → 차감 → OWNED [Use]."""
    assert _catalog(client)[PAID]["owned"] is False

    r = _buy(client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "owned"
    assert b["charged"] == CREDITS
    assert b["already_owned"] is False
    assert b["credits_remaining"] == funded - CREDITS
    assert b["currency"] == "CREDIT"
    assert b["order_id"]

    row = _catalog(client)[PAID]
    assert row["owned"] is True
    assert row["purchasable"] is False   # 이미 샀으므로 [Buy] 가 사라진다
    assert _balance() == funded - CREDITS


def test_ownership_is_permanent(client: ASGITestClient, funded):
    _buy(client)
    ents = _sync(theme_entitlement.list_entitlements, BUYER)
    assert len(ents) == 1
    assert ents[0].expires_at is None
    assert ents[0].active is True


def test_the_krw_ttl_setting_cannot_expire_a_credit_purchase(
    client: ASGITestClient, funded, monkeypatch
):
    """
    **Phase 10 종료 조건의 테마 쪽 면.**

    THEME_ENTITLEMENT_TTL_DAYS 는 레거시 KRW 부여 경로(_grant)의 설정이다.
    크레딧으로 산 테마는 영구다 — 이 설정이 켜져 있어도 만료되지 않는다.
    섞이면 "크레딧을 썼는데 30일 뒤에 사라졌다"가 된다.
    """
    monkeypatch.setenv("THEME_ENTITLEMENT_TTL_DAYS", "30")
    _buy(client)
    assert _sync(theme_entitlement.list_entitlements, BUYER)[0].expires_at is None


def test_expired_entitlement_is_not_owned(client: ASGITestClient, funded):
    """만료된 소유권은 쓸 수 없다 — 해석할 수 없는 값도 만료로 본다(fail closed)."""
    _buy(client)
    key = theme_entitlement._key(BUYER, PAID)
    theme_entitlement._MOCK_ENTITLEMENTS[key]["expires_at"] = "2020-01-01T00:00:00+00:00"
    assert _catalog(client)[PAID]["owned"] is False

    theme_entitlement._MOCK_ENTITLEMENTS[key]["expires_at"] = "쓰레기값"
    assert _catalog(client)[PAID]["owned"] is False


def test_insufficient_credits_grants_nothing(client: ASGITestClient):
    """
    잔액이 모자라면 소유권이 생기지 않는다.

    ⚠️ 예전 KRW 판(카드 거절)의 자리다. 실패 신호가 결제사에서 지갑으로 옮겨
    갔을 뿐, 지켜야 할 것은 같다 — **실패는 소유권을 만들지 않는다.**
    """
    _sync(
        wallet_service.add_credits, BUYER, CREDITS - 1,
        reason=credit_ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="short",
    )

    r = _buy(client)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "INSUFFICIENT_CREDITS"
    assert _sync(theme_entitlement.list_entitlements, BUYER) == []
    assert _catalog(client)[PAID]["owned"] is False
    assert _balance() == CREDITS - 1   # 한 크레딧도 사라지지 않았다


# ── 멱등성 · 중복 결제 ───────────────────────────────────────────────────────


def test_duplicate_purchase_does_not_charge_twice(client: ASGITestClient, funded):
    """**핵심 회귀**: 두 번 눌러도 두 번 청구되지 않는다."""
    first = _buy(client).json()
    assert first["charged"] == CREDITS
    assert first["already_owned"] is False

    for _ in range(3):
        again = _buy(client).json()
        assert again["charged"] == 0
        assert again["already_owned"] is True
        assert again["status"] == "owned"

    assert _balance() == funded - CREDITS          # 딱 한 번만 빠졌다
    assert len(_sync(theme_entitlement.list_entitlements, BUYER)) == 1


def test_duplicate_purchase_never_touches_the_wallet_again(
    client: ASGITestClient, funded, monkeypatch
):
    """
    이미 보유면 지갑을 **건드리지도 않는다.**

    "차감은 했지만 금액이 0" 이 아니라 "차감 시도 자체가 없다"여야 한다 —
    차감 시도는 그 자체로 원장 한 줄이고 실패 가능성이다.
    """
    _buy(client)

    async def _forbidden(*_a, **_k):
        raise AssertionError("이미 보유한 테마에 차감을 시도했다")

    monkeypatch.setattr(wallet_service, "deduct_credits", _forbidden)

    again = _buy(client)
    assert again.status_code == 200, again.text
    assert again.json()["charged"] == 0


def test_the_order_id_is_stable_because_it_is_the_idempotency_axis(
    client: ASGITestClient, funded
):
    """
    ⚠️ 계약이 뒤집힌 자리다. KRW 판에서는 구매마다 **새** order_id 를 만들었다
    (결제사가 주문 재사용을 거절하므로). 크레딧 판에서 order_id 는 멱등 키
    자체이고, 따라서 **같은 (사용자, 테마) 면 항상 같아야 한다.**
    변하면 더블탭이 두 번 청구된다.
    """
    a = _buy(client).json()["order_id"]
    b = _buy(client).json()["order_id"]
    assert a == b
    assert a == credit_ledger.theme_purchase_key(BUYER, PAID)

    # 사람이 다르면 키도 다르다 — 아니면 남의 구매가 내 것을 멱등 처리한다.
    assert _buy(client, user=OTHER).json()["order_id"] != a


def test_order_lookup_finds_the_granting_purchase(client: ASGITestClient, funded):
    order_id = _buy(client).json()["order_id"]
    found = _sync(theme_entitlement.find_by_order, order_id)
    assert found is not None
    assert found.user_id == BUYER
    assert found.theme_key == PAID


# ── 사용자 간 격리 ───────────────────────────────────────────────────────────


def test_ownership_does_not_leak_across_users(client: ASGITestClient, funded):
    """**핵심 회귀**: 남이 산 테마는 내 것이 아니다."""
    assert _buy(client, PAID, user=BUYER).status_code == 200

    assert _catalog(client, BUYER)[PAID]["owned"] is True
    assert _catalog(client, OTHER)[PAID]["owned"] is False
    assert _catalog(client, OTHER)[PAID]["purchasable"] is True

    assert _sync(theme_entitlement.list_entitlements, OTHER) == []
    assert _balance(OTHER) == funded     # 남의 구매가 내 지갑을 건드리지 않았다


def test_two_users_buy_independently(client: ASGITestClient, funded):
    _buy(client, PAID, user=BUYER)
    second = _buy(client, PAID, user=OTHER).json()
    assert second["charged"] == CREDITS       # 남이 샀다고 공짜가 되지 않는다
    assert second["already_owned"] is False
    assert _catalog(client, OTHER)[PAID]["owned"] is True


# ── 구독과 완전히 별개 ───────────────────────────────────────────────────────


def test_theme_purchase_never_reads_subscription_entitlement(
    client: ASGITestClient, funded, monkeypatch
):
    """
    **핵심 회귀**: 테마 구매가 구독 자격을 조회하지 않는다.

    조회하지 않으면 섞일 수도 없다 — "회원이면 공짜"도, "비회원이면 못 산다"도
    한 줄로 생길 수 없다.
    """
    from backend.services import premium_entitlement

    async def _forbidden(*_a, **_k):
        raise AssertionError("테마 경로가 구독 자격을 조회했다")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _forbidden)

    assert _buy(client).status_code == 200
    assert _catalog(client)[PAID]["owned"] is True


def test_theme_purchase_does_not_change_subscription_state(
    client: ASGITestClient, funded
):
    """테마를 사도 구독 저장소는 한 글자도 바뀌지 않는다."""
    from backend.services import subscription_store_service as sub_store

    before = dict(sub_store._MOCK_SUBS)
    _buy(client)
    assert sub_store._MOCK_SUBS == before


def test_subscription_does_not_grant_themes(client: ASGITestClient, priced):
    """
    반대 방향: 활성 구독이 있어도 유료 테마가 공짜로 생기지 않는다.

    멤버십은 **크레딧을 지급할 뿐** 소유권을 주지 않는다 (Phase 10). 그 크레딧으로
    사면 그때 소유가 생기고, 해지해도 그 소유는 남는다.
    """
    assert _catalog(client)[PAID]["owned"] is False
    assert _sync(theme_entitlement.owned_theme_keys, BUYER) == set()


def test_theme_modules_are_independent_of_subscription_and_generation():
    """
    구조로 고정한다 — 테마 모듈이 구독·생성 모듈을 **import 하지 않는다.**

    AST 로 보는 이유는 함수 안에서 하는 지연 import 까지 잡기 위해서다.

    ── wallet_service 는 이제 허용된다 (Phase 4) ────────────────────────────
    예전에는 금지 목록에 있었다. 그때 테마는 KRW 전용이었고 "테마 구매가 크레딧을
    건드리지 않는다"가 지켜야 할 계약이었기 때문이다.

    Phase 4 에서 그 계약이 **의도적으로 바뀌었다**: 테마는 Beam Credit 으로 팔린다
    (Aurora = 5 크레딧). 지갑을 건드리는 것이 이제 이 모듈의 일이다.

    나머지 셋은 그대로 금지다 — 그 축들은 여전히 독립이어야 한다:
        구독   테마를 사도 구독 상태는 한 글자도 바뀌지 않는다
        생성   테마를 사도 BREATHING·프리미엄 행동이 다시 만들어지지 않는다
        프리미엄 구매 원장  테마 소유권은 premium_purchases 와 무관하다
    """
    import ast

    forbidden = {
        "premium_entitlement",
        "subscription_store_service",
        "subscription_webhook_service",
        "premium_generation",
        "generation_queue",
        "credit_generation_service",
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
    client: ASGITestClient, funded, monkeypatch
):
    """
    **핵심 회귀**: 테마를 사거나 바꿔도 BREATHING/프리미엄 행동이 다시 만들어지지 않는다.

    생성 진입점을 전부 폭탄으로 갈아 끼우고 스토어 경로를 두드린다.

    ⚠️ wallet_service.deduct_credits 는 목록에서 빠졌다 — 이제 테마 구매가 그것을
    **부르는 것이 정상**이다(Phase 4). 지갑은 생성 진입점이 아니다. 지갑을 건드리는
    것과 영상을 만드는 것은 다른 일이고, 여기서 잡으려는 것은 후자다.
    """
    from backend.services import (
        credit_generation_service,
        generation_queue,
        premium_generation,
        premium_purchase,
        video_generation,
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
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _catalog(client)
    _buy(client)
    _catalog(client)
    _buy(client)               # 멱등 경로
    _buy(client, "sunset")     # 가격 미설정 → 거절 경로
    assert fired == []


# ── 은퇴한 KRW 경로는 돌아오지 않는다 (Phase 11) ────────────────────────────


@pytest.mark.parametrize("path", ["purchase", "checkout"])
def test_the_krw_purchase_endpoints_are_gone(client: ASGITestClient, path: str):
    """
    새 KRW 주문을 만들던 두 경로는 삭제됐다.

    404 를 확인하는 이유: 라우터에서만 떼어 내고 서비스 함수를 남겨 두면
    "임시로 하나만 열자"가 한 줄로 가능하다. 이 테스트가 그 한 줄을 깨뜨린다.
    ✅ 구매는 크레딧으로만 한다. **POST /confirm 은 살아 있다**(드레인) —
    test_theme_legacy_retired.py 참고.
    """
    r = client.post(
        f"/api/v1/themes/{path}", json={"theme_key": PAID}, headers=_auth(BUYER)
    )
    assert r.status_code == 404


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
