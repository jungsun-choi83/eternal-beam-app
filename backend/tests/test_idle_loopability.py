"""
루프 가능성(loopability) 패치 검증 — 2부.

1) 프롬프트: 클립이 **시작 = 끝** 이라는 종료 상태(end-state)를 명시하는가.
2) 검증기: 실제 마지막 프레임을 duration 기반으로 샘플링하는 새 지표를 만들되,
   **재생성(retry) 판정은 이전과 완전히 동일**해야 한다.

측정 결과 포즈 드리프트는 단조(monotonic)였다 — 한 방향으로 멀어지기만 한다.
그래서 기존 ss="2.85" 고정 샘플은 실제 이음매보다 항상 낙관적이다. 임계값을
보정하기 전에 게이트를 바꾸면 유료 재생성이 늘어나므로, 이 패치는 측정만 한다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.services.idle_validation_service import (
    TRUE_LAST_FRAME_OFFSET_SEC,
    IdleValidationResult,
    true_last_frame_sample_time,
    validate_idle_video,
)
from backend.services.luma_prompts import (
    IDLE_COMMON_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT_HEAD_ROTATION,
    LUMA_ACTION_PROMPTS,
)


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10, check=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe 없음")


# ══ 1. 프롬프트 — 시작/끝 동일 상태 ═══════════════════════════════════════════


def _final_idle_prompt() -> str:
    """실제 메인 경로가 Wan/Luma 로 보내는 최종 IDLE 문자열."""
    import io

    from PIL import Image

    from backend.services.luma_service import build_idle_action_prompts

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 180, 150)).save(buf, format="JPEG")
    idle, _action = build_idle_action_prompts(buf.getvalue())
    return idle


def test_main_idle_prompt_requires_same_begin_and_end_state():
    p = _final_idle_prompt().lower()
    assert "begin and end in the identical resting pose" in p
    assert "final frame must match the first frame" in p


def test_main_idle_prompt_names_what_must_match_at_the_end():
    """포즈만이 아니라 위치·스케일·머리 방향까지 일치해야 이음매가 안 보인다."""
    p = _final_idle_prompt().lower()
    for attr in ("pose", "position", "scale", "head orientation"):
        assert attr in p, f"종료 상태 일치 항목에 {attr!r} 이 없다"
    assert "loop naturally" in p


def test_micro_motion_must_complete_a_full_cycle():
    p = _final_idle_prompt().lower()
    assert "complete a full cycle" in p
    assert "return to its starting state before the clip ends" in p


@pytest.mark.parametrize("template_key", ["IDLE_BREATH", "IDLE_HEAD_TILT", "IDLE_TAIL_WAG", "IDLE_EAR_FLICK"])
def test_variants_sharing_common_constraint_inherit_the_loop_rule(template_key: str):
    """IDLE_COMMON_CONSTRAINT 를 쓰는 프리셋은 전부 종료 상태 규칙을 물려받아야 한다."""
    from backend.services.luma_idle_templates import build_idle_variant_prompt

    prompt = build_idle_variant_prompt(template_key).lower()
    assert "begin and end in the identical resting pose" in prompt


def test_head_rotation_constraint_not_touched_yet():
    """요청대로 IDLE_COMMON_CONSTRAINT_HEAD_ROTATION 은 아직 건드리지 않는다."""
    assert "begin and end in the identical resting pose" not in IDLE_COMMON_CONSTRAINT_HEAD_ROTATION
    assert "complete a full cycle" not in IDLE_COMMON_CONSTRAINT_HEAD_ROTATION


def test_visible_region_rules_still_protect_what_is_visible():
    """
    보이는 것은 그대로 보존된다 — 이건 정책이 바뀌어도 불변이다.

    바뀐 것은 **보이지 않는 것**의 처리다: 예전엔 무조건 금지였고, 지금은
    조건부 완성(BODY COMPLETION)이다. 그래서 "invent anatomy that is not visible"
    금지는 더 이상 요구하지 않는다 — 그 문구가 남아 있으면 완성 지시를 부정
    토큰으로 밀어낸다(COME_CLOSER 의 'walking' 과 같은 함정).
    """
    c = IDLE_COMMON_CONSTRAINT.lower()
    assert "stay exactly as visible" in c
    assert "every body part that is visible in the reference" in c
    assert "same fur pattern, same lighting, same background" in c
    # 완성은 프레임 안에서만 — 줌아웃으로 몸을 드러내는 것은 여전히 금지.
    assert "do not zoom out, widen the framing" in c
    assert "drawn inside the existing frame" in c
    idle = LUMA_ACTION_PROMPTS["IDLE"].lower()
    assert "already visible in the reference" in idle
    assert "if the chest or upper torso is visibly present" in idle


# ══ 2. 검증기 — duration 기반 마지막 프레임 샘플링 ═════════════════════════════


@pytest.mark.parametrize(
    "duration, expected",
    [
        (5.21, 5.11),   # 5.2s 클립 → 5.1s 근처 (2.85 아님)
        (4.00, 3.90),   # 4.0s 클립 → 3.9s
        (0.05, 0.00),   # 아주 짧은 클립도 음수로 내려가지 않는다
        (0.10, 0.00),
    ],
)
def test_sample_time_is_derived_from_duration(duration: float, expected: float):
    assert true_last_frame_sample_time(duration) == pytest.approx(expected, abs=1e-6)


def test_sample_time_never_negative():
    assert true_last_frame_sample_time(0.0) == 0.0


def test_offset_constant_is_the_requested_100ms():
    assert TRUE_LAST_FRAME_OFFSET_SEC == 0.10


@needs_ffmpeg
@pytest.mark.parametrize("duration, expect_near", [(5.2, 5.1), (4.0, 3.9)])
def test_real_clip_samples_near_the_end_not_2_85(tmp_path, duration: float, expect_near: float):
    """
    합성 클립으로 실제 경로를 돌린다. 시간에 따라 밝기가 단조 증가하는 영상이라
    2.85s 프레임과 진짜 마지막 프레임은 확실히 다른 값을 낸다.
    """
    from PIL import Image

    clip = tmp_path / "ramp.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"gradients=s=128x128:d={duration}:speed=0.15",
            "-t", str(duration), "-r", "24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
        timeout=120,
    )
    ref = tmp_path / "ref.png"
    Image.new("RGB", (128, 128), (128, 128, 128)).save(ref)

    res = validate_idle_video(clip.read_bytes(), ref.read_bytes(), template_key="IDLE_BREATH")

    assert res.video_duration_sec == pytest.approx(duration, abs=0.25)
    assert res.ssim_first_vs_true_last is not None, "새 지표가 채워지지 않았다"
    # duration 에서 유도된 지점이어야 한다 — 2.85 고정이 아니라.
    assert true_last_frame_sample_time(res.video_duration_sec) == pytest.approx(expect_near, abs=0.25)
    assert abs(true_last_frame_sample_time(res.video_duration_sec) - 2.85) > 0.5


@needs_ffmpeg
def test_new_metric_is_returned_and_serialised(tmp_path):
    from PIL import Image

    clip = tmp_path / "c.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "gradients=s=96x96:d=4:speed=0.15",
            "-t", "4", "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
        timeout=120,
    )
    ref = tmp_path / "r.png"
    Image.new("RGB", (96, 96), (120, 120, 120)).save(ref)

    d = validate_idle_video(clip.read_bytes(), ref.read_bytes()).to_dict()
    assert "ssim_first_vs_true_last" in d
    assert "video_duration_sec" in d
    # 기존 필드도 그대로 유지 — 하위호환.
    for legacy in ("passed", "ssim_first_vs_reference", "ssim_first_vs_last",
                   "preset_violation", "needs_human_review", "message"):
        assert legacy in d


# ══ 3. 게이트는 true-last 로 판정한다 (legacy 는 기록용) ══════════════════════


def test_new_metric_defaults_to_none_so_old_construction_still_works():
    """새 필드는 기본값이 있어 기존 호출부가 그대로 동작한다."""
    r = IdleValidationResult(
        passed=True,
        ssim_first_vs_reference=0.9,
        ssim_first_vs_last=0.9,
        preset_violation=False,
        needs_human_review=False,
        message="ok",
    )
    assert r.ssim_first_vs_true_last is None
    assert r.video_duration_sec is None


def test_default_loop_threshold_is_065():
    from backend.services.idle_validation_service import DEFAULT_LOOP_SSIM_THRESHOLD

    assert DEFAULT_LOOP_SSIM_THRESHOLD == 0.65


def _stub_metrics(monkeypatch, *, legacy: float, true_last, duration: float = 5.03):
    """
    ffmpeg/ffprobe 없이 두 지표를 원하는 값으로 고정한다.
    ss=0.15 → 첫 프레임, ss=2.85 → legacy, 그 외(duration-0.10) → true-last.
    """
    from backend.services import idle_validation_service as svc

    def fake_extract(b, ss="0.15"):
        if ss != svc.LEGACY_LAST_FRAME_SS and ss != "0.15" and true_last is None:
            return None
        return f"frame@{ss}".encode()

    monkeypatch.setattr(svc, "_extract_frame_png", fake_extract)
    monkeypatch.setattr(svc, "_probe_duration_sec", lambda b: duration)

    def fake_cmp(a, b, background_rgb=None):
        tag = b.decode() if isinstance(b, bytes) else ""
        if tag == f"frame@{svc.LEGACY_LAST_FRAME_SS}":
            return legacy
        if tag.startswith("frame@") and tag != "frame@0.15":
            return true_last
        return 0.95  # 레퍼런스 비교는 항상 통과 → 루프 게이트만 남는다

    monkeypatch.setattr(svc, "compare_reference_to_frame", fake_cmp)


def test_high_legacy_but_low_true_last_now_FAILS(monkeypatch):
    """실측 #10 형태: legacy 0.84 라 예전엔 통과했지만 실제 이음매 0.59 → 이제 실패."""
    _stub_metrics(monkeypatch, legacy=0.84, true_last=0.59)
    r = validate_idle_video(b"v", b"ref", template_key="IDLE_BREATH")
    assert r.ssim_first_vs_last == pytest.approx(0.84)
    assert r.ssim_first_vs_true_last == pytest.approx(0.59)
    assert r.preset_violation is True
    assert r.passed is False
    assert "preset_pose_drift(0.590" in r.message


def test_low_legacy_but_high_true_last_now_PASSES(monkeypatch):
    """실측 #8 형태: legacy 0.79 라 예전엔 탈락했지만 실제 이음매 0.86 → 이제 통과."""
    _stub_metrics(monkeypatch, legacy=0.79, true_last=0.86)
    r = validate_idle_video(b"v", b"ref", template_key="IDLE_BREATH")
    assert r.ssim_first_vs_last == pytest.approx(0.79)
    assert r.ssim_first_vs_true_last == pytest.approx(0.86)
    assert r.preset_violation is False
    assert r.passed is True


@pytest.mark.parametrize(
    "true_last, expect_violation",
    [(0.6499, True), (0.65, False), (0.6501, False), (0.4485, True), (0.7171, False)],
)
def test_threshold_065_applied_to_true_last_only(monkeypatch, true_last, expect_violation):
    """legacy 를 임계값 양 끝(0.99 / 0.10)에 둬도 판정이 흔들리면 안 된다."""
    for legacy in (0.99, 0.10):
        _stub_metrics(monkeypatch, legacy=legacy, true_last=true_last)
        r = validate_idle_video(b"v", b"ref", template_key="IDLE_BREATH")
        assert r.preset_violation is expect_violation, (
            f"true_last={true_last} legacy={legacy} 에서 판정이 legacy 에 끌려갔다"
        )


def test_legacy_metric_remains_in_diagnostics(monkeypatch):
    _stub_metrics(monkeypatch, legacy=0.8433, true_last=0.5884)
    d = validate_idle_video(b"v", b"ref").to_dict()
    assert d["ssim_first_vs_last"] == pytest.approx(0.8433), "legacy 지표가 진단에서 사라졌다"
    assert d["ssim_first_vs_true_last"] == pytest.approx(0.5884)
    assert d["video_duration_sec"] == pytest.approx(5.03)


def test_loop_gate_fails_open_when_true_last_unavailable(monkeypatch):
    """측정 실패가 곧바로 유료 재생성이 되면 안 된다 — fail-open."""
    _stub_metrics(monkeypatch, legacy=0.10, true_last=None)
    r = validate_idle_video(b"v", b"ref")
    assert r.ssim_first_vs_true_last is None
    assert r.preset_violation is False
    assert r.passed is True
    assert "loop_ssim_unavailable" in r.message


# ══ 4. IDLE_LOOK_AROUND 면제 제거 ═════════════════════════════════════════════


def test_look_around_exemption_removed_from_source():
    import inspect

    assert 'template_key != "IDLE_LOOK_AROUND"' not in inspect.getsource(validate_idle_video)


@pytest.mark.parametrize(
    "template_key",
    ["IDLE_BREATH", "IDLE_HEAD_TILT", "IDLE_TAIL_WAG", "IDLE_EAR_FLICK", "IDLE_LOOK_AROUND"],
)
def test_every_idle_type_is_now_loop_validated(monkeypatch, template_key):
    """닫힌 주기를 약속하는 프리셋일수록 종료 상태를 검사해야 한다."""
    _stub_metrics(monkeypatch, legacy=0.99, true_last=0.50)
    r = validate_idle_video(b"v", b"ref", template_key=template_key)
    assert r.preset_violation is True, f"{template_key} 가 루프 검사를 면제받고 있다"
    assert r.passed is False


def test_reference_ssim_logic_unchanged(monkeypatch):
    """루프 게이트와 무관하게 레퍼런스 SSIM 은 예전 임계값(0.72) 그대로."""
    from backend.services import idle_validation_service as svc

    assert svc.DEFAULT_SSIM_THRESHOLD == 0.72
    monkeypatch.setattr(svc, "_extract_frame_png", lambda b, ss="0.15": f"frame@{ss}".encode())
    monkeypatch.setattr(svc, "_probe_duration_sec", lambda b: 5.03)
    # 레퍼런스만 낮고 루프는 아주 좋게 → 레퍼런스 사유로만 실패해야 한다.
    monkeypatch.setattr(
        svc,
        "compare_reference_to_frame",
        lambda a, b, background_rgb=None: 0.50 if a == b"ref" else 0.95,
    )
    r = validate_idle_video(b"v", b"ref")
    assert r.passed is False
    assert "low_ssim_vs_reference(0.500<0.72)" in r.message
    assert r.preset_violation is False
