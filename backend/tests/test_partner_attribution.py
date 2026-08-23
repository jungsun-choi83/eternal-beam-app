"""
파트너 귀속 (Phase 15) — 동물병원 / 장례식장.

핵심 계약:
  * 귀속은 **서버가** 정한다. 브라우저는 코드만 넘기고 partner_id 는 못 넘긴다.
  * 직접 유입(파트너 없음)은 기존 흐름 그대로다 — 전부 nullable.
  * 주문은 **주문 시점 스냅샷**을 든다 — 나중에 편지가 바뀌어도 흔들리지 않는다.
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
    production_package,
    shaker_qr_artifact,
    shaker_share,
    soul_trace_import,
    soul_trace_letter,
)

from .conftest import ASGITestClient

USER = "buyer@example.com"
PET = "pet_partner1"
BODY = "엄마, 나 보리야. 현관에서 기다리던 시간이 제일 좋았어."
HANDOFF = "h" * 43

HOSPITAL = dict(partner_id="ptn_hosp_001", partner_type="HOSPITAL", partner_name="서울동물병원")
FUNERAL = dict(partner_id="ptn_fnrl_002", partner_type="FUNERAL", partner_name="무지개장례식장")

TRACE_DIRECT = "11111111-1111-1111-1111-111111111111"
TRACE_HOSP = "22222222-2222-2222-2222-222222222222"
TRACE_FNRL = "33333333-3333-3333-3333-333333333333"

SHIPPING = {
    "recipient_name": "김보호", "recipient_phone": "010-1234-5678",
    "postal_code": "06236", "address_line1": "서울시 강남구 테헤란로 1",
}

#: Soul Trace 가 traceId 별로 돌려줄 귀속. **서버 대 서버 응답이 정본이다.**
ATTRIBUTION = {TRACE_DIRECT: {}, TRACE_HOSP: HOSPITAL, TRACE_FNRL: FUNERAL}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://device.eternalbeam.com")
    for m in (physical_order, soul_trace_letter, pet_registry,
              production_package, shaker_share, shaker_qr_artifact):
        m.__reset_for_tests()

    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        a = ATTRIBUTION.get(trace_id, {})
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=BODY, pet_name="보리", **a
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)
    yield
    for m in (physical_order, soul_trace_letter, pet_registry,
              production_package, shaker_share, shaker_qr_artifact):
        m.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(orders_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *a, **k):
    return anyio.run(functools.partial(afn, *a, **k))


def _auth(u: str = USER):
    return {"Authorization": f"Bearer test:{u}"}


def _register_pet(pet: str = PET, user: str = USER):
    _sync(pet_registry.register, user_id=user, pet_id=pet,
          content_id=pet.removeprefix("pet_"), breathing_bucket="user-assets",
          breathing_object_path=f"{user}/{pet}/idle_loop.mp4",
          source=pet_registry.SOURCE_OPS, verify=False)


def _claim(client, trace: str, user: str = USER, **extra) -> str:
    r = client.post("/api/v1/orders/letter/claim",
                    json={"trace_id": trace, "handoff": HANDOFF, **extra},
                    headers=_auth(user))
    assert r.status_code == 200, r.text
    return r.json()["letter_id"]


def _order(client, trace: str, pet: str = PET, user: str = USER) -> str:
    _register_pet(pet, user)
    lid = _claim(client, trace, user)
    client.post("/api/v1/orders/letter/link-pet",
                json={"letter_id": lid, "pet_id": pet}, headers=_auth(user))
    o = client.post("/api/v1/orders/checkout",
                    json={"pet_id": pet, "product_type": "LETTER",
                          "soul_trace_letter_id": lid, **SHIPPING},
                    headers=_auth(user)).json()
    return o["order_id"]


# ── A. 직접 유입은 그대로다 ──────────────────────────────────────────────────


def test_direct_entry_has_no_partner_and_still_works(client: ASGITestClient):
    """**핵심 계약**: 파트너 없이 들어온 고객의 흐름은 조금도 달라지지 않는다."""
    oid = _order(client, TRACE_DIRECT)
    o = _sync(physical_order.get, oid)
    assert o.partner_id is None
    assert o.partner_type is None
    assert o.partner_name is None
    assert o.amount == 14_900 and o.payment_status == physical_order.PAYMENT_PENDING


# ── B/C. 병원 · 장례식장 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("trace,expected", [(TRACE_HOSP, HOSPITAL), (TRACE_FNRL, FUNERAL)])
def test_partner_flows_letter_to_order(client: ASGITestClient, trace, expected):
    """D·E·F: 핸드오프 → 편지 → 주문까지 같은 partner_id 가 유지된다."""
    lid = _claim(client, trace)
    letter = _sync(soul_trace_letter.get_letter, lid)
    assert letter.partner_id == expected["partner_id"]
    assert letter.partner_type == expected["partner_type"]
    assert letter.partner_name == expected["partner_name"]

    oid = _order(client, trace)
    o = _sync(physical_order.get, oid)
    assert o.partner_id == expected["partner_id"]
    assert o.partner_type == expected["partner_type"]
    assert o.partner_name == expected["partner_name"]


# ── H. 변조 방어 ─────────────────────────────────────────────────────────────


def test_browser_cannot_supply_or_override_partner(client: ASGITestClient):
    """
    **핵심 보안 계약**: 브라우저가 귀속을 정할 수 없다.

    귀속은 Soul Trace 가 서버에서 코드로 확정해 S2S 로 넘긴 값만 쓴다.
    요청 바디에 무엇을 넣든 저장되는 값은 바뀌지 않는다.
    """
    # 직접 유입 편지에 병원 귀속을 끼워 넣으려는 시도
    lid = _claim(client, TRACE_DIRECT,
                 partner_id="ptn_hosp_001", partner_type="HOSPITAL",
                 partner_name="가짜병원")
    letter = _sync(soul_trace_letter.get_letter, lid)
    assert letter.partner_id is None, "브라우저가 귀속을 만들어 냈다"
    assert letter.partner_name is None

    # 이미 장례식장에 귀속된 편지를 병원으로 바꾸려는 시도
    lid2 = _claim(client, TRACE_FNRL, partner_id="ptn_hosp_001",
                  partner_name="가짜병원")
    letter2 = _sync(soul_trace_letter.get_letter, lid2)
    assert letter2.partner_id == FUNERAL["partner_id"], "브라우저가 귀속을 바꿨다"


def test_checkout_ignores_partner_in_request_body(client: ASGITestClient):
    """주문 귀속은 **편지에서 서버가 복사한다** — 요청 바디를 보지 않는다."""
    _register_pet()
    lid = _claim(client, TRACE_HOSP)
    client.post("/api/v1/orders/letter/link-pet",
                json={"letter_id": lid, "pet_id": PET}, headers=_auth())
    o = client.post("/api/v1/orders/checkout",
                    json={"pet_id": PET, "product_type": "LETTER",
                          "soul_trace_letter_id": lid,
                          "partner_id": "ptn_fnrl_002",
                          "partner_name": "가짜장례식장", **SHIPPING},
                    headers=_auth()).json()
    stored = _sync(physical_order.get, o["order_id"])
    assert stored.partner_id == HOSPITAL["partner_id"]
    assert stored.partner_name == HOSPITAL["partner_name"]


def test_unknown_partner_type_is_dropped_not_stored():
    """모르는 유형을 그대로 저장하면 운영 필터가 조용히 어긋난다."""
    import backend.services.soul_trace_import as imp

    assert imp.SourceLetter(letter_id="x", letter_body="y", pet_name="z").partner_id is None


# ── G. 운영 필터 ─────────────────────────────────────────────────────────────


def test_ops_can_filter_by_partner(client: ASGITestClient, monkeypatch):
    """운영은 전체 / 유형별 / 특정 파트너로 좁힐 수 있어야 한다."""
    for trace, pet in ((TRACE_DIRECT, "pet_a"), (TRACE_HOSP, "pet_b"), (TRACE_FNRL, "pet_c")):
        oid = _order(client, trace, pet=pet)
        _sync(physical_order.mark_paid, order_id=oid, payment_key="pk", amount=14_900)

    all_rows = _sync(physical_order.search)
    assert len(all_rows) == 3

    hosp = _sync(physical_order.search, partner_type="HOSPITAL")
    assert [o.partner_id for o in hosp] == [HOSPITAL["partner_id"]]

    fnrl = _sync(physical_order.search, partner_type="FUNERAL")
    assert [o.partner_id for o in fnrl] == [FUNERAL["partner_id"]]

    exact = _sync(physical_order.search, partner_id=HOSPITAL["partner_id"])
    assert len(exact) == 1 and exact[0].partner_name == HOSPITAL["partner_name"]

    # 부분 일치로 새지 않는다 — 정산 숫자가 부풀면 안 된다.
    assert _sync(physical_order.search, partner_id="ptn_hosp") == []
