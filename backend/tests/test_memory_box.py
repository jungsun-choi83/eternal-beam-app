"""
MEMORY BOX ₩49,000 (Phase 17) — 결제부터 인쇄소에 넘길 한 덩어리까지.

이 파일이 지키는 계약:
  * 체크아웃은 **기존 물리 체크아웃 그대로**다 (₩49,000, 같은 편지·펫).
  * 자동 완결이 사진 카드 원본까지 확정해 **패키지 ZIP 이 실제로 만들어진다.**
    (예전에는 photo_image_url 이 None 으로 굳어 ZIP 이 영영 실패했다.)
  * 규약 밖에 저장된 펫은 운영이 사진을 **나중에 붙일 수 있다** — prepare 는
    멱등이라 그 길로는 고칠 수 없었다.
  * 메시지 카드는 문구가 승인되기 전까지 **패키지에 들어가지 않는다.**
  * 중복 콜백·중단 후 재시도가 공유·QR·패키지를 복제하지 않는다.
  * MEMORY BOX 의 QR 은 **고객 keepsake(Shaker)** 이고 파트너 코드가 아니다.
"""

from __future__ import annotations

import functools
import io
import zipfile

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import orders_v1, production_ops_v1
from backend.services import (
    order_finalization,
    pet_registry,
    physical_order,
    physical_product,
    print_render,
    production_package,
    shaker_ops,
    shaker_qr_artifact,
    shaker_share,
    soul_trace_import,
    soul_trace_letter,
)

from .conftest import ASGITestClient

USER = "boxbuyer@example.com"
OPS = "ops@example.com"
PET = "pet_box123"
CONTENT = "box123"
BUCKET = "user-assets"
OBJ = f"{USER}/{CONTENT}/idle_loop.mp4"
PHOTO = f"https://storage.example.com/{BUCKET}/{USER}/{CONTENT}/background_source/original.jpg?token=x"
TRACE = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
HANDOFF = "h" * 43
BODY = "엄마, 나 보리야. 현관에서 기다리던 그 시간이 제일 좋았어."

HOSPITAL = dict(
    partner_id="ptn_hosp_001",
    partner_type="HOSPITAL",
    partner_name="silim hospital",
    partner_code="AbCdEf1234567890",
    partner_track="memorial",
    partner_share_rate=0.15,
)

SHIPPING = {
    "recipient_name": "김보호",
    "recipient_phone": "010-1234-5678",
    "postal_code": "06236",
    "address_line1": "서울시 강남구 테헤란로 1",
}

_MODULES = (
    physical_order,
    soul_trace_letter,
    pet_registry,
    production_package,
    shaker_share,
    shaker_qr_artifact,
)

#: 이 trace 로 클레임하면 파트너 귀속이 붙는다.
TRACE_PARTNER = "cccccccc-dddd-eeee-ffff-000000000000"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://device.eternalbeam.com")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    # 메시지 카드 문구는 아직 승인되지 않았다 — 그것이 기본 상태다.
    monkeypatch.delenv(print_render.MESSAGE_CARD_ENV, raising=False)
    for m in _MODULES:
        m.__reset_for_tests()

    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        extra = HOSPITAL if trace_id == TRACE_PARTNER else {}
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=BODY, pet_name="보리", **extra
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)

    # 스토리지는 이 테스트의 대상이 아니다. 위치 탐색만 대역으로 둔다.
    async def _locate(_user: str, _pet: str):
        return (
            shaker_ops.BreathingLocation(bucket=BUCKET, object_path=OBJ),
            f"https://storage.example.com/{BUCKET}/{OBJ}?token=x",
        )

    async def _photo(_user: str, _pet: str):
        return PHOTO

    monkeypatch.setattr(shaker_ops, "locate_breathing", _locate)
    monkeypatch.setattr(shaker_ops, "locate_pet_photo", _photo)

    # 사진 카드는 실제 바이트가 필요하다. 네트워크 대신 작은 PNG 를 돌려준다.
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), (180, 150, 120)).save(buf, format="PNG")
    png = buf.getvalue()

    async def _fetch_bytes(_url: str):
        return png

    monkeypatch.setattr(production_package, "_fetch_bytes", _fetch_bytes)

    yield
    for m in _MODULES:
        m.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(orders_v1.router, prefix="/api")
    app.include_router(production_ops_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *a, **k):
    return anyio.run(functools.partial(afn, *a, **k))


def _auth(u: str = USER) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _register_pet():
    _sync(
        pet_registry.register,
        user_id=USER,
        pet_id=PET,
        content_id=CONTENT,
        breathing_bucket=BUCKET,
        breathing_object_path=OBJ,
        source=pet_registry.SOURCE_OPS,
        verify=False,
    )


def _buy_box(client: ASGITestClient, *, trace: str = TRACE) -> dict:
    """편지 클레임 → 펫 연결 → MEMORY BOX 체크아웃 → 결제 확인."""
    _register_pet()
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": trace, "handoff": HANDOFF},
        headers=_auth(),
    ).json()["letter_id"]
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(),
    )
    order = client.post(
        "/api/v1/orders/checkout",
        json={
            "pet_id": PET,
            "product_type": "MEMORY_BOX",
            "soul_trace_letter_id": lid,
            **SHIPPING,
        },
        headers=_auth(),
    ).json()
    client.post(
        "/api/v1/orders/confirm",
        json={
            "payment_key": "pk",
            "order_id": order["order_id"],
            "amount": order["amount"],
        },
        headers=_auth(),
    )
    order["letter_id"] = lid
    return order


# ── 카탈로그 · 체크아웃 ──────────────────────────────────────────────────────


def test_catalog_price_and_contents():
    """₩49,000 과 7개 구성품. 가격은 발명이 아니라 PM 확정값이다."""
    p = physical_product.get_product("MEMORY_BOX")
    assert p.price_krw == 49_000
    assert p.currency == "KRW"
    assert set(p.contents) == {
        "printed_letter",
        "envelope",
        "photo_card",
        "qr_memory_card",
        "rigid_box",
        "black_tissue",
        "message_card",
    }
    assert p.includes_letter and p.needs_photo_card and p.needs_message_card


def test_checkout_uses_existing_physical_flow(client: ASGITestClient):
    """같은 펫·같은 편지·기존 체크아웃. 새 결제 시스템이 없다."""
    order = _buy_box(client)
    o = _sync(physical_order.get, order["order_id"])
    assert o.product_type == "MEMORY_BOX"
    assert o.amount == 49_000
    assert o.payment_status == physical_order.PAYMENT_PAID
    assert o.pet_id == PET
    assert o.soul_trace_letter_id == order["letter_id"]


# ── 자동 완결 ────────────────────────────────────────────────────────────────


def test_paid_box_auto_finalizes_to_ready(client: ASGITestClient):
    """PAID → 공유 → QR 산출물 → 패키지 → READY. 사람이 누르지 않는다."""
    oid = _buy_box(client)["order_id"]
    o = _sync(physical_order.get, oid)
    assert o.production_status == physical_order.PRODUCTION_READY
    assert o.shaker_share_id

    pkg = _sync(production_package.get_package, oid)
    assert pkg is not None
    # **이것이 Phase 17 의 핵심 수정이다.** 예전에는 None 으로 굳었다.
    assert pkg.photo_image_url == PHOTO
    assert _sync(shaker_qr_artifact.get, o.shaker_share_id) is not None


def test_all_memory_box_assets_render(client: ASGITestClient):
    """편지 A5 + 사진 카드 + QR 카드가 실제 바이트로 나온다."""
    from PIL import Image

    oid = _buy_box(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)

    letter = _sync(production_package.render_file, pkg, "letter_pdf")
    assert letter.data[:5] == b"%PDF-"

    for kind in ("photo_card", "qr_card"):
        f = _sync(production_package.render_file, pkg, kind)
        assert f.content_type == "image/png"
        img = Image.open(io.BytesIO(f.data))
        assert img.size == (print_render.CARD_W_PX, print_render.CARD_H_PX), kind
        assert img.size == (1004, 650), kind


def test_zip_actually_builds(client: ASGITestClient):
    """
    **회귀 방지**: 메모리 박스 패키지 ZIP 이 만들어진다.

    예전에는 사진 원본이 없어 photo_card 가 실패했고, 구성 파일 하나가 실패하면
    ZIP 을 만들지 않는 규칙 때문에 **ZIP 자체가 영영 나오지 않았다** — 결제는
    끝났는데 인쇄소에 넘길 것이 없는 상태였다.
    """
    oid = _buy_box(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    z = _sync(production_package.render_zip, pkg)

    with zipfile.ZipFile(io.BytesIO(z.data)) as zf:
        names = zf.namelist()
    assert any(n.endswith("-letter-a5.pdf") for n in names), names
    assert any(n.endswith("-photo-card-85x55.png") for n in names), names
    assert any(n.endswith("-qr-card-85x55.png") for n in names), names
    assert any(n.endswith("-manifest.json") for n in names), names


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_duplicate_confirm_does_not_duplicate_anything(client: ASGITestClient):
    """중복 콜백이 공유·QR·패키지를 복제하지 않는다."""
    order = _buy_box(client)
    oid = order["order_id"]
    before = _sync(physical_order.get, oid)

    for _ in range(3):
        client.post(
            "/api/v1/orders/confirm",
            json={"payment_key": "pk", "order_id": oid, "amount": order["amount"]},
            headers=_auth(),
        )

    after = _sync(physical_order.get, oid)
    assert after.shaker_share_id == before.shaker_share_id
    shares = _sync(shaker_share.list_shares, user_id=USER, pet_id=PET)
    assert len(shares) == 1, f"공유가 복제됐다: {shares}"
    assert after.production_status == physical_order.PRODUCTION_READY


def test_retry_after_interrupted_finalization(client: ASGITestClient, monkeypatch):
    """
    완결이 중간에 끊겨도 재시도로 복구된다 — 그리고 공유를 태우지 않는다.

    첫 시도는 패키지 준비 직전에 죽는다. 주문은 PAID·pending 으로 남고, 두 번째
    시도가 **같은 공유**로 이어서 끝낸다.
    """
    # 첫 완결이 패키지 준비 단계에서 죽도록 만들어 둔 뒤 결제를 확인한다.
    real_prepare = production_package.prepare
    boom = {"n": 0}

    async def _flaky(**kw):
        boom["n"] += 1
        if boom["n"] == 1:
            raise production_package.ProductionError("BOOM", "일시적 실패", status=503)
        return await real_prepare(**kw)

    monkeypatch.setattr(production_package, "prepare", _flaky)

    _register_pet()
    lid = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": TRACE, "handoff": HANDOFF},
        headers=_auth(),
    ).json()["letter_id"]
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(),
    )
    order = client.post(
        "/api/v1/orders/checkout",
        json={
            "pet_id": PET,
            "product_type": "MEMORY_BOX",
            "soul_trace_letter_id": lid,
            **SHIPPING,
        },
        headers=_auth(),
    ).json()
    oid = order["order_id"]
    client.post(
        "/api/v1/orders/confirm",
        json={"payment_key": "pk", "order_id": oid, "amount": order["amount"]},
        headers=_auth(),
    )

    # 돈은 받았고 생산 준비는 실패했다 — 되돌리지 않는다.
    interrupted = _sync(physical_order.get, oid)
    assert interrupted.payment_status == physical_order.PAYMENT_PAID
    assert interrupted.production_status == physical_order.PRODUCTION_PENDING
    first_share = interrupted.shaker_share_id
    assert first_share, "공유는 이미 확보돼 있어야 한다 — 재시도가 태우면 안 된다"

    out2 = _sync(order_finalization.finalize_quietly, order_id=oid)
    assert out2.package_ready is True
    assert out2.production_status == physical_order.PRODUCTION_READY
    assert _sync(physical_order.get, oid).shaker_share_id == first_share, "공유가 새로 발급됐다"
    assert len(_sync(shaker_share.list_shares, user_id=USER, pet_id=PET)) == 1


# ── 사진 늦게 붙이기 ─────────────────────────────────────────────────────────


def test_photo_can_be_attached_after_finalization(client: ASGITestClient, monkeypatch):
    """
    규약 밖에 저장된 펫: 자동 완결이 사진을 못 찾아도 운영이 나중에 붙일 수 있다.

    **회귀 방지**: prepare 는 멱등이라 photo_image_url 을 넘겨도 무시한다.
    그 길밖에 없던 시절에는 사진을 붙일 방법이 세상에 없었다.
    """
    async def _no_photo(_user: str, _pet: str):
        return None

    monkeypatch.setattr(shaker_ops, "locate_pet_photo", _no_photo)

    oid = _buy_box(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    assert pkg.photo_image_url is None
    # 완결 자체는 막히지 않는다 — 편지와 QR 은 준비돼 있다.
    assert _sync(physical_order.get, oid).production_status == physical_order.PRODUCTION_READY

    # prepare 로는 고쳐지지 않는다(멱등). 이것이 전용 경로가 필요한 이유다.
    again = _sync(production_package.prepare, order_id=oid, photo_image_url=PHOTO)
    assert again.photo_image_url is None

    r = client.post(
        f"/api/v1/ops/production/{oid}/photo",
        json={"photo_image_url": PHOTO},
        headers=_auth(OPS),
    )
    assert r.status_code == 200, r.text
    assert r.json()["photo_ready"] is True
    assert _sync(production_package.get_package, oid).photo_image_url == PHOTO
    # 이제 ZIP 이 만들어진다.
    _sync(production_package.render_zip, _sync(production_package.get_package, oid))


def test_photo_cannot_change_once_in_production(client: ASGITestClient):
    """인쇄가 시작된 뒤 사진이 바뀌면 인쇄소 파일과 서버가 어긋난다."""
    oid = _buy_box(client)["order_id"]
    _sync(
        physical_order.advance_production,
        order_id=oid,
        to=physical_order.PRODUCTION_IN_PRODUCTION,
    )
    r = client.post(
        f"/api/v1/ops/production/{oid}/photo",
        json={"photo_image_url": PHOTO},
        headers=_auth(OPS),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PRODUCTION_ALREADY_STARTED"


def test_photo_attach_requires_ops(client: ASGITestClient):
    oid = _buy_box(client)["order_id"]
    assert client.post(f"/api/v1/ops/production/{oid}/photo",
                       json={"photo_image_url": PHOTO}).status_code == 401
    # 주문의 주인이라도 운영자가 아니면 안 된다.
    assert client.post(f"/api/v1/ops/production/{oid}/photo",
                       json={"photo_image_url": PHOTO},
                       headers=_auth(USER)).status_code == 403


# ── 메시지 카드 ──────────────────────────────────────────────────────────────


def test_message_card_is_tbd_and_excluded_from_package(client: ASGITestClient):
    """
    문구가 승인되기 전에는 패키지에 **들어가지 않는다.**

    자리표시자가 ZIP 에 실리면 인쇄소로 넘어가고, 언젠가 그대로 찍힌다.
    빠졌다는 사실 자체는 pending_files 로 남는다 — 조용히 사라지면 아무도 모른다.
    """
    oid = _buy_box(client)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    m = production_package.manifest(pkg)

    assert "message_card" not in m["files"]
    pending = {p["kind"]: p for p in m["pending_files"]}
    assert pending["message_card"]["status"] == "TBD"

    with zipfile.ZipFile(io.BytesIO(_sync(production_package.render_zip, pkg).data)) as zf:
        assert not any("message-card" in n for n in zf.namelist())

    # 다만 운영은 규격 확인용 교정지를 **볼 수** 있다.
    proof = _sync(production_package.render_file, pkg, "message_card")
    assert proof.filename.endswith("-message-card-TBD-proof.png")


def test_message_card_joins_package_once_copy_is_approved(
    client: ASGITestClient, monkeypatch
):
    """승인 문구가 설정되면 자동으로 패키지에 합류한다 — 코드 변경 없이."""
    oid = _buy_box(client)["order_id"]
    monkeypatch.setenv(print_render.MESSAGE_CARD_ENV, "승인된 문구입니다.\n{pet_name} 올림")

    pkg = _sync(production_package.get_package, oid)
    m = production_package.manifest(pkg)
    assert "message_card" in m["files"]
    assert m["pending_files"] == []


# ── 파트너 · 직접 유입 ───────────────────────────────────────────────────────


def test_partner_attribution_snapshot_on_box(client: ASGITestClient):
    """파트너 귀속 6개 값이 메모리 박스 주문에도 그대로 스냅샷된다."""
    oid = _buy_box(client, trace=TRACE_PARTNER)["order_id"]
    o = _sync(physical_order.get, oid)
    assert o.partner_id == HOSPITAL["partner_id"]
    assert o.partner_type == "HOSPITAL"
    assert o.partner_name == HOSPITAL["partner_name"]
    assert o.partner_code == HOSPITAL["partner_code"]
    assert o.partner_track == "memorial"
    assert o.partner_share_rate == 0.15


def test_direct_entry_box_has_null_partner(client: ASGITestClient):
    oid = _buy_box(client)["order_id"]
    o = _sync(physical_order.get, oid)
    assert o.partner_id is None
    assert o.partner_code is None
    assert o.partner_share_rate is None
    assert o.amount == 49_000


# ── 두 QR 을 섞지 않는다 ─────────────────────────────────────────────────────


def test_memory_box_qr_is_shaker_not_partner(client: ASGITestClient):
    """
    **핵심 규칙**: 메모리 박스 QR 은 고객 keepsake(Shaker)이지 파트너 획득 QR 이
    아니다. 파트너 코드가 섞이면 상자를 연 고객이 설문 랜딩으로 떨어진다.
    """
    oid = _buy_box(client, trace=TRACE_PARTNER)["order_id"]
    pkg = _sync(production_package.get_package, oid)

    # 인쇄될 주소 — 완결이 발급해 패키지에 스냅샷한 그 값이다.
    url = pkg.qr_share_url
    assert url, "인쇄될 QR 주소가 없다"
    assert "/shaker" in url, url
    assert f"petId={PET}" in url, url
    assert "share=" in url, url
    # 파트너 코드 파라미터가 절대 들어가면 안 된다.
    assert "?p=" not in url and "&p=" not in url, url
    assert HOSPITAL["partner_code"] not in url, url

    # 산출물도 같은 펫의 **인쇄용**으로 보관돼 있어야 한다.
    art = _sync(shaker_qr_artifact.get, pkg.shaker_share_id)
    assert art.pet_id == PET
    # 보관 시 대문자로 정규화된다 — 대소문자로 비교하지 않는다.
    assert (art.purpose or "").upper() == order_finalization.PRINT_PURPOSE.upper()


def test_printed_qr_payload_is_the_shaker_url(client: ASGITestClient):
    """
    인쇄된 QR 이 **실제로 무엇을 담고 있는가.**

    디코더를 쓰지 않고 증명한다: 보관된 산출물의 PNG 가 Shaker URL 을 인코딩한
    렌더와 **바이트 단위로 같고**, 파트너 URL 을 인코딩한 렌더와는 다르다.
    QR 렌더는 결정적이므로 이 일치가 곧 payload 일치다.

    파트너 QR 과 섞이면 상자를 연 고객이 아이의 BREATHING 대신 설문 랜딩으로
    떨어진다. 종이는 회수할 수 없다.
    """
    oid = _buy_box(client, trace=TRACE_PARTNER)["order_id"]
    pkg = _sync(production_package.get_package, oid)
    art = _sync(shaker_qr_artifact.get, pkg.shaker_share_id)

    from backend.services import qr_service

    same = qr_service.render_qr(pkg.qr_share_url, kind="png", filename_hint=PET).data
    assert art.qr_png == same, "인쇄될 QR 이 Shaker 공유 주소를 담고 있지 않다"

    partner_url = qr_service.partner_share_url(HOSPITAL["partner_code"])
    other = qr_service.render_partner_qr(HOSPITAL["partner_code"], kind="png").data
    assert art.qr_png != other, "인쇄될 QR 이 파트너 획득 QR 과 같다"
    assert HOSPITAL["partner_code"] not in (pkg.qr_share_url or "")
    assert "soultrace" not in (pkg.qr_share_url or "").lower(), partner_url

    # 그리고 카드에 실제로 박히는 것이 그 산출물 바이트다.
    card = _sync(production_package.render_file, pkg, "qr_card")
    assert card.data[:8] == b"\x89PNG\r\n\x1a\n"

