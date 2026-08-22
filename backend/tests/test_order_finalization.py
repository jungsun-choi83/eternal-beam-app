"""
결제 완료 → Shaker 공유 → QR → 생산 패키지 → READY.

이 파일이 지키는 계약:
  * 결제가 확정되면 **자동으로** 생산 준비까지 간다 (예전에는 수동이었다).
  * 중복 결제 콜백이 공유·패키지를 **복제하지 않는다.**
  * 인쇄용 QR 은 웹앱 도메인을 가리키고 만료가 없다.
  * 생산 준비가 실패해도 **주문은 PAID 로 남는다** — 돈을 잃지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import orders_v1
from backend.services import (
    order_finalization,
    shaker_ops,
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
PET = "pet_final123"
CONTENT = "final123"
BUCKET = "user-assets"
OBJ = f"{USER}/{CONTENT}/idle_loop.mp4"
TRACE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HANDOFF = "h" * 43
BODY = "엄마, 나 보리야. 현관에서 기다리던 그 시간이 제일 좋았어. 이제 여기서 편안해."

SHIPPING = {
    "recipient_name": "김보호",
    "recipient_phone": "010-1234-5678",
    "postal_code": "06236",
    "address_line1": "서울시 강남구 테헤란로 1",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    # 인쇄용 QR 은 웹앱 도메인을 가리켜야 한다.
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://device.eternalbeam.com")
    for m in (physical_order, soul_trace_letter, pet_registry,
              production_package, shaker_share, shaker_qr_artifact):
        m.__reset_for_tests()

    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=BODY, pet_name="보리"
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)

    # 스토리지는 이 테스트의 대상이 아니다. BREATHING 위치 탐색만 대역으로 둔다 —
    # 여기서 보려는 것은 "결제가 생산 준비까지 이어지는가" 이지 객체 저장이 아니다.
    async def _locate(_user: str, _pet: str):
        return shaker_ops.BreathingLocation(bucket=BUCKET, object_path=OBJ), \
            f"https://storage.example.com/{BUCKET}/{OBJ}?token=x"

    monkeypatch.setattr(shaker_ops, "locate_breathing", _locate)
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


def _auth(u: str = USER) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _register_pet():
    _sync(
        pet_registry.register,
        user_id=USER, pet_id=PET, content_id=CONTENT,
        breathing_bucket=BUCKET, breathing_object_path=OBJ,
        source=pet_registry.SOURCE_OPS, verify=False,
    )


def _paid_order(client: ASGITestClient, product: str = "LETTER") -> dict:
    """편지 클레임 → 펫 연결 → 체크아웃 → 결제 확인."""
    _register_pet()
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": TRACE, "handoff": HANDOFF}, headers=_auth(),
    ).json()["letter_id"]
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET}, headers=_auth(),
    )
    order = client.post(
        "/api/v1/orders/checkout",
        json={"pet_id": PET, "product_type": product,
              "soul_trace_letter_id": lid, **SHIPPING},
        headers=_auth(),
    ).json()
    client.post(
        "/api/v1/orders/confirm",
        json={"payment_key": "pk_1", "order_id": order["order_id"],
              "amount": order["amount"]},
        headers=_auth(),
    )
    return order


# ── 핵심: 결제가 생산 준비까지 이어진다 ──────────────────────────────────────


def test_payment_finalizes_the_whole_chain(client: ASGITestClient):
    """
    **핵심 계약**: 결제 확인 하나로 공유·QR·패키지·READY 까지 간다.

    예전에는 여기서 멈췄다 — PAID 인데 생산 준비는 아무도 하지 않았다.
    """
    order = _paid_order(client)
    oid = order["order_id"]

    stored = _sync(physical_order.get, oid)
    assert stored.payment_status == physical_order.PAYMENT_PAID
    assert stored.shaker_share_id, "결제 후에도 shaker_share_id 가 비어 있다"
    assert stored.production_status == physical_order.PRODUCTION_READY

    pkg = _sync(production_package.get_package, oid)
    assert pkg is not None, "production_packages 행이 없다"
    assert pkg.shaker_share_id == stored.shaker_share_id
    assert pkg.soul_trace_letter_id == stored.soul_trace_letter_id


def test_qr_points_at_the_web_app_for_the_right_pet(client: ASGITestClient):
    """QR 은 API 도메인이 아니라 **웹앱**의 Shaker 경험을 가리켜야 한다."""
    oid = _paid_order(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    url = pkg.qr_share_url or ""
    assert url.startswith("https://device.eternalbeam.com"), url
    assert "/shaker" in url, url
    assert PET in url, "QR 이 이 주문의 펫을 가리키지 않는다"


def test_print_share_has_no_expiry(client: ASGITestClient):
    """
    인쇄물은 오래 산다. 만료가 있는 공유를 종이에 찍으면 며칠 뒤 죽은 QR 이 된다.
    """
    oid = _paid_order(client)["order_id"]
    sid = _sync(physical_order.get, oid).shaker_share_id
    rows = _sync(shaker_share.list_shares, user_id=USER, pet_id=PET)
    rec = next(r for r in rows if r.share_id == sid)
    assert rec.expires_at is None, "인쇄용 공유에 만료가 걸려 있다"
    assert rec.revoked_at is None


# ── 멱등성: 중복 콜백 ────────────────────────────────────────────────────────


def test_duplicate_payment_callbacks_do_not_duplicate_share_or_package(
    client: ASGITestClient,
):
    """**핵심 회귀**: 콜백이 두 번 와도 공유도 패키지도 하나다."""
    order = _paid_order(client)
    oid = order["order_id"]
    first_share = _sync(physical_order.get, oid).shaker_share_id

    for _ in range(3):
        client.post(
            "/api/v1/orders/confirm",
            json={"payment_key": "pk_1", "order_id": oid, "amount": order["amount"]},
            headers=_auth(),
        )
        _sync(order_finalization.finalize, order_id=oid)

    assert _sync(physical_order.get, oid).shaker_share_id == first_share
    shares = _sync(shaker_share.list_shares, user_id=USER, pet_id=PET)
    assert len(shares) == 1, f"공유가 {len(shares)}개 — 중복 발급됐다"


def test_finalize_reuses_an_existing_share_instead_of_minting(client: ASGITestClient):
    """이미 그 펫의 공유가 있으면 **재사용**한다 — 펫 경험을 중복 생성하지 않는다."""
    _register_pet()
    existing, _tok = _sync(
        shaker_share.create_share,
        user_id=USER, pet_id=PET,
        breathing_url=f"https://storage.example.com/{BUCKET}/{OBJ}",
        breathing_bucket=BUCKET, breathing_object_path=OBJ,
    )
    oid = _paid_order(client)["order_id"]

    assert _sync(physical_order.get, oid).shaker_share_id == existing
    assert len(_sync(shaker_share.list_shares, user_id=USER, pet_id=PET)) == 1


def test_attach_share_never_swaps_an_existing_one():
    """
    붙은 공유는 바뀌지 않는다 — 이미 인쇄된 QR 과 DB 가 어긋나면 안 된다.
    """
    _register_pet()
    _sync(
        physical_order.create,
        order_id="o_swap", user_id=USER, pet_id=PET, product_type="LETTER",
        amount=14900, soul_trace_letter_id=None, recipient_name="x",
        recipient_phone="x", postal_code="x", address_line1="x",
        address_line2=None, shaker_share_id="share_first", currency="KRW",
    )
    _sync(physical_order.attach_share, order_id="o_swap", shaker_share_id="share_second")
    assert _sync(physical_order.get, "o_swap").shaker_share_id == "share_first"


# ── 실패해도 돈을 잃지 않는다 ────────────────────────────────────────────────


def test_production_failure_keeps_the_order_paid_and_retryable(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    **핵심 계약**: 생산 준비가 실패해도 주문은 PAID 다.

    여기서 예외를 올리면 고객은 결제 실패 화면을 보고 다시 결제한다 —
    이중 청구는 되돌리기 어렵고, 생산 준비는 언제든 다시 시도할 수 있다.
    """
    real_prepare = production_package.prepare

    async def _boom(**_k):
        raise production_package.ProductionError("BOOM", "생산 준비 실패", status=500)

    monkeypatch.setattr(production_package, "prepare", _boom)

    order = _paid_order(client)
    oid = order["order_id"]
    stored = _sync(physical_order.get, oid)

    assert stored.payment_status == physical_order.PAYMENT_PAID, "결제가 유실됐다"
    assert stored.production_status == physical_order.PRODUCTION_PENDING
    assert _sync(production_package.get_package, oid) is None

    # 재시도 가능: 원인이 사라지면 그대로 이어서 완료된다.
    # ⚠️ monkeypatch.undo() 를 쓰면 안 된다 — env·locate_breathing 대역까지 함께
    #    풀려서 "재시도가 되는지"가 아니라 다른 것을 재는 테스트가 된다.
    monkeypatch.setattr(production_package, "prepare", real_prepare)
    out = _sync(order_finalization.finalize, order_id=oid)
    assert out.package_ready is True
    assert _sync(physical_order.get, oid).production_status == physical_order.PRODUCTION_READY


def test_finalize_quietly_never_raises(monkeypatch: pytest.MonkeyPatch):
    """결제 경로에서 부르는 버전은 어떤 실패에도 예외를 올리지 않는다."""
    out = _sync(order_finalization.finalize_quietly, order_id="does-not-exist")
    assert out.package_ready is False
    assert out.error_code == "ORDER_NOT_FOUND"


def test_unpaid_order_is_never_finalized(client: ASGITestClient):
    """돈을 받기 전에 QR 을 발급하면 취소된 주문의 공유가 세상에 남는다."""
    _register_pet()
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": TRACE, "handoff": HANDOFF}, headers=_auth(),
    ).json()["letter_id"]
    client.post("/api/v1/orders/letter/link-pet",
                json={"letter_id": lid, "pet_id": PET}, headers=_auth())
    order = client.post(
        "/api/v1/orders/checkout",
        json={"pet_id": PET, "product_type": "LETTER",
              "soul_trace_letter_id": lid, **SHIPPING},
        headers=_auth(),
    ).json()

    with pytest.raises(order_finalization.FinalizationError) as e:
        _sync(order_finalization.finalize, order_id=order["order_id"])
    assert e.value.code == "ORDER_NOT_PAID"
    assert _sync(production_package.get_package, order["order_id"]) is None


# ── 인쇄물은 정확히 Soul Trace 본문이다 ──────────────────────────────────────


def test_a5_output_carries_the_exact_soul_trace_letter(client: ASGITestClient):
    """생산은 가져온 편지를 **그대로** 인쇄한다 — 다시 만들지 않는다."""
    oid = _paid_order(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    letter = _sync(soul_trace_letter.get_letter, pkg.soul_trace_letter_id)
    assert letter.letter_body == BODY

    rendered = _sync(production_package.render_file, pkg, "letter_pdf")
    assert rendered.data[:4] == b"%PDF"


def test_memory_box_includes_the_extra_assets(client: ASGITestClient):
    """MEMORY BOX = 편지 구성 + 사진 카드 + QR 메모리 카드 + 패키징."""
    oid = _paid_order(client, product="MEMORY_BOX")["order_id"]
    pkg = _sync(production_package.get_package, oid)
    files = set(production_package.manifest(pkg).get("files") or [])
    assert "letter_pdf" in files
    assert len(files) > 1, f"메모리 박스인데 편지만 있다: {files}"
