"""
펫 소유권 — 편지 연결과 실물 주문에서 **같은 답**을 쓴다.

왜 중요한가: 주문은 pet_id 로 생산 패키지를 만들고, 그 패키지가 그 펫의 Shaker
공유로 QR 을 찍는다. 검사가 없으면 **남의 펫 QR 이 인쇄된** 실물이 내 주소로
배송된다 — 종이라서 되돌릴 수 없다.

Phase 12 에는 이 검사가 **없었다**: physical_checkout 은 pet_id 가 비어 있지
않은지만 봤고, letter/link 는 pet_id 를 그대로 저장했다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import orders_v1
from backend.services import (
    pet_ownership,
    pet_registry,
    physical_order,
    premium_purchase,
    soul_trace_import,
    soul_trace_letter,
)

from .conftest import ASGITestClient

OWNER = "owner@example.com"
STRANGER = "stranger@example.com"
PET = "pet_abc123"
TRACE_OWNER = "aaaaaaaa-1111-2222-3333-444444444444"
TRACE_STRANGER = "bbbbbbbb-1111-2222-3333-444444444444"
HANDOFF = "h" * 43
BODY = "안녕, 엄마 아빠. 나는 지금도 곁에 머물고 있어요."

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
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    pet_registry.__reset_for_tests()
    premium_purchase.__reset_for_tests()

    async def _fake_fetch(*, trace_id: str, handoff: str, consumed_by: str):
        return soul_trace_import.SourceLetter(
            letter_id=trace_id, letter_body=BODY, pet_name="고야"
        )

    monkeypatch.setattr(soul_trace_import, "fetch_source_letter", _fake_fetch)
    yield
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()
    pet_registry.__reset_for_tests()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(orders_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _register_pet_to(user: str, pet_id: str = PET):
    """public.pets 에 **인증된 소유자**로 등록한다."""
    content = pet_id.removeprefix("pet_")
    _sync(
        pet_registry.register,
        user_id=user,
        pet_id=pet_id,
        content_id=content,
        breathing_bucket="user-assets",
        breathing_object_path=f"{user}/{content}/idle_loop.mp4",
        source=pet_registry.SOURCE_OPS,
        # 스토리지에 실제 객체가 없다 — 이 테스트가 보는 것은 소유권이지
        # BREATHING 존재 여부가 아니다.
        verify=False,
    )


def _claim(client: ASGITestClient, user: str, trace: str) -> str:
    r = client.post(
        "/api/v1/orders/letter/claim",
        json={"trace_id": trace, "handoff": HANDOFF},
        headers=_auth(user),
    )
    assert r.status_code == 200, r.text
    return r.json()["letter_id"]


# ── 편지 → 펫 연결 ───────────────────────────────────────────────────────────


def test_claimed_letter_starts_with_no_pet(client: ASGITestClient):
    """Soul Trace 만 마친 사용자는 아직 펫이 없다 — 그래도 편지를 받을 수 있다."""
    lid = _claim(client, OWNER, TRACE_OWNER)
    assert _sync(soul_trace_letter.get_letter, lid).pet_id is None


def test_letter_links_only_to_a_pet_you_own(client: ASGITestClient):
    _register_pet_to(OWNER)
    lid = _claim(client, OWNER, TRACE_OWNER)

    r = client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(OWNER),
    )
    assert r.status_code == 200
    assert _sync(soul_trace_letter.get_letter, lid).pet_id == PET


def test_letter_cannot_be_linked_to_someone_elses_pet(client: ASGITestClient):
    """**핵심 회귀**: 남의 펫에 내 편지를 붙일 수 없다."""
    _register_pet_to(OWNER)
    lid = _claim(client, STRANGER, TRACE_STRANGER)

    r = client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(STRANGER),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PET_NOT_OWNED"
    assert _sync(soul_trace_letter.get_letter, lid).pet_id is None


def test_cannot_link_someone_elses_letter(client: ASGITestClient):
    """편지 소유권도 함께 본다 — 둘 중 하나만 보면 다른 쪽이 열린다."""
    _register_pet_to(STRANGER)
    lid = _claim(client, OWNER, TRACE_OWNER)

    r = client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(STRANGER),
    )
    # 남의 편지는 "없음"과 같은 답을 준다 — 존재 여부를 알려 주지 않는다.
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "LETTER_NOT_FOUND"


# ── 체크아웃 ─────────────────────────────────────────────────────────────────


def test_checkout_refuses_someone_elses_pet(client: ASGITestClient):
    """
    **핵심 회귀**: 남의 펫으로 실물을 주문할 수 없다.

    막지 않으면 생산 패키지가 남의 Shaker 공유로 QR 을 찍어 인쇄한다.
    """
    _register_pet_to(OWNER)
    lid = _claim(client, STRANGER, TRACE_STRANGER)

    r = client.post(
        "/api/v1/orders/checkout",
        json={
            "pet_id": PET, "product_type": "LETTER",
            "soul_trace_letter_id": lid, **SHIPPING,
        },
        headers=_auth(STRANGER),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PET_NOT_OWNED"
    # 주문 행이 생기지 않았다 — 거절은 흔적을 남기지 않는다.
    assert _sync(physical_order.list_pending, user_id=STRANGER) == []


def test_checkout_allows_your_own_pet(client: ASGITestClient):
    _register_pet_to(OWNER)
    lid = _claim(client, OWNER, TRACE_OWNER)
    client.post(
        "/api/v1/orders/letter/link-pet",
        json={"letter_id": lid, "pet_id": PET},
        headers=_auth(OWNER),
    )

    r = client.post(
        "/api/v1/orders/checkout",
        json={
            "pet_id": PET, "product_type": "LETTER",
            "soul_trace_letter_id": lid, **SHIPPING,
        },
        headers=_auth(OWNER),
    )
    assert r.status_code == 200
    assert r.json()["pet_id"] == PET


# ── 미등록 펫 (예전 신원으로 올라간 자산) ───────────────────────────────────


def test_unregistered_pet_falls_back_to_trust_on_first_use():
    """
    레지스트리에 없으면 TOFU 로 넘긴다 — 처음 쓴 사람이 소유자가 되고,
    그 뒤 다른 사용자는 거절된다.
    """
    _sync(pet_ownership.assert_owned, OWNER, "pet_unregistered")

    with pytest.raises(pet_ownership.PetOwnershipError) as e:
        _sync(pet_ownership.assert_owned, STRANGER, "pet_unregistered")
    assert e.value.status == 403


@pytest.mark.parametrize("uid,pid", [("", PET), (OWNER, ""), ("", "")])
def test_missing_ids_are_refused(uid: str, pid: str):
    with pytest.raises(pet_ownership.PetOwnershipError) as e:
        _sync(pet_ownership.assert_owned, uid, pid)
    assert e.value.code == "PET_REQUIRED"


def test_registry_failure_is_not_read_as_no_owner(monkeypatch: pytest.MonkeyPatch):
    """
    조회 실패를 "소유자 없음"으로 해석하면, 레지스트리가 잠깐 흔들리는 동안
    소유권 검사가 통째로 열린다.
    """
    async def _boom(_pet_id: str):
        raise pet_registry.PetRegistryError(
            "PET_REGISTRY_UNAVAILABLE", "down", status=503
        )

    monkeypatch.setattr(pet_registry, "owner_of", _boom)
    with pytest.raises(pet_ownership.PetOwnershipError) as e:
        _sync(pet_ownership.assert_owned, OWNER, PET)
    assert e.value.status == 503
