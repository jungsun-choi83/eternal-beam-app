"""
펫 적응형 중립 배경 — 선택 규칙 + 정본 계보 전체에서의 재사용.

  선택기:  흰/밝은 펫 → 어두운 회색, 검은 펫 → 밝은 회색, 흑백 얼룩 → 안전한
           중간 톤, 중간 톤 펫 → 결정론적 선택, 순흑/순백 없음.
  계보:    정본 output_spec 에 박제된 **하나의** 결정이 정본 플레이트, STAND_READY
           플레이트, 키프레임 프롬프트, 모션 계약에 그대로 쓰인다.
"""

from __future__ import annotations

import io

import anyio
import pytest
from PIL import Image

from backend.services import action_keyframe_service as kf
from backend.services import action_keyframe_spec as kf_spec
from backend.services import canonical_prompt
from backend.services import clean_plate_service
from backend.services import motion_spec as ms
from backend.services import pet_background as bg

from .test_action_keyframes import (  # noqa: F401  (fixtures: _mock_backend, storage)
    VLM_KF_OK,
    RecordingProvider,
    _build_kf,
    _mock_backend,
    _prepare_canonical,
    install_kf_vlm,
    storage,
)
from .test_canonical_pet_builder import GOOD
from .test_pet_reference_sets import PET, USER


def _run(coro):
    return anyio.run(lambda: coro)


def _coat(**fractions: float) -> dict:
    """융합 프로필과 같은 형태 — 색 이름 + 비율만 (RGB 없음)."""
    return {
        "coat": {
            "status": "measured",
            "palette": [{"name": name, "fraction": frac} for name, frac in fractions.items()],
            # 평균 명도는 일부러 "중간"으로 둔다 — 선택기가 이것을 믿으면 안 된다.
            "mean_luminance": 128.0,
            "tone": "medium",
        }
    }


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


# ══════════════════════════════════════════════════════════════════════════
# 선택 규칙
# ══════════════════════════════════════════════════════════════════════════


def test_palette_is_four_neutral_grays_without_pure_black_or_white():
    assert [(label, rgb) for label, rgb, _ in bg.BACKGROUND_PALETTE] == [
        ("light", (200, 200, 200)),
        ("medium", (152, 152, 152)),
        ("medium_dark", (112, 112, 112)),
        ("dark", (80, 80, 80)),
    ]
    for _label, rgb, tone in bg.BACKGROUND_PALETTE:
        assert rgb[0] == rgb[1] == rgb[2]  # 중립(무채색)
        assert 0 < rgb[0] < 255 and rgb not in ((0, 0, 0), (255, 255, 255))
        assert "gray" in tone


def test_white_or_light_pet_gets_a_darker_gray():
    for coat in (_coat(white=0.7, cream=0.3), _coat(cream=1.0), _coat(white=1.0)):
        decision = bg.select_background(coat)
        assert decision["background_label"] == "dark"
        assert decision["background_rgb"] == [80, 80, 80]


def test_black_or_dark_pet_gets_a_lighter_gray():
    for coat in (_coat(black=1.0), _coat(black=0.9, dark_gray=0.1), _coat(black=0.7, dark_brown=0.3)):
        decision = bg.select_background(coat)
        assert decision["background_label"] == "light"
        assert decision["background_rgb"] == [200, 200, 200]


def test_mixed_black_and_white_pet_gets_a_safe_middle_tone():
    decision = bg.select_background(_coat(black=0.5, white=0.5))
    # 평균 명도는 "중간"이지만 팔레트는 양극이다 — 양쪽 모두에서 떨어진 중간 톤.
    assert decision["background_label"] in ("medium", "medium_dark")
    rgb = decision["background_rgb"]
    for coat_rgb in ((25, 22, 20), (240, 238, 232)):
        assert max(abs(c - g) for c, g in zip(coat_rgb, rgb)) >= 80
    # 양 끝 톤은 한쪽 색을 버린다.
    by_label = {s["label"]: s for s in decision["tone_scores"]}
    assert by_label[decision["background_label"]]["worst_separation"] > by_label["light"]["worst_separation"]
    assert by_label[decision["background_label"]]["worst_separation"] > by_label["dark"]["worst_separation"]
    # 비율이 한쪽으로 기울어도 소수 색(≥10%)을 버리지 않는다.
    mostly_white = bg.select_background(_coat(white=0.8, black=0.2))
    assert mostly_white["background_label"] in ("medium", "medium_dark")


def test_mid_tone_pet_choice_is_stable_and_deterministic():
    coat = _coat(brown=0.7, tan=0.3)
    first = bg.select_background(coat)
    assert first == bg.select_background(coat)
    # 입력 순서가 바뀌어도 같은 결정이다.
    assert bg.select_background(_coat(tan=0.3, brown=0.7)) == first
    assert first["background_label"] == "light"
    assert first["reason"] == "max_worst_case_separation_from_coat_palette"
    assert first["selector_version"] == bg.SELECTOR_VERSION
    assert [c["name"] for c in first["coat_summary"]] == ["brown", "tan"]


def test_neutral_gray_coat_avoids_the_shadow_like_band():
    """무채색 회색 털은 더 밝은 회색 배경 위에서 '그림자'처럼 보인다 — 피한다."""
    decision = bg.select_background(_coat(gray=1.0))
    assert _luma(decision["background_rgb"]) < _luma((128, 128, 126))


def test_selection_never_returns_pure_black_or_white_and_always_a_palette_tone():
    names = ["black", "dark_brown", "brown", "red_brown", "tan", "golden", "cream", "white", "gray", "dark_gray"]
    palette_rgbs = {rgb for _, rgb, _ in bg.BACKGROUND_PALETTE}
    for a in names:
        for b in names:
            decision = bg.select_background(_coat(**({a: 0.6, b: 0.4} if a != b else {a: 1.0})))
            rgb = tuple(decision["background_rgb"])
            assert rgb in palette_rgbs
            assert rgb not in ((0, 0, 0), (255, 255, 255))


def test_unknown_coat_falls_back_to_the_existing_default_gray():
    for visual in (None, {}, {"coat": {"status": "unknown"}}, {"coat": {"status": "measured", "palette": []}}):
        decision = bg.select_background(visual)
        assert decision["background_rgb"] == [200, 200, 200]
        assert decision["reason"] == "coat_palette_unavailable_default"


def test_legacy_canonical_without_a_stored_decision_keeps_the_fixed_gray(monkeypatch):
    monkeypatch.delenv("CLEAN_PLATE_BG_RGB", raising=False)
    legacy = bg.from_output_spec({"background": "plain solid neutral light-gray"})
    assert legacy["background_rgb"] == [200, 200, 200] and legacy["background_label"] == "light"
    # 저장된 결정은 그대로 읽힌다 — 다시 고르지 않는다.
    stored = bg.select_background(_coat(white=1.0))
    assert bg.from_output_spec({"background_selection": stored})["background_rgb"] == [80, 80, 80]
    # 순흑/순백/깨진 값은 저장돼 있어도 쓰지 않는다.
    for bad in ([0, 0, 0], [255, 255, 255], [80, 80], "gray"):
        assert bg.from_output_spec({"background_selection": {"background_rgb": bad}})["background_rgb"] == [200, 200, 200]


def test_prompt_wording_follows_the_tone():
    for label, _rgb, tone in bg.BACKGROUND_PALETTE:
        assert bg.prompt_tone(label) == tone
        canonical = canonical_prompt.build_canonical_prompt(
            visual_identity={}, structural_identity={}, reference_roles=[], background_tone=tone
        )
        compact = canonical_prompt.build_compact_canonical_prompt(visual_identity={}, background_tone=tone)
        stand = kf_spec.KEYFRAME_ROLES["STAND_READY"]
        keyframe = kf_spec.build_keyframe_prompt(stand, {}, background_tone=tone)
        keyframe_compact = kf_spec.build_compact_keyframe_prompt(stand, {}, background_tone=tone)
        for prompt in (canonical, compact, keyframe, keyframe_compact):
            assert f"{tone} background" in prompt
            assert "{background_tone}" not in prompt
            if label != "light":
                assert "light-gray" not in prompt
        assert len(compact) <= 1000 and len(keyframe_compact) <= 1000
        # 배경 계약은 톤과 무관하게 그대로다.
        for kept in ("single flat tone", "no gradient", "no floor plane", "no horizon line", "no reflection"):
            assert kept in canonical and kept in keyframe, kept
        assert "No contact shadow" in canonical and "no cast shadow" in canonical
    # 기본(톤 미지정)은 지금까지의 문구와 같다.
    assert "Plain solid neutral light-gray background" in canonical_prompt._BASE
    assert "{background_tone}" not in canonical_prompt._COMPACT_BASE


def test_clean_plate_uses_the_given_background():
    plate, meta = clean_plate_service.build_clean_plate(GOOD(), (80, 80, 80))
    assert meta["background_rgb"] == [80, 80, 80]
    with Image.open(io.BytesIO(plate)) as im:
        assert im.convert("RGB").getpixel((0, 0)) == (80, 80, 80)
    # 미지정이면 전역 기본 그대로다.
    _, default_meta = clean_plate_service.build_clean_plate(GOOD())
    assert default_meta["background_rgb"] == [200, 200, 200]


# ══════════════════════════════════════════════════════════════════════════
# 계보 — 정본이 고른 하나의 배경이 하류 전부에 쓰인다
# ══════════════════════════════════════════════════════════════════════════


def _corner(storage_map, path) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(storage_map[path])) as im:
        return im.convert("RGB").getpixel((0, 0))


@pytest.mark.parametrize("forced", [None, "dark", "medium_dark"])
def test_canonical_and_stand_ready_share_one_stored_background(storage, monkeypatch, forced):
    """정본 output_spec 의 결정 하나가 정본 플레이트 / STAND_READY 프롬프트 /
    STAND_READY 플레이트 / 모션 계약에 그대로 쓰인다 (forced=None 은 픽스처
    펫의 실제 코트로 고른 값, 나머지는 어두운 톤을 강제한 경우)."""
    monkeypatch.delenv("CLEAN_PLATE_BG_RGB", raising=False)
    calls: list[dict] = []
    real_select = bg.select_background

    def counting_select(visual_identity):
        decision = (
            real_select(visual_identity)
            if forced is None
            else bg._decision(forced, "forced_for_test", coat_summary=[], tone_scores=[])
        )
        calls.append(decision)
        return decision

    monkeypatch.setattr(bg, "select_background", counting_select)

    h, canonical = _prepare_canonical(monkeypatch, storage)

    # ── 정본: 결정이 output_spec 에 박제된다 ──────────────────────────────
    chosen = canonical.output_spec["background_selection"]
    assert len(calls) == 1 and chosen == calls[0]
    for key in ("background_rgb", "background_label", "selector_version", "reason", "coat_summary"):
        assert key in chosen, key
    rgb = tuple(chosen["background_rgb"])
    assert rgb in {p[1] for p in bg.BACKGROUND_PALETTE}
    if forced:
        assert chosen["background_label"] == forced and rgb != (200, 200, 200)
    tone = bg.prompt_tone(chosen["background_label"])

    # 프롬프트/스펙 문구가 저장된 톤과 일치한다.
    assert f"Plain solid neutral {tone} background" in canonical.prompt
    assert tone in canonical.output_spec["background"]
    if chosen["background_label"] != "light":
        assert "light-gray" not in canonical.prompt
        assert "light-gray" not in canonical.output_spec["background"]

    # 정본 클린 플레이트가 그 색이다.
    canonical_sel = next(c for c in canonical.candidates if c.selected)
    assert canonical_sel.generation_metadata["clean_plate"]["background_rgb"] == list(rgb)
    assert _corner(storage, canonical_sel.plate_object_path) == rgb

    # ── STAND_READY: 다시 고르지 않고 정본의 결정을 쓴다 ───────────────────
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = RecordingProvider("runway", [GOOD()] * 5)
    stand = _build_kf(h, [provider], role="STAND_READY")
    assert stand.status == kf.STATUS_COMPLETE
    assert len(calls) == 1, "키프레임 단계가 배경을 다시 골랐다"

    assert f"Plain solid neutral {tone} background" in provider.seen_prompts[0]
    assert f"Plain solid neutral {tone} background" in stand.prompt
    if chosen["background_label"] != "light":
        assert "light-gray" not in provider.seen_prompts[0]
    stand_sel = next(c for c in stand.candidates if c.selected)
    assert stand_sel.generation_metadata["clean_plate"]["background_rgb"] == list(rgb)
    assert _corner(storage, stand_sel.plate_object_path) == rgb

    # 다른 키프레임 역할도 같은 배경이다.
    lie = _build_kf(h, [RecordingProvider("runway", [GOOD()] * 5)], role="LIE")
    lie_sel = next(c for c in lie.candidates if c.selected)
    assert _corner(storage, lie_sel.plate_object_path) == rgb
    assert len(calls) == 1

    # ── 모션: 계약이 같은 결정을 실어 보낸다 (플레이트 재생성 시 같은 색) ───
    contract = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="BREATHING"))
    assert contract["background"]["background_rgb"] == list(rgb)
    assert contract["background"]["background_label"] == chosen["background_label"]
    assert len(calls) == 1


def test_plate_backfill_uses_the_lineage_background(storage, monkeypatch):
    """모션 단계가 플레이트를 다시 만들어야 할 때(ensure_plate 백필)도 계약이
    실어 온 정본의 배경을 쓴다 — 전역 기본 회색으로 새지 않는다."""
    from backend.services import pet_reference_service

    recorded: list[dict] = []

    async def fake_record_derived(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(pet_reference_service, "record_derived", fake_record_derived)
    contract_background = bg.select_background(_coat(black=0.5, white=0.5))
    rgb = bg.background_rgb(contract_background)

    plate = _run(
        clean_plate_service.ensure_plate(
            user_id=USER,
            content_id="content",
            derived_kind=clean_plate_service.GENERATED_KIND_KEYFRAME_PLATE,
            fetch_bytes=lambda ref: None,
            raw_object_path="u/c/keyframes/stand_ready/v1/runway_a1_raw.png",
            cutout_object_path="u/c/keyframes/stand_ready/v1/runway_a1_cutout.png",
            cutout_bytes=GOOD(),
            background=rgb,
        )
    )

    assert plate.created is True and plate.meta["background_rgb"] == list(rgb)
    assert _corner(storage, plate.object_path) == rgb
    assert recorded[0]["diagnostics"]["clean_plate"]["background_rgb"] == list(rgb)
    # 계약에 배경이 없으면(옛 계보) None → 전역 기본.
    assert bg.background_rgb(None) is None
