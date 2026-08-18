"""
COME_CLOSER 자동 생성 — **서버측 멱등성**.

클라이언트 가드(인플라이트 맵, StrictMode 방어)는 편의일 뿐이다. 새로고침,
다른 탭, 다른 기기에서 오는 중복 호출은 여기서만 막을 수 있다. 그래서 최종
권위는 서버이고, 이 파일이 그것을 검증한다.

계약:
  * canonical 이 이미 있으면 프로바이더를 부르지 않는다 (status=ready)
  * 같은 키로 진행 중인 작업이 있으면 새로 제출하지 않는다 (generated=False)
  * 키가 다르면(새 pet 또는 새 place) 각각 1회씩 제출한다
  * 레거시 4종 계약은 건드리지 않는다

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.models.hybrid_business import MotionJobStatus
from backend.routers import dev_premium
from backend.scenarios.pet_scenarios import ACTION_ORDER
from backend.services import generated_motions_service as gms
from backend.services.video_generation import SubmittedJob


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    for d in (gms._MOCK_JOBS, gms._MOCK_SESSIONS, gms._MOCK_MOTIONS, gms._LUMA_INDEX):
        d.clear()
    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", "1")
    for k in ("VIDEO_PROVIDER", "VIDEO_PROVIDER_ACTION", "VIDEO_PROVIDER_COME_CLOSER"):
        monkeypatch.delenv(k, raising=False)
    yield
    for d in (gms._MOCK_JOBS, gms._MOCK_SESSIONS, gms._MOCK_MOTIONS, gms._LUMA_INDEX):
        d.clear()


class _Req:
    base_url = "https://hook.test/"


@pytest.fixture
def spy(monkeypatch):
    calls = {"submits": 0, "keyframes": 0}
    seq = iter(range(1, 999))

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        calls["submits"] += 1
        return SubmittedJob(provider=provider, external_id=f"cc-{next(seq)}", model="M")

    async def fake_keyframe(url, session_id):
        calls["keyframes"] += 1
        return "https://cdn/black_plate.jpg"

    monkeypatch.setattr(dev_premium, "submit_generation", fake_submit)
    monkeypatch.setattr(dev_premium, "prepare_black_plate_keyframe", fake_keyframe)
    return calls


def call(pet_id="p1", place="snow_forest", user="u1"):
    body = dev_premium.ComeCloserRequest(
        user_id=user,
        pet_image_url="https://cdn/cutout.png",
        selected_place_id=place,
        pet_id=pet_id,
    )
    return asyncio.run(dev_premium.trigger_come_closer(_Req(), body))


# ── A. 최초 1회 ─────────────────────────────────────────────────────────────


def test_first_call_submits_exactly_once(spy):
    r = call()
    assert r.generated is True
    assert r.status == "processing"
    assert spy["submits"] == 1


# ── B/C. 중복 호출 — 진행 중이면 재제출 없음 ────────────────────────────────


def test_second_call_while_processing_does_not_resubmit(spy):
    call()
    r = call()
    assert spy["submits"] == 1, "프로바이더는 한 번만 불려야 한다"
    assert r.generated is False
    assert r.status == "processing"


def test_many_concurrent_style_calls_still_one_submit(spy):
    for _ in range(5):
        call()
    assert spy["submits"] == 1


def test_idempotent_hit_does_not_build_another_keyframe(spy):
    call()
    call()
    assert spy["keyframes"] == 1, "멱등 히트는 키프레임도 다시 만들지 않는다"


def test_idempotent_hit_creates_no_extra_session(spy):
    call()
    n = len(gms._MOCK_SESSIONS)
    call()
    assert len(gms._MOCK_SESSIONS) == n, "고아 세션을 만들지 않는다"


# ── D. canonical 이 이미 있으면 생성 안 함 ──────────────────────────────────


def test_existing_canonical_short_circuits(spy):
    call()
    job = next(iter(gms._MOCK_JOBS.values()))
    asyncio.run(gms._record_promoted_motion(job, "snow_forest", "https://cdn/CC.mp4"))

    r = call()
    assert r.status == "ready"
    assert r.generated is False
    assert r.come_closer_video_url == "https://cdn/CC.mp4"
    assert spy["submits"] == 1, "canonical 이 있으면 프로바이더를 부르지 않는다"


# ── E/F. 키가 다르면 각각 1회 ───────────────────────────────────────────────


def test_new_pet_id_is_a_new_key(spy):
    call(pet_id="pet_old")
    call(pet_id="pet_new")
    assert spy["submits"] == 2, "새 펫은 새로 생성해야 한다"


def test_new_pet_does_not_inherit_old_canonical(spy):
    call(pet_id="pet_old")
    job = next(iter(gms._MOCK_JOBS.values()))
    asyncio.run(gms._record_promoted_motion(job, "snow_forest", "https://cdn/OLD.mp4"))

    r = call(pet_id="pet_new")
    assert r.generated is True, "예전 펫의 자산을 물려받으면 안 된다"
    assert r.come_closer_video_url is None
    assert spy["submits"] == 2


# ── G. place 별 키 ──────────────────────────────────────────────────────────


def test_place_is_NOT_part_of_the_key(spy):
    """B: 테마를 바꿔 가며 눌러도 추가 생성 0건."""
    call(place="snow_forest")
    call(place="celestial")
    call(place="fresh_forest")
    call(place="custom_photo_bg")
    assert spy["submits"] == 1, "테마 전환은 프로바이더 호출을 유발하면 안 된다"


def test_response_reports_theme_independent_place(spy):
    r = call(place="celestial")
    assert r.place_id == "any", "저장 키의 place 는 센티널 하나로 접힌다"


# ── A. 기본 테마 fresh_forest 에서 자동 생성 ────────────────────────────────


def test_default_theme_fresh_forest_generates_exactly_once(spy):
    """
    A: fresh_forest 는 앱 기본 테마(DEFAULT_THEME_ID=8)다. 이제 place 를 아예
    보지 않으므로 백엔드 PLACES 에 없어도 그냥 동작한다.
    """
    r = call(place="fresh_forest", pet_id="pet_new")
    assert r.generated is True
    assert r.status == "processing"
    assert r.place_id == "any"
    assert spy["submits"] == 1


def test_place_can_be_omitted_entirely(spy):
    """테마 독립이므로 place 없이도 생성된다."""
    r = call(place=None, pet_id="pet_new")
    assert r.generated is True
    assert spy["submits"] == 1


def test_fresh_forest_second_call_is_idempotent(spy):
    """B: 새로고침/StrictMode 로 다시 불려도 제출은 1회."""
    call(place="fresh_forest", pet_id="pet_new")
    r = call(place="fresh_forest", pet_id="pet_new")
    assert r.generated is False
    assert spy["submits"] == 1


def test_fresh_forest_canonical_short_circuits(spy):
    """C: canonical 이 있으면 새로 만들지 않는다."""
    call(place="fresh_forest", pet_id="pet_new")
    job = next(iter(gms._MOCK_JOBS.values()))
    asyncio.run(gms._record_promoted_motion(job, "fresh_forest", "https://cdn/FF.mp4"))
    r = call(place="fresh_forest", pet_id="pet_new")
    assert r.status == "ready"
    assert r.come_closer_video_url == "https://cdn/FF.mp4"
    assert spy["submits"] == 1


def test_storage_name_has_no_place(spy):
    from backend.scenarios.pet_scenarios import storage_object_name

    assert storage_object_name("web_fresh_forest", "COME_CLOSER") == "COME_CLOSER.mp4"
    assert storage_object_name("01_snow_forest", "COME_CLOSER") == "COME_CLOSER.mp4"


# ── E. 커스텀 배경은 여전히 미지원 — 깨끗한 400 ─────────────────────────────


def test_custom_background_reuses_the_same_asset(spy):
    """D: 커스텀 배경도 같은 펫이면 같은 클립을 그대로 쓴다."""
    call(place="snow_forest", pet_id="pet_new")
    job = next(iter(gms._MOCK_JOBS.values()))
    asyncio.run(gms._record_promoted_motion(job, "any", "https://cdn/CC.mp4"))

    r = call(place="custom_photo_bg", pet_id="pet_new")
    assert r.status == "ready"
    assert r.come_closer_video_url == "https://cdn/CC.mp4"
    assert spy["submits"] == 1, "커스텀 배경이라고 새로 만들면 안 된다"


# ── D. 레거시 기기/NFC 경계 불변 ────────────────────────────────────────────


def test_legacy_places_registry_untouched(spy):
    """PLACES 는 NFC 슬롯 1~10 그대로여야 한다 — 웹 전용 장소가 섞이면 안 된다."""
    from backend.scenarios.pet_scenarios import PLACES, WEB_ONLY_PLACES

    assert len(PLACES) == 10
    assert sorted(p["slot"] for p in PLACES.values()) == list(range(1, 11))
    assert "fresh_forest" not in [p["theme_key"] for p in PLACES.values()]
    assert all("slot" not in p for p in WEB_ONLY_PLACES.values()), (
        "웹 전용 장소에 slot 이 생기면 NFC 매핑을 침범한다"
    )


def test_legacy_resolver_still_rejects_web_only_places(spy):
    """
    레거시 해석기는 NFC 슬롯 번호까지 받는 기기 경로다. 여기에 웹 전용 장소가
    통과하면 기기 매핑이 오염된다.
    """
    from backend.scenarios.pet_scenarios import resolve_place_id

    with pytest.raises(ValueError):
        resolve_place_id("fresh_forest")
    assert resolve_place_id("snow_forest") == "01_snow_forest"
    assert resolve_place_id("1") == "01_snow_forest", "슬롯 번호 해석 불변"


def test_web_resolver_accepts_both_but_slots_stay_legacy(spy):
    from backend.scenarios.pet_scenarios import resolve_generation_place_id

    assert resolve_generation_place_id("fresh_forest") == "web_fresh_forest"
    assert resolve_generation_place_id("snow_forest") == "01_snow_forest"
    assert resolve_generation_place_id("1") == "01_snow_forest"


# ── I. 실패는 진행 중이 아니다 (재시도 정책은 기존 경로가 담당) ─────────────


@pytest.mark.parametrize("terminal", [MotionJobStatus.failed, MotionJobStatus.rejected])
def test_terminal_job_is_not_treated_as_pending(spy, terminal):
    call()
    for j in gms._MOCK_JOBS.values():
        j.status = terminal
    # 종료 상태는 '진행 중'이 아니다 — 다음 명시적 호출은 다시 제출할 수 있다.
    # (자동 재제출 루프를 막는 건 클라이언트 attempted 가드 쪽 책임이다.)
    r = call()
    assert r.generated is True
    assert spy["submits"] == 2


def test_completed_job_without_canonical_is_not_resubmitted_blindly(spy):
    """completed = 승격까지 끝났다는 뜻 — canonical 검사가 먼저 걸러야 한다."""
    call()
    job = next(iter(gms._MOCK_JOBS.values()))
    job.status = MotionJobStatus.completed
    asyncio.run(gms._record_promoted_motion(job, "snow_forest", "https://cdn/CC.mp4"))
    r = call()
    assert r.status == "ready"
    assert spy["submits"] == 1


# ── 레거시 불변 ─────────────────────────────────────────────────────────────


def test_legacy_contract_untouched(spy):
    from backend.scenarios.pet_scenarios import ACTION_ORDER, CREDIT_COST_PER_PLACE_SET

    call()
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")
    assert "COME_CLOSER" not in ACTION_ORDER
    assert CREDIT_COST_PER_PLACE_SET == 4
    submitted = [j.action_id for j in gms._MOCK_JOBS.values()]
    assert submitted == ["COME_CLOSER"], "레거시 4종은 제출조차 되면 안 된다"


def test_no_credits_charged(spy):
    r = call()
    assert r.credits_charged == 0
    assert all(s["credits_charged"] == 0 for s in gms._MOCK_SESSIONS.values())


# ── E. 레거시 장소별 자산 호환 ──────────────────────────────────────────────


def test_legacy_place_scoped_asset_is_reused_not_regenerated(spy):
    """
    테마 독립 이전에 만들어진 행은 place_id 가 'snow_forest' 같은 실제 테마다.
    조회가 place 를 무시하므로 그 행이 그대로 잡히고, 재생성 없이 재사용된다.
    (자산을 지우거나 옮기지 않는다 — 행은 그 자리에 그대로 둔다.)
    """
    asyncio.run(
        gms._record_promoted_motion(
            gms.MotionJobRow(
                session_id="legacy", user_id="u1", pet_id="p1",
                place_key="01_snow_forest", action_id="COME_CLOSER",
                luma_generation_id="legacy-1",
            ),
            "snow_forest",              # ← 예전 방식: 실제 테마가 place_id
            "https://cdn/LEGACY_CC.mp4",
        )
    )
    r = call(place="fresh_forest")      # 전혀 다른 테마로 접근
    assert r.status == "ready"
    assert r.come_closer_video_url == "https://cdn/LEGACY_CC.mp4"
    assert spy["submits"] == 0, "예전 자산이 있는데 다시 만들면 돈이 두 번 나간다"


def test_legacy_asset_reused_from_every_theme(spy):
    asyncio.run(
        gms._record_promoted_motion(
            gms.MotionJobRow(
                session_id="legacy", user_id="u1", pet_id="p1",
                place_key="01_snow_forest", action_id="COME_CLOSER",
                luma_generation_id="legacy-1",
            ),
            "snow_forest", "https://cdn/LEGACY_CC.mp4",
        )
    )
    for place in ("snow_forest", "celestial", "fresh_forest", "custom_photo_bg", None):
        assert call(place=place).come_closer_video_url == "https://cdn/LEGACY_CC.mp4"
    assert spy["submits"] == 0


# ── F. 다른 펫은 새로 생성 ──────────────────────────────────────────────────


def test_different_pet_still_generates_its_own(spy):
    call(pet_id="pet_a")
    call(pet_id="pet_b")
    assert spy["submits"] == 2, "펫이 다르면 각각 하나씩"


def test_other_pets_asset_is_never_reused(spy):
    call(pet_id="pet_a")
    job = next(iter(gms._MOCK_JOBS.values()))
    asyncio.run(gms._record_promoted_motion(job, "any", "https://cdn/A.mp4"))

    r = call(pet_id="pet_b")
    assert r.generated is True
    assert r.come_closer_video_url is None
    assert spy["submits"] == 2


# ── G. 레거시 4종은 여전히 장소별 ───────────────────────────────────────────


def test_legacy_actions_remain_place_scoped():
    from backend.scenarios.pet_scenarios import is_theme_independent_action, storage_object_name

    for a in ACTION_ORDER:
        assert is_theme_independent_action(a) is False, f"{a} 는 장소별이어야 한다"
        assert storage_object_name("01_snow_forest", a) == f"SNOW_FOREST_{a}.mp4"
        assert storage_object_name("02_celestial", a) == f"CELESTIAL_{a}.mp4"


def test_legacy_lookup_still_filters_by_place():
    """/device/sync 경로는 장소별 조회 그대로여야 한다."""
    for a in ACTION_ORDER:
        asyncio.run(
            gms._record_promoted_motion(
                gms.MotionJobRow(
                    session_id="s", user_id="u9", pet_id="p9",
                    place_key="01_snow_forest", action_id=a, luma_generation_id=f"x-{a}",
                ),
                "snow_forest", f"https://cdn/{a}.mp4",
            )
        )
    snow = asyncio.run(gms.list_motions_for_place("u9", "snow_forest", "p9"))
    other = asyncio.run(gms.list_motions_for_place("u9", "celestial", "p9"))
    assert len(snow) == 4
    assert other == [], "다른 테마에서 레거시 자산이 보이면 기기 동작이 깨진다"


# ── H. ACTION_ORDER 불변 ────────────────────────────────────────────────────


def test_action_order_unchanged_again():
    from backend.scenarios.pet_scenarios import CREDIT_COST_PER_PLACE_SET

    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")
    assert "COME_CLOSER" not in ACTION_ORDER
    assert CREDIT_COST_PER_PLACE_SET == 4


# ── I. 후보/검증/승격/재시도 동작 유지 ──────────────────────────────────────


def test_candidate_validation_promotion_still_work(spy):
    """테마 독립화가 후보→검증→승격 경로를 건드리지 않았는지 확인."""
    call(pet_id="pet_x")
    job = next(iter(gms._MOCK_JOBS.values()))

    accepted, meta = gms.validate_candidate(job, b"bytes")
    assert accepted is True and meta.get("gate_enforced") is False

    asyncio.run(gms._record_promoted_motion(job, "any", "https://cdn/X.mp4"))
    assert job.status == gms.MotionJobStatus.completed
    assert job.promoted_at is not None
    assert gms.MAX_ACTION_ATTEMPTS == 2, "재시도 정책 불변"
