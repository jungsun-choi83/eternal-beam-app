"""
COME_PLAY v2 motion quality (2026-10-30) — what the first live run audit added:

* motion-aware segment trimming at the join (stationary lead-in, excess bow hold) that only
  ever cuts inside quiet runs, keeps the outer endpoint frames and refuses cuts that would
  create a visible jump;
* a deterministic motion profile (stationary time, approach growth/progression, bow hold,
  return progression) attached as QUALITY_ADVISORY diagnostics — REVIEW at most, never FAIL;
* dense gait windows for the VLM (two runs of three consecutive 12 fps frames) and three
  advisory gait/turn fields whose "no" is REVIEW, while broken anatomy stays a hard FAIL;
* motion-scoped versions — PLAY_WITH_ME keeps join v1 and no profile.

No paid call anywhere: providers, joiners and the VLM are injected; the only real tool is
ffmpeg on synthetic clips (skipped when ffmpeg is absent).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import os

import numpy as np
import pytest

from backend.services import (
    business_qa,
    motion_profile as mp,
    motion_spec as ms,
    motion_video_qa,
    motion_video_segments as seg,
    motion_video_service as mv,
    species_motion_variants as sv,
    vlm_escalation,
    vlm_identity,
)

from .test_canonical_pet_builder import GOOD
from .test_come_play import (
    CP,
    PWM,
    _build_come_play,
    _contract,
    _prepare_come_play,
    come_play_evidence,
    judge_come_play,
)
from .test_motion_video_generation import (  # noqa: F401 — fixtures (incl. the autouse mock backend)
    FakeVideoProvider,
    _build_motion,
    _mock_backend,
    storage,
)
from .test_play_with_me import _prepare_play, passthrough_join


def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe 필요")

FPS = 24
W, H = 160, 90
BG = 80


# ══════════════════════════════════════════════════════════════════════════
# Synthetic clips: a bright square that idles, travels/grows, then holds
# ══════════════════════════════════════════════════════════════════════════


def _square_frame(cx: float, cy: float, size: float, *, wag: float = 0.0) -> np.ndarray:
    frame = np.full((H, W), BG, dtype=np.uint8)
    half = max(2, int(size / 2))
    x0, x1 = int(cx - half), int(cx + half)
    y0, y1 = int(cy - half), int(cy + half)
    frame[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = 200
    if wag:  # a small "tail" pixel block that moves while the body holds still
        tx = int(min(W - 4, max(0, x1 + 2 + wag)))
        frame[max(0, y0):max(0, y0) + 3, tx:tx + 3] = 200
    return frame


HOME = (80.0, 45.0, 16.0)
NEAR = (80.0, 60.0, 48.0)


def _lerp(t: float, a, b):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _segment_a_frames(idle_sec=1.0, travel_sec=1.0, hold_sec=0.75) -> list[np.ndarray]:
    frames = []
    for _ in range(int(idle_sec * FPS)):
        frames.append(_square_frame(*HOME))
    n = int(travel_sec * FPS)
    for i in range(n):
        frames.append(_square_frame(*_lerp((i + 1) / n, HOME, NEAR)))
    for _ in range(int(hold_sec * FPS)):
        frames.append(_square_frame(*NEAR))
    return frames


def _segment_b_frames(hold_sec=1.0, travel_sec=1.0, settle_sec=0.5, *, alive=False) -> list[np.ndarray]:
    frames = []
    for i in range(int(hold_sec * FPS)):
        frames.append(_square_frame(*NEAR, wag=(i % 6) if alive else 0.0))
    n = int(travel_sec * FPS)
    for i in range(n):
        frames.append(_square_frame(*_lerp((i + 1) / n, NEAR, HOME)))
    for _ in range(int(settle_sec * FPS)):
        frames.append(_square_frame(*HOME))
    return frames


def _encode(frames: list[np.ndarray]) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "clip.mp4")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", out],
            input=np.stack(frames).tobytes(), capture_output=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        with open(out, "rb") as fh:
            return fh.read()


def _timeline_from_frames(frames: list[np.ndarray]) -> mp.MotionTimeline:
    gray = mp.GrayTimeline(frames=np.stack(frames), fps=float(FPS), duration_sec=len(frames) / FPS)
    timeline = mp.motion_timeline(gray)
    assert timeline is not None
    return timeline


# ══════════════════════════════════════════════════════════════════════════
# Trim planning (pure numpy)
# ══════════════════════════════════════════════════════════════════════════


def test_trim_plan_cuts_only_inside_quiet_runs_and_keeps_the_outer_endpoints():
    a = _timeline_from_frames(_segment_a_frames(idle_sec=1.25, hold_sec=0.75))
    b = _timeline_from_frames(_segment_b_frames(hold_sec=1.5))
    plan = mp.plan_come_play_trim([a, b])
    assert plan["applied"] is True and plan["version"] == "come-play-trim-v1"
    pa, pb = plan["_plans"]
    keep_lead = int(round(0.25 * FPS))
    # Lead-in: the first 0.25 s (incl. frame 0 = the STAND_READY still) stays, the rest of the idle goes.
    assert pa.lead_trim_frames > 0 and pa.drop_ranges[0][0] == keep_lead
    assert pa.drop_ranges[0][1] <= pa.lead_quiet_frames
    # Seam: the combined hold shrinks to the target without touching moving frames.
    assert plan["seam_hold_sec_before"] > 0.6 >= plan["seam_hold_sec_after"]
    assert pb.head_trim_frames > 0 and pb.drop_ranges == ((1, 1 + pb.head_trim_frames),)
    assert pb.head_trim_frames <= pb.lead_quiet_frames - 1
    assert pa.tail_trim_frames <= max(0, pa.tail_quiet_frames - 1)
    # B's settle tail (the STAND_READY ending) is never cut; nothing outside quiet runs is.
    assert all(end <= pb.lead_quiet_frames for _, end in pb.drop_ranges)
    for start, end in pa.drop_ranges:
        assert start >= keep_lead
        assert end <= pa.lead_quiet_frames or start >= a.frame_count - pa.tail_quiet_frames
    assert plan["trimmed_sec"] == pytest.approx((pa.dropped_frames + pb.dropped_frames) / FPS, abs=1e-3)
    # Identical hold frames: the cut pair joins frames that differ by (near) nothing.
    assert plan["seam_cut_diff"] is not None and plan["seam_cut_diff"] <= plan["cut_bound"]


def test_trim_plan_refuses_cuts_that_would_jump_and_degrades_to_v1_when_unmeasurable():
    a = _timeline_from_frames(_segment_a_frames(idle_sec=0.2, hold_sec=0.4))
    # An "alive" hold whose tail wags far enough that no later frame resembles A's last frame.
    b_frames = _segment_b_frames(hold_sec=1.5, alive=True)
    for i, frame in enumerate(b_frames[: int(1.5 * FPS)]):
        frame[60:80, 10 + i:14 + i] = 200  # a drifting blob: every hold frame differs a lot
    b = _timeline_from_frames(b_frames)
    tight = mp.TrimPolicy(cut_bound_floor=0.5, cut_bound_ratio=0.0)
    plan = mp.plan_come_play_trim([a, b], policy=tight)
    assert plan["segments"][1]["head_trimmed_sec"] == 0.0, "no cut within the bound → seam left as v1"
    assert plan["seam_cut_diff"] is None
    # Unmeasurable input never raises and never trims.
    assert mp.plan_come_play_trim([None, b])["applied"] is False
    assert mp.plan_come_play_trim([a])["applied"] is False
    assert mp.decode_gray_timeline(b"not a video") is None


def test_quiet_threshold_and_profile_policy_come_from_env_with_measured_defaults(monkeypatch):
    energy = np.array([0.0] + [1.0] * 10 + [30.0] * 40 + [2.0] * 5)
    assert mp.quiet_threshold(energy) == pytest.approx(max(1.5, 0.15 * 30.0))
    assert mp.leading_quiet(energy, 4.5) == 11 and mp.trailing_quiet(energy, 4.5) == 5
    monkeypatch.setenv("MOTION_PROFILE_QUIET_RATIO", "0.5")
    assert mp.quiet_threshold(energy) == pytest.approx(15.0)
    monkeypatch.setenv("COME_PLAY_PROFILE_HOLD_MAX_SEC", "2.5")
    assert mp.come_play_profile_policy().bow_hold_max_sec == 2.5
    assert mp.come_play_trim_policy().seam_hold_target_sec == 0.6
    monkeypatch.setenv("COME_PLAY_TRIM_SEAM_HOLD_SEC", "0.3")
    assert mp.come_play_trim_policy().seam_hold_target_sec == 0.3


# ══════════════════════════════════════════════════════════════════════════
# Join with real ffmpeg
# ══════════════════════════════════════════════════════════════════════════


@needs_ffmpeg
def test_join_v2_trims_the_idle_and_the_hold_but_v1_and_play_with_me_are_unchanged():
    a_frames = _segment_a_frames(idle_sec=1.25, hold_sec=0.75)
    b_frames = _segment_b_frames(hold_sec=1.5)
    a_bytes, b_bytes = _encode(a_frames), _encode(b_frames)

    v1, meta_v1 = seg.join_segment_videos([a_bytes, b_bytes])
    assert meta_v1["version"] == "motion-segment-join-v1" and "trim" not in meta_v1
    assert meta_v1["joined_frames"] == len(a_frames) + len(b_frames) - 1

    v2, meta = seg.join_segment_videos(
        [a_bytes, b_bytes], trim_policy=mp.come_play_trim_policy(),
        join_version=seg.COME_PLAY_SEGMENT_JOIN_VERSION,
    )
    assert meta["version"] == "motion-segment-join-v2-come-play-v1"
    assert meta["trim"]["applied"] is True and "_plans" not in meta["trim"]
    assert meta["trimmed_sec"] > 1.0
    assert meta["joined_frames"] == sum(meta["kept_frames"]) and meta["seam_frame"] == meta["kept_frames"][0]
    assert meta["joined_frames"] < meta_v1["joined_frames"]
    # Outer endpoints survive: first frame ≈ A's first, last frame ≈ B's last.
    joined_gray = mp.decode_gray_timeline(v2, width=W)
    assert joined_gray is not None
    assert np.mean(np.abs(joined_gray.frames[0].astype(int) - a_frames[0].astype(int))) < 4
    assert np.mean(np.abs(joined_gray.frames[-1].astype(int) - b_frames[-1].astype(int))) < 4
    # No cut lands inside a moving run: every adjacent-frame difference in the joined clip stays
    # below the largest adjacent difference of the untrimmed sources (no new jump).
    src = mp.motion_timeline(mp.decode_gray_timeline(v1, width=W))
    out = mp.motion_timeline(joined_gray)
    assert out is not None and src is not None
    assert out.energy.max() <= src.energy.max() * 1.1

    # The profile reads the trimmed asset as fixed where v1 was slow.
    before = mp.evaluate_come_play_profile(src, seam_frame=len(a_frames))
    after = mp.evaluate_come_play_profile(out, seam_frame=meta["seam_frame"])
    assert before["checks"]["come_play_profile_lead_stationary"] == "REVIEW"
    assert before["checks"]["come_play_profile_bow_hold"] == "REVIEW"
    assert after["checks"]["come_play_profile_lead_stationary"] == "PASS"
    assert after["checks"]["come_play_profile_bow_hold"] == "PASS"
    assert after["checks"]["come_play_profile_approach_growth"] == "PASS"
    assert after["checks"]["come_play_profile_approach_progression"] == "PASS"
    assert after["metrics"]["lead_stationary_sec"] <= 0.6
    assert set(after["checks"]) == set(mp.COME_PLAY_PROFILE_CHECK_NAMES)
    assert all(v in ("PASS", "REVIEW", "unknown") for v in after["checks"].values()), "never FAIL"


@needs_ffmpeg
def test_profile_flags_a_return_crammed_into_the_last_half_second():
    a_frames = _segment_a_frames(idle_sec=0.2, hold_sec=0.3)
    # B: hold, then stays big for 3 s (sideways walk), then shrinks only in the last 0.4 s.
    b_frames = []
    for _ in range(int(0.3 * FPS)):
        b_frames.append(_square_frame(*NEAR))
    for i in range(int(3.0 * FPS)):
        b_frames.append(_square_frame(NEAR[0] + 10 * np.sin(i / 4.0), NEAR[1], NEAR[2]))
    n = int(0.4 * FPS)
    for i in range(n):
        b_frames.append(_square_frame(*_lerp((i + 1) / n, NEAR, HOME)))
    for _ in range(int(0.3 * FPS)):
        b_frames.append(_square_frame(*HOME))
    joined, meta = seg.join_segment_videos([_encode(a_frames), _encode(b_frames)])
    timeline = mp.motion_timeline(mp.decode_gray_timeline(joined, width=W))
    profile = mp.evaluate_come_play_profile(timeline, seam_frame=len(a_frames))
    assert profile["checks"]["come_play_profile_return_progression"] == "REVIEW"
    assert profile["metrics"]["return_arrival_before_end_sec"] < 1.0
    assert profile["status"] == "REVIEW"


def test_profile_is_unmeasurable_without_a_timeline_or_a_valid_seam():
    assert mp.evaluate_come_play_profile(None, seam_frame=10)["status"] == "unmeasurable"
    t = _timeline_from_frames(_segment_a_frames())
    assert mp.evaluate_come_play_profile(t, seam_frame=None)["status"] == "unmeasurable"
    assert mp.evaluate_come_play_profile(t, seam_frame=0)["status"] == "unmeasurable"
    assert mp.evaluate_come_play_profile(t, seam_frame=10 ** 6)["checks"] == {}


# ══════════════════════════════════════════════════════════════════════════
# Attaching the profile + judgement semantics
# ══════════════════════════════════════════════════════════════════════════


def _segments_qa(decision="PASS"):
    return {"version": seg.SEGMENT_QA_VERSION,
            "checks": {name: "PASS" for name in sv.COME_PLAY_SEGMENT_CHECK_NAMES},
            "metrics": {}, "reasons": [], "decision": decision}


def test_attach_motion_profile_only_ever_downgrades_pass_to_review():
    profile = {"version": mp.COME_PLAY_PROFILE_QA_VERSION, "status": "REVIEW",
               "checks": {"come_play_profile_bow_hold": "REVIEW", "come_play_profile_lead_stationary": "PASS"},
               "metrics": {"bow_hold_sec": 2.4}, "reasons": ["bow_hold 2.4s > 1.2s"]}
    merged = seg.attach_motion_profile(_segments_qa(), profile)
    assert merged["decision"] == "REVIEW" and merged["profile"]["metrics"]["bow_hold_sec"] == 2.4
    assert merged["checks"]["come_play_profile_bow_hold"] == "REVIEW"
    assert "profile:bow_hold 2.4s > 1.2s" in merged["reasons"]
    assert seg.attach_motion_profile(_segments_qa("FAIL"), profile)["decision"] == "FAIL"
    clean = {**profile, "status": "PASS", "checks": {k: "PASS" for k in profile["checks"]}, "reasons": []}
    assert seg.attach_motion_profile(_segments_qa(), clean)["decision"] == "PASS"
    unmeasurable = {"version": mp.COME_PLAY_PROFILE_QA_VERSION, "status": "unmeasurable", "checks": {}, "metrics": {}, "reasons": []}
    assert seg.attach_motion_profile(_segments_qa(), unmeasurable)["decision"] == "PASS"
    assert seg.attach_motion_profile(_segments_qa(), None) == _segments_qa()


def test_advisory_gait_fields_and_profile_reviews_deliver_with_advisory_but_anatomy_still_blocks():
    # Advisory VLM "no" → REVIEW check, quality REVIEW, DELIVER_WITH_ADVISORY, no regeneration.
    for field in ("run_gait_coordinated", "run_ground_contact_plausible", "turn_natural"):
        judged = judge_come_play(come_play_evidence(**{field: "no"}))
        check = next(c for c, k in sv.COME_PLAY_ADVISORY_CHECKS if k == field)
        assert judged["checks"][check] == "REVIEW", field
        assert judged["decision"] == "REVIEW"
        assert judged["business_qa"]["integrity_status"] == "PASS"
        assert judged["business_qa"]["quality_status"] == "REVIEW"
        assert judged["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
        assert check in judged["business_qa"]["authority_evidence"]["QUALITY_ADVISORY"]
        assert not business_qa.should_regenerate(judged)
    # Profile REVIEW rides along with the segment checks and is advisory too.
    profile_checks = {**{n: "PASS" for n in sv.COME_PLAY_SEGMENT_CHECK_NAMES},
                      "come_play_profile_bow_hold": "REVIEW", "come_play_profile_lead_stationary": "PASS"}
    judged = judge_come_play(come_play_evidence(), segment_checks=profile_checks)
    assert judged["decision"] == "REVIEW" and judged["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert judged["business_qa"]["authority_evidence"]["QUALITY_ADVISORY"]["come_play_profile_bow_hold"] == "REVIEW"
    assert mv.candidate_is_publishable({"motion_id": CP, "decision": "REVIEW", "qa_result": judged})
    # Hard anatomy stays hard: a broken limb blocks even when every advisory field is yes.
    broken = judge_come_play(come_play_evidence(anatomy_plausible_all_frames="no"))
    assert broken["decision"] == "FAIL" and broken["business_qa"]["delivery_action"] == "BLOCK"
    broken = judge_come_play(come_play_evidence(joint_anatomy_plausible="no"))
    assert broken["decision"] == "FAIL" and broken["business_qa"]["delivery_action"] == "BLOCK"
    # A sliding/zooming "approach" is still a functional failure (hard), unknown is REVIEW.
    assert judge_come_play(come_play_evidence(approach_gait_natural="no"))["decision"] == "FAIL"
    assert judge_come_play(come_play_evidence(turn_natural="unknown"))["decision"] == "REVIEW"


# ══════════════════════════════════════════════════════════════════════════
# Dense gait windows for the VLM
# ══════════════════════════════════════════════════════════════════════════


def test_gait_windows_pick_sustained_motion_in_each_half():
    count = 120  # 10 s at 12 fps
    scores = []
    for index in range(1, count):
        value = 1.0
        if 20 <= index <= 32:
            value = 30.0  # approach run
        if 50 <= index <= 52:
            value = 80.0  # a single flicker — must not win over the sustained run
        if 80 <= index <= 90:
            value = 25.0  # the turn / trot home
        scores.append((value, index))
    windows = dict(mv._come_play_gait_windows(scores, count))
    assert set(windows) == {"approach", "return"}
    assert all(20 <= i <= 32 for i in windows["approach"]), windows
    assert all(80 <= i <= 90 for i in windows["return"]), windows
    for indices in windows.values():
        assert len(indices) == mv.COME_PLAY_GAIT_WINDOW_FRAMES
        assert [b - a for a, b in zip(indices, indices[1:])] == [mv.COME_PLAY_GAIT_WINDOW_STRIDE] * 2
    assert mv._come_play_gait_windows([], 0) == []


def _frame(level: int) -> np.ndarray:
    frame = np.full((48, 64, 3), 80, dtype=np.uint8)
    frame[10:40, 20 + (level % 20):44 + (level % 20)] = 200
    return frame


def test_come_play_evidence_adds_two_consecutive_windows_and_keeps_absence_unreliable():
    base_fractions = motion_video_qa.SAMPLE_FRACTIONS
    base_frames = [_frame(0) for _ in base_fractions]
    dense_frames = [_frame(i if 20 <= i <= 40 or 90 <= i <= 110 else 0) for i in range(132)]
    dense = {"frames": dense_frames, "fractions": [i / 131 for i in range(132)], "requested_fps": 12.0,
             "decoded_frames": 132, "duration_sec": 11.0, "coverage_ratio": 1.0}
    routed = mv._come_play_gait_evidence(base_frames=base_frames, base_fractions=base_fractions, dense_scan=dense)
    prov = routed.provenance
    assert prov["version"] == mv.COME_PLAY_VLM_SAMPLING_VERSION == "motion-vlm-sampling-v2-come-play-v2"
    assert prov["strategy"] == "come_play_gait_windows" and prov["dense_scan_attempted"] is True
    assert prov["absence_reliable"] is False, "15 frames still cannot prove a brief event never happened"
    assert [w["segment"] for w in prov["gait_windows"]] == ["approach", "return"]
    assert all(len(w["fractions"]) == 3 and w["consecutive"] for w in prov["gait_windows"])
    assert prov["gait_window_spacing_sec"] == pytest.approx(2 / 12, abs=1e-3)
    assert len(routed) == 9 + 6 == mv.COME_PLAY_VLM_MAX_FRAMES
    assert prov["evidence_fractions"] == sorted(prov["evidence_fractions"])
    assert all(0.15 < f < 0.31 for f in prov["gait_windows"][0]["fractions"])
    assert all(0.68 < f < 0.85 for f in prov["gait_windows"][1]["fractions"])
    # Without a dense scan the nine base frames go through untouched.
    plain = mv._come_play_gait_evidence(base_frames=base_frames, base_fractions=base_fractions, dense_scan=None)
    assert len(plain) == 9 and plain.provenance["strategy"] == "come_play_gait_windows_unavailable"


def test_breathing_evidence_routes_come_play_through_the_dense_gait_scan(monkeypatch):
    seen = {}

    def fake_dense(video_bytes, *, fps, max_frames):
        seen.update(fps=fps, max_frames=max_frames)
        return None

    monkeypatch.setattr(motion_video_qa, "sample_dense_frames", fake_dense)
    import asyncio

    _temporal, frames, fractions = asyncio.run(
        mv._breathing_evidence(CP, b"video", None, [_frame(0)] * 9)
    )
    assert seen == {"fps": 12.0, "max_frames": mv.COME_PLAY_DENSE_SCAN_MAX_FRAMES}
    assert len(frames) == 9 and tuple(fractions) == tuple(motion_video_qa.SAMPLE_FRACTIONS)
    assert frames.provenance["motion_id"] == CP
    # PLAY_WITH_ME keeps its own salience routing (9 salient base frames → no dense scan).
    seen.clear()
    moving = [_frame(i * 7) for i in range(9)]
    asyncio.run(mv._breathing_evidence(PWM, b"video", None, moving))
    assert seen == {}, "the dense COME_PLAY scan is COME_PLAY-only"


def test_vlm_prompt_and_schema_carry_the_gait_window_fields():
    acceptance = ms.acceptance_for_variant(ms.MOTIONS[CP], sv.COME_PLAY_BOW)
    specs = vlm_identity._motion_targeted_specs(
        questions=["motion_correctness", "temporal_integrity"],
        motion_description=sv.COME_PLAY_BOW.description, motion_class="INTERACTION",
        expected_direction=None, interaction_type="come_play", allow_generated_hand=False,
        has_target=True, acceptance_spec=acceptance, has_intermediate=True,
        evidence_context={"temporal_sampling": {"gait_windows": [{"segment": "approach", "fractions": [0.2, 0.21, 0.23]}]}},
    )
    schema, prompt = specs[vlm_escalation.MOTION_VLM]
    for field in ("run_gait_coordinated", "run_ground_contact_plausible", "turn_natural"):
        assert field in schema["properties"] and field in prompt
    assert "GAIT WINDOWS" in prompt and "gait_windows" in prompt
    assert "answer no ONLY for a slide, float, zoom or teleport" in prompt
    assert "hips lifted higher than standing or an arched spine are NOT required" in prompt
    assert vlm_identity.motion_acceptance_evaluator_version(acceptance) == "vlm-motion-qa-v3-come-play-v2"
    import inspect

    assert inspect.signature(vlm_identity.qa_motion_video).parameters["max_frames"].default == 12


# ══════════════════════════════════════════════════════════════════════════
# Generation path (providers/joiner/VLM injected)
# ══════════════════════════════════════════════════════════════════════════


def test_generation_joins_come_play_with_the_trim_policy_and_adjusts_conformance(storage, monkeypatch):
    h = _prepare_come_play(monkeypatch, storage)
    joins = []
    conformance_specs = []

    def recording_join(videos, **kw):
        joins.append(kw)
        joined, meta = passthrough_join(videos)
        meta = {**meta, "version": kw.get("join_version") or meta["version"], "trimmed_sec": 2.0,
                "trim": {"applied": True, "trimmed_sec": 2.0}, "kept_frames": [90, 150], "seam_frame": 90}
        return joined, meta

    def conformance(video_bytes, output_spec):
        conformance_specs.append(dict(output_spec))
        return {"status": "PASS", "checks": {}, "reasons": [], "version": "test"}

    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    version = _build_motion(h, CP, [provider], conformance=conformance, segment_join_fn=recording_join)
    assert version.status == mv.STATUS_COMPLETE
    assert len(joins) == 1
    assert isinstance(joins[0]["trim_policy"], mp.TrimPolicy)
    assert joins[0]["join_version"] == seg.COME_PLAY_SEGMENT_JOIN_VERSION
    # Conformance expects the planned 13 s minus the deliberate 2 s trim, and records both.
    assert conformance_specs and conformance_specs[0]["duration_sec"] == 11.0
    assert conformance_specs[0]["planned_duration_sec"] == 13 and conformance_specs[0]["trimmed_sec"] == 2.0
    assert version.output_spec["duration_sec"] == 13, "the version row keeps the planned spec"
    selected = next(c for c in version.candidates if c.selected)
    segments = selected.generation_metadata["segments"]
    assert segments["version"] == seg.COME_PLAY_SEGMENT_JOIN_VERSION
    assert selected.generation_metadata["delivered_duration_sec"] == 11.0
    # The fake "video" is a PNG: the profile is unmeasurable and must not downgrade the decision.
    assert segments["qa"]["profile"]["status"] == "unmeasurable"
    assert selected.qa_result["decision"] == "PASS"
    assert selected.qa_result["business_qa"]["delivery_action"] == "DELIVER"
    assert not any(name in selected.qa_result["checks"] for name in mp.COME_PLAY_PROFILE_CHECK_NAMES)
    assert selected.qa_result["motion_business_contract"]["check_authority"]["vlm_come_play_turn"] == "QUALITY_ADVISORY"


def test_play_with_me_generation_still_joins_without_trimming(storage, monkeypatch):
    h, _role = _prepare_play(monkeypatch, storage, "DOG")
    joins = []

    def recording_join(videos, **kw):
        joins.append(kw)
        return passthrough_join(videos)

    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    version = _build_motion(h, PWM, [provider], segment_join_fn=recording_join)
    assert version.status == mv.STATUS_COMPLETE
    assert joins == [{}], "PLAY_WITH_ME keeps join v1: no trim policy, no version override"
    selected = next(c for c in version.candidates if c.selected)
    assert selected.generation_metadata["segments"]["version"] == seg.SEGMENT_JOIN_VERSION
    assert "profile" not in selected.generation_metadata["segments"]["qa"]
    assert "delivered_duration_sec" not in selected.generation_metadata
