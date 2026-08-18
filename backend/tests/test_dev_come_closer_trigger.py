"""
개발 전용 COME_CLOSER 트리거 (Phase 15B).

핵심 계약:
  * 기본 꺼짐 — 플래그 없이는 라우트가 앱에 존재하지 않는다.
  * COME_CLOSER **한 건만** 제출한다. 레거시 4종은 제출조차 되지 않는다.
  * 크레딧을 차감하지 않는다 (credits_charged=0).
  * 완료 처리는 기존 웹훅 경로를 그대로 쓴다 — 별도 구현 없음.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.routers import dev_premium
from backend.services import generated_motions_service as gms
from backend.services.video_generation import SubmittedJob


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()
    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", "1")
    for k in ("VIDEO_PROVIDER", "VIDEO_PROVIDER_ACTION", "VIDEO_PROVIDER_COME_CLOSER"):
        monkeypatch.delenv(k, raising=False)
    yield
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()


class _Req:
    base_url = "https://hook.test/"


def _body(**kw):
    return dev_premium.ComeCloserRequest(
        user_id=kw.get("user_id", "u1"),
        pet_image_url=kw.get("pet_image_url", "https://cdn/cutout.png"),
        selected_place_id=kw.get("selected_place_id", "snow_forest"),
        pet_id=kw.get("pet_id", "p1"),
        keyframe_url=kw.get("keyframe_url"),
    )


@pytest.fixture
def spy(monkeypatch):
    calls = {"submits": [], "keyframes": 0, "deducts": []}

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        calls["submits"].append(
            {"provider": provider, "prompt": prompt, "image_url": image_url,
             "callback_url": callback_url}
        )
        return SubmittedJob(provider=provider, external_id="cc-1", model="MODEL-X")

    async def fake_keyframe(url, session_id):
        calls["keyframes"] += 1
        return "https://cdn/black_plate.jpg"

    async def fake_deduct(uid, cost):
        calls["deducts"].append((uid, cost))
        raise AssertionError("dev 트리거는 크레딧을 차감하면 안 된다")

    monkeypatch.setattr(dev_premium, "submit_generation", fake_submit)
    monkeypatch.setattr(dev_premium, "prepare_black_plate_keyframe", fake_keyframe)
    import backend.services.wallet_service as ws

    monkeypatch.setattr(ws, "deduct_credits", fake_deduct)
    return calls


def run(body=None):
    return asyncio.run(dev_premium.trigger_come_closer(_Req(), body or _body()))


# ── 기본 꺼짐 ───────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_DEV_PREMIUM_TRIGGER", raising=False)
    assert dev_premium.dev_trigger_enabled() is False


@pytest.mark.parametrize("v", ["0", "false", "no", ""])
def test_falsey_values_keep_it_off(monkeypatch, v):
    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", v)
    assert dev_premium.dev_trigger_enabled() is False


def test_route_is_mounted_behind_the_flag():
    """
    main.py 가 플래그 뒤에서만 라우터를 마운트하는지 소스로 확인한다.

    importlib.reload(backend.main) 로 검사하면 안 된다 — 재적재 시 load_dotenv 가
    실제 .env.local 을 프로세스 환경에 다시 밀어 넣어 다른 테스트를 오염시킨다
    (실제로 DEV_ACTION_SUBSET 이 새어 들어가 4종 제출 테스트가 깨졌다).
    """
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'ENABLE_DEV_PREMIUM_TRIGGER' in main_src
    idx = main_src.index("ENABLE_DEV_PREMIUM_TRIGGER")
    tail = main_src[idx : idx + 400]
    assert "dev_premium" in tail, "플래그 블록 안에서만 import/mount 되어야 한다"
    # 무조건 마운트하는 줄이 없어야 한다.
    assert "\napp.include_router(dev_premium" not in main_src


def test_endpoint_404s_when_disabled(monkeypatch, spy):
    from fastapi import HTTPException

    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", "0")
    with pytest.raises(HTTPException) as ei:
        run()
    assert ei.value.status_code == 404


# ── COME_CLOSER 만 제출 ─────────────────────────────────────────────────────


def test_submits_exactly_one_action(spy):
    res = run()
    assert len(spy["submits"]) == 1
    assert res.action_id == "COME_CLOSER"
    assert res.external_id == "cc-1"


def test_no_legacy_actions_submitted(spy):
    run()
    jobs = list(gms._MOCK_JOBS.values())
    assert [j.action_id for j in jobs] == ["COME_CLOSER"]
    for legacy in ("IDLE", "TOUCH", "VOICE", "NFC"):
        assert legacy not in [j.action_id for j in jobs]


def test_action_order_never_consulted(spy):
    """레거시 4종 순서 상수는 이 경로에서 쓰이지 않는다."""
    from pathlib import Path

    src = Path(dev_premium.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith(("#", "*", '"""'))
    )
    assert "ACTION_ORDER" not in code, "레거시 4종 순서 상수를 실제로 참조하면 안 된다"
    assert not hasattr(dev_premium, "ACTION_ORDER"), "import 되어 있으면 안 된다"


def test_no_credits_charged(spy):
    res = run()
    assert res.credits_charged == 0
    assert spy["deducts"] == [], "차감 함수가 호출되면 안 된다"
    sess = gms._MOCK_SESSIONS[res.session_id]
    assert sess["credits_charged"] == 0


# ── 파이프라인 재사용 ───────────────────────────────────────────────────────


def test_uses_black_plate_keyframe(spy):
    res = run()
    assert spy["keyframes"] == 1
    assert res.keyframe_url == "https://cdn/black_plate.jpg"
    assert spy["submits"][0]["image_url"] == "https://cdn/black_plate.jpg"


def test_existing_keyframe_is_reused_without_reflattening(spy):
    res = run(_body(keyframe_url="https://cdn/already_black.jpg"))
    assert spy["keyframes"] == 0, "이미 있으면 다시 만들지 않는다"
    assert res.keyframe_url == "https://cdn/already_black.jpg"


def test_callback_points_at_the_shared_webhook(spy):
    res = run()
    cb = spy["submits"][0]["callback_url"]
    assert "/api/v1/pet/generation-webhook" in cb
    assert f"session_id={res.session_id}" in cb
    assert res.webhook_path == "/api/v1/pet/generation-webhook"


def test_prompt_is_the_come_closer_prompt(spy):
    from backend.services.luma_prompts import ACTION_COMMON_CONSTRAINT, COME_CLOSER_CONSTRAINT

    run()
    p = spy["submits"][0]["prompt"]
    assert COME_CLOSER_CONSTRAINT in p
    assert ACTION_COMMON_CONSTRAINT not in p


def test_provider_resolution_is_reused(spy, monkeypatch):
    # 같은 키로 두 번 부르면 이제 멱등 처리돼 두 번째는 제출되지 않는다.
    # 이 테스트의 의도는 "프로바이더 해석이 env 를 따르는가" 이므로 두 번째는
    # 다른 pet_id(= 다른 생성 키)로 부른다.
    assert run(_body(pet_id="p1")).provider == "luma"
    monkeypatch.setenv("VIDEO_PROVIDER_COME_CLOSER", "wan_turbo")
    res = run(_body(pet_id="p2"))
    assert res.provider == "wan_turbo"
    assert res.generated is True
    assert res.provider_model == "MODEL-X"


def test_job_is_registered_for_webhook_resolution(spy):
    res = run()
    job = gms._MOCK_JOBS["cc-1"]
    assert job.session_id == res.session_id
    assert job.action_id == "COME_CLOSER"
    assert job.attempt == 1
    assert job.provider == "luma"


def test_session_completes_with_single_action(spy):
    """세션 상태 계산은 실제 제출된 액션 집합을 쓴다 — 1/1 이면 completed."""
    from backend.models.hybrid_business import MotionJobStatus, SessionStatus

    run()
    job = gms._MOCK_JOBS["cc-1"]
    job.status = MotionJobStatus.completed
    assert gms.compute_session_status([job]) == SessionStatus.completed


# ── 결과 조회 ───────────────────────────────────────────────────────────────


def test_get_returns_null_before_promotion(spy):
    run()
    r = asyncio.run(dev_premium.get_come_closer("u1", place_id="snow_forest", pet_id="p1"))
    assert r["come_closer_video_url"] is None
    assert r["ready"] is False


def test_get_returns_url_after_promotion(spy):
    from backend.models.hybrid_business import GeneratedMotion

    run()
    gms._MOCK_MOTIONS[gms._motion_key("u1", "p1", "snow_forest", "COME_CLOSER")] = GeneratedMotion(
        user_id="u1", pet_id="p1", place_id="snow_forest",
        action_id="COME_CLOSER", video_url="https://cdn/cc.mp4",
    )
    r = asyncio.run(dev_premium.get_come_closer("u1", place_id="snow_forest", pet_id="p1"))
    assert r["come_closer_video_url"] == "https://cdn/cc.mp4"
    assert r["ready"] is True


# ── 입력 검증 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["user_id", "pet_image_url"])
def test_missing_required_fields_rejected(spy, field):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        run(_body(**{field: "  "}))
    assert ei.value.status_code == 400
