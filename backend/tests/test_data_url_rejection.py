"""
data: URL 은 제출 **전에** 명확히 거부한다.

배경: 웹 플로우는 누끼를 save_to_storage=false 로 뽑아 브라우저 안에서만
data: URL 로 들고 있었다. 그 값이 COME_CLOSER 제출로 흘러가 httpx 가 실패했고,
결과는 stage="download" — 마치 스토리지/네트워크 장애처럼 보였다. 실제 원인은
"누끼가 애초에 업로드된 적 없음"이다.

계약:
  * 스킴 검사는 세션 생성·프로바이더 제출보다 먼저 일어난다
  * 고아 세션을 남기지 않는다
  * 프로바이더는 한 번도 호출되지 않는다
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.routers import dev_premium
from backend.services import generated_motions_service as gms
from backend.services.credit_keyframe import KeyframePreparationError, is_remote_asset_url


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()
    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", "1")
    yield
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()


class _Req:
    base_url = "https://hook.test/"


# ── 스킴 판정 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://cdn.example/dog.png", True),
        ("http://cdn.example/dog.png", True),
        ("HTTPS://CDN.EXAMPLE/DOG.PNG", True),
        ("  https://cdn.example/dog.png  ", True),
        ("data:image/png;base64,iVBORw0KGgo=", False),
        ("blob:https://app/uuid", False),
        ("file:///tmp/dog.png", False),
        ("/relative/dog.png", False),
        ("", False),
    ],
)
def test_remote_url_detection(url, ok):
    assert is_remote_asset_url(url) is ok


# ── 키프레임 준비: download 가 아니라 invalid_url ────────────────────────────


def test_keyframe_rejects_data_url_before_download():
    from backend.services.credit_keyframe import prepare_black_plate_keyframe

    with pytest.raises(KeyframePreparationError) as ei:
        asyncio.run(prepare_black_plate_keyframe("data:image/png;base64,iVBORw0K", "s1"))
    assert ei.value.stage == "invalid_url", "download 로 뭉개면 원인이 가려진다"
    assert "http" in str(ei.value).lower()


# ── dev 엔드포인트: 400 + 부작용 없음 ───────────────────────────────────────


def _call(pet_image_url: str):
    body = dev_premium.ComeCloserRequest(
        user_id="u1",
        pet_image_url=pet_image_url,
        selected_place_id="snow_forest",
        pet_id="p1",
    )
    return asyncio.run(dev_premium.trigger_come_closer(_Req(), body))


def test_dev_endpoint_rejects_data_url_with_clear_400():
    with pytest.raises(HTTPException) as ei:
        _call("data:image/png;base64,iVBORw0KGgo=")
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "PET_IMAGE_URL_NOT_REMOTE"
    assert ei.value.detail["got_scheme"] == "data"


def test_rejection_leaves_no_orphan_session():
    with pytest.raises(HTTPException):
        _call("data:image/png;base64,iVBORw0KGgo=")
    assert gms._MOCK_SESSIONS == {}, "세션 생성 전에 걸러야 한다"
    assert gms._MOCK_JOBS == {}


def test_provider_is_never_called_for_data_url(monkeypatch):
    calls = []

    async def _boom(*a, **kw):
        calls.append(a)
        raise AssertionError("프로바이더가 호출되면 안 된다 — 유료 제출이다")

    monkeypatch.setattr(dev_premium, "submit_generation", _boom)
    with pytest.raises(HTTPException):
        _call("data:image/png;base64,iVBORw0KGgo=")
    assert calls == []
