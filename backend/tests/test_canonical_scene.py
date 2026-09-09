"""
정본 장면 (Phase 19) — 배경이 구워진 생성.

이 파일이 지키는 계약:
  * 장면이 있으면 **그 그림 자체**가 프로바이더 키프레임이다 (펫을 두 번 그리지 않는다).
  * 장면이 없으면 예전 단색 판 그대로다 (레거시 자산이 계속 동작한다).
  * 여섯 행동이 **같은 seam** 을 지난다 — 배경 처리에 행동별 분기가 없다.
  * 프롬프트가 보이드 요구에서 배경 보존 요구로 바뀐다.
  * 같은 장면 + 같은 행동은 **두 번 과금하지 않는다.**
"""

from __future__ import annotations

import functools
import io

import anyio
import pytest
from PIL import Image

from backend.services import scene_generation_jobs, scene_input
from backend.services.luma_idle_templates import build_idle_variant_prompt
from backend.services.luma_keyframe import (
    BG_BLACK,
    build_keyframe_jpeg,
    flatten_rgba_to_jpeg_bytes,
    scene_to_jpeg_bytes,
)
from backend.services.luma_prompts import (
    SCENE_BACKGROUND_SENTENCE,
    VOID_BACKGROUND_SENTENCE,
    bake_scene_background,
)
from backend.services.prompt_factory import build_scenario_prompt

#: 요구사항이 이름을 댄 행동들. 하나라도 빠지면 그 행동만 배경을 잃는다.
BEHAVIORS = (
    "IDLE",
    "BLINKING",
    "EAR_TWITCHING",
    "HEAD_TILTING",
    "TAIL_WAGGING",
    "COME_CLOSER",
)

PLACE = "01_snow_forest"


def _sync(afn, *a, **k):
    return anyio.run(functools.partial(afn, *a, **k))


def _cutout_png() -> bytes:
    """가운데에 불투명 사각형이 있는 투명 PNG — 누끼 대역."""
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    for x in range(60, 140):
        for y in range(60, 140):
            im.putpixel((x, y), (200, 150, 100, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _scene_png(color=(20, 120, 40)) -> bytes:
    """배경이 이미 들어 있는 불투명 장면 이미지."""
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    scene_generation_jobs.__reset_for_tests()
    yield
    scene_generation_jobs.__reset_for_tests()


# ── 키프레임 seam ────────────────────────────────────────────────────────────


def test_scene_becomes_the_keyframe_unchanged():
    """
    장면이 있으면 **그 그림**이 키프레임이다.

    누끼를 다시 얹으면 펫이 두 번 그려진다. 크기가 장면 크기와 같은지로 그것을
    확인한다 — 누끼(200×200)를 평탄화했다면 크기가 다르게 나온다.
    """
    scene = _scene_png()
    out = build_keyframe_jpeg(_cutout_png(), scene_bytes=scene)
    im = Image.open(io.BytesIO(out))
    assert im.size == (1280, 720), "장면이 아니라 누끼를 키프레임으로 썼다"
    assert im.mode == "RGB"


def test_without_scene_the_legacy_plate_is_unchanged():
    """장면이 없으면 예전과 **바이트 단위로** 같아야 한다 — 레거시 회귀 방지."""
    cutout = _cutout_png()
    legacy = flatten_rgba_to_jpeg_bytes(cutout, bg_rgb=BG_BLACK)
    from backend.services.luma_keyframe import resolve_keyframe_bg_rgb

    expected = flatten_rgba_to_jpeg_bytes(
        cutout, bg_rgb=resolve_keyframe_bg_rgb(cutout)
    )
    got = build_keyframe_jpeg(cutout, scene_bytes=None)
    assert got == expected
    assert isinstance(legacy, bytes)


def test_transparent_scene_is_flattened_not_left_with_alpha():
    """JPEG 에는 알파가 없다 — 남겨 두면 투명 영역이 예측 불가한 색이 된다."""
    im = Image.new("RGBA", (640, 360), (10, 10, 10, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = scene_to_jpeg_bytes(buf.getvalue())
    assert Image.open(io.BytesIO(out)).mode == "RGB"


# ── 프롬프트: 여섯 행동이 한 seam 을 지난다 ─────────────────────────────────


@pytest.mark.parametrize("behavior", BEHAVIORS)
def test_baked_prompt_preserves_background(behavior):
    baked = build_scenario_prompt(
        "https://x/scene.jpg", PLACE, behavior, background_baked=True
    )
    assert VOID_BACKGROUND_SENTENCE not in baked, behavior
    assert "Preserve the input keyframe" in baked, behavior
    assert "camera locked" in baked, behavior
    assert "do not" in baked.lower(), behavior


@pytest.mark.parametrize("behavior", BEHAVIORS)
def test_legacy_prompt_is_untouched(behavior):
    """배경 처리를 켜지 않으면 예전 프롬프트 그대로여야 한다."""
    legacy = build_scenario_prompt("https://x/k.jpg", PLACE, behavior)
    assert SCENE_BACKGROUND_SENTENCE not in legacy, behavior


def test_bake_is_idempotent():
    """두 번 구워도 보존 문장이 중복되지 않는다."""
    once = bake_scene_background(f"Motion. {VOID_BACKGROUND_SENTENCE}")
    twice = bake_scene_background(once)
    assert once.count(SCENE_BACKGROUND_SENTENCE) == 1
    assert twice.count(SCENE_BACKGROUND_SENTENCE) == 1


def test_bake_adds_preservation_even_without_the_void_sentence():
    """
    보이드 문장을 쓰지 않는 행동(아이들 이벤트)도 보존 요구를 받아야 한다.
    빠뜨리면 그 행동만 조용히 배경을 잃는다.
    """
    out = bake_scene_background("Only the ears twitch.")
    assert SCENE_BACKGROUND_SENTENCE in out


@pytest.mark.parametrize(
    "template", ["IDLE_BREATH", "IDLE_HEAD_TILT", "IDLE_TAIL_WAG", "IDLE_EAR_FLICK"]
)
def test_idle_variant_templates_have_both_modes(template):
    baked = build_idle_variant_prompt(template, background_baked=True)
    legacy = build_idle_variant_prompt(template)
    assert "pure solid black" not in baked.lower(), template
    assert "Preserve the input" in baked, template
    assert "pure solid black" in legacy.lower(), template
    # 모션 문구 자체는 두 경로가 **공유한다** — 배경만 갈린다.
    motion = "ears flick" if template == "IDLE_EAR_FLICK" else None
    if motion:
        assert motion in baked and motion in legacy


# ── 장면 입력 파싱 ───────────────────────────────────────────────────────────


def test_absent_flag_is_legacy_and_stays_silent():
    """
    background_baked 가 없거나 false 면 **레거시**다 — 장면을 요구한 적 없는
    요청이고, 지금까지처럼 조용히 진행한다. 여기서 400 을 던지면 구버전
    클라이언트가 생성 자체를 못 하게 된다.
    """
    for flag in (None, "", "false", "0", "no"):
        req = scene_input.parse(
            scene_id="s1", background_type="theme", background_id="x",
            scene_keyframe_url="https://x/s.png", background_baked=flag,
        )
        assert req.requested is False, flag
        assert req.scene is None, flag


def test_requested_but_malformed_is_not_silently_downgraded():
    """
    **여기가 바뀐 계약이다 (Phase 26).**

    예전에는 이 경우도 None 이라 레거시와 구분되지 않았고, 그대로 단색 판으로
    생성이 돌았다. 고객은 자기가 고른 적 없는 배경의 영상을 받았다.

    이제 requested=True 로 남아 호출부가 멈출 수 있다.
    """
    cases = [
        dict(scene_id="", background_type="theme",
             scene_keyframe_url="https://x/s.png"),           # 장면 id 없음
        dict(scene_id="s1", background_type="theme",
             scene_keyframe_url=""),                          # 키프레임 없음
        dict(scene_id="s1", background_type="hologram",
             scene_keyframe_url="https://x/s.png"),           # 모르는 배경 종류
    ]
    for c in cases:
        req = scene_input.parse(background_id="x", background_baked="true", **c)
        assert req.requested is True, c
        assert req.scene is None, c


@pytest.mark.parametrize("btype", ["original", "theme", "custom"])
def test_three_background_types_all_parse(btype):
    """세 갈래가 **같은 경로**로 들어온다 — 배경별 아키텍처가 없다는 증거."""
    req = scene_input.parse(
        scene_id="s1", background_type=btype, background_id="bg",
        scene_keyframe_url="https://x/s.png", background_baked="true",
    )
    assert req.requested is True
    assert req.scene is not None and req.scene.background_type == btype


def test_scene_request_unpacks_as_a_pair():
    """호출부가 `requested, scene = ...` 로 받는다 — 그 모양을 고정한다."""
    requested, scene = scene_input.parse(
        scene_id="s1", background_type="theme", background_id="bg",
        scene_keyframe_url="https://x/s.png", background_baked="true",
    )
    assert requested is True
    assert scene is not None and scene.scene_id == "s1"


# ── 과금 보호 ────────────────────────────────────────────────────────────────


def test_same_scene_and_behavior_reserves_once():
    """
    **핵심 과금 계약**: 같은 (사용자, 장면, 행동) 은 한 번만 예약된다.

    두 번째 호출이 is_new=True 를 받으면 그 자리에서 유료 제출이 한 번 더 일어난다.
    """
    a, new_a = _sync(
        scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE"
    )
    b, new_b = _sync(
        scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert new_a is True
    assert new_b is False, "같은 장면·행동이 두 번 제출된다 — 이중 과금"
    assert a.job_key == b.job_key


def test_different_behaviors_on_one_scene_are_separate_jobs():
    """한 장면에서 여러 행동을 만드는 것은 정상이다 — 서로 막지 않는다."""
    keys = set()
    for behavior in BEHAVIORS:
        job, is_new = _sync(
            scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior=behavior
        )
        assert is_new is True, behavior
        keys.add(job.job_key)
    assert len(keys) == len(BEHAVIORS)


def test_completed_job_is_reused_not_regenerated():
    _sync(scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        scene_generation_jobs.mark_completed,
        user_id="u", scene_id="s1", behavior="IDLE", video_url="https://s/idle.mp4",
    )
    job = _sync(scene_generation_jobs.get, "u", "s1", "IDLE")
    assert job.completed is True
    assert job.video_url == "https://s/idle.mp4"
    # 재예약해도 새 작업이 아니다.
    _, is_new = _sync(
        scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert is_new is False


def test_provider_job_id_survives_for_recovery():
    """
    타임아웃 뒤 복구의 근거는 provider_job_id 다. 없으면 되찾을 방법이 없고,
    남은 선택지는 재제출(= 이중 과금)뿐이다.
    """
    _sync(scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        scene_generation_jobs.mark_submitted,
        user_id="u", scene_id="s1", behavior="IDLE",
        provider="luma", provider_job_id="gen_123",
    )
    job = _sync(scene_generation_jobs.get, "u", "s1", "IDLE")
    assert job.active is True
    assert job.provider_job_id == "gen_123"
    assert job.provider == "luma"


def test_failed_job_clears_so_retry_is_possible():
    """진행 중을 막는 것과 실패를 막는 것은 다르다 — 실패는 재시도돼야 한다."""
    _sync(scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        scene_generation_jobs.mark_failed,
        user_id="u", scene_id="s1", behavior="IDLE", error="provider 502",
    )
    _sync(
        scene_generation_jobs.clear_for_retry,
        user_id="u", scene_id="s1", behavior="IDLE",
    )
    _, is_new = _sync(
        scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert is_new is True, "실패한 작업이 재시도를 영영 막는다"


def test_scene_id_is_part_of_the_key():
    """장면이 다르면 다른 그림이다 — 재사용하면 안 된다."""
    _sync(scene_generation_jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _, is_new = _sync(
        scene_generation_jobs.reserve, user_id="u", scene_id="s2", behavior="IDLE"
    )
    assert is_new is True


def test_user_is_part_of_the_key():
    """남의 장면 결과를 물려받지 않는다."""
    _sync(scene_generation_jobs.reserve, user_id="u1", scene_id="s1", behavior="IDLE")
    _, is_new = _sync(
        scene_generation_jobs.reserve, user_id="u2", scene_id="s1", behavior="IDLE"
    )
    assert is_new is True
