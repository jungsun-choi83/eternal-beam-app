"""
인쇄 생산 파이프라인 (Phase 13).

    PAID 주문 + canonical petId + Soul Trace 편지 + 사진 + 기존 Shaker 공유
      → 생산 패키지 (A5 편지 PDF · 사진 카드 · QR 카드 · 구성표)

핵심 계약:
  * **결제된 주문만** 생산에 들어간다.
  * 같은 canonical petId · 같은 Soul Trace 편지 · **기존** Shaker 공유를 쓴다.
  * 편지도 펫도 공유도 **새로 만들지 않는다.**
  * 생성(WAN/Luma)·프리미엄 행동을 절대 부르지 않는다.
  * 생산 준비는 멱등이다.
  * 상태: PENDING → READY → IN_PRODUCTION → PRODUCED / PENDING → SHIPPED → DELIVERED
"""

from __future__ import annotations

import functools
import io
import json
import zipfile

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import orders_v1, production_ops_v1
from backend.services import (
    pet_registry,
    physical_order,
    print_render,
    production_package,
    soul_trace_import,
    soul_trace_letter,
    toss_billing,
)

from .conftest import ASGITestClient

CUSTOMER = "buyer@example.com"
OTHER = "other@example.com"
OPS = "ops@eternalbeam.com"
PET = "pet_abc123"
OTHER_PET = "pet_other999"
LETTER_SRC = "st_letter_1"
HANDOFF = "h" * 43
LETTER_BODY = (
    "안녕, 엄마 아빠. 나는 지금도 엄마 아빠 곁의 공기처럼 조용히 머물고 있어요. "
    "슬퍼하지 말아요 — 우리가 나눴던 웃음은 시간 너머에서도 빛나거든요."
)
SHARE_URL = "https://eternalbeam.com/shaker?petId=pet_abc123&share=" + "a" * 43

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
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    monkeypatch.delenv("PRINT_LETTER_FONT_PATH", raising=False)
    monkeypatch.delenv("PRINT_CARD_FONT_PATH", raising=False)
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    production_package.__reset_for_tests()
    pet_registry.__reset_for_tests()

    # Soul Trace 는 다른 프로젝트다 — 테스트에서 실제 HTTP 를 쏘지 않는다.
    # 본문은 **서버 경로로만** 들어온다는 성질이 여기서도 그대로 유지된다.
    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=LETTER_BODY, pet_name="고야"
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)
    yield
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    production_package.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(orders_v1.router, prefix="/api")
    app.include_router(production_ops_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(u: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _png_bytes(w: int = 800, h: int = 600) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 140, 100)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _photo(monkeypatch: pytest.MonkeyPatch):
    """사진 다운로드는 네트워크를 타지 않는다."""
    async def _fetch(_url: str):
        return _png_bytes()

    monkeypatch.setattr(production_package, "_fetch_bytes", _fetch)


def _paid_order(client: ASGITestClient, product: str = "LETTER", user: str = CUSTOMER) -> str:
    """편지 연결 → 체크아웃 → 결제까지 마친 주문 id."""
    pet = PET if user != OTHER else OTHER_PET
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": f"{LETTER_SRC}_{user}", "handoff": HANDOFF},
        headers=_auth(user),
    ).json()["letter_id"]
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": pet},
        headers=_auth(user),
    )

    order = client.post(
        "/api/v1/orders/checkout",
        json={"pet_id": pet, "product_type": product, "soul_trace_letter_id": lid, **SHIPPING},
        headers=_auth(user),
    ).json()
    client.post(
        "/api/v1/orders/confirm",
        json={"payment_key": "pk", "order_id": order["order_id"], "amount": order["amount"]},
        headers=_auth(user),
    )
    return order["order_id"]


def _prepare(client: ASGITestClient, order_id: str, **body):
    payload = {"qr_share_url": SHARE_URL, "photo_image_url": "https://cdn.test/pet.png", **body}
    return client.post(
        f"/api/v1/ops/production/{order_id}/prepare", json=payload, headers=_auth(OPS)
    )


# ── 인가 ─────────────────────────────────────────────────────────────────────


def test_production_requires_ops(client: ASGITestClient):
    oid = _paid_order(client)
    assert client.get(f"/api/v1/ops/production/{oid}").status_code == 401
    # 주문의 **주인**이라도 운영자가 아니면 생산에 접근할 수 없다.
    assert client.get(f"/api/v1/ops/production/{oid}", headers=_auth(CUSTOMER)).status_code == 403
    assert _prepare(client, oid).status_code == 200


def test_ops_uses_the_same_allowlist_as_phase10(client: ASGITestClient, monkeypatch):
    monkeypatch.delenv("SHAKER_OPS_USER_IDS", raising=False)
    oid = _paid_order(client)
    assert client.get(f"/api/v1/ops/production/{oid}", headers=_auth(OPS)).status_code == 403


# ── 결제된 주문만 생산 ───────────────────────────────────────────────────────


def test_unpaid_order_cannot_enter_production(client: ASGITestClient):
    """**핵심 회귀**: 돈을 받기 전에 인쇄하면 취소 시 그대로 손실이다."""
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": "st_unpaid", "handoff": HANDOFF},
        headers=_auth(CUSTOMER),
    ).json()["letter_id"]
    order = client.post(
        "/api/v1/orders/checkout",
        json={"pet_id": PET, "product_type": "LETTER", "soul_trace_letter_id": lid, **SHIPPING},
        headers=_auth(CUSTOMER),
    ).json()
    # confirm 하지 않는다 — pending 이다.

    r = _prepare(client, order["order_id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ORDER_NOT_PAID"
    assert _sync(production_package.get_package, order["order_id"]) is None


def test_production_transition_requires_paid(client: ASGITestClient):
    oid = _paid_order(client)
    _prepare(client, oid)
    # 결제를 강제로 되돌려도 전이가 막힌다.
    physical_order._MOCK_ORDERS[oid]["payment_status"] = "pending"
    r = client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    assert r.status_code == 409


# ── 링크: 같은 펫 · 같은 편지 · 기존 공유 ────────────────────────────────────


def test_package_links_canonical_pet_letter_and_share(client: ASGITestClient):
    """**핵심 계약**: 패키지가 주문과 같은 펫·편지·QR 을 가리킨다."""
    oid = _paid_order(client)
    _prepare(client, oid)

    order = _sync(physical_order.get, oid)
    pkg = _sync(production_package.get_package, oid)
    assert pkg.pet_id == order.pet_id == PET
    assert pkg.soul_trace_letter_id == order.soul_trace_letter_id
    assert pkg.qr_share_url == SHARE_URL
    assert pkg.user_id == order.user_id


def test_prepare_never_creates_pet_letter_or_share(client: ASGITestClient, monkeypatch):
    """**핵심 회귀**: 생산이 새 펫 경험·편지를 찍어 내지 않는다."""
    from backend.services import shaker_share

    async def _boom_share(**_kw):
        raise AssertionError("생산이 Shaker 공유를 새로 발급했다")

    async def _boom_letter(**_kw):
        raise AssertionError("생산이 편지를 새로 만들었다")

    # ⚠️ 폭탄은 주문을 만든 **뒤** 장착한다. 주문 생성/결제는 생산이 아니고,
    #    먼저 장착하면 준비 단계가 아니라 준비 이전 단계에서 터진다.
    oid = _paid_order(client)
    letters_before = dict(soul_trace_letter._MOCK_LETTERS)

    monkeypatch.setattr(shaker_share, "create_share", _boom_share)
    monkeypatch.setattr(soul_trace_letter, "link_letter", _boom_letter)

    _prepare(client, oid)
    client.get(f"/api/v1/ops/production/{oid}/file/letter_pdf", headers=_auth(OPS))

    assert soul_trace_letter._MOCK_LETTERS == letters_before


def test_prepare_refuses_to_invent_a_qr(client: ASGITestClient):
    """
    QR URL 없이는 준비할 수 없다.

    Phase 10 은 공유 토큰을 해시로만 저장하므로 share_id 로 URL 을 복원할 수 없다.
    여기서 새 공유를 발급하면 "펫 경험 중복 금지"가 깨지므로 **거절**한다.
    """
    oid = _paid_order(client)
    r = client.post(
        f"/api/v1/ops/production/{oid}/prepare", json={}, headers=_auth(OPS)
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] in ("QR_SHARE_MISSING", "QR_URL_REQUIRED")


def test_qr_must_be_a_shaker_url(client: ASGITestClient):
    """스토리지·영상 주소가 인쇄되면 7일 뒤 죽고 폐기할 방법도 없다."""
    oid = _paid_order(client)
    bad = "https://proj.supabase.co/storage/v1/object/sign/b/o.mp4?share=" + "a" * 43
    r = _prepare(client, oid, qr_share_url=bad)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "QR_URL_NOT_SHAKER"


def test_missing_letter_blocks_production(client: ASGITestClient):
    oid = _paid_order(client)
    # 편지 본문을 지운다 — 여백만 인쇄된 종이를 보내지 않는다.
    lid = _sync(physical_order.get, oid).soul_trace_letter_id
    soul_trace_letter._MOCK_LETTERS[lid]["letter_body"] = "   "

    r = _prepare(client, oid)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LETTER_BODY_EMPTY"


# ── 출력물 ───────────────────────────────────────────────────────────────────


def test_letter_product_outputs(client: ASGITestClient):
    """LETTER = A5 편지 PDF + QR (별도 카드 없음)."""
    oid = _paid_order(client, "LETTER")
    state = _prepare(client, oid).json()
    assert state["files"] == ["letter_pdf"]

    r = client.get(f"/api/v1/ops/production/{oid}/file/letter_pdf", headers=_auth(OPS))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    assert r.headers["cache-control"] == "no-store"

    # 편지 상품에는 카드가 없다.
    for kind in ("photo_card", "qr_card"):
        bad = client.get(f"/api/v1/ops/production/{oid}/file/{kind}", headers=_auth(OPS))
        assert bad.status_code == 404, kind


def test_memory_box_outputs(client: ASGITestClient):
    """MEMORY BOX = A5 편지 + 85×55 사진 카드 + 85×55 QR 카드 + 패키징."""
    oid = _paid_order(client, "MEMORY_BOX")
    state = _prepare(client, oid).json()
    assert set(state["files"]) == {"letter_pdf", "photo_card", "qr_card"}

    pdf = client.get(f"/api/v1/ops/production/{oid}/file/letter_pdf", headers=_auth(OPS))
    assert pdf.content[:5] == b"%PDF-"

    for kind in ("photo_card", "qr_card"):
        r = client.get(f"/api/v1/ops/production/{oid}/file/{kind}", headers=_auth(OPS))
        assert r.status_code == 200, kind
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_cards_are_85x55mm_at_300dpi(client: ASGITestClient):
    """명함 규격 — 인쇄소가 그대로 재단한다."""
    from PIL import Image

    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)
    for kind in ("photo_card", "qr_card"):
        r = client.get(f"/api/v1/ops/production/{oid}/file/{kind}", headers=_auth(OPS))
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (print_render.CARD_W_PX, print_render.CARD_H_PX), kind
        assert img.size == (1004, 650), kind


def test_letter_pdf_contains_the_soul_trace_body(client: ASGITestClient):
    """
    인쇄된 편지가 **Soul Trace 본문**이다.

    PDF 내부 텍스트는 CID 인코딩이라 원문 비교가 불가능하므로, 렌더러가 본문을
    실제로 소비하는지를 줄바꿈 결과로 확인한다.
    """
    font = print_render.letter_font_name()
    lines = print_render.wrap_korean(LETTER_BODY, font, 11, 300)
    assert lines and "".join(lines).replace(" ", "").startswith("안녕,엄마아빠.")

    oid = _paid_order(client)
    _prepare(client, oid)
    r = client.get(f"/api/v1/ops/production/{oid}/file/letter_pdf", headers=_auth(OPS))
    # 본문이 실제로 들어가면 빈 A5 보다 확실히 크다.
    assert len(r.content) > 2000


def test_render_is_deterministic(client: ASGITestClient):
    """
    같은 입력이면 같은 바이트다 — 그래서 파일을 저장하지 않아도 된다.

    생성 시각을 넣었다면 여기서 깨진다.
    """
    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)
    for kind in ("letter_pdf", "qr_card"):
        a = client.get(f"/api/v1/ops/production/{oid}/file/{kind}", headers=_auth(OPS)).content
        b = client.get(f"/api/v1/ops/production/{oid}/file/{kind}", headers=_auth(OPS)).content
        assert a == b, kind


def test_download_package_is_a_complete_zip(client: ASGITestClient):
    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)

    r = client.get(f"/api/v1/ops/production/{oid}/download", headers=_auth(OPS))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert any(n.endswith("-letter-a5.pdf") for n in names)
    assert any(n.endswith("-photo-card-85x55.png") for n in names)
    assert any(n.endswith("-qr-card-85x55.png") for n in names)

    m = json.loads(z.read(f"{oid}-manifest.json"))
    assert m["pet_id"] == PET
    assert m["qr_share_url"] == SHARE_URL
    assert m["card_size_mm"] == [85.0, 55.0]
    assert m["card_dpi"] == 300
    assert m["letter_page_size"] == "A5"


def test_zip_is_not_built_when_a_file_fails(client: ASGITestClient, monkeypatch):
    """반쪽짜리 패키지가 인쇄소로 넘어가면 무엇이 빠졌는지 아무도 모른다."""
    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)

    async def _no_photo(_url: str):
        return None

    monkeypatch.setattr(production_package, "_fetch_bytes", _no_photo)
    r = client.get(f"/api/v1/ops/production/{oid}/download", headers=_auth(OPS))
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "PHOTO_UNREACHABLE"


def test_manifest_lists_packaging_for_memory_box(client: ASGITestClient):
    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)
    m = client.get(f"/api/v1/ops/production/{oid}/package", headers=_auth(OPS)).json()
    assert {"rigid_box", "black_tissue", "message_card"} <= set(m["packaging"])
    assert m["recipient"]["name"] == SHIPPING["recipient_name"]


def test_package_endpoints_need_preparation_first(client: ASGITestClient):
    oid = _paid_order(client)
    for path in ("package", "download", "file/letter_pdf"):
        r = client.get(f"/api/v1/ops/production/{oid}/{path}", headers=_auth(OPS))
        assert r.status_code == 409, path
        assert r.json()["detail"]["code"] == "PACKAGE_NOT_READY"


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_prepare_is_idempotent(client: ASGITestClient):
    """**핵심 회귀**: 두 번 눌러도 패키지가 두 벌 생기지 않는다."""
    oid = _paid_order(client, "MEMORY_BOX")
    first = _prepare(client, oid).json()
    assert first["production_status"] == "ready"

    second = _prepare(client, oid).json()
    assert second["production_status"] == "ready"
    assert len(production_package._MOCK_PACKAGES) == 1


def test_prepare_does_not_overwrite_inputs(client: ASGITestClient):
    """
    이미 준비된 주문의 입력을 조용히 바꾸지 않는다.

    인쇄가 시작된 뒤 QR 이 바뀌면 고객이 받은 종이와 시스템이 어긋난다.
    """
    oid = _paid_order(client)
    _prepare(client, oid)

    other_url = "https://eternalbeam.com/shaker?petId=pet_abc123&share=" + "b" * 43
    _prepare(client, oid, qr_share_url=other_url)

    assert _sync(production_package.get_package, oid).qr_share_url == SHARE_URL


def test_prepare_after_production_started_is_safe(client: ASGITestClient):
    """이미 IN_PRODUCTION 인 주문에 prepare 를 다시 불러도 되돌리지 않는다."""
    oid = _paid_order(client)
    _prepare(client, oid)
    client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))

    again = _prepare(client, oid)
    assert again.status_code == 200
    assert again.json()["production_status"] == "in_production"


# ── 상태 기계 ────────────────────────────────────────────────────────────────


def test_production_states_advance_in_order(client: ASGITestClient):
    oid = _paid_order(client)
    assert _sync(physical_order.get, oid).production_status == "pending"

    assert _prepare(client, oid).json()["production_status"] == "ready"
    assert client.post(
        f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS)
    ).json()["production_status"] == "in_production"
    assert client.post(
        f"/api/v1/ops/production/{oid}/produced", headers=_auth(OPS)
    ).json()["production_status"] == "produced"


def test_production_cannot_skip_states(client: ASGITestClient):
    """PENDING 에서 바로 PRODUCED 로 갈 수 없다 — 불가능한 이력을 만들지 않는다."""
    oid = _paid_order(client)
    r = client.post(f"/api/v1/ops/production/{oid}/produced", headers=_auth(OPS))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PRODUCTION_TRANSITION_INVALID"


def test_production_start_requires_a_package(client: ASGITestClient):
    oid = _paid_order(client)
    r = client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PACKAGE_NOT_READY"


def test_repeat_transition_is_idempotent(client: ASGITestClient):
    oid = _paid_order(client)
    _prepare(client, oid)
    client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    a = client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    assert a.status_code == 200
    assert a.json()["production_status"] == "in_production"


# ── 배송 ─────────────────────────────────────────────────────────────────────


def _produce(client: ASGITestClient, oid: str) -> None:
    _prepare(client, oid)
    client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    client.post(f"/api/v1/ops/production/{oid}/produced", headers=_auth(OPS))


def test_shipping_flow(client: ASGITestClient):
    oid = _paid_order(client)
    _produce(client, oid)

    tracked = client.post(
        f"/api/v1/ops/production/{oid}/tracking",
        json={"tracking_number": "1234-5678"}, headers=_auth(OPS),
    ).json()
    assert tracked["tracking_number"] == "1234-5678"
    assert tracked["shipping_status"] == "pending"  # 송장 등록 ≠ 발송

    shipped = client.post(f"/api/v1/ops/production/{oid}/ship", headers=_auth(OPS)).json()
    assert shipped["shipping_status"] == "shipped"

    delivered = client.post(
        f"/api/v1/ops/production/{oid}/delivered", headers=_auth(OPS)
    ).json()
    assert delivered["shipping_status"] == "delivered"


def test_cannot_ship_without_tracking(client: ASGITestClient):
    """송장 없는 '배송 중'은 고객에게 아무것도 알려 주지 못하고 문의만 만든다."""
    oid = _paid_order(client)
    _produce(client, oid)
    r = client.post(f"/api/v1/ops/production/{oid}/ship", headers=_auth(OPS))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "TRACKING_REQUIRED"


def test_cannot_ship_before_produced(client: ASGITestClient):
    """만들지 않은 것을 보낼 수는 없다."""
    oid = _paid_order(client)
    _prepare(client, oid)
    client.post(
        f"/api/v1/ops/production/{oid}/tracking",
        json={"tracking_number": "1234"}, headers=_auth(OPS),
    )
    r = client.post(f"/api/v1/ops/production/{oid}/ship", headers=_auth(OPS))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NOT_PRODUCED"


def test_shipping_cannot_skip_states(client: ASGITestClient):
    oid = _paid_order(client)
    _produce(client, oid)
    r = client.post(f"/api/v1/ops/production/{oid}/delivered", headers=_auth(OPS))
    assert r.status_code == 409


# ── 주문 간 격리 ─────────────────────────────────────────────────────────────


def test_orders_do_not_bleed_into_each_other(client: ASGITestClient):
    """**핵심 회귀**: 한 주문의 준비가 다른 주문을 건드리지 않는다."""
    a = _paid_order(client, "LETTER", user=CUSTOMER)
    b = _paid_order(client, "MEMORY_BOX", user=OTHER)

    _prepare(client, a)

    assert _sync(production_package.get_package, b) is None
    assert _sync(physical_order.get, b).production_status == "pending"
    pkg_a = _sync(production_package.get_package, a)
    assert pkg_a.user_id == CUSTOMER
    assert pkg_a.product_type == "LETTER"


def test_unknown_order_is_rejected(client: ASGITestClient):
    r = client.get("/api/v1/ops/production/eb_order_nope", headers=_auth(OPS))
    assert r.status_code == 404


# ── 생성 금지 ────────────────────────────────────────────────────────────────


def test_production_never_generates(client: ASGITestClient, monkeypatch):
    """
    **핵심 회귀**: 인쇄 파이프라인이 WAN/Luma·프리미엄 행동·결제를 부르지 않는다.
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
            raise AssertionError(f"{name} 호출됨 — 생산은 생성하지 않는다")

        return _boom

    # ⚠️ 주문 생성·결제를 먼저 끝낸다. 그건 Phase 12 의 정상 경로이고, 여기서
    #    검증하려는 것은 **생산 단계**가 아무것도 부르지 않는다는 것이다.
    oid = _paid_order(client, "MEMORY_BOX")

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
        (credit_generation_service, "generate_with_credit"),
        (wallet_service, "deduct_credits"),
        (toss_billing, "charge"),
        (toss_billing, "confirm_payment"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _prepare(client, oid)
    client.get(f"/api/v1/ops/production/{oid}/download", headers=_auth(OPS))
    client.post(f"/api/v1/ops/production/{oid}/start", headers=_auth(OPS))
    client.post(f"/api/v1/ops/production/{oid}/produced", headers=_auth(OPS))
    assert fired == []


def test_production_modules_are_independent():
    """구조로 고정 — 생산 모듈이 생성·구독·테마 모듈을 import 하지 않는다."""
    import ast

    forbidden = {
        "premium_entitlement", "subscription_store_service", "premium_generation",
        "generation_queue", "credit_generation_service", "wallet_service",
        "premium_purchase", "theme_entitlement", "theme_purchase",
        "luma_service", "luma_batch_service", "wan_service", "video_generation",
        "live_portrait_service", "pet_generation_store",
    }
    for path in (
        "backend/services/production_package.py",
        "backend/services/print_render.py",
        "backend/routers/production_ops_v1.py",
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


def test_print_render_has_no_letter_generation():
    """렌더러가 문장을 만들어 내지 않는다 — 본문은 항상 인자로 들어온다."""
    import ast

    tree = ast.parse(open("backend/services/print_render.py", encoding="utf-8").read())
    defined = {
        n.name.lower()
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for bad in ("generate_letter", "compose_letter", "write_letter", "make_letter_text"):
        assert bad not in defined

    # 빈 본문은 렌더되지 않는다 (여백만 인쇄된 종이를 보내지 않는다).
    with pytest.raises(print_render.PrintRenderError):
        print_render.render_letter_pdf(
            print_render.LetterContent(body="   "), order_id="x"
        )


# ── Ops 화면 ─────────────────────────────────────────────────────────────────


def test_ops_view_has_everything_the_target_screen_needs(client: ASGITestClient):
    """
    Order #… / Payment / Letter PDF / Photo Card / QR Card / Production / Shipping
    """
    oid = _paid_order(client, "MEMORY_BOX")
    _prepare(client, oid)

    s = client.get(f"/api/v1/ops/production/{oid}", headers=_auth(OPS)).json()
    assert s["order_id"] == oid
    assert s["payment_status"] == "paid"
    assert s["production_status"] == "ready"
    assert s["shipping_status"] == "pending"
    assert s["package_ready"] is True
    assert set(s["files"]) == {"letter_pdf", "photo_card", "qr_card"}
    assert s["pet_id"] == PET
    assert s["soul_trace_letter_id"]
    assert s["recipient_name"] == SHIPPING["recipient_name"]


def test_ops_view_before_preparation(client: ASGITestClient):
    oid = _paid_order(client)
    s = client.get(f"/api/v1/ops/production/{oid}", headers=_auth(OPS)).json()
    assert s["package_ready"] is False
    assert s["files"] == []
    assert s["production_status"] == "pending"


# ── 폰트 (인쇄 위험) ─────────────────────────────────────────────────────────


def test_manifest_reports_font_embedding(client: ASGITestClient):
    """
    ⚠️ 내장 CID 폰트는 PDF 에 **임베드되지 않는다.** 인쇄소 RIP 에 해당 CJK
    리소스가 없으면 글자가 깨진다. 구성표가 그 사실을 드러내야 한다.
    """
    oid = _paid_order(client)
    _prepare(client, oid)
    m = client.get(f"/api/v1/ops/production/{oid}/package", headers=_auth(OPS)).json()
    assert m["font_embedded"] is False  # TTF 미지정 상태
    assert print_render.font_is_embedded() is False
