"""
모션 비디오 QA (Phase 6) — 프레임 샘플링 기반, fail-open 금지.

── 왜 기존 SSIM 검증보다 강한가 ────────────────────────────────────────────
idle_validation_service 는 첫/끝 프레임 SSIM 만 본다. 여기서는:
  * 영상 전체에서 결정론적으로 샘플한 프레임(0/12.5/.../87.5/true-last)마다
    시작 키프레임 대비 시그니처 유사도 — 시간축 신원 드리프트를 잡는다
  * 인접 프레임 간 급변 — 플리커/장면 컷/펫 교체
  * returns_to_start_pose 모션: 마지막≈첫 프레임
  * TRANSITION: 첫 프레임≈시작 키프레임, 마지막 프레임≈**목표** 키프레임
  * VLM(옵션): 프레임 시퀀스에 대한 same-pet/해부학/요청 모션/시간 품질 확인

── 판정 ────────────────────────────────────────────────────────────────────
Phase 4/5 와 같은 철학: 측정 실패는 unknown → 최대 REVIEW. VLM 확언 없이는
PASS 불가. FAIL 후보는 절대 승격되지 않는다 — fail-open 은 없다.

시그니처는 전체 프레임 기준이다(마스크 없음) — 배경이 잠긴 중립 배경 + 카메라
고정이라는 Phase 6 생성 계약 위에서만 의미가 있다. 배경이 흔들리면 그것 자체가
계약 위반이고 유사도가 떨어져 잡힌다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# v3: BREATHING 시간축 증거(breathing_temporal_qa) 통합 — vlm_motion=unknown 을
#     결정론 증거로 해소할 수 있고, 전신 펄스/드리프트가 명시적 사유가 된다.
#     VLM "no" 는 여전히 FAIL 이고 시간축 증거는 상반된 VLM 증거를 뒤집지 않는다.
MOTION_VIDEO_QA_VERSION = "motion-video-qa-v3"
FRAME_SAMPLING_VERSION = "frame-sampling-v2"

#: 결정론적 샘플 지점. 마지막은 끝 구간을 순차 디코딩한 실제 마지막 프레임.
# v1 의 1/4 간격은 5초 동안 약 두 번 반복되는 BREATHING 과 위상이 겹쳐
# 미세한 주기 운동을 정지 화면처럼 보이게 할 수 있었다. v2 는 1/8 간격으로
# 중간 위상을 보존하고, 1.0 은 duration-0.10 추정값이 아니라 디코딩된 마지막
# 프레임을 사용한다.
SAMPLE_FRACTIONS = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)
_TRUE_LAST_OFFSET_SEC = 0.10

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"


def _f(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except ValueError:
        return default


# ══════════════════════════════════════════════════════════════════════════
# 프레임 샘플링 (ffmpeg — 주입 가능)
# ══════════════════════════════════════════════════════════════════════════


def _probe_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def sample_frames(
    video_bytes: bytes, fractions: Optional[tuple[float, ...]] = None
) -> Optional[list[Optional[np.ndarray]]]:
    """
    영상 → 지정 분율(기본 SAMPLE_FRACTIONS) 지점의 RGB 프레임들. ffmpeg/ffprobe 가
    없거나 실패하면 None — 호출자는 측정 불가(unknown → REVIEW 상한)로 다룬다.
    """
    from PIL import Image

    if not video_bytes:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="eb_motion_qa_") as td:
            path = os.path.join(td, "input.mp4")
            with open(path, "wb") as tmp:
                tmp.write(video_bytes)
            duration = _probe_duration(path)
            if not duration or duration <= 0:
                return None
            frames: list[Optional[np.ndarray]] = []
            for frac in (fractions if fractions is not None else SAMPLE_FRACTIONS):
                if frac == 1.0:
                    # 실제 재생 이음매는 마지막 디코딩 프레임 → 첫 프레임이다.
                    # 끝 근처를 디코딩한 뒤 마지막 산출물을 고르면 duration 정확히
                    # seek 했을 때 프레임이 안 나오는 문제도 피할 수 있다.
                    tail_dir = os.path.join(td, "tail")
                    os.makedirs(tail_dir, exist_ok=True)
                    tail_pattern = os.path.join(tail_dir, "frame_%05d.png")
                    tail_sec = min(0.5, duration)
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-v", "quiet", "-sseof", f"-{tail_sec:.3f}",
                         "-i", path, "-vsync", "0", tail_pattern],
                        capture_output=True, timeout=60,
                    )
                    files = sorted(
                        os.path.join(tail_dir, name)
                        for name in os.listdir(tail_dir)
                        if name.endswith(".png")
                    )
                    if r.returncode != 0 or not files:
                        # 일부 컨테이너는 -sseof 디코딩을 지원하지 않는다. 측정 불가로
                        # 버리지 않고 v1 의 안전한 끝-0.10초 방식으로 폴백한다.
                        t = max(0.0, duration - _TRUE_LAST_OFFSET_SEC)
                        out_path = os.path.join(td, "last_fallback.png")
                        r = subprocess.run(
                            ["ffmpeg", "-y", "-v", "quiet", "-ss", f"{t:.3f}",
                             "-i", path, "-frames:v", "1", out_path],
                            capture_output=True, timeout=60,
                        )
                        files = [out_path] if r.returncode == 0 and os.path.isfile(out_path) else []
                    try:
                        with Image.open(files[-1]) as im:
                            frames.append(np.asarray(im.convert("RGB"), dtype=np.uint8))
                    except Exception:
                        frames.append(None)
                    continue

                t = min(duration * frac, max(0.0, duration - _TRUE_LAST_OFFSET_SEC))
                out_path = os.path.join(td, f"frame_{len(frames):02d}.png")
                r = subprocess.run(
                    ["ffmpeg", "-y", "-v", "quiet", "-ss", f"{t:.3f}", "-i", path,
                     "-frames:v", "1", out_path],
                    capture_output=True, timeout=60,
                )
                if r.returncode != 0:
                    frames.append(None)
                    continue
                try:
                    with Image.open(out_path) as im:
                        frames.append(np.asarray(im.convert("RGB"), dtype=np.uint8))
                except Exception:
                    frames.append(None)
            return frames if any(f is not None for f in frames) else None
    except Exception:
        logger.warning("프레임 샘플링 실패", exc_info=True)
        return None


def _frame_signature(rgb: np.ndarray) -> Optional[dict[str, Any]]:
    """전체 프레임 시그니처 — RGBA 로 승격해 기존 시그니처 코드를 재사용한다."""
    from .pet_identity_service import compute_reference_signature

    if rgb is None:
        return None
    h, w = rgb.shape[:2]
    rgba = np.dstack([rgb, np.full((h, w), 255, dtype=np.uint8)])
    return compute_reference_signature(rgba)


# ══════════════════════════════════════════════════════════════════════════
# 평가
# ══════════════════════════════════════════════════════════════════════════


def evaluate_motion_video(
    *,
    frames: Optional[list[Optional[np.ndarray]]],
    spec_contract: dict[str, Any],
    start_keyframe_rgb: Optional[np.ndarray],
    target_keyframe_rgb: Optional[np.ndarray],
    vlm_qa: Optional[dict[str, Any]],
    temporal_qa: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from .pet_identity_service import signature_similarity

    checks: dict[str, str] = {}
    reasons: list[str] = []
    identity_similarity: Optional[float] = None
    frame_similarities: list[Optional[float]] = []

    id_pass = _f("PHASE6_QA_IDENTITY_PASS", 0.55)
    id_fail = _f("PHASE6_QA_IDENTITY_FAIL", 0.20)
    adj_fail = _f("PHASE6_QA_ADJACENT_FAIL", 0.20)
    adj_review = _f("PHASE6_QA_ADJACENT_REVIEW", 0.50)
    # idle_validation_service 에서 사람 눈으로 판정한 10개 클립으로 보정된 동일
    # first↔last SSIM 기준을 재사용한다. v1 의 HSV histogram 0.85 는 중립 배경의
    # 1~2 RGB 단계 변화가 bin 경계를 넘을 때 자세가 같아도 급락했다.
    loop_ssim_min = _f("PHASE6_QA_LOOP_SSIM_MIN", 0.65)
    end_pass = _f("PHASE6_QA_ENDPOINT_PASS", 0.55)
    end_fail = _f("PHASE6_QA_ENDPOINT_FAIL", 0.25)

    valid = [f for f in (frames or []) if f is not None]
    sigs = [_frame_signature(f) if f is not None else None for f in (frames or [])]
    start_sig = _frame_signature(start_keyframe_rgb) if start_keyframe_rgb is not None else None

    if not valid or start_sig is None:
        checks["identity_over_time"] = "unknown"
        checks["temporal_stability"] = "unknown"
        reasons.append("frame_sampling_unavailable")
    else:
        # ── 신원 드리프트: 모든 샘플 프레임 vs 시작 키프레임 ─────────────
        sims = []
        for s in sigs:
            if s is None:
                frame_similarities.append(None)
                continue
            sim = signature_similarity(s, start_sig)
            v = sim.get("hist_intersection") if sim.get("comparable") else None
            frame_similarities.append(v)
            if v is not None:
                sims.append(v)
        if sims:
            identity_similarity = round(float(np.mean(sims)), 4)
            worst = min(sims)
            if worst < id_fail:
                checks["identity_over_time"] = FAIL
                reasons.append(f"identity_drift worst_frame {round(worst, 3)} < {id_fail}")
            elif worst >= id_pass:
                checks["identity_over_time"] = PASS
            else:
                checks["identity_over_time"] = REVIEW
                reasons.append(f"identity borderline worst_frame {round(worst, 3)}")
        else:
            checks["identity_over_time"] = "unknown"
            reasons.append("no_comparable_frames")

        # ── 시간 안정성: 인접 프레임 급변 ────────────────────────────────
        adjacent = []
        for a, b in zip(sigs, sigs[1:]):
            if a and b:
                sim = signature_similarity(a, b)
                if sim.get("comparable"):
                    adjacent.append(sim["hist_intersection"])
        if adjacent:
            worst_adj = min(adjacent)
            if worst_adj < adj_fail:
                checks["temporal_stability"] = FAIL
                reasons.append(f"scene_cut_or_swap adjacent {round(worst_adj, 3)} < {adj_fail}")
            elif worst_adj < adj_review:
                checks["temporal_stability"] = REVIEW
                reasons.append(f"flicker adjacent {round(worst_adj, 3)}")
            else:
                checks["temporal_stability"] = PASS
        else:
            checks["temporal_stability"] = "unknown"

    # ── 루프 복귀 (returns_to_start_pose) ────────────────────────────────
    compat = spec_contract.get("video_compat") or {}
    loop_metrics: Optional[dict[str, Any]] = None
    if compat.get("returns_to_start_pose"):
        first_sig, last_sig = (sigs[0] if sigs else None), (sigs[-1] if sigs else None)
        first_frame = (frames[0] if frames else None)
        last_frame = (frames[-1] if frames else None)
        if first_sig and last_sig and first_frame is not None and last_frame is not None:
            sim = signature_similarity(first_sig, last_sig)
            hist = sim.get("hist_intersection") if sim.get("comparable") else None
            phash = sim.get("phash_hamming") if sim.get("comparable") else None
            try:
                from .idle_validation_service import _ssim_rgb

                ssim = _ssim_rgb(
                    first_frame.astype(np.float64), last_frame.astype(np.float64)
                )
            except Exception:
                ssim = None
            loop_metrics = {
                "metric": "global_grayscale_ssim",
                "ssim_first_vs_decoded_last": (
                    round(float(ssim), 6) if ssim is not None else None
                ),
                "ssim_min": loop_ssim_min,
                # v1 값은 회귀 진단용으로 남기되 판정에는 쓰지 않는다.
                "legacy_hist_intersection": (
                    round(float(hist), 6) if hist is not None else None
                ),
                "phash_hamming": phash,
            }
            if ssim is None:
                checks["loop_return"] = "unknown"
            elif ssim >= loop_ssim_min:
                checks["loop_return"] = PASS
            else:
                checks["loop_return"] = REVIEW
                reasons.append(
                    f"loop_ssim_below_threshold {round(float(ssim), 3)} < {loop_ssim_min}"
                )
        else:
            checks["loop_return"] = "unknown"
            reasons.append("loop_return_unmeasurable")

    # ── TRANSITION 시작/목표 도달 (결정론) ───────────────────────────────
    if str(spec_contract.get("motion_class")) == "TRANSITION":
        target_sig = _frame_signature(target_keyframe_rgb) if target_keyframe_rgb is not None else None
        first_sig, last_sig = (sigs[0] if sigs else None), (sigs[-1] if sigs else None)

        def _endpoint(sig_a, sig_b, label: str) -> str:
            if not sig_a or not sig_b:
                reasons.append(f"{label}_unmeasurable")
                return "unknown"
            sim = signature_similarity(sig_a, sig_b)
            v = sim.get("hist_intersection") if sim.get("comparable") else None
            if v is None:
                return "unknown"
            if v < end_fail:
                reasons.append(f"{label}_not_reached {round(v, 3)} < {end_fail}")
                return FAIL
            if v >= end_pass:
                return PASS
            reasons.append(f"{label}_borderline {round(v, 3)}")
            return REVIEW

        checks["starts_at_start_pose"] = _endpoint(first_sig, start_sig, "start_pose")
        checks["reaches_target_pose"] = _endpoint(last_sig, target_sig, "target_pose")

    # ── VLM 확인 ─────────────────────────────────────────────────────────
    def v(key: str) -> str:
        return str((vlm_qa or {}).get(key) or "unknown")

    if vlm_qa:
        mapping = [
            ("vlm_same_pet", "same_pet_all_frames", True),
            ("vlm_anatomy", "anatomy_plausible_all_frames", True),
            ("vlm_motion", "requested_motion_occurs", True),
        ]
        for check_name, key, positive in mapping:
            val = v(key)
            if val == ("no" if positive else "yes"):
                checks[check_name] = FAIL
                reasons.append(f"vlm:{key}={val}")
            elif val == ("yes" if positive else "no"):
                checks[check_name] = PASS
            else:
                checks[check_name] = "unknown"

        composition = PASS
        if v("duplicated_pet") == "yes" or v("scene_cut") == "yes" or v("human_present") == "yes" and not (
            (compat.get("allow_generated_hand")) and str(spec_contract.get("motion_class")) == "INTERACTION"
        ):
            composition = FAIL
            reasons.append("vlm_composition_contaminated")
        elif v("unintended_large_motion") == "yes" and str(spec_contract.get("motion_class")) == "MICRO":
            composition = FAIL
            reasons.append("vlm_unintended_large_motion")
        elif v("major_flicker") == "yes" or v("camera_stable") == "no" or v("background_neutral") == "no":
            composition = REVIEW
            reasons.append("vlm_temporal_or_background_issue")
        elif v("single_pet") != "yes":
            composition = "unknown"
        checks["vlm_composition"] = composition

        if str(spec_contract.get("motion_class")) == "TRANSITION":
            val = v("ends_in_target_pose")
            checks["vlm_target_pose"] = (
                PASS if val == "yes" else (FAIL if val == "no" else "unknown")
            )
            if val == "no":
                reasons.append("vlm_did_not_reach_target")
    else:
        checks["vlm_same_pet"] = "unknown"
        checks["vlm_anatomy"] = "unknown"
        checks["vlm_motion"] = "unknown"
        checks["vlm_composition"] = "unknown"
        reasons.append("vlm_qa_unavailable")

    # ── BREATHING 시간축 증거 (v3) — 사용 규칙이 계약이다 ────────────────
    #   * VLM "no" 는 이미 위에서 FAIL 이다 — 시간축 증거가 되살리지 못한다.
    #   * breathing_detected 는 vlm_motion 이 **unknown 일 때만** PASS 로 해소한다.
    #   * 전신 펄스/드리프트는 REVIEW 사유이고, VLM 이 같은 방향을 증언하면
    #     (unintended_large_motion=yes — 이미 FAIL) 자연히 FAIL 이다. 시간축
    #     증거 단독으로는 FAIL 을 만들지 않는다 (fail-closed 는 유지, 승격만 차단).
    if temporal_qa is not None:
        verdict = str(temporal_qa.get("verdict") or "unmeasurable")
        if verdict == "breathing_detected":
            checks["temporal_breathing"] = PASS
            if checks.get("vlm_motion") == "unknown":
                checks["vlm_motion"] = PASS
                reasons.append("vlm_motion_resolved_by_temporal_evidence")
        elif verdict in ("global_pulse", "unlocalized_motion"):
            checks["temporal_breathing"] = REVIEW
            reasons.append(f"temporal_{verdict}: {temporal_qa.get('reason')}")
        elif verdict == "no_motion":
            checks["temporal_breathing"] = REVIEW
            reasons.append(f"temporal_no_breathing: {temporal_qa.get('reason')}")
        # inconclusive / unmeasurable: 체크를 **추가하지 않는다** — unknown 으로
        # 넣으면 VLM 이 yes 라고 확언한 클립까지 REVIEW 로 끌어내려, "시간축
        # 증거는 상반된 VLM 증거를 뒤집지 않는다" 규칙을 어기게 된다.

    values = list(checks.values())
    if FAIL in values:
        decision = FAIL
    elif all(x == PASS for x in values):
        decision = PASS  # 결정론 + VLM 확언 전부 — unknown 이 있으면 여기 못 온다
    else:
        decision = REVIEW

    return {
        "qa_version": MOTION_VIDEO_QA_VERSION,
        "sampling_version": FRAME_SAMPLING_VERSION,
        "sample_fractions": list(SAMPLE_FRACTIONS),
        "identity_similarity": identity_similarity,
        "frame_similarities": frame_similarities,
        "loop_metrics": loop_metrics,
        "checks": checks,
        "reasons": reasons,
        "decision": decision,
        # v1 은 source/model 만 남겨 REVIEW 의 실제 설명(notes)과 원 판정을
        # 잃었다. 운영자가 직접 DB 를 추측하지 않도록 구조화 VLM 근거 전체를
        # 후보 QA 결과에 보존한다.
        "vlm": (dict(vlm_qa) if vlm_qa else None),
        # v3 — BREATHING 시간축 증거 전체 (판정·지표·임계). 없으면 None.
        "temporal": (dict(temporal_qa) if temporal_qa else None),
    }


# ══════════════════════════════════════════════════════════════════════════
# 출력 규격 검증 (Phase 6.5) — 프로바이더가 요청 사양을 실제로 지켰는가
# ══════════════════════════════════════════════════════════════════════════
#
# ── 정본 종횡비 판정 (Phase 6.5 검증 결과) ─────────────────────────────────
# 이 저장소에는 두 개의 화면 규격이 공존한다:
#   * 1280×720 (16:9)  — **장면(scene) 합성 캔버스**: scene-export.ts SCENE_W/H.
#     배경이 구워진 Phase 19 합성 레이어의 규격이다.
#   * 720×1280 (9:16)  — **펫 전용 모션 자산**: wan_service.py:22-23 이
#     "아이들 경로는 세로(Luma 는 720x1280) 전제"라고 명시하고 16:9 를 사고로
#     규정해 9:16 으로 못박았다. device-renderer 기본 캔버스도 720×1280 이다.
# Phase 6 은 테마 독립 **펫 전용** 자산을 만든다(장면 합성은 하류 레이어) —
# 따라서 정본은 9:16 이다. 아래 검증은 프로바이더가 응답에서조차 이를 어길 수
# 없게 한다: 요청은 명시했는데 출력이 다르면 QA FAIL 이다.

OUTPUT_CONFORMANCE_VERSION = "output-conformance-v1"

#: 요청 대비 허용 오차.
_ASPECT_TOLERANCE = 0.08          # |실제비율/요청비율 − 1|
_DURATION_TOLERANCE_SEC = 2.0     # 프로바이더는 길이를 양자화한다
_RESOLUTION_MIN_FRACTION = 0.9    # 요청 해상도 클래스의 최소 충족 비율


def _probe_video_streams(video_bytes: bytes) -> Optional[dict[str, Any]]:
    """ffprobe → {width, height, duration, has_audio}. 실패는 None (unknown)."""
    import json as _json

    if not video_bytes:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            path = tmp.name
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        data = _json.loads(out.stdout or "{}")
        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video:
            return None
        duration = None
        try:
            duration = float((data.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            pass
        return {
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "duration": duration,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        }
    except Exception:
        logger.warning("출력 규격 probe 실패", exc_info=True)
        return None


def _parse_ratio(text: str) -> Optional[float]:
    try:
        w, h = str(text).replace("×", ":").split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return None


def _resolution_min_side(resolution: str) -> Optional[int]:
    digits = "".join(ch for ch in str(resolution) if ch.isdigit())
    return int(digits) if digits else None


def verify_output_conformance(
    video_bytes: bytes,
    output_spec: dict[str, Any],
    *,
    probe: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    실제 출력 vs 요청 사양. 프로바이더 기본값이 요청을 덮어쓴 경우를 잡는다.
      * 종횡비/오디오 불일치 → FAIL (요청을 명시했는데 어겼다 — 규격 위반)
      * 해상도/길이 미달   → REVIEW (품질 저하, 사람 판단)
      * 측정 불가          → unknown (기록만; PASS 로 승격되지는 않는다)
    """
    meta = probe if probe is not None else _probe_video_streams(video_bytes)
    checks: dict[str, str] = {}
    reasons: list[str] = []

    if not meta or not meta.get("width") or not meta.get("height"):
        return {
            "version": OUTPUT_CONFORMANCE_VERSION,
            "status": "unknown",
            "checks": {"probe": "unknown"},
            "reasons": ["probe_unavailable"],
            "probe": meta,
        }

    requested_ratio = _parse_ratio(output_spec.get("aspect_ratio") or "")
    actual_ratio = meta["width"] / meta["height"]
    if requested_ratio:
        if abs(actual_ratio / requested_ratio - 1.0) <= _ASPECT_TOLERANCE:
            checks["aspect_ratio"] = PASS
        else:
            checks["aspect_ratio"] = FAIL
            reasons.append(
                f"aspect_mismatch requested {output_spec.get('aspect_ratio')} "
                f"got {meta['width']}x{meta['height']}"
            )
    else:
        checks["aspect_ratio"] = "unknown"

    min_side = _resolution_min_side(output_spec.get("resolution") or "")
    if min_side:
        actual_min = min(meta["width"], meta["height"])
        if actual_min >= int(min_side * _RESOLUTION_MIN_FRACTION):
            checks["resolution"] = PASS
        else:
            checks["resolution"] = REVIEW
            reasons.append(f"resolution_below_requested {actual_min} < {min_side}")
    else:
        checks["resolution"] = "unknown"

    requested_dur = output_spec.get("duration_sec")
    if isinstance(requested_dur, (int, float)) and meta.get("duration"):
        if abs(float(meta["duration"]) - float(requested_dur)) <= _DURATION_TOLERANCE_SEC:
            checks["duration"] = PASS
        else:
            checks["duration"] = REVIEW
            reasons.append(
                f"duration_off requested {requested_dur}s got {round(meta['duration'], 2)}s"
            )
    else:
        checks["duration"] = "unknown"

    if output_spec.get("audio") is False:
        if meta.get("has_audio"):
            checks["audio_disabled"] = FAIL
            reasons.append("audio_stream_present_despite_audio_false")
        else:
            checks["audio_disabled"] = PASS

    values = list(checks.values())
    status = FAIL if FAIL in values else (REVIEW if REVIEW in values else (
        PASS if values and all(v == PASS for v in values) else "unknown"
    ))
    return {
        "version": OUTPUT_CONFORMANCE_VERSION,
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "probe": meta,
    }
