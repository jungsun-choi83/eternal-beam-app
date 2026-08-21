"""
물리 제품 주문 (Phase 12) — LETTER ₩14,900 / MEMORY BOX ₩49,000.

핵심 계약:
  * **무료 사용자**(구독·테마·크레딧 없음)가 편지를 살 수 있다.
  * 결제 성공이 하는 일은 **주문 한 행을 PAID 로 바꾸는 것**이 전부다.
    구독·테마·크레딧·생성은 한 글자도 바뀌지 않는다.
  * **BREATHING 은 이 주문과 무관하게 무료다** — ₩14,900 은 종이 값이다.
  * canonical petId 하나. 주문용 펫도, 중복 편지도, 새 Shaker 공유도 만들지 않는다.
  * 주문은 user_id + pet_id + soul_trace_letter_id 를 함께 들고 있다.
  * 남의 주문·남의 편지를 쓸 수 없다.
  * 운영이 결제된 주문을 고객/펫/주문번호로 찾을 수 있다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import orders_v1
from backend.services import (
    pet_registry,
    physical_order,
    physical_product,
    soul_trace_import,
    soul_trace_letter,
    toss_billing,
)

from .conftest import ASGITestClient

#: 구독도 테마도 크레딧도 없는 사용자.
FREE_USER = "free@example.com"
OTHER = "other@example.com"
OPS = "ops@eternalbeam.com"
PET = "pet_abc123"
OTHER_PET = "pet_other999"
LETTER_SRC = "st_letter_0001"
#: 실제 편지 길이(생성기 목표: 한국어 380~650자)에 가깝게 둔다. 짧은 더미를 쓰면
#: 발췌가 본문 전체와 같아져서 "본문은 목록에 싣지 않는다"를 검증하지 못한다.
LETTER_BODY = (
    "안녕, 엄마 아빠. 나는 지금도 곁에 머물고 있어요. "
    "그때 현관에서 나던 냄새 기억나? 문 열리는 소리가 나면 나는 벌써 꼬리를 흔들고 있었어. "
    "산책 나가면 엄마 손이 늘 따뜻했는데, 나는 그 손을 제일 좋아했어. "
    "아, 맞다. 소파 끝자리 그거, 내 자리였잖아. 거기 햇빛이 제일 잘 들었거든. "
    "나 지금 여기서 편해. 그러니까 너무 오래 슬퍼하지 마."
)
#: 정상 핸드오프 토큰(스텁이 받는 값). 모양만 맞으면 된다.
HANDOFF = "h" * 43
#: 이미 소비됐거나 만료된 토큰을 흉내 낸다.
BAD_HANDOFF = "b" * 43
#: Soul Trace 가 빈 본문을 돌려주는 경우를 흉내 낸다.
EMPTY_BODY_HANDOFF = "e" * 43

SHIPPING = {
    "recipient_name": "김보호",
    "recipient_phone": "010-1234-5678",
    "postal_code": "06236",
    "address_line1": "서울特별시 강남구 테헤란로 1",
    "address_line2": "101동 1001호",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    for p in ("LETTER", "MEMORY_BOX"):
        monkeypatch.delenv(f"PRODUCT_PRICE_{p}_KRW", raising=False)
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    pet_registry.__reset_for_tests()

    # Soul Trace 는 **다른 프로젝트**다. 테스트에서 실제 HTTP 를 쏘지 않는다.
    # 대신 그쪽이 돌려줄 정본을 흉내 낸다 — 중요한 것은 본문이 **서버 경로로만**
    # 들어온다는 사실이며, 그 성질은 스텁으로도 그대로 검증된다.
    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        if handoff == BAD_HANDOFF:
            raise soul_trace_import.ImportError_(
                "HANDOFF_CONSUMED", "이미 사용된 링크입니다.", status=409
            )
        if handoff == EMPTY_BODY_HANDOFF:
            raise soul_trace_import.ImportError_(
                "SOURCE_BODY_EMPTY", "본문이 비어 있습니다.", status=409
            )
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=LETTER_BODY, pet_name="고야"
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)
    yield
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(orders_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _pet_for(user: str) -> str:
    """
    사용자마다 **자기 펫**을 쓴다.

    펫 소유권 검사가 붙은 뒤로는 두 사용자가 같은 pet_id 를 쓸 수 없다 —
    먼저 쓴 쪽이 소유자가 되고 다른 쪽은 403 이다. 그것이 바로 검사의 목적이며,
    테스트도 실제 사용 모양을 따라야 한다.
    """
    return PET if user != OTHER else OTHER_PET


def _claim_letter(
    client: ASGITestClient,
    user: str = FREE_USER,
    src: str = LETTER_SRC,
    handoff: str = HANDOFF,
):
    """핸드오프 교환. **본문을 보내지 않는다** — 서버가 Soul Trace 에서 가져온다."""
    return client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": src, "handoff": handoff},
        headers=_auth(user),
    )


def _link_pet(
    client: ASGITestClient, letter_id: str, *, user: str = FREE_USER,
    pet_id: str | None = None,
):
    pet_id = pet_id or _pet_for(user)
    return client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": letter_id, "pet_id": pet_id},
        headers=_auth(user),
    )


def _link_letter(
    client: ASGITestClient, user: str = FREE_USER, src: str = LETTER_SRC,
    *, pet_id: str | None = None,
):
    """가져오기 + 펫 연결. 주문 흐름 테스트가 쓰는 준비 단계."""
    r = _claim_letter(client, user, src)
    if r.status_code != 200:
        return r
    _link_pet(client, r.json()["letter_id"], user=user, pet_id=pet_id or _pet_for(user))
    return r


def _checkout(
    client: ASGITestClient,
    *,
    user: str = FREE_USER,
    product: str = "LETTER",
    letter_id: str | None = None,
    pet_id: str | None = None,
):
    if pet_id is None:
        pet_id = _pet_for(user)
    body = {"pet_id": pet_id, "product_type": product, **SHIPPING}
    if letter_id is not None:
        body["soul_trace_letter_id"] = letter_id
    return client.post("/api/v1/orders/checkout", json=body, headers=_auth(user))


def _confirm(client: ASGITestClient, order_id: str, *, user: str = FREE_USER,
             amount: int | None = None, payment_key: str = "pk_1"):
    body: dict = {"payment_key": payment_key, "order_id": order_id}
    if amount is not None:
        body["amount"] = amount
    return client.post("/api/v1/orders/confirm", json=body, headers=_auth(user))


def _buy(client: ASGITestClient, product: str = "LETTER", user: str = FREE_USER):
    """편지 연결 → 체크아웃 → 확인. 전체 흐름 한 번."""
    letter_id = _link_letter(client, user).json()["letter_id"]
    order = _checkout(client, user=user, product=product, letter_id=letter_id).json()
    done = _confirm(client, order["order_id"], user=user, amount=order["amount"])
    return order, done


# ── 카탈로그 ──────────────────────────────────────────────────────────────────


def test_catalog_prices_match_pm(client: ASGITestClient):
    r = client.get("/api/v1/orders/products")
    assert r.status_code == 200
    by = {p["product_type"]: p for p in r.json()["products"]}
    assert by["LETTER"]["price_krw"] == 14_900
    assert by["MEMORY_BOX"]["price_krw"] == 49_000
    assert by["LETTER"]["currency"] == "KRW"


def test_memory_box_contents_include_letter_and_qr_card(client: ASGITestClient):
    by = {p["product_type"]: p for p in client.get("/api/v1/orders/products").json()["products"]}
    letter = set(by["LETTER"]["contents"])
    box = set(by["MEMORY_BOX"]["contents"])

    assert {"printed_letter", "envelope", "qr"} <= letter
    # 메모리 박스는 편지 구성 + 사진 카드 + QR 메모리 카드 + 패키징.
    assert letter <= box | {"qr"}
    assert {"photo_card", "qr_memory_card", "rigid_box", "black_tissue", "message_card"} <= box


def test_nfc_product_is_not_sellable(client: ASGITestClient):
    """핸드오프가 NFC 를 나중으로 미뤘다 — 만들 수 없는 것을 팔지 않는다."""
    types = {p["product_type"] for p in client.get("/api/v1/orders/products").json()["products"]}
    assert not any("NFC" in t for t in types)

    r = _checkout(client, product="MEMORY_BOX_NFC", letter_id="x")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "PRODUCT_UNKNOWN"


def test_catalog_is_public(client: ASGITestClient):
    """가격은 공개 정보다 — 로그인 전에도 보여 줘야 한다."""
    assert client.get("/api/v1/orders/products").status_code == 200


# ── Soul Trace 편지: 연결이지 생성이 아니다 ─────────────────────────────────


def test_letter_must_be_supplied_not_generated(client: ASGITestClient):
    """
    **핵심 계약**: Eternal Beam 은 편지를 만들지 않는다.

    Soul Trace 가 빈 본문을 돌려주면 거절한다. 기본 문구를 채우면 그 순간 우리가
    편지를 생성한 것이 되고, 고객은 Soul Trace 가 쓴 적 없는 문장이 인쇄된
    종이를 받는다.
    """
    r = _claim_letter(client, handoff=EMPTY_BODY_HANDOFF)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SOURCE_BODY_EMPTY"


def test_browser_cannot_supply_the_letter_body(client: ASGITestClient):
    """
    **핵심 보안 계약**: 브라우저가 인쇄될 본문을 정할 수 없다.

    예전 /letter/link 는 요청 바디의 letter_body 를 그대로 저장했고 그것이 A5 로
    인쇄되어 배송됐다. 그 경로는 이제 존재하지 않으며, claim 은 본문을 받는
    자리 자체가 없다 — 보내도 무시되고 저장되는 것은 Soul Trace 의 정본이다.
    """
    # 예전 엔드포인트는 사라졌다.
    gone = client.post(
        "/api/v1/orders/letter/link",
        json={"source_letter_id": LETTER_SRC, "letter_body": "공격자가 고른 문장"},
        headers=_auth(FREE_USER),
    )
    assert gone.status_code == 404

    # claim 에 본문을 끼워 넣어도 저장되는 것은 Soul Trace 가 준 값이다.
    r = client.post(
        "/api/v1/orders/letter/claim",
        json={
            "trace_id": LETTER_SRC,
            "handoff": HANDOFF,
            "letter_body": "공격자가 고른 문장",
            "child_name": "공격자",
        },
        headers=_auth(FREE_USER),
    )
    assert r.status_code == 200
    stored = _sync(soul_trace_letter.get_letter, r.json()["letter_id"])
    assert stored.letter_body == LETTER_BODY
    assert "공격자" not in (stored.letter_body or "")
    assert stored.child_name == "고야"


def test_consumed_or_expired_handoff_is_rejected(client: ASGITestClient):
    """1회용이다 — 두 번째 교환은 통과하지 않는다."""
    r = _claim_letter(client, handoff=BAD_HANDOFF)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "HANDOFF_CONSUMED"
    assert _sync(soul_trace_letter.list_letters, FREE_USER) == []


def test_claim_leaves_pet_unlinked_until_asked(client: ASGITestClient):
    """
    Soul Trace 만 마친 사용자는 아직 펫이 없다 — 클레임이 펫을 요구하면
    편지를 받을 수 없고, 여기서 펫을 만들면 두 번째 펫 신원이 생긴다.
    """
    lid = _claim_letter(client).json()["letter_id"]
    assert _sync(soul_trace_letter.get_letter, lid).pet_id is None


def test_linking_the_same_letter_twice_does_not_duplicate(client: ASGITestClient):
    """**핵심 회귀**: 같은 Soul Trace 편지가 두 벌이 되지 않는다."""
    a = _link_letter(client).json()["letter_id"]
    b = _link_letter(client).json()["letter_id"]
    assert a == b

    rows = _sync(soul_trace_letter.list_letters, FREE_USER)
    assert len(rows) == 1
    assert rows[0].source_letter_id == LETTER_SRC


def test_letter_keeps_a_print_snapshot(client: ASGITestClient):
    """인쇄는 되돌릴 수 없다 — 주문 시점 본문이 남아 있어야 한다."""
    lid = _link_letter(client).json()["letter_id"]
    stored = _sync(soul_trace_letter.get_letter, lid)
    assert stored is not None
    assert stored.letter_body == LETTER_BODY
    assert stored.source == "soul_trace"


def test_letter_links_to_canonical_pet(client: ASGITestClient):
    lid = _link_letter(client).json()["letter_id"]
    assert _sync(soul_trace_letter.get_letter, lid).pet_id == PET


def test_letter_body_is_not_returned_in_list(client: ASGITestClient):
    """본문은 인쇄용이지 화면용이 아니다 — 목록에 싣지 않는다."""
    _link_letter(client)
    r = client.get("/api/v1/orders/letters", headers=_auth(FREE_USER))
    assert LETTER_BODY not in r.text
    assert "letter_body" not in r.text

    # 발췌는 **자른 것**이지 새로 쓴 문장이 아니다 — 본문의 접두사여야 한다.
    excerpt = r.json()["letters"][0]["letter_excerpt"]
    assert len(excerpt) < len(LETTER_BODY)
    assert LETTER_BODY.startswith(excerpt.rstrip("…"))


def test_letters_do_not_leak_across_users(client: ASGITestClient):
    _link_letter(client, user=FREE_USER)
    r = client.get("/api/v1/orders/letters", headers=_auth(OTHER))
    assert r.json()["letters"] == []


# ── 무료 사용자가 편지를 산다 ────────────────────────────────────────────────


def test_free_user_can_buy_letter(client: ASGITestClient):
    """
    **핵심 흐름**: 구독도 테마도 크레딧도 없는 사용자가 LETTER 를 산다.
    """
    order, done = _buy(client, "LETTER")

    assert order["amount"] == 14_900
    assert order["product_type"] == "LETTER"
    assert done.status_code == 200, done.text
    b = done.json()
    assert b["payment_status"] == "paid"
    assert b["charged"] == 14_900
    assert b["already_paid"] is False


def test_memory_box_checkout(client: ASGITestClient):
    order, done = _buy(client, "MEMORY_BOX")
    assert order["amount"] == 49_000
    assert done.json()["payment_status"] == "paid"
    assert done.json()["product_type"] == "MEMORY_BOX"


def test_checkout_requires_no_membership_or_saved_card(client: ASGITestClient, monkeypatch):
    """
    멤버십도 저장된 카드도 요구하지 않는다.

    billing_store 를 폭탄으로 만들어 **조회조차 하지 않음**을 확인한다.
    """
    from backend.services import billing_store

    async def _boom(*_a, **_k):
        raise AssertionError("물리 주문이 구독/카드를 조회했다")

    monkeypatch.setattr(billing_store, "get_subscription", _boom)

    lid = _link_letter(client).json()["letter_id"]
    assert _checkout(client, letter_id=lid).status_code == 200


def test_order_links_user_pet_and_letter(client: ASGITestClient):
    """**핵심 계약**: 주문이 user_id + pet_id + soul_trace_letter_id 를 함께 든다."""
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()

    stored = _sync(physical_order.get, order["order_id"])
    assert stored.user_id == FREE_USER
    assert stored.pet_id == PET
    assert stored.soul_trace_letter_id == lid


def test_letter_is_required_for_both_products(client: ASGITestClient):
    """둘 다 편지를 인쇄한다 — 편지 없이 주문할 수 없다."""
    for product in ("LETTER", "MEMORY_BOX"):
        r = _checkout(client, product=product, letter_id=None)
        assert r.status_code == 409, product
        assert r.json()["detail"]["code"] == "LETTER_REQUIRED"


def test_shipping_fields_are_required(client: ASGITestClient):
    """배송지가 없으면 인쇄해도 보낼 곳이 없다."""
    lid = _link_letter(client).json()["letter_id"]
    for missing in ("recipient_name", "recipient_phone", "postal_code", "address_line1"):
        body = {"pet_id": PET, "product_type": "LETTER", "soul_trace_letter_id": lid, **SHIPPING}
        body[missing] = "  "
        r = client.post("/api/v1/orders/checkout", json=body, headers=_auth(FREE_USER))
        assert r.status_code == 400, missing
        assert r.json()["detail"]["code"] == "SHIPPING_INCOMPLETE"


def test_shipping_details_are_stored(client: ASGITestClient):
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    stored = _sync(physical_order.get, order["order_id"])
    assert stored.recipient_name == SHIPPING["recipient_name"]
    assert stored.recipient_phone == SHIPPING["recipient_phone"]
    assert stored.postal_code == SHIPPING["postal_code"]
    assert stored.address_line1 == SHIPPING["address_line1"]


def test_pet_id_is_required(client: ASGITestClient):
    """주문은 canonical petId 를 **가리킨다** — 없으면 만들지 않고 거절한다."""
    lid = _link_letter(client).json()["letter_id"]
    r = _checkout(client, letter_id=lid, pet_id="")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PET_REQUIRED"


def test_order_starts_unpaid_and_unfulfilled(client: ASGITestClient):
    """체크아웃은 결제가 아니다. 생산·배송도 아직 시작되지 않았다."""
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    stored = _sync(physical_order.get, order["order_id"])
    assert stored.payment_status == "pending"
    assert stored.production_status == "pending"
    assert stored.shipping_status == "pending"
    assert stored.tracking_number is None


def test_payment_only_changes_payment_status(client: ASGITestClient):
    """
    결제됐다고 인쇄가 시작되지 않는다 — Phase 13 의 몫이다.
    """
    order, _ = _buy(client)
    stored = _sync(physical_order.get, order["order_id"])
    assert stored.payment_status == "paid"
    assert stored.production_status == "pending"
    assert stored.shipping_status == "pending"


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_confirming_twice_does_not_charge_twice(client: ASGITestClient):
    order, first = _buy(client)
    assert first.json()["charged"] == 14_900

    for _ in range(3):
        again = _confirm(client, order["order_id"], amount=14_900).json()
        assert again["charged"] == 0
        assert again["already_paid"] is True

    assert len(_sync(physical_order.list_for_user, FREE_USER)) == 1


def test_repeat_confirm_never_calls_the_provider(client: ASGITestClient, monkeypatch):
    order, _ = _buy(client)

    async def _tracked(**_kw):
        raise AssertionError("이미 결제된 주문에 재승인을 시도했다")

    monkeypatch.setattr(toss_billing, "confirm_payment", _tracked)
    assert _confirm(client, order["order_id"], amount=14_900).json()["charged"] == 0


def test_amount_from_redirect_cannot_lower_the_price(client: ASGITestClient):
    """URL 의 amount 를 고쳐도 싸게 살 수 없다."""
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()

    r = _confirm(client, order["order_id"], amount=100)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ORDER_AMOUNT_MISMATCH"
    assert _sync(physical_order.get, order["order_id"]).payment_status == "pending"

    # 주문은 살아 있다 — 정당한 확인이 여전히 성공한다 (Phase 11 에서 배운 것).
    assert _confirm(client, order["order_id"], amount=14_900).status_code == 200


def test_confirm_asks_toss_with_the_stored_amount(client: ASGITestClient, monkeypatch):
    seen: list[int] = []
    real = toss_billing.confirm_payment

    async def _spy(**kw):
        seen.append(kw["amount"])
        return await real(**kw)

    monkeypatch.setattr(toss_billing, "confirm_payment", _spy)

    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    _confirm(client, order["order_id"])
    assert seen == [14_900]


def test_failed_payment_leaves_order_unpaid(client: ASGITestClient, monkeypatch):
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()

    async def _fail(**kw):
        return toss_billing.ConfirmResult(
            ok=False, payment_key=kw["payment_key"], order_id=kw["order_id"],
            amount=kw["amount"], raw={}, failure_code="PAY_PROCESS_CANCELED",
            failure_message="취소됨",
        )

    monkeypatch.setattr(toss_billing, "confirm_payment", _fail)

    r = _confirm(client, order["order_id"], amount=14_900)
    assert r.status_code == 402
    assert _sync(physical_order.get, order["order_id"]).payment_status == "failed"


# ── 사용자 간 보호 ───────────────────────────────────────────────────────────


def test_cannot_confirm_someone_elses_order(client: ASGITestClient):
    """**핵심 회귀**: order_id 는 리다이렉트 URL 에 노출된다."""
    lid = _link_letter(client, user=FREE_USER).json()["letter_id"]
    order = _checkout(client, user=FREE_USER, letter_id=lid).json()

    r = _confirm(client, order["order_id"], user=OTHER, amount=14_900)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ORDER_NOT_FOUND"
    assert _sync(physical_order.get, order["order_id"]).payment_status == "pending"


def test_cannot_order_with_someone_elses_letter(client: ASGITestClient):
    """
    **핵심 회귀**: 남의 편지를 자기 주문에 붙일 수 없다.

    붙일 수 있다면 남의 편지를 인쇄해 배송받게 된다 — 실물이라 되돌릴 수 없다.
    """
    victim_letter = _link_letter(client, user=FREE_USER).json()["letter_id"]

    r = _checkout(client, user=OTHER, letter_id=victim_letter)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "LETTER_NOT_FOUND"


def test_my_orders_only_shows_mine(client: ASGITestClient):
    _buy(client, "LETTER", user=FREE_USER)
    r = client.get("/api/v1/orders", headers=_auth(OTHER))
    assert r.json()["orders"] == []
    assert len(client.get("/api/v1/orders", headers=_auth(FREE_USER)).json()["orders"]) == 1


def test_order_endpoints_require_auth(client: ASGITestClient):
    assert client.post("/api/v1/orders/checkout", json={}).status_code == 401
    assert client.post(
        "/api/v1/orders/confirm", json={"payment_key": "p", "order_id": "o"}
    ).status_code == 401
    assert client.get("/api/v1/orders").status_code == 401
    assert client.post("/api/v1/orders/letter/claim", json={}).status_code == 401
    assert client.post("/api/v1/orders/letter/link-pet", json={}).status_code == 401


def test_customer_order_list_omits_shipping_details(client: ASGITestClient):
    """이미 아는 값이고, 응답에 개인정보를 담을수록 새어 나갈 표면이 넓어진다."""
    _buy(client)
    r = client.get("/api/v1/orders", headers=_auth(FREE_USER))
    assert SHIPPING["recipient_phone"] not in r.text
    assert SHIPPING["address_line1"] not in r.text


# ── 다른 축을 건드리지 않는다 ───────────────────────────────────────────────


def test_purchase_does_not_change_subscription_theme_or_credits(client: ASGITestClient):
    """**핵심 계약**: 실물 결제는 네 번째 축이다."""
    from backend.services import subscription_store_service as sub_store
    from backend.services import theme_entitlement, wallet_service

    subs_before = dict(sub_store._MOCK_SUBS)
    wallets_before = dict(wallet_service._MOCK_WALLETS)
    themes_before = dict(theme_entitlement._MOCK_ENTITLEMENTS)

    _buy(client, "MEMORY_BOX")

    assert sub_store._MOCK_SUBS == subs_before, "구독이 바뀌었다"
    assert wallet_service._MOCK_WALLETS == wallets_before, "크레딧이 바뀌었다"
    assert theme_entitlement._MOCK_ENTITLEMENTS == themes_before, "테마 소유권이 바뀌었다"


def test_buyer_is_still_not_a_member(client: ASGITestClient):
    """₩14,900 은 종이 값이지 멤버십 값이 아니다."""
    from backend.services import premium_entitlement

    _buy(client)
    ent = _sync(premium_entitlement.get_entitlement, FREE_USER)
    assert ent.entitled is False
    assert ent.status is None


def test_order_never_generates(client: ASGITestClient, monkeypatch):
    """
    **핵심 회귀**: 실물 주문이 펫 생성이나 프리미엄 행동을 부르지 않는다.
    """
    from backend.services import (
        credit_generation_service,
        generation_queue,
        premium_generation,
        premium_purchase,
        wallet_service,
    )

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨 — 실물 주문은 생성하지 않는다")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
        (credit_generation_service, "generate_with_credit"),
        (wallet_service, "deduct_credits"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _buy(client, "LETTER")
    _buy(client, "MEMORY_BOX", user=OTHER)
    client.get("/api/v1/orders/products")
    assert fired == []


def test_order_modules_are_independent():
    """구조로 고정 — 주문 모듈이 구독·테마·크레딧·생성 모듈을 import 하지 않는다."""
    import ast

    forbidden = {
        "premium_entitlement", "subscription_store_service", "subscription_webhook_service",
        "premium_generation", "generation_queue", "credit_generation_service",
        "wallet_service", "premium_purchase", "theme_entitlement", "theme_purchase",
        "luma_service", "wan_service", "video_generation",
    }
    for path in (
        "backend/services/physical_order.py",
        "backend/services/physical_product.py",
        "backend/services/physical_checkout.py",
        "backend/services/soul_trace_letter.py",
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
        assert not (imported & forbidden), f"{path}: {imported & forbidden}"


def test_letter_service_has_no_generation_capability():
    """
    Soul Trace 편지 모듈에 **문장을 만들어 내는 수단이 없다.**

    모델 클라이언트도 프롬프트 조립기도 템플릿도 없다 — 그것이 "중복 편지를
    만들지 않는다"의 실제 보장이다.

    ⚠️ AST 로 본다. 원시 문자열 검색은 "LLM 을 쓰지 않는다"고 **설명하는**
    독스트링에 걸려 엉뚱하게 실패한다(실제로 그렇게 한 번 실패했다).
    """
    import ast

    tree = ast.parse(open("backend/services/soul_trace_letter.py", encoding="utf-8").read())

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.rsplit(".", 1)[-1].lower())
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.name.lower())
            if node.module:
                imported.add(node.module.rsplit(".", 1)[-1].lower())
        elif isinstance(node, ast.Attribute):
            called.add(node.attr.lower())
        elif isinstance(node, ast.Name):
            called.add(node.id.lower())

    forbidden = {
        "openai", "anthropic", "llm", "luma_service", "luma_prompts",
        "prompt_factory", "person_prompting", "wan_service", "transformers",
    }
    assert not (imported & forbidden), f"편지 모듈이 {imported & forbidden} 를 import 한다"

    # 문장을 만들어 내는 이름의 함수가 정의돼 있지 않다.
    defined = {
        n.name.lower()
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for bad in ("generate_letter", "compose_letter", "write_letter", "render_letter"):
        assert bad not in defined, f"편지 모듈에 {bad} 가 있다"


# ── canonical 펫 · Shaker 공유 재사용 ───────────────────────────────────────


def test_order_reuses_existing_shaker_share(client: ASGITestClient, monkeypatch):
    """
    **핵심 회귀**: 주문마다 새 펫 경험(Shaker 공유)을 찍어 내지 않는다.

    기존 활성 공유가 있으면 그것을 가리킨다.
    """
    from backend.services import shaker_share

    class FakeShare:
        share_id = "shr_existing"
        revoked_at = None

    async def _list(**_kw):
        return [FakeShare()]

    monkeypatch.setattr(shaker_share, "list_shares", _list)

    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    assert _sync(physical_order.get, order["order_id"]).shaker_share_id == "shr_existing"


def test_order_does_not_mint_a_share_when_none_exists(client: ASGITestClient, monkeypatch):
    """
    공유가 없으면 **만들지 않는다** — null 로 두고 Phase 13 에서 운영이 붙인다.

    발급은 판매자/운영 권한이다(Phase 10 소유 모델).
    """
    from backend.services import shaker_share

    created: list[str] = []

    async def _create(**kw):
        created.append(kw.get("pet_id", ""))
        raise AssertionError("주문이 Shaker 공유를 새로 발급했다")

    monkeypatch.setattr(shaker_share, "create_share", _create)

    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    assert _sync(physical_order.get, order["order_id"]).shaker_share_id is None
    assert created == []


def test_two_orders_share_one_canonical_pet(client: ASGITestClient):
    """편지 주문과 박스 주문이 같은 펫을 가리킨다 — 펫이 갈라지지 않는다."""
    lid = _link_letter(client).json()["letter_id"]
    a = _checkout(client, product="LETTER", letter_id=lid).json()
    b = _checkout(client, product="MEMORY_BOX", letter_id=lid).json()

    assert a["order_id"] != b["order_id"]
    assert a["pet_id"] == b["pet_id"] == PET
    assert a["soul_trace_letter_id"] == b["soul_trace_letter_id"] == lid


# ── 운영 ──────────────────────────────────────────────────────────────────────


def test_ops_can_find_paid_orders(client: ASGITestClient):
    order, _ = _buy(client)

    r = client.get("/api/v1/orders/ops/search", headers=_auth(OPS))
    assert r.status_code == 200
    ids = [o["order_id"] for o in r.json()["orders"]]
    assert order["order_id"] in ids


def test_ops_search_by_customer_pet_and_order_id(client: ASGITestClient):
    order, _ = _buy(client)
    for q in (FREE_USER, PET, order["order_id"], SHIPPING["recipient_name"]):
        r = client.get("/api/v1/orders/ops/search", params={"query": q}, headers=_auth(OPS))
        assert [o["order_id"] for o in r.json()["orders"]] == [order["order_id"]], q


def test_ops_search_hides_unpaid_orders_by_default(client: ASGITestClient):
    """운영이 처리할 것은 **결제된** 주문이다."""
    lid = _link_letter(client).json()["letter_id"]
    _checkout(client, letter_id=lid)  # 결제하지 않는다

    r = client.get("/api/v1/orders/ops/search", headers=_auth(OPS))
    assert r.json()["orders"] == []

    everything = client.get(
        "/api/v1/orders/ops/search", params={"paid_only": "false"}, headers=_auth(OPS)
    )
    assert len(everything.json()["orders"]) == 1


def test_ops_sees_shipping_details(client: ASGITestClient):
    """운영은 배송지를 봐야 인쇄·발송할 수 있다."""
    _buy(client)
    o = client.get("/api/v1/orders/ops/search", headers=_auth(OPS)).json()["orders"][0]
    assert o["recipient_name"] == SHIPPING["recipient_name"]
    assert o["address_line1"] == SHIPPING["address_line1"]
    assert o["user_id"] == FREE_USER


def test_ops_search_requires_ops_allowlist(client: ASGITestClient):
    """Phase 10 과 **같은 allowlist** 를 쓴다 — 권한을 두 벌 만들지 않는다."""
    assert client.get("/api/v1/orders/ops/search").status_code == 401
    assert client.get("/api/v1/orders/ops/search", headers=_auth(FREE_USER)).status_code == 403


def test_ops_search_surfaces_fulfilment_fields(client: ASGITestClient):
    """Phase 13 이 채울 자리가 응답에 이미 있다."""
    _buy(client)
    o = client.get("/api/v1/orders/ops/search", headers=_auth(OPS)).json()["orders"][0]
    assert o["production_status"] == "pending"
    assert o["shipping_status"] == "pending"
    assert o["tracking_number"] is None
    assert o["soul_trace_letter_id"]
    assert o["pet_id"] == PET


# ── 가격 설정 ─────────────────────────────────────────────────────────────────


def test_price_is_overridable_without_code_change(monkeypatch):
    monkeypatch.setenv("PRODUCT_PRICE_LETTER_KRW", "16900")
    assert physical_product.price_krw("LETTER") == 16_900


def test_bad_price_config_falls_back_to_confirmed_price(monkeypatch):
    """설정 오타가 0원 배송이 되면 안 된다."""
    for bad in ("abc", "-1", "0", " "):
        monkeypatch.setenv("PRODUCT_PRICE_LETTER_KRW", bad)
        assert physical_product.price_krw("LETTER") == 14_900


# ── 재조정: 브라우저가 돌아오지 못한 결제 ───────────────────────────────────
#
# 실패 양상: 결제창 승인 **직후** 브라우저가 닫히면 successUrl 로 돌아오지 못한다.
# Toss 에는 승인된 결제가 있는데 우리 주문은 pending — **돈은 받고 물건은 만들지
# 않는 상태**다. 실물이라 고객은 결제 문자만 받고 아무것도 받지 못한다.


def _approved_at_toss(monkeypatch, amount: int = 14_900, payment_key: str = "pk_late"):
    """Toss 에는 승인된 결제가 있는 상태를 만든다."""
    async def _lookup(order_id: str):
        return toss_billing.ConfirmResult(
            ok=True, payment_key=payment_key, order_id=order_id, amount=amount, raw={},
        )

    monkeypatch.setattr(toss_billing, "lookup_payment_by_order", _lookup)


def test_interrupted_return_is_reconciled(client: ASGITestClient, monkeypatch):
    """
    **핵심 회귀**: 승인됐지만 confirm 이 오지 않은 주문을 재조정이 PAID 로 만든다.
    """
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    # confirm 을 부르지 않는다 — 브라우저가 닫힌 상황이다.
    assert _sync(physical_order.get, order["order_id"]).payment_status == "pending"

    _approved_at_toss(monkeypatch)
    r = client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER))
    assert r.status_code == 200
    assert r.json()["confirmed_order_ids"] == [order["order_id"]]

    stored = _sync(physical_order.get, order["order_id"])
    assert stored.payment_status == "paid"
    assert stored.payment_key == "pk_late"
    # 생산·배송은 여전히 손대지 않는다 — 재조정은 결제만 정리한다.
    assert stored.production_status == "pending"
    assert stored.shipping_status == "pending"


def test_reconcile_is_idempotent(client: ASGITestClient, monkeypatch):
    """두 번 돌려도 두 번 확정되지 않는다 — 이미 PAID 면 대상이 아니다."""
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    _approved_at_toss(monkeypatch)

    first = client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER)).json()
    assert first["confirmed_order_ids"] == [order["order_id"]]

    second = client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER)).json()
    assert second["confirmed_order_ids"] == []


def test_reconcile_then_confirm_does_not_double_charge(client: ASGITestClient, monkeypatch):
    """
    재조정으로 확정된 뒤 뒤늦게 confirm 이 도착해도(느린 탭) 재승인하지 않는다.
    """
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    _approved_at_toss(monkeypatch)
    client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER))

    async def _boom(**_kw):
        raise AssertionError("이미 확정된 주문에 재승인을 시도했다")

    monkeypatch.setattr(toss_billing, "confirm_payment", _boom)
    late = _confirm(client, order["order_id"], amount=14_900).json()
    assert late["charged"] == 0
    assert late["already_paid"] is True


def test_reconcile_ignores_unpaid_orders(client: ASGITestClient, monkeypatch):
    """
    결제창을 열지 않았거나 승인되지 않은 주문은 **건드리지 않는다.**

    실패로 만들지도 않는다 — 사용자가 결제창을 다시 열어 끝낼 수 있어야 한다.
    """
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()

    async def _none(_order_id: str):
        return None  # 결제한 적 없음

    monkeypatch.setattr(toss_billing, "lookup_payment_by_order", _none)

    assert client.post(
        "/api/v1/orders/reconcile", headers=_auth(FREE_USER)
    ).json()["confirmed_order_ids"] == []
    assert _sync(physical_order.get, order["order_id"]).payment_status == "pending"


def test_reconcile_refuses_on_amount_mismatch(client: ASGITestClient, monkeypatch):
    """
    승인 금액이 주문과 다르면 **자동으로 확정하지 않는다.**

    자동 경로가 불일치를 덮으면 사람이 알아차릴 기회 없이 잘못된 주문이 생산으로
    넘어간다 — 실물이라 되돌릴 수 없다.
    """
    lid = _link_letter(client).json()["letter_id"]
    order = _checkout(client, letter_id=lid).json()
    _approved_at_toss(monkeypatch, amount=100)

    assert client.post(
        "/api/v1/orders/reconcile", headers=_auth(FREE_USER)
    ).json()["confirmed_order_ids"] == []
    assert _sync(physical_order.get, order["order_id"]).payment_status == "pending"


def test_reconcile_only_touches_my_orders(client: ASGITestClient, monkeypatch):
    """**핵심 회귀**: 재조정이 남의 주문을 확정하지 않는다."""
    lid = _link_letter(client, user=FREE_USER).json()["letter_id"]
    mine = _checkout(client, user=FREE_USER, letter_id=lid).json()
    _approved_at_toss(monkeypatch)

    r = client.post("/api/v1/orders/reconcile", headers=_auth(OTHER))
    assert r.json()["confirmed_order_ids"] == []
    assert _sync(physical_order.get, mine["order_id"]).payment_status == "pending"


def test_reconcile_requires_auth(client: ASGITestClient):
    assert client.post("/api/v1/orders/reconcile").status_code == 401


def test_reconcile_survives_lookup_failure(client: ASGITestClient, monkeypatch):
    """조회가 죽어도 스윕이 통째로 멈추지 않는다."""
    lid = _link_letter(client).json()["letter_id"]
    _checkout(client, letter_id=lid)

    async def _boom(_order_id: str):
        raise RuntimeError("Toss 장애")

    monkeypatch.setattr(toss_billing, "lookup_payment_by_order", _boom)
    r = client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER))
    assert r.status_code == 200
    assert r.json()["confirmed_order_ids"] == []


def test_reconcile_never_generates_or_charges(client: ASGITestClient, monkeypatch):
    """재조정은 **이미 일어난 승인을 반영**할 뿐 새 결제를 만들지 않는다."""
    from backend.services import generation_queue, premium_generation, wallet_service

    lid = _link_letter(client).json()["letter_id"]
    _checkout(client, letter_id=lid)
    _approved_at_toss(monkeypatch)

    async def _boom(*_a, **_k):
        raise AssertionError("재조정이 결제/생성을 호출했다")

    monkeypatch.setattr(toss_billing, "confirm_payment", _boom)
    monkeypatch.setattr(toss_billing, "charge", _boom)
    for mod, attr in (
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (wallet_service, "deduct_credits"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _boom)

    assert len(
        client.post("/api/v1/orders/reconcile", headers=_auth(FREE_USER))
        .json()["confirmed_order_ids"]
    ) == 1


# ── 크론 스윕 ─────────────────────────────────────────────────────────────────


def test_cron_sweep_confirms_across_users(client: ASGITestClient, monkeypatch):
    """
    사용자가 **영영 돌아오지 않는** 경우의 마지막 보루.

    앱 재방문에만 기대면 그 사람은 결제만 하고 아무것도 받지 못한다.
    """
    monkeypatch.setenv("BILLING_CRON_SECRET", "s3cret")

    a_letter = _link_letter(client, user=FREE_USER).json()["letter_id"]
    a = _checkout(client, user=FREE_USER, letter_id=a_letter).json()
    b_letter = _link_letter(client, user=OTHER, src="st_letter_b").json()["letter_id"]
    b = _checkout(client, user=OTHER, letter_id=b_letter).json()
    _approved_at_toss(monkeypatch)

    r = client.post("/api/v1/orders/reconcile-due", headers={"X-Cron-Secret": "s3cret"})
    assert r.status_code == 200
    assert set(r.json()["confirmed_order_ids"]) == {a["order_id"], b["order_id"]}


def test_cron_sweep_rejects_bad_secret(client: ASGITestClient, monkeypatch):
    monkeypatch.setenv("BILLING_CRON_SECRET", "s3cret")
    assert client.post("/api/v1/orders/reconcile-due").status_code == 403
    assert client.post(
        "/api/v1/orders/reconcile-due", headers={"X-Cron-Secret": "wrong"}
    ).status_code == 403


def test_cron_sweep_is_closed_when_unconfigured(client: ASGITestClient, monkeypatch):
    """시크릿 미설정이면 열리는 게 아니라 닫힌다."""
    monkeypatch.delenv("BILLING_CRON_SECRET", raising=False)
    r = client.post("/api/v1/orders/reconcile-due", headers={"X-Cron-Secret": "anything"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "CRON_NOT_CONFIGURED"


def test_mock_lookup_never_invents_an_approval():
    """
    ⚠️ 목업이 "승인됨"을 지어내면 재조정 테스트가 실재하지 않는 결제를 확정하게
    되고, 그 습관이 프로덕션 버그가 된다. 목업은 항상 None 이어야 한다.
    """
    import os as _os

    _os.environ["TOSS_MOCK"] = "1"
    assert _sync(toss_billing.lookup_payment_by_order, "eb_order_whatever") is None
