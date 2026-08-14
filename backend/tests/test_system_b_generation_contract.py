"""
System B 생성 계약 — 펫 전용 레이어(Phase 7).

확인하는 것:
  1. 프롬프트에 `Environment:` (장소 설명) 이 더 이상 없다.
  2. place_key 는 과금·저장·조회·/device/sync 경로에 그대로 살아 있다.
  3. Luma 제출 전에 키프레임이 **순정 검정**으로 평탄화된다.
  4. ACTION_COMMON_CONSTRAINT 가 TOUCH/VOICE/NFC 최종 프롬프트에 도달한다.
  5. 여전히 정확히 4건(IDLE/TOUCH/VOICE/NFC)이 제출된다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from backend.scenarios.pet_scenarios import (
    ACTION_ORDER,
    PLACES,
    place_public_id,
    storage_object_name,
)
from backend.services.luma_prompts import (
    ACTION_COMMON_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT,
    LUMA_ACTION_PROMPTS,
)
from backend.services.prompt_factory import build_scenario_prompt

PLACE = "01_snow_forest"
ACTIONS = ("IDLE", "TOUCH", "VOICE", "NFC")


def _prompt(action: str) -> str:
    return build_scenario_prompt("<IMG>", PLACE, action)


# ── 1. Environment: 제거 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("action", ACTIONS)
def test_no_environment_clause(action: str):
    p = _prompt(action)
    assert "Environment:" not in p, "장소 설명이 펫 클립 프롬프트에 남아 있다"


@pytest.mark.parametrize("action", ACTIONS)
def test_place_description_text_absent(action: str):
    """PLACES[...]['prompt'] 문구가 통째로 새어 들어가지 않아야 한다."""
    place_text = PLACES[PLACE]["prompt"]
    p = _prompt(action).lower()
    for phrase in ("snow-covered pine forest", "falling snow", "winter light"):
        assert phrase not in p, f"장소 묘사가 남아 있다: {phrase!r}"
    assert place_text.lower() not in p


@pytest.mark.parametrize("action", ACTIONS)
def test_black_background_is_requested_not_scenery(action: str):
    p = _prompt(action).lower()
    if action == "IDLE":
        # IDLE 은 기존 로직 유지 — 키프레임 배경을 그대로 지키라고 말한다.
        assert "same fur pattern, same lighting, same background" in p
    else:
        assert "pure solid black background" in p
        assert "no scenery" in p


# ── 2. place_key 는 비즈니스/저장 경로에 그대로 ──────────────────────────────


def test_place_key_still_validated_by_prompt_builder():
    with pytest.raises(KeyError):
        build_scenario_prompt("<IMG>", "definitely_not_a_place", "TOUCH")


def test_place_key_still_drives_storage_and_lookup():
    assert storage_object_name(PLACE, "TOUCH") == "SNOW_FOREST_TOUCH.mp4"
    assert place_public_id(PLACE) == "snow_forest"
    assert PLACE in PLACES


# ── 3. 검정 플레이트 키프레임 ────────────────────────────────────────────────


def _rgba_cutout_with_bright_background() -> bytes:
    """투명 영역 RGB 가 밝은 원본 사진인 누끼 — 평탄화 대상."""
    rgba = Image.new("RGBA", (40, 40), (240, 230, 220, 0))  # 투명하지만 RGB 는 밝음
    for y in range(12, 28):
        for x in range(12, 28):
            rgba.putpixel((x, y), (200, 160, 120, 255))  # 불투명 피사체
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return buf.getvalue()


def test_keyframe_is_flattened_to_pure_black(monkeypatch):
    from backend.services import credit_keyframe as ck

    uploaded: dict = {}

    async def fake_fetch(url: str):
        return _rgba_cutout_with_bright_background()

    async def fake_upload(path: str, data: bytes, content_type: str):
        uploaded["path"] = path
        uploaded["data"] = data
        uploaded["content_type"] = content_type
        return "https://cdn/kf.jpg"

    monkeypatch.setattr(ck, "_fetch_bytes", fake_fetch)
    import backend.services.supabase_assets as sa

    monkeypatch.setattr(sa, "upload_asset_to_storage", fake_upload)

    url = asyncio.run(ck.prepare_black_plate_keyframe("https://cdn/cutout.png", "sess1"))

    assert url == "https://cdn/kf.jpg"
    assert uploaded["content_type"] == "image/jpeg"
    assert uploaded["path"] == "creditkf/sess1/keyframe_black.jpg"

    out = Image.open(io.BytesIO(uploaded["data"]))
    assert out.mode == "RGB", "알파가 남아 있으면 안 된다"
    # 모서리(원래 투명이던 곳)는 검정이어야 한다 — 밝은 원본 배경이 아니라.
    for xy in ((0, 0), (39, 0), (0, 39), (39, 39)):
        r, g, b = out.getpixel(xy)
        assert r < 12 and g < 12 and b < 12, f"{xy} 가 검정이 아니다: {(r, g, b)}"


def test_keyframe_failure_raises_and_never_falls_back(monkeypatch):
    """
    Phase 7B 에서 fail-open → fail-closed 로 바꿨다.

    원본 RGBA URL 로 폴백하면 알파 뒤 원본 사진 배경이 영상에 살아나고, 그 픽셀이
    S23 검정 키를 통과해 Pi 배경 위에 겹친다. 그런 결과에 크레딧 4개를 태우느니
    한 건도 제출하지 않고 환불하는 편이 낫다.
    자세한 계약은 tests/test_credit_keyframe_failure.py 참고.
    """
    from backend.services import credit_keyframe as ck
    from backend.services.credit_keyframe import KeyframePreparationError

    async def fail_fetch(url: str):
        return None

    monkeypatch.setattr(ck, "_fetch_bytes", fail_fetch)
    with pytest.raises(KeyframePreparationError):
        asyncio.run(ck.prepare_black_plate_keyframe("https://cdn/orig.png", "sess2"))


def test_generation_uses_the_flattened_keyframe(monkeypatch):
    """generate_with_credit 이 평탄화된 URL 을 제출 단계로 넘기는지."""
    from backend.services import credit_generation_service as svc

    seen: dict = {}

    async def fake_prepare(image_url: str, session_id: str) -> str:
        return "https://cdn/black_plate.jpg"

    async def fake_submit(**kw):
        seen.update(kw)
        return 4, []

    async def fake_session(*a, **k):
        return "sess9"

    async def fake_deduct(uid, cost):
        class W:
            current_credits = 10

        return W()

    monkeypatch.setattr(svc, "prepare_black_plate_keyframe", fake_prepare)
    monkeypatch.setattr(svc, "submit_place_motion_set", fake_submit)
    monkeypatch.setattr(svc.motions_svc, "create_credit_session", fake_session)
    monkeypatch.setattr(svc, "deduct_credits", fake_deduct)

    asyncio.run(
        svc.generate_with_credit(
            user_id="u1",
            pet_image_url="https://cdn/cutout.png",
            selected_place_id="snow_forest",
            webhook_base_url="https://api/x",
        )
    )
    assert seen["pet_image_url"] == "https://cdn/black_plate.jpg"
    assert seen["place_key"] == PLACE, "place_key 는 계속 흘러가야 한다"


# ── 4. ACTION_COMMON_CONSTRAINT 도달 ────────────────────────────────────────


@pytest.mark.parametrize("action", ["TOUCH", "VOICE", "NFC"])
def test_action_common_constraint_reaches_actions(action: str):
    assert ACTION_COMMON_CONSTRAINT in _prompt(action)


def test_idle_does_not_get_action_common_constraint():
    """IDLE 은 자체 제약을 갖고 있다 — 중복/모순 방지."""
    assert ACTION_COMMON_CONSTRAINT not in _prompt("IDLE")
    assert IDLE_COMMON_CONSTRAINT in _prompt("IDLE")


@pytest.mark.parametrize(
    "meaning, needle",
    [
        ("animate only this pet", "animate only this one dog"),
        ("identity/markings", "preserve the dog's identity, fur colour, markings"),
        ("visible body kept visible", "keep the dog's visible body visible"),
        ("fixed camera", "camera is completely fixed"),
        ("scale/position", "same scale and the same position on screen"),
        ("no walking", "no walking"),
        ("no large translation", "no large translation of the body"),
        ("no large rotation", "no large rotation"),
        ("no duplicated/missing limbs", "cut off, duplicated, or removed"),
        ("no people/hands/leash", "no people, no human hands or arms, no leash"),
        ("pure black background", "pure solid black background"),
        ("no scenery", "no scenery, no environment, no floor"),
    ],
)
def test_action_common_constraint_content(meaning: str, needle: str):
    assert needle in ACTION_COMMON_CONSTRAINT.lower(), f"빠진 항목: {meaning}"


@pytest.mark.parametrize(
    "forbidden",
    [
        "begin and end in the identical",  # 시작/끝 동일 — IDLE 전용
        "loop",                            # 루프 요구 — IDLE 전용
        "head does not move",              # 개별 행동 정의 소관
        "head, legs, tail, and camera angle do not move",
    ],
)
def test_action_common_constraint_excludes_idle_only_rules(forbidden: str):
    assert forbidden not in ACTION_COMMON_CONSTRAINT.lower()


def test_individual_action_motions_unchanged():
    assert "gently petted on the head" in LUMA_ACTION_PROMPTS["TOUCH"]
    # VOICE 는 Phase 8 에서 재설계 — test_voice_action.py 가 계약을 고정한다.
    assert "familiar owner's voice" in LUMA_ACTION_PROMPTS["VOICE"]
    # NFC 는 Phase 10 에서 재설계 — test_nfc_action.py 가 계약을 고정한다.
    assert "notices that a familiar place has appeared" in LUMA_ACTION_PROMPTS["NFC"]


# ── 5. 여전히 정확히 4건 ────────────────────────────────────────────────────


def test_exactly_four_motions_submitted(monkeypatch):
    from backend.services import credit_luma_batch as batch
    from backend.services.video_generation import SubmittedJob

    submitted: list[str] = []

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        submitted.append(prompt)
        return SubmittedJob(provider=provider, external_id="gen_x", model="m")

    async def fake_register(*a, **k):
        return None

    monkeypatch.setattr(batch, "submit_generation", fake_submit)
    monkeypatch.setattr(batch.motions_svc, "register_generation_job", fake_register)

    ok, errors = asyncio.run(
        batch.submit_place_motion_set(
            session_id="s",
            user_id="u",
            pet_id="p",
            place_key=PLACE,
            pet_image_url="https://cdn/black.jpg",
            webhook_base_url="https://api/hook",
        )
    )
    assert ok == 4
    assert errors == []
    assert len(submitted) == 4
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")
