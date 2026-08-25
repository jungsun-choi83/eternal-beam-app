"""
파트너 코드 · 갈래 · 정산 비율 (Phase 16).

Phase 15 가 "어느 파트너인가"를 세웠다면 여기서 검증하는 것은 "정산을 계산할 수
있는가"다. 계약은 셋이다:

  * 코드·갈래·비율이 **핸드오프 → 편지 → 주문**까지 끊기지 않고 간다
  * 주문은 **주문 시점 스냅샷**을 든다 — 나중에 비율이 바뀌어도 흔들리지 않는다
  * 갈래는 Soul Trace 의 LetterMode 와 **같은 낱말**이다 (두 벌을 만들지 않는다)
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
OPS_USER = "ops@example.com"
PET = "pet_track1"
BODY = "엄마, 나 보리야. 현관에서 기다리던 시간이 제일 좋았어."
HANDOFF = "h" * 43

TRACE_DIRECT = "11111111-1111-1111-1111-111111111111"
TRACE_MEMORIAL = "44444444-4444-4444-4444-444444444444"
TRACE_LIVING = "55555555-5555-5555-5555-555555555555"

MEMORIAL = dict(
    partner_id="ptn_hosp_001",
    partner_type="HOSPITAL",
    partner_name="silim hospital",
    partner_code="AbCdEf1234567890",
    partner_track="memorial",
    partner_share_rate=0.15,
)
LIVING = dict(
    partner_id="ptn_hosp_001",
    partner_type="HOSPITAL",
    partner_name="silim hospital",
    partner_code="ZyXwVu0987654321",
    partner_track="living",
    partner_share_rate=0.15,
)

SHIPPING = {
    "recipient_name": "김보호",
    "recipient_phone": "010-1234-5678",
    "postal_code": "06236",
    "address_line1": "서울시 강남구 테헤란로 1",
}

ATTRIBUTION = {TRACE_DIRECT: {}, TRACE_MEMORIAL: MEMORIAL, TRACE_LIVING: LIVING}

#: 진짜 파서. autouse 픽스처가 fetch_source_letter 를 대역으로 갈아 끼우므로,
#: **값 위생을 검사하려면 갈아 끼우기 전의 함수**를 붙잡아 둬야 한다.
_REAL_FETCH = soul_trace_import.fetch_source_letter

_MODULES = (
    physical_order,
    soul_trace_letter,
    pet_registry,
    production_package,
    shaker_share,
    shaker_qr_artifact,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://device.eternalbeam.com")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS_USER)
    for m in _MODULES:
        m.__reset_for_tests()

    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        a = ATTRIBUTION.get(trace_id, {})
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=BODY, pet_name="보리", **a
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)
    yield
    for m in _MODULES:
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


def _order(client, trace: str, pet: str = PET, user: str = USER) -> str:
    _sync(
        pet_registry.register,
        user_id=user,
        pet_id=pet,
        content_id=pet.removeprefix("pet_"),
        breathing_bucket="user-assets",
        breathing_object_path=f"{user}/{pet}/idle_loop.mp4",
        source=pet_registry.SOURCE_OPS,
        verify=False,
    )
    r = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": trace, "handoff": HANDOFF},
        headers=_auth(user),
    )
    assert r.status_code == 200, r.text
    lid = r.json()["letter_id"]
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": pet},
        headers=_auth(user),
    )
    o = client.post(
        "/api/v1/orders/checkout",
        json={
            "pet_id": pet,
            "product_type": "LETTER",
            "soul_trace_letter_id": lid,
            **SHIPPING,
        },
        headers=_auth(user),
    ).json()
    return o["order_id"]


# ── 전파 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("trace,expected", [(TRACE_MEMORIAL, MEMORIAL), (TRACE_LIVING, LIVING)])
def test_code_track_rate_flow_letter_to_order(client: ASGITestClient, trace, expected):
    """핸드오프 → 편지 → 주문까지 여섯 값이 모두 살아 간다."""
    oid = _order(client, trace)
    o = _sync(physical_order.get, oid)

    assert o.partner_id == expected["partner_id"]
    assert o.partner_type == expected["partner_type"]
    assert o.partner_name == expected["partner_name"]
    assert o.partner_code == expected["partner_code"]
    assert o.partner_track == expected["partner_track"]
    assert o.partner_share_rate == expected["partner_share_rate"]


def test_two_codes_same_partner_are_distinguishable(client: ASGITestClient):
    """
    한 파트너가 지점·갈래별로 여러 코드를 갖는 것이 설계 전제다.

    partner_id 만 남기면 그 구분이 통계에서 사라진다 — 어느 QR 이 실제로
    사람을 데려왔는지 알 수 없게 된다.
    """
    memorial = _sync(physical_order.get, _order(client, TRACE_MEMORIAL, pet="pet_a"))
    living = _sync(physical_order.get, _order(client, TRACE_LIVING, pet="pet_b"))

    assert memorial.partner_id == living.partner_id, "같은 파트너여야 한다"
    assert memorial.partner_code != living.partner_code, "코드가 구분되지 않는다"
    assert (memorial.partner_track, living.partner_track) == ("memorial", "living")


# ── 스냅샷 불변 ──────────────────────────────────────────────────────────────


def test_order_snapshot_survives_later_rate_change(client: ASGITestClient):
    """
    **핵심 정산 계약**: 비율이 바뀌어도 과거 주문은 움직이지 않는다.

    파트너의 현재 비율로 과거 주문을 계산하면 이미 정산이 끝난 달의 숫자가
    조용히 달라진다. 그건 장부가 아니다.
    """
    oid = _order(client, TRACE_MEMORIAL)
    before = _sync(physical_order.get, oid)
    assert before.partner_share_rate == 0.15

    # 계약이 바뀌었다 — 편지 쪽 귀속을 통째로 갱신한다(이름·비율·코드 전부).
    _sync(
        soul_trace_letter.link_letter,
        user_id=USER,
        source_letter_id=TRACE_MEMORIAL,
        letter_body=BODY,
        partner_id="ptn_hosp_001",
        partner_type="HOSPITAL",
        partner_name="silim animal medical center",
        partner_code="NEWCODE1234567890",
        partner_track="living",
        partner_share_rate=0.30,
    )

    after = _sync(physical_order.get, oid)
    assert after.partner_share_rate == 0.15, "과거 주문의 비율이 재계산됐다"
    assert after.partner_name == "silim hospital", "과거 주문의 이름이 바뀌었다"
    assert after.partner_code == MEMORIAL["partner_code"], "과거 주문의 코드가 바뀌었다"
    assert after.partner_track == "memorial", "과거 주문의 갈래가 바뀌었다"


# ── 직접 유입 ────────────────────────────────────────────────────────────────


def test_direct_entry_still_null(client: ASGITestClient):
    """파트너 없이 들어온 고객의 흐름은 조금도 달라지지 않는다."""
    o = _sync(physical_order.get, _order(client, TRACE_DIRECT))
    assert o.partner_id is None
    assert o.partner_code is None
    assert o.partner_track is None
    assert o.partner_share_rate is None
    assert o.amount == 14_900


# ── 값 위생 ──────────────────────────────────────────────────────────────────


def test_unknown_track_and_out_of_range_rate_are_dropped(monkeypatch):
    """
    모르는 갈래·범위 밖 비율은 **버린다 — 귀속은 살린다.**

    틀린 비율로 정산하느니 비어 있는 편이 낫다. 빈 값은 눈에 띄지만 15.0 은
    그럴듯해 보이는 채로 매출의 1500% 가 된다.
    """
    import json as _json

    class _Res:
        status_code = 200
        text = ""

        def json(self):
            return {
                "letterId": TRACE_MEMORIAL,
                "letterBody": BODY,
                "petName": "보리",
                "partnerId": "ptn_hosp_001",
                "partnerType": "HOSPITAL",
                "partnerName": "silim hospital",
                "partnerCode": "AbCdEf1234567890",
                "partnerTrack": "PRE_LOSS",   # 우리가 모르는 어휘
                "partnerShareRate": 15,        # 15% 를 15 로 적은 실수
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Res()

    import httpx

    monkeypatch.setenv("SOUL_TRACE_SERVICE_TOKEN", "t" * 32)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    src = _sync(
        _REAL_FETCH,
        trace_id=TRACE_MEMORIAL,
        handoff=HANDOFF,
        consumed_by=USER,
    )
    assert src.partner_id == "ptn_hosp_001", "귀속까지 함께 버렸다"
    assert src.partner_code == "AbCdEf1234567890"
    assert src.partner_track is None, "모르는 갈래를 그대로 저장했다"
    assert src.partner_share_rate is None, "범위 밖 비율을 그대로 저장했다"


def test_track_vocabulary_matches_soul_trace():
    """
    갈래는 Soul Trace 의 LetterMode 와 **같은 낱말**이어야 한다.

    이 검사가 실패하면 누군가 갈래 어휘를 늘린 것이고, 그 순간 프롬프트를
    가르는 개념이 두 개가 된다 — 요구사항이 금지한 바로 그것이다.
    """
    from backend.services import partner_admin

    assert partner_admin.TRACKS == ("living", "memorial")


# ── 운영 목록 응답 완전성 (Phase 23) ─────────────────────────────────────────


def _paid_order(client, trace: str, pet: str = PET) -> str:
    """결제까지 마친 주문 — 운영 목록은 기본이 paid_only 다."""
    oid = _order(client, trace, pet=pet)
    o = _sync(physical_order.get, oid)
    client.post(
        "/api/v1/orders/confirm",
        json={"payment_key": "pk", "order_id": oid, "amount": o.amount},
        headers=_auth(),
    )
    return oid


def test_ops_search_row_carries_created_at_partner_and_attention(client: ASGITestClient):
    """
    목록 한 번으로 대시보드가 필요한 것을 다 알 수 있어야 한다 —
    주문마다 상세를 부르지 않기 위해서다.
    """
    oid = _paid_order(client, TRACE_MEMORIAL)
    r = client.get("/api/v1/orders/ops/search", headers=_auth(OPS_USER))
    assert r.status_code == 200, r.text
    row = next(o for o in r.json()["orders"] if o["order_id"] == oid)

    # 최근순 정렬의 근거. 주문번호로 대신하지 않는다.
    assert row["created_at"], "created_at 이 없으면 최근순을 만들 수 없다"

    # 파트너 최소 필드.
    assert row["partner_id"] == MEMORIAL["partner_id"]
    assert row["partner_type"] == "HOSPITAL"
    assert row["partner_name"] == MEMORIAL["partner_name"]
    assert row["partner_track"] == "memorial"
    # 주문 시점 스냅샷 — 파트너의 현재 비율이 아니다.
    assert row["partner_share_rate"] == 0.15

    # 처리 필요 여부가 목록에 실린다.
    assert row["needs_attention"] is True   # 결제됐지만 아직 생산 준비 전
    assert row["attention_code"] == "NOT_PREPARED"
    assert row["attention_reason"]


def test_ops_search_does_not_duplicate_full_pending_files(client: ASGITestClient):
    """목록에는 불리언과 사유만 싣는다 — pendingFiles 전체를 복제하지 않는다."""
    _paid_order(client, TRACE_DIRECT)
    row = client.get("/api/v1/orders/ops/search", headers=_auth(OPS_USER)).json()["orders"][0]
    assert "pending_files" not in row
    assert "files" not in row


def test_ops_search_partner_filters_are_exact(client: ASGITestClient):
    """부분 일치로 두면 한 단어가 여러 병원을 긁어 와 정산이 부풀어 오른다."""
    _paid_order(client, TRACE_MEMORIAL, pet="pet_p")
    _paid_order(client, TRACE_DIRECT, pet="pet_d")

    hit = client.get(
        f"/api/v1/orders/ops/search?partner_id={MEMORIAL['partner_id']}",
        headers=_auth(OPS_USER),
    ).json()["orders"]
    assert hit and all(o["partner_id"] == MEMORIAL["partner_id"] for o in hit)

    miss = client.get(
        "/api/v1/orders/ops/search?partner_id=ptn_nope", headers=_auth(OPS_USER)
    ).json()["orders"]
    assert miss == []

    by_type = client.get(
        "/api/v1/orders/ops/search?partner_type=HOSPITAL", headers=_auth(OPS_USER)
    ).json()["orders"]
    assert by_type and all(o["partner_type"] == "HOSPITAL" for o in by_type)


def test_share_rate_in_list_is_the_order_time_snapshot(client: ASGITestClient):
    """목록의 비율도 주문 시점 값이다 — 파트너의 현재 비율로 바뀌지 않는다."""
    oid = _paid_order(client, TRACE_MEMORIAL)
    _sync(
        soul_trace_letter.link_letter,
        user_id=USER,
        source_letter_id=TRACE_MEMORIAL,
        letter_body=BODY,
        partner_id=MEMORIAL["partner_id"],
        partner_type="HOSPITAL",
        partner_name="renamed",
        partner_share_rate=0.30,
    )
    row = next(
        o
        for o in client.get("/api/v1/orders/ops/search", headers=_auth(OPS_USER)).json()["orders"]
        if o["order_id"] == oid
    )
    assert row["partner_share_rate"] == 0.15, "목록이 현재 비율로 재계산됐다"
    assert row["partner_name"] == MEMORIAL["partner_name"]


def test_list_does_not_fetch_detail_per_order(client: ASGITestClient, monkeypatch):
    """
    **핵심**: 목록이 주문마다 패키지를 개별 조회하면 안 된다. 일괄 조회 한 번이다.
    """
    calls = {"single": 0, "bulk": 0}
    real_single = production_package.get_package
    real_bulk = production_package.get_packages

    async def _single(order_id):
        calls["single"] += 1
        return await real_single(order_id)

    async def _bulk(order_ids):
        calls["bulk"] += 1
        return await real_bulk(order_ids)

    monkeypatch.setattr(production_package, "get_package", _single)
    monkeypatch.setattr(production_package, "get_packages", _bulk)

    for i in range(3):
        _paid_order(client, TRACE_DIRECT, pet=f"pet_n{i}")
    calls["single"] = 0
    calls["bulk"] = 0

    client.get("/api/v1/orders/ops/search", headers=_auth(OPS_USER))
    assert calls["bulk"] == 1, "일괄 조회가 한 번이 아니다"
    assert calls["single"] == 0, "목록이 주문마다 상세를 부른다"
