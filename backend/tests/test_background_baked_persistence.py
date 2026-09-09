"""
`background_baked` 가 **생성에서 QR 재생까지 살아남는가.** (Phase 27)

── 이 값이 어디서 죽었는가 ─────────────────────────────────────────────────
생성 응답에는 있었지만 **어느 테이블에도 없었다.** 브라우저 sessionStorage 한
곳에서만 살았고, QR 재생(Shaker)은 그 사실을 알 방법이 없었다 — shaker-api.ts
가 `body.background_baked` 를 읽고 있었는데 서버는 그 필드를 보낸 적이 없다.

그래서 배경이 이미 들어 있는 영상을 QR 로 열면 블랙키 제거가 걸려, 장면의
어두운 픽셀(그림자·나무 그늘)이 뚫린 채 재생됐다.

── 브라우저에게 묻지 않는다 ────────────────────────────────────────────────
가장 쉬운 길은 등록 요청에 플래그를 실어 받는 것이었다. 그러면 브라우저가
자산에 대한 사실을 주장하게 되고, 틀렸을 때 재생이 조용히 깨진다.

그럴 필요가 없다. 구운 생성은 전부 scene_generation_jobs 를 거치고(유료 제출의
유일한 통로다) 완료 시점에 video_url 이 남는다. 등록하려는 객체가 그 기록과
같은 객체인지 보면 된다 — 서명이 아니라 (bucket, object_path) 로.

── 절대 추측하지 않는다 ────────────────────────────────────────────────────
기록이 없으면 false 다. 추측으로 true 를 적으면 멀쩡한 레거시 영상이 검은
사각형인 채로 재생된다. 모르는 쪽의 대가가 훨씬 싸다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import shaker_v1
from backend.services import pet_registry, scene_generation_jobs as jobs
from backend.services import shaker_rate_limit, shaker_share

from .conftest import ASGITestClient

OWNER = "owner@example.com"
CONTENT = "c-abc-123"
PET = f"pet_{CONTENT}"
BUCKET = "pet-assets"
OBJ = f"{OWNER}/{CONTENT}/idle_loop.mp4"
#: 업로드가 돌려주는 모양 — 서명이 붙는다. 서명은 매번 다르다.
SIGNED = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?token=aaa"
SIGNED_LATER = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?token=zzz"


def _sync(afn, *a, **k):
    return anyio.run(functools.partial(afn, *a, **k))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("SHAKER_PROXY_ASSETS", "0")  # 프록시를 끄면 URL 을 직접 본다
    pet_registry.__reset_for_tests()
    jobs.__reset_for_tests()
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    yield
    pet_registry.__reset_for_tests()
    jobs.__reset_for_tests()
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


def _record_baked_generation(video_url: str = SIGNED, *, content_id: str = CONTENT) -> None:
    """구운 생성이 완료된 상태를 만든다 — 라우터가 실제로 남기는 기록 그대로."""
    _sync(
        jobs.reserve,
        user_id=OWNER,
        scene_id="scene-1",
        behavior="IDLE",
        content_id=content_id,
    )
    _sync(
        jobs.mark_submitted,
        user_id=OWNER,
        scene_id="scene-1",
        behavior="IDLE",
        provider="luma",
        provider_job_id="job-1",
    )
    _sync(
        jobs.mark_completed,
        user_id=OWNER,
        scene_id="scene-1",
        behavior="IDLE",
        video_url=video_url,
    )


def _register(breathing_url: str = SIGNED_LATER, *, pet_id: str = PET):
    return _sync(
        pet_registry.register,
        user_id=OWNER,
        pet_id=pet_id,
        content_id=CONTENT,
        breathing_url=breathing_url,
        verify=False,  # 스토리지가 없는 환경
    )


# ── 서버가 자기 기록으로 판정한다 ────────────────────────────────────────────


def test_registration_marks_baked_from_our_own_generation_record():
    """
    **핵심.** 등록 요청은 플래그를 싣지 않는다. 그런데도 true 가 적힌다 —
    서버가 자기 생성 기록에서 같은 객체를 찾았기 때문이다.
    """
    _record_baked_generation()
    pet = _register()
    assert pet.background_baked is True


def test_signature_differences_do_not_break_the_match():
    """
    저장된 값과 등록 시 받은 값은 **다른 서명**이다(서명은 매번 새로 만든다).
    문자열로 비교하면 언제나 실패한다 — 경로로 비교해야 한다.
    """
    _record_baked_generation(SIGNED)
    pet = _register(SIGNED_LATER)
    assert SIGNED != SIGNED_LATER
    assert pet.background_baked is True


def test_no_generation_record_means_legacy():
    """기록이 없으면 레거시다. 추측하지 않는다."""
    pet = _register()
    assert pet.background_baked is False


def test_a_different_object_does_not_count():
    """
    같은 콘텐츠라도 **다른 객체**면 아니다. 배경을 바꿔 다시 만들면 예전 구운
    영상 기록이 남아 있는데, 지금 등록하는 것은 그것이 아닐 수 있다.
    """
    _record_baked_generation(
        f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OWNER}/{CONTENT}/other.mp4"
    )
    pet = _register(SIGNED_LATER)
    assert pet.background_baked is False


def test_incomplete_job_does_not_count():
    """제출만 되고 끝나지 않은 작업은 근거가 아니다."""
    _sync(jobs.reserve, user_id=OWNER, scene_id="scene-1", behavior="IDLE", content_id=CONTENT)
    _sync(
        jobs.mark_submitted,
        user_id=OWNER, scene_id="scene-1", behavior="IDLE",
        provider="luma", provider_job_id="job-1",
    )
    assert _register().background_baked is False


def test_another_users_generation_does_not_leak():
    """남의 생성 기록으로 내 펫이 구운 것이 되지 않는다."""
    _sync(
        jobs.reserve,
        user_id="someone@else.com", scene_id="scene-x", behavior="IDLE", content_id=CONTENT,
    )
    _sync(
        jobs.mark_submitted,
        user_id="someone@else.com", scene_id="scene-x", behavior="IDLE",
        provider="luma", provider_job_id="j",
    )
    _sync(
        jobs.mark_completed,
        user_id="someone@else.com", scene_id="scene-x", behavior="IDLE", video_url=SIGNED,
    )
    assert _register().background_baked is False


def test_lookup_failure_registers_as_legacy(monkeypatch):
    """
    구움 판정이 터져도 **등록은 성공해야 한다.** 등록되지 않은 펫은 운영에서
    보이지 않고 QR 도 붙지 않는다 — 배경 표시 하나와 바꿀 수 있는 것이 아니다.
    """
    async def boom(**_kw):
        raise RuntimeError("scene_generation_jobs down")

    monkeypatch.setattr(jobs, "produced_baked_object", boom)
    pet = _register()
    assert pet.pet_id == PET
    assert pet.background_baked is False


# ── 공유(QR) 로 복제된다 ─────────────────────────────────────────────────────


def test_share_copies_the_flag_at_issue_time():
    """
    공유는 **종이에 인쇄되어** 다른 어떤 행보다 오래 산다. 이 표가 이미
    breathing_url·bucket·object_path 를 복제하는 것과 같은 이유로 복제한다.
    """
    _record_baked_generation()
    _register()
    _sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER
    )
    rec = _sync(shaker_share.resolve_share, token)
    assert rec.background_baked is True


def test_share_of_a_legacy_pet_stays_legacy():
    _register()
    _sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER
    )
    assert _sync(shaker_share.resolve_share, token).background_baked is False


def test_share_for_an_unregistered_pet_is_legacy():
    """펫 행이 없으면 근거도 없다 — 레거시다. 발급 자체는 막지 않는다."""
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id="pet_unknown", breathing_url=SIGNED_LATER,
    )
    assert _sync(shaker_share.resolve_share, token).background_baked is False


# ── 라운드트립: 생성 → 행 → shaker_v1 응답 ──────────────────────────────────


def test_round_trip_baked_generation_reaches_the_qr_response(client):
    """
    **이번 단계가 사려는 결과다.** 생성에서 QR 응답까지 한 번에 확인한다.
    """
    _record_baked_generation()
    _register()
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER, pet_name="고야",
    )

    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["background_baked"] is True
    assert body["pet_id"] == PET


def test_round_trip_legacy_pet_reports_false(client):
    _register()
    _sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER
    )
    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()
    assert body["background_baked"] is False


def test_pre_migration_shaped_record_reports_false(client):
    """
    컬럼이 없던 시절에 만들어진 행 — 응답에 필드가 아예 없다. 기존 인쇄물이
    전부 여기 해당하므로, 이것이 false 로 읽히지 않으면 멀쩡히 재생되던 QR 이
    한꺼번에 깨진다.
    """
    _sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER
    )
    # 저장된 행에서 컬럼을 지운다 = 마이그레이션 이전 모양.
    for row in shaker_share._MOCK_SHARES.values():
        row.pop("background_baked", None)

    rec = _sync(shaker_share.resolve_share, token)
    assert rec.background_baked is False

    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()
    assert body["background_baked"] is False


def test_pre_migration_pet_row_reports_false():
    """펫 행 쪽도 같다 — 컬럼이 없으면 레거시."""
    _record_baked_generation()
    _register()
    for row in pet_registry._MOCK_PETS.values():
        row.pop("background_baked", None)
    assert _sync(pet_registry.get, PET).background_baked is False


# ── 백필하지 않는다 ──────────────────────────────────────────────────────────


def test_migration_declares_a_false_default_and_no_backfill():
    """
    default false 가 곧 "오늘과 같은 동작"이다. 어떤 형태로든 기존 행을
    true 로 바꾸는 문장이 있으면 안 된다.
    """
    import pathlib

    sql = pathlib.Path(
        "supabase/migrations/20260922000000_background_baked.sql"
    ).read_text()

    assert sql.count("add column if not exists background_baked boolean not null default false") == 2
    lowered = sql.lower()
    assert "update public.pets" not in lowered, "백필 문장이 있다"
    assert "update public.shaker_shares" not in lowered, "백필 문장이 있다"
    assert "default true" not in lowered


def test_public_response_gained_only_a_boolean(client):
    """
    공개 응답에 늘어난 것이 재생 방식 불리언 하나뿐인지 — 소유자·구독·주문·
    프로바이더에 대해서는 아무것도 말하지 않아야 한다.
    """
    _record_baked_generation()
    _register()
    _sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=SIGNED_LATER
    )
    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()
    assert isinstance(body["background_baked"], bool)
    assert OWNER not in str(body)
