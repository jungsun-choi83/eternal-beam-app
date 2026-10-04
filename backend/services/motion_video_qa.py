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

예외 (v4): LOCOMOTION 의 **신원 검사만** 펫 크롭 정규화 시그니처를 쓴다.
전체 프레임 비교는 다가오기가 성공할수록(펫이 프레임을 채울수록) 히스토그램이
시작 키프레임과 멀어지는 구조적 편향이 있다 — 모션의 성공이 신원 점수를
떨어뜨린다(COME_CLOSER 라이브 REVIEW, worst_frame 0.498). 시간 안정성·루프
복귀·TRANSITION 끝점 검사는 계속 전체 프레임이다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from copy import deepcopy
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# v3: BREATHING 시간축 증거(breathing_temporal_qa) 통합 — vlm_motion=unknown 을
#     결정론 증거로 해소할 수 있고, 전신 펄스/드리프트가 명시적 사유가 된다.
#     VLM "no" 는 여전히 FAIL 이고 시간축 증거는 상반된 VLM 증거를 뒤집지 않는다.
# v4: LOCOMOTION 신원 검사 개편 — 펫 크롭 정규화 시그니처(배경 제외, 스케일
#     불변) + 최악 프레임 대신 평균·인접 일관성·VLM same-pet 증거로 판정.
#     임계값(PHASE6_QA_IDENTITY_*)은 전역과 동일하고, 다른 클래스의 신원 규칙과
#     나머지 검사(시간 안정성/루프/끝점/VLM/합성)는 v3 그대로다.
# v5: MICRO 도 신원 **시그니처**를 펫 크롭 정규화로 바꾼다 (판정 규칙은 worst_frame
#     그대로 — v4 의 평균/인접 규칙은 LOCOMOTION 전용이다). 이유는 v4 와 반대
#     방향의 같은 구조적 편향이다: MICRO 는 펫이 거의 안 움직이는데 전체 프레임
#     히스토그램이 **평평한 배경**에 지배된다. BREATHING wan 라이브 실측에서
#     h264 첫 프레임의 배경이 (182,180,181)→(183,183,186) 로 1~5 단계 양자화
#     이동하자 시각적으로 동일한 프레임의 hist_intersection 이 0.353 까지 꺼졌다
#     (frame0 vs frame2 = 0.473). 루프 복귀 검사가 같은 이유로 이미 SSIM 으로
#     옮겨 간 그 편향이다. 임계값과 나머지 검사는 불변.
# v6: INTERACTION 도 같은 정규화 시그니처로 (판정 규칙은 worst_frame 그대로).
#     PET_HEAD wan 라이브 실측에서 같은 프레임이 전체 프레임 0.33~0.71 vs 펫
#     정규화 0.845~0.944 로 갈렸다 — 스펙이 **허용한** 손(allow_generated_hand)의
#     피부 픽셀과 첫 프레임 배경 양자화가 신원 점수를 지배했다. 즉 허용된 연출이
#     신원 점수를 깎는 구조다. 임계값과 나머지 검사는 불변.
# v7: 구조/해부학 도메인 분리 강화 — pinned morphology profile + 레지스트리의
#     structural_anatomy requirements 를 소비해 sampled frames 전반의 형태 일관성
#     (torso/legs/head/muzzle/ear-tail visible/limb placement/body deformation/
#     joint plausibility)을 평가한다. 증거 부족은 unknown/review, 강한 구조 모순과
#     중증 해부학 붕괴만 FAIL.
# v8: pose-dependent morphology/deformation REVIEW 를 advisory evidence 로 분리.
#     hard required checks 가 전부 PASS 면 advisory REVIEW 만으로 전체/도메인 결정을
#     REVIEW 로 내리지 않는다. 검사값·사유·증거는 보존하고 모든 FAIL 은 계속 차단.
# v9: BREATHING temporal v3 의 전역 이동/카메라 drift verdict 를 hard FAIL 로 소비.
#     다른 모션과 BREATHING 의 identity/anatomy/general QA 집계는 v8 그대로다.
# v10 (플래그 뒤): 판정 단계를 순수 함수(apply_judgement)로 분리하고 규칙 집합을
#      추가한다. MOTION_VIDEO_QA_RULESET=v10 일 때만 켜지며 기본은 v9 그대로다.
#      (a) VLM camera_stable/major_flicker 소견은 결정론 시간축 게이트(스케일·
#          이동·침하)와 temporal_stability 가 전부 통과하면 자문(advisory)이다;
#          MICRO 의 unintended_large_motion 은 같은 조건에서 FAIL→REVIEW 로 강등.
#      (b) global_pulse 의 위반 게이트가 전부 경계 구간(≤ 1+band, 기본 15%) 이고
#          호흡 증거(torso_snr·scale_oscillation)가 강하면 FAIL 대신 REVIEW.
#      (d) 한계의 hard_fail_ratio(기본 1.5)배 이상, scene_cut/duplicated_pet/
#          human_present, VLM anatomy/same_pet/motion=no, identity FAIL, 규격
#          위반은 그대로 hard FAIL. FAIL→REVIEW 강등은 절대 자문으로 풀리지 않는다.
#      + 휴리스틱 구조 FAIL(strong_contradiction / body_deformation)은 VLM 이
#        해부학·동일 개체를 확언하고 경계 구간 안이면 REVIEW 로 강등.
#      저장된 qa_result 는 rescore_stored_qa_result 로 영상 없이 재판정할 수 있다
#      (backend/scripts/replay_motion_qa_rules.py).
MOTION_VIDEO_QA_VERSION = "motion-video-qa-v9"
MOTION_VIDEO_QA_VERSION_V10 = "motion-video-qa-v10"
FRAME_SAMPLING_VERSION = "frame-sampling-v2"

RULESET_V9 = "v9"
RULESET_V10 = "v10"
#: 환경 변수로 규칙 집합을 고른다. 미설정/알 수 없는 값 = v9 (현재 동작).
MOTION_VIDEO_QA_RULESET_ENV = "MOTION_VIDEO_QA_RULESET"
_QA_VERSION_BY_RULESET = {
    RULESET_V9: MOTION_VIDEO_QA_VERSION,
    RULESET_V10: MOTION_VIDEO_QA_VERSION_V10,
}


def active_ruleset(override: Optional[str] = None) -> str:
    """인자 > 환경 변수 > v9. 알 수 없는 값은 조용히 v9 로 닫힌다(fail-closed)."""
    value = str(override or os.getenv(MOTION_VIDEO_QA_RULESET_ENV) or RULESET_V9).strip().lower()
    if value in ("motion-video-qa-v10", "10"):
        value = RULESET_V10
    elif value in ("motion-video-qa-v9", "9"):
        value = RULESET_V9
    return value if value in _QA_VERSION_BY_RULESET else RULESET_V9


def active_qa_version(ruleset: Optional[str] = None) -> str:
    """현재 규칙 집합의 qa_version 스탬프 — 후보 qa_result / analyzer_versions 에 기록된다."""
    return _QA_VERSION_BY_RULESET[active_ruleset(ruleset)]

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


#: 전경 판정 후 프레임 대비 전경 비율 허용 범위. 아래로 벗어나면 펫이 없거나
#: 배경 모델이 전경을 삼켰고, 위로 벗어나면 배경 모델 자체가 실패했다(테두리가
#: 펫으로 덮인 극단 클로즈업 등). 어느 쪽이든 측정 불가로 다룬다 — 추측 금지.
_PET_FG_MIN_FRACTION = 0.01
_PET_FG_MAX_FRACTION = 0.90


def _pet_foreground_alpha(rgb: np.ndarray) -> Optional[np.ndarray]:
    """
    중립 배경 프레임의 전경(펫) 알파 추정 — 행별 테두리 배경 모델과의 색 거리.

    행별 모델을 쓰는 이유는 포장(motion_delivery_service._rowwise_background)과
    같은 실측이다: Seedance 배경은 균일한 한 색이 아니라 벽→바닥 세로
    그라디언트라, 단일 배경색으로는 바닥이 통째로 전경으로 남는다. 판정용
    거친 마스크면 충분하므로 매팅처럼 반투명을 다루지는 않는다.
    """
    try:
        f = rgb.astype(np.float32)
        h, w = f.shape[:2]
        if h < 16 or w < 16:
            return None
        edges = np.concatenate([f[:, :8, :], f[:, -8:, :]], axis=1)
        bg_row = np.median(edges, axis=1)  # (H, 3)
        k = 15
        pad = np.pad(bg_row, ((k // 2, k // 2), (0, 0)), mode="edge")
        kernel = np.ones(k) / k
        bg = np.stack(
            [np.convolve(pad[:, c], kernel, mode="valid") for c in range(3)], axis=1
        )
        dist = np.sqrt(((f - bg[:, None, :]) ** 2).sum(axis=2))
        mask = dist > _f("PHASE6_QA_PET_FG_DIST", 30.0)
        frac = float(mask.mean())
        if frac < _PET_FG_MIN_FRACTION or frac > _PET_FG_MAX_FRACTION:
            return None
        return mask.astype(np.uint8) * 255
    except Exception:
        return None


def _pet_normalized_signature(rgb: Optional[np.ndarray]) -> Optional[dict[str, Any]]:
    """
    펫 크롭 정규화 시그니처 (v4, LOCOMOTION 신원 전용).

    전경 알파를 실어 보내면 기존 시그니처 코드가 그대로 정규화를 수행한다:
    HSV 히스토그램은 **마스크 픽셀만** 세고(배경 희석 제거), pHash 는 피사체
    bbox 크롭을 32×32 로 리사이즈한다(스케일 제거). 펫이 작든 프레임을 채우든
    같은 펫이면 비슷한 시그니처가 나온다. 측정 불가면 None — 호출자는 전체
    프레임 방식으로 폴백한다(기존 동작 유지).
    """
    from .pet_identity_service import compute_reference_signature

    if rgb is None:
        return None
    alpha = _pet_foreground_alpha(rgb)
    if alpha is None:
        return None
    return compute_reference_signature(np.dstack([rgb, alpha]))


UNKNOWN = "unknown"

_CONFIDENCE_RANK = {
    UNKNOWN: 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "measured": 4,
}

_TORSO_ORDER = ("COMPACT", "STANDARD", "LONG")
_LEG_ORDER = ("SHORT", "STANDARD", "LONG")
_HEAD_ORDER = ("SMALL", "STANDARD", "LARGE")
_MUZZLE_ORDER = ("SHORT", "STANDARD", "LONG")
_BUILD_ORDER = ("SLENDER", "BALANCED", "STOCKY")


def _norm_token(v: Any) -> str:
    s = str(v or "").strip().upper()
    return s if s else UNKNOWN.upper()


def _rank_confidence(v: Any) -> int:
    return _CONFIDENCE_RANK.get(str(v or UNKNOWN).strip().lower(), 0)


def _classify_torso(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 1.15:
        return "COMPACT"
    if v > 1.65:
        return "LONG"
    return "STANDARD"


def _classify_leg(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 0.34:
        return "SHORT"
    if v > 0.48:
        return "LONG"
    return "STANDARD"


def _classify_head(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 0.13:
        return "SMALL"
    if v > 0.26:
        return "LARGE"
    return "STANDARD"


def _classify_muzzle(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 0.42:
        return "SHORT"
    if v > 0.78:
        return "LONG"
    return "STANDARD"


def _classify_frame_occupancy(v: Optional[float]) -> str:
    """프레임 점유율 구간 — 진단용이다. 실제 체급(SMALL/MEDIUM/LARGE)이 아니다."""
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 0.14:
        return "SMALL_IN_FRAME"
    if v > 0.55:
        return "LARGE_IN_FRAME"
    return "MEDIUM_IN_FRAME"


def _classify_build(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return UNKNOWN
    if v < 0.82:
        return "SLENDER"
    if v > 0.93:
        return "STOCKY"
    return "BALANCED"


def _distance(a: Optional[dict[str, Any]], b: Optional[dict[str, Any]]) -> Optional[float]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    ax, ay = a.get("x"), a.get("y")
    bx, by = b.get("x"), b.get("y")
    if not all(isinstance(v, (int, float)) for v in (ax, ay, bx, by)):
        return None
    return float(np.hypot(float(ax) - float(bx), float(ay) - float(by)))


def _kp_visible(kp: Any, *, conf_floor: float = 0.25) -> bool:
    if not isinstance(kp, dict):
        return False
    if kp.get("visible") is False:
        return False
    conf = kp.get("confidence")
    if isinstance(conf, (int, float)) and float(conf) < conf_floor:
        return False
    return isinstance(kp.get("x"), (int, float)) and isinstance(kp.get("y"), (int, float))


def _ordered_compat(expected: str, observed: str, order: tuple[str, ...]) -> str:
    e = _norm_token(expected)
    o = _norm_token(observed)
    if e == UNKNOWN.upper() or o == UNKNOWN.upper():
        return "UNVERIFIED"
    if e == o:
        return "EXACT"
    try:
        if abs(order.index(e) - order.index(o)) == 1:
            return "NEAR"
    except ValueError:
        return "UNVERIFIED"
    return "MISMATCH"


def _trait_source_confidence(profile: dict[str, Any], field: str) -> str:
    src = str(((profile.get("sources") or {}).get(field.replace("_class", "")) or "")).strip().lower()
    if src.startswith("morphology_profile:"):
        return src.split(":", 1)[1] or UNKNOWN
    if src in ("measured", "high", "medium", "low"):
        return src
    return UNKNOWN


#: 프레임 점유율(카메라 거리)의 함수라 실제 체급이 아니다 — 구조 QA 비교 축에서
#: 영구 제외한다(레거시 계약이 compare_fields 에 남겨 두었더라도).
_FRAME_OCCUPANCY_FIELDS = frozenset({"body_size_class"})

#: 자세가 바뀌면 함께 변하는 구조 신호 (기본값; 계약이 선언하면 그쪽 우선).
_DEFAULT_POSE_DEPENDENT_FIELDS = (
    "body_length_class",
    "leg_length_class",
    "body_build_class",
)

#: 사지 개수/배치를 **주장하려면** 포즈 백엔드가 그 프레임에서 사지 구조를
#: 하나라도 실제로 측정했어야 한다. 휴리스틱 마스크 기하 백엔드는 "다리가
#: 렌더에서 사라졌다"와 "내가 다리를 못 찾았다"를 구분하지 못한다 — 키포인트가
#: 하나도 없는 프레임은 붕괴의 근거가 아니라 근거 없음이다.
_MIN_LIMB_KEYPOINTS_FOR_CLAIM = 1

#: QA 가 **스스로 파생하는** 구조 신호들. 전부 휴리스틱 마스크 기하 기반이라
#: (보이는 발/근위점 개수, 관절 각도, bbox 종횡비 변화) 자세·가림·양식화에
#: 쉽게 오작동한다 — 레지스트리가 required_checks 로 **명시 요구하지 않은**
#: REVIEW 는 advisory 다. 단 실제 FAIL 판정은 required 여부와 무관하게 차단한다.
#: 진짜 해부학 붕괴의 하드 게이트는 required 인 vlm_anatomy 와
#: structural_morphology_consistency 가 그대로 담당한다(fail-closed 유지).
_ADVISORY_STRUCTURAL_CHECKS = (
    "anatomy_limb_count_placement",
    "anatomy_joint_plausibility",
    "anatomy_body_deformation",
)


def _profile_expectations(spec_contract: dict[str, Any]) -> dict[str, Any]:
    req = (((spec_contract.get("requirements") or {}).get("qa") or {}).get("structural_anatomy") or {})
    morph_req = req.get("morphology_consistency") if isinstance(req, dict) else None
    if not isinstance(morph_req, dict):
        morph_req = {}
    profile = (spec_contract.get("pet_motion_profile") or {}) if isinstance(spec_contract, dict) else {}
    min_conf = str(((spec_contract.get("requirements") or {}).get("morphology") or {}).get("confidence_floor") or "medium").lower()
    compare_fields = morph_req.get("compare_fields") or []
    if not isinstance(compare_fields, list):
        compare_fields = []
    compare_fields = [
        str(f)
        for f in compare_fields
        if isinstance(f, str) and f.strip() and str(f) not in _FRAME_OCCUPANCY_FIELDS
    ]
    expected: dict[str, dict[str, str]] = {}
    for field in compare_fields:
        value = str(profile.get(field) or "").strip().upper()
        if not value or value == UNKNOWN.upper():
            continue
        conf = _trait_source_confidence(profile, field)
        if _rank_confidence(conf) < _rank_confidence(min_conf):
            continue
        expected[field] = {"value": value, "confidence": conf}
    pose_fields = morph_req.get("pose_dependent_fields")
    if not isinstance(pose_fields, list) or not pose_fields:
        pose_fields = list(_DEFAULT_POSE_DEPENDENT_FIELDS)
    pose_policy = str(morph_req.get("pose_dependent_policy") or "").strip().lower()
    if not pose_policy:
        # 레거시 계약: 모션 클래스로부터 유추한다.
        pose_policy = (
            "review_never_fail"
            if str(spec_contract.get("motion_class") or "").upper() == "TRANSITION"
            else "fail_on_strong_contradiction"
        )
    return {
        "profile": profile,
        "expected": expected,
        "minimum_support_frames": int(morph_req.get("minimum_support_frames") or 2),
        "strong_contradiction_ratio": float(morph_req.get("strong_contradiction_ratio") or 0.7),
        "pose_dependent_fields": [str(f) for f in pose_fields if isinstance(f, str)],
        "pose_dependent_advisory": pose_policy == "review_never_fail",
    }


def _record_anatomy_signal(
    checks: dict[str, str],
    name: str,
    status: Optional[str],
    *,
    required: set[str],
) -> None:
    """
    QA 가 파생한 해부학 신호 하나를 기록한다. status=None 은 **측정 불가**다.

    측정 불가는 후보에 대한 부정적 판단이 아니다:
      - required_checks 에 선언된 검사면 UNKNOWN 으로 남긴다 — 필수인데 확인
        못 했으면 PASS 가 될 수 없다(fail-closed 유지).
      - required 가 아니면 키를 아예 넣지 않는다(skip). 잴 수 없었다는 이유로
        멀쩡한 후보의 PASS 를 막지 않는다.
    어느 쪽이든 reasons 에 부정적 사유를 남기지 않는다.
    """
    if status is not None:
        checks[name] = status
    elif name in required:
        checks[name] = UNKNOWN


def _frame_structural_observation(rgb: Optional[np.ndarray]) -> dict[str, Any]:
    from .pet_identity_service import analyze_structural_identity

    if rgb is None:
        return {"measurable": False, "reason": "frame_missing"}
    alpha = _pet_foreground_alpha(rgb)
    if alpha is None:
        return {"measurable": False, "reason": "foreground_unmeasurable"}
    rgba = np.dstack([rgb, alpha])
    structural = analyze_structural_identity(rgba)
    if str(structural.get("status") or UNKNOWN) == UNKNOWN:
        return {"measurable": False, "reason": str(structural.get("reason") or UNKNOWN)}

    sil = structural.get("silhouette") or {}
    pose = structural.get("pose") or {}
    kps = pose.get("keypoints") if isinstance(pose.get("keypoints"), dict) else {}
    bbox = sil.get("bbox") if isinstance(sil.get("bbox"), list) and len(sil.get("bbox")) == 4 else None
    bbox_h = float(max(1, int(bbox[3]) - int(bbox[1]) + 1)) if bbox else None
    border = set((sil.get("border_contact") or []) if isinstance(sil.get("border_contact"), list) else [])

    leg_pairs = (
        ("front_left_shoulder", "front_left_paw"),
        ("front_right_shoulder", "front_right_paw"),
        ("back_left_hip", "back_left_paw"),
        ("back_right_hip", "back_right_paw"),
    )
    leg_spans: list[float] = []
    visible_paws = 0
    visible_prox = 0
    for a, b in leg_pairs:
        ka, kb = kps.get(a), kps.get(b)
        if _kp_visible(ka):
            visible_prox += 1
        if _kp_visible(kb):
            visible_paws += 1
        if _kp_visible(ka) and _kp_visible(kb) and bbox_h:
            d = _distance(ka, kb)
            if d is not None and d > 1.0:
                leg_spans.append(float(d) / float(bbox_h))

    head_frac = pose.get("head_height_fraction") if isinstance(pose.get("head_height_fraction"), (int, float)) else None
    muzzle_frac = None
    nose, head_top, neck = kps.get("nose"), kps.get("head_top"), kps.get("neck")
    if _kp_visible(nose) and _kp_visible(head_top) and _kp_visible(neck):
        muzzle = _distance(nose, head_top)
        head = _distance(neck, head_top)
        if muzzle is not None and head is not None and head > 1e-6:
            muzzle_frac = float(muzzle) / float(head)

    def _joint_ok(prox: str, joint: str, paw: str) -> Optional[bool]:
        p0, p1, p2 = kps.get(prox), kps.get(joint), kps.get(paw)
        if not (_kp_visible(p0) and _kp_visible(p1) and _kp_visible(p2)):
            return None
        upper = _distance(p0, p1)
        lower = _distance(p1, p2)
        span = _distance(p0, p2)
        if upper is None or lower is None or span is None:
            return None
        if upper < 1.0 or lower < 1.0:
            return False
        if span > (upper + lower + 1e-3):
            return False
        return True

    joint_flags = [
        _joint_ok("front_left_shoulder", "front_left_elbow", "front_left_paw"),
        _joint_ok("front_right_shoulder", "front_right_elbow", "front_right_paw"),
        _joint_ok("back_left_hip", "back_left_knee", "back_left_paw"),
        _joint_ok("back_right_hip", "back_right_knee", "back_right_paw"),
    ]
    measurable_joints = [j for j in joint_flags if j is not None]
    joints_implausible = any(j is False for j in measurable_joints)

    # 사지 신호는 3-상태다: True(붕괴 근거) / False(정상 근거) / None(근거 없음).
    # 백엔드가 사지 키포인트를 하나도 못 냈다면 아무것도 주장하지 않는다.
    limb_points_visible = visible_prox + visible_paws
    severe_limb_contradiction: Optional[bool]
    if limb_points_visible < _MIN_LIMB_KEYPOINTS_FOR_CLAIM:
        severe_limb_contradiction = None
    else:
        severe_limb_contradiction = (
            visible_paws <= 1
            and visible_prox <= 1
            and not (border & {"left", "right", "top", "bottom"})
            and isinstance(sil.get("area_fraction"), (int, float))
            and float(sil.get("area_fraction")) >= 0.08
        )

    return {
        "measurable": True,
        "silhouette": {
            "bbox_aspect_ratio": sil.get("bbox_aspect_ratio"),
            "area_fraction": sil.get("area_fraction"),
            "bbox_fill_ratio": sil.get("bbox_fill_ratio"),
            "silhouette_solidity": sil.get("silhouette_solidity"),
            "border_contact": sorted(border),
        },
        "morphology": {
            "body_length_class": _classify_torso(sil.get("bbox_aspect_ratio")),
            "leg_length_class": _classify_leg(float(np.median(np.asarray(leg_spans, dtype=np.float64))) if leg_spans else None),
            "head_proportion_class": _classify_head(head_frac),
            "muzzle_proportion_class": _classify_muzzle(muzzle_frac),
            # 진단 전용 — 비교 축이 아니다(_FRAME_OCCUPANCY_FIELDS 참고).
            "frame_occupancy_class": _classify_frame_occupancy(sil.get("area_fraction")),
            "body_build_class": _classify_build(sil.get("silhouette_solidity")),
            "ear_form": UNKNOWN.upper(),
            "tail_form": UNKNOWN.upper(),
        },
        "visibility": {
            "head_visible": _kp_visible(head_top) and not ("top" in border),
            "tail_visible": _kp_visible(kps.get("tail_tip")) and not ("bottom" in border),
            "visible_paw_points": int(visible_paws),
            "visible_prox_points": int(visible_prox),
        },
        "anatomy": {
            "joint_samples": len(measurable_joints),
            "joint_implausible": joints_implausible,
            # None = 이 프레임에서 잴 수 없었다(FAIL 근거로 쓰지 않는다).
            "limb_count_contradiction": severe_limb_contradiction,
            "limb_points_visible": int(limb_points_visible),
        },
    }


def _structural_domain_from_frames(
    frames: list[Optional[np.ndarray]],
    spec_contract: dict[str, Any],
    *,
    checks: dict[str, str],
    reasons: list[str],
) -> dict[str, Any]:
    qa_req = ((spec_contract.get("requirements") or {}).get("qa") or {})
    structural_req = (qa_req.get("structural_anatomy") or {}) if isinstance(qa_req, dict) else {}
    required = set(structural_req.get("required_checks") or [])
    if "structural_morphology_consistency" not in required:
        return {"enabled": False, "required_checks": sorted(required)}

    expectations = _profile_expectations(spec_contract)
    expected = expectations["expected"]
    min_support = max(1, int(expectations["minimum_support_frames"]))
    contradiction_ratio = float(expectations["strong_contradiction_ratio"])
    pose_dependent_fields = set(expectations.get("pose_dependent_fields") or ())
    pose_advisory = bool(expectations.get("pose_dependent_advisory"))

    observations = [_frame_structural_observation(f) for f in (frames or [])]
    usable = [o for o in observations if o.get("measurable")]
    if not usable:
        checks["structural_morphology_consistency"] = UNKNOWN
        reasons.append("structural_evidence_unavailable")
        return {
            "enabled": True,
            "required_checks": sorted(required),
            "sampled_frames": len(frames or []),
            "measurable_frames": 0,
            "trait_summary": {},
            "advisory_checks": [],
            "advisory_findings": [],
        }

    trait_summary: dict[str, Any] = {}
    structural_values: list[str] = []
    compatible_traits = 0
    reviewed_traits = 0
    hard_reviewed_traits = 0
    advisory_reviewed_traits = 0
    failed_traits = 0
    advisory_checks: set[str] = set()
    advisory_findings: list[dict[str, Any]] = []

    for trait_field, meta in expected.items():
        target = _norm_token(meta.get("value"))
        observed = [
            _norm_token((o.get("morphology") or {}).get(trait_field))
            for o in usable
            if _norm_token((o.get("morphology") or {}).get(trait_field)) != UNKNOWN.upper()
        ]
        support = len(observed)
        mismatches = 0
        near = 0
        if support:
            for ob in observed:
                if trait_field == "body_length_class":
                    comp = _ordered_compat(target, ob, _TORSO_ORDER)
                elif trait_field == "leg_length_class":
                    comp = _ordered_compat(target, ob, _LEG_ORDER)
                elif trait_field == "head_proportion_class":
                    comp = _ordered_compat(target, ob, _HEAD_ORDER)
                elif trait_field == "muzzle_proportion_class":
                    comp = _ordered_compat(target, ob, _MUZZLE_ORDER)
                elif trait_field == "body_build_class":
                    comp = _ordered_compat(target, ob, _BUILD_ORDER)
                else:
                    comp = "EXACT" if target == ob else "MISMATCH"
                if comp == "MISMATCH":
                    mismatches += 1
                if comp == "NEAR":
                    near += 1
        ratio = (float(mismatches) / float(support)) if support else None
        status = UNKNOWN
        if support < min_support:
            status = UNKNOWN
            reviewed_traits += 1
            hard_reviewed_traits += 1
            reasons.append(f"structural_{trait_field}_insufficient_visibility")
        elif ratio is not None and ratio >= contradiction_ratio:
            if pose_advisory and trait_field in pose_dependent_fields:
                # 앉기/서기/눕기에서 몸통 비율·다리 길이·실루엣 충실도는 자세와
                # 함께 변하는 게 정상이다 — 요청한 동작 자체를 하드 FAIL 로
                # 처리하지 않고 자문(REVIEW)으로 남긴다.
                status = REVIEW
                reviewed_traits += 1
                advisory_reviewed_traits += 1
                reasons.append(
                    f"structural_{trait_field}_pose_dependent_change_advisory "
                    f"{round(ratio, 3)}"
                )
                advisory_findings.append(
                    {
                        "check": "structural_morphology_consistency",
                        "trait": trait_field,
                        "status": REVIEW,
                        "reason": "pose_dependent_change",
                        "mismatch_frames": mismatches,
                        "support_frames": support,
                    }
                )
            else:
                status = FAIL
                failed_traits += 1
                reasons.append(
                    f"structural_{trait_field}_strong_contradiction {round(ratio, 3)} >= {round(contradiction_ratio, 3)}"
                )
        elif mismatches > 0:
            status = REVIEW
            reviewed_traits += 1
            reasons.append(f"structural_{trait_field}_drift {mismatches}/{support}")
            if pose_advisory and trait_field in pose_dependent_fields:
                advisory_reviewed_traits += 1
                advisory_findings.append(
                    {
                        "check": "structural_morphology_consistency",
                        "trait": trait_field,
                        "status": REVIEW,
                        "reason": "pose_dependent_drift",
                        "mismatch_frames": mismatches,
                        "support_frames": support,
                    }
                )
            else:
                hard_reviewed_traits += 1
        else:
            status = PASS
            compatible_traits += 1
        structural_values.append(status)
        trait_summary[trait_field] = {
            "expected": target,
            "support_frames": support,
            "mismatch_frames": mismatches,
            "near_frames": near,
            "status": status,
            "source_confidence": meta.get("confidence"),
        }

    # ear/tail: visibility가 확보되면 추적 가능 여부만 확인(형태 분류 추측 금지).
    head_visible_frames = sum(1 for o in usable if (o.get("visibility") or {}).get("head_visible"))
    tail_visible_frames = sum(1 for o in usable if (o.get("visibility") or {}).get("tail_visible"))
    if "ear_form" in expected:
        if head_visible_frames < min_support:
            trait_summary["ear_form"] = {
                "expected": _norm_token(expected["ear_form"].get("value")),
                "support_frames": head_visible_frames,
                "status": UNKNOWN,
                "note": "ear structure evidence occluded_or_unmeasurable",
            }
            structural_values.append(UNKNOWN)
            reviewed_traits += 1
            hard_reviewed_traits += 1
            reasons.append("structural_ear_form_insufficient_visibility")
        else:
            trait_summary["ear_form"] = {
                "expected": _norm_token(expected["ear_form"].get("value")),
                "support_frames": head_visible_frames,
                "status": PASS,
                "note": "head/ear region consistently visible; no deterministic contradiction",
            }
            structural_values.append(PASS)
            compatible_traits += 1
    if "tail_form" in expected:
        if tail_visible_frames < min_support:
            trait_summary["tail_form"] = {
                "expected": _norm_token(expected["tail_form"].get("value")),
                "support_frames": tail_visible_frames,
                "status": UNKNOWN,
                "note": "tail structure evidence occluded_or_unmeasurable",
            }
            structural_values.append(UNKNOWN)
            reviewed_traits += 1
            hard_reviewed_traits += 1
            reasons.append("structural_tail_form_insufficient_visibility")
        else:
            trait_summary["tail_form"] = {
                "expected": _norm_token(expected["tail_form"].get("value")),
                "support_frames": tail_visible_frames,
                "status": PASS,
                "note": "tail region visible; no deterministic contradiction",
            }
            structural_values.append(PASS)
            compatible_traits += 1

    # severe anatomy corruption signals — **근거가 있는 프레임만** 센다.
    # 잴 수 없었던 프레임은 분모에도 분자에도 들어가지 않는다.
    limb_verdicts = [(o.get("anatomy") or {}).get("limb_count_contradiction") for o in usable]
    limb_evidence_frames = sum(1 for v in limb_verdicts if v is not None)
    limb_contradictions = sum(1 for v in limb_verdicts if v is True)

    joint_evidence_frames = sum(
        1 for o in usable if int((o.get("anatomy") or {}).get("joint_samples") or 0) > 0
    )
    joint_samples = sum(int((o.get("anatomy") or {}).get("joint_samples") or 0) for o in usable)
    joint_implausible = sum(1 for o in usable if ((o.get("anatomy") or {}).get("joint_implausible")))

    aspect_values = [
        float((o.get("silhouette") or {}).get("bbox_aspect_ratio"))
        for o in usable
        if isinstance((o.get("silhouette") or {}).get("bbox_aspect_ratio"), (int, float))
    ]
    deformation_ratios: list[float] = []
    for a, b in zip(aspect_values, aspect_values[1:]):
        if a > 1e-6 and b > 1e-6:
            deformation_ratios.append(max(a / b, b / a))
    severe_deformation = any(r > 2.0 for r in deformation_ratios)

    # 사지: 근거 프레임이 min_support 에 못 미치면 판단 자체를 하지 않는다.
    if limb_evidence_frames < min_support:
        limb_status: Optional[str] = None
    elif limb_contradictions >= min_support:
        limb_status = FAIL
    else:
        limb_status = PASS
    _record_anatomy_signal(checks, "anatomy_limb_count_placement", limb_status, required=required)
    if limb_status == FAIL:
        reasons.append("anatomy_limb_count_or_placement_corrupted")

    # 관절: 관절을 실제로 잰 프레임이 min_support 이상일 때만 말한다.
    if joint_evidence_frames < min_support:
        joint_status: Optional[str] = None
    elif joint_implausible >= min_support:
        joint_status = FAIL
    elif joint_implausible > 0:
        joint_status = REVIEW
    else:
        joint_status = PASS
    _record_anatomy_signal(checks, "anatomy_joint_plausibility", joint_status, required=required)
    if joint_status == FAIL:
        reasons.append("anatomy_joint_implausible")
    elif joint_status == REVIEW:
        reasons.append("anatomy_joint_borderline")

    if not deformation_ratios:
        # 비교할 종횡비 쌍이 없다 — 변형을 잴 수 없었던 것이지 변형이 아니다.
        _record_anatomy_signal(checks, "anatomy_body_deformation", None, required=required)
    elif severe_deformation and pose_advisory:
        # bbox 종횡비 급변은 sit↔stand↔lie 전환에서 정상 신호다.
        checks["anatomy_body_deformation"] = REVIEW
        reasons.append("anatomy_body_deformation_pose_transition_advisory")
        advisory_checks.add("anatomy_body_deformation")
        advisory_findings.append(
            {
                "check": "anatomy_body_deformation",
                "status": REVIEW,
                "reason": "pose_dependent_deformation",
                "ratio_max": round(max(deformation_ratios), 4),
            }
        )
    elif severe_deformation:
        checks["anatomy_body_deformation"] = FAIL
        reasons.append("anatomy_severe_body_deformation")
    elif any(r > 1.6 for r in deformation_ratios):
        checks["anatomy_body_deformation"] = REVIEW
        reasons.append("anatomy_body_deformation_review")
        if pose_advisory:
            advisory_checks.add("anatomy_body_deformation")
            advisory_findings.append(
                {
                    "check": "anatomy_body_deformation",
                    "status": REVIEW,
                    "reason": "pose_dependent_deformation",
                    "ratio_max": round(max(deformation_ratios), 4),
                }
            )
    else:
        checks["anatomy_body_deformation"] = PASS

    if failed_traits > 0:
        checks["structural_morphology_consistency"] = FAIL
    elif reviewed_traits > 0:
        checks["structural_morphology_consistency"] = REVIEW
    elif compatible_traits > 0:
        checks["structural_morphology_consistency"] = PASS
    else:
        checks["structural_morphology_consistency"] = UNKNOWN

    if checks["structural_morphology_consistency"] == UNKNOWN:
        reasons.append("structural_morphology_evidence_insufficient")
    elif (
        checks["structural_morphology_consistency"] == REVIEW
        and advisory_reviewed_traits > 0
        and hard_reviewed_traits == 0
    ):
        advisory_checks.add("structural_morphology_consistency")

    for name in _ADVISORY_STRUCTURAL_CHECKS:
        if checks.get(name) != REVIEW or name in required:
            continue
        advisory_checks.add(name)
        if not any(finding.get("check") == name for finding in advisory_findings):
            advisory_findings.append(
                {
                    "check": name,
                    "status": REVIEW,
                    "reason": "heuristic_structural_review",
                }
            )

    return {
        "enabled": True,
        "required_checks": sorted(required),
        "sampled_frames": len(frames or []),
        "measurable_frames": len(usable),
        "minimum_support_frames": min_support,
        "strong_contradiction_ratio": contradiction_ratio,
        "pose_dependent_fields": sorted(pose_dependent_fields),
        "pose_dependent_advisory": pose_advisory,
        "trait_summary": trait_summary,
        "advisory_checks": sorted(advisory_checks),
        "advisory_findings": advisory_findings,
        "anatomy_signals": {
            "limb_count_contradictions": limb_contradictions,
            "limb_evidence_frames": limb_evidence_frames,
            "joint_implausible_frames": joint_implausible,
            "joint_evidence_frames": joint_evidence_frames,
            "joint_samples": joint_samples,
            "unmeasurable_signals": sorted(
                name
                for name in _ADVISORY_STRUCTURAL_CHECKS
                if checks.get(name, UNKNOWN) == UNKNOWN
            ),
            "deformation_ratio_max": (round(max(deformation_ratios), 4) if deformation_ratios else None),
        },
    }


def _domain_status(
    checks: dict[str, str],
    required: list[str],
    *,
    advisory_reviews: Optional[set[str]] = None,
) -> str:
    if not required:
        return UNKNOWN
    advisory_reviews = advisory_reviews or set()
    values = [
        (
            PASS
            if checks.get(name) == REVIEW and name in advisory_reviews
            else checks.get(name, UNKNOWN)
        )
        for name in required
    ]
    if any(v == FAIL for v in values):
        return FAIL
    if values and all(v == PASS for v in values):
        return PASS
    return REVIEW


def _business_integrity_signal(status: Any) -> str:
    """Expose only severe FAIL/unknown as hard; ordinary REVIEW stays advisory."""

    value = str(status or UNKNOWN)
    if value == FAIL:
        return FAIL
    if value == UNKNOWN:
        return REVIEW
    return PASS


#: breathing-v2 catastrophic gate (existing temporal metrics only).
#: PROVISIONAL — one negative control: midpoint between the largest
#: human-accepted scale_range (0.0473) and the one human-rejected whole-body
#: scale pulse (0.0569). Re-derive when more labelled failures exist.
BREATHING_CATASTROPHIC_SCALE_RANGE = 0.052
#: With VLM identity/anatomy unconfirmed, scale_range above this is REVIEW
#: (still deliverable with advisory; never FAIL).
BREATHING_UNCONFIRMED_SCALE_REVIEW = 0.040
#: UNVALIDATED backstop — no stored candidate reaches it (human-accepted drift
#: goes up to 0.1056). Drift below it is advisory-only.
BREATHING_CATASTROPHIC_DRIFT_FRAC = 0.20
#: TEMPORARY (2026-10-03): the scale_range/height gate is advisory (REVIEW), not a
#: hard FAIL. The foreground-height measurement cannot separate a real size pulse
#: (48e31aff, 0.057) from floor reflections / low-contrast keying on acceptable
#: clips (MiniMax 5c4b9e16 / adec172b, 0.105). The 0.052 threshold is unchanged and
#: still recorded. Restore the hard gate with BREATHING_QA_SCALE_RANGE_HARD=1 once
#: a calibrated detector exists. The drift backstop stays a hard FAIL.
BREATHING_SCALE_RANGE_HARD_DEFAULT = False


def _breathing_catastrophic_motion(
    checks: dict[str, str],
    temporal: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """breathing-v2 hard gate: whole-body scale pulse faking the breath.

    Missing metrics never fail, and a VLM unknown can only produce REVIEW.
    """

    metrics = temporal.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    thresholds = {
        "scale_range_fail": _f(
            "BREATHING_QA_CATASTROPHIC_SCALE_RANGE", BREATHING_CATASTROPHIC_SCALE_RANGE
        ),
        "scale_range_review_when_vlm_unconfirmed": _f(
            "BREATHING_QA_UNCONFIRMED_SCALE_REVIEW", BREATHING_UNCONFIRMED_SCALE_REVIEW
        ),
        "drift_frac_fail": _f(
            "BREATHING_QA_CATASTROPHIC_DRIFT_FRAC", BREATHING_CATASTROPHIC_DRIFT_FRAC
        ),
    }

    def _num(name: str) -> Optional[float]:
        value = metrics.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    scale_range = _num("scale_range")
    drift = _num("translation_drift_frac_of_pet")
    vlm_confirmed = (
        checks.get("vlm_same_pet") == PASS and checks.get("vlm_anatomy") == PASS
    )
    scale_hard = os.getenv(
        "BREATHING_QA_SCALE_RANGE_HARD", "1" if BREATHING_SCALE_RANGE_HARD_DEFAULT else "0"
    ).strip().lower() in ("1", "true", "yes")
    scale_over = scale_range is not None and scale_range >= thresholds["scale_range_fail"]
    status, rule = PASS, None
    if scale_over and scale_hard:
        status, rule = FAIL, "scale_range_catastrophic_PROVISIONAL"
    elif drift is not None and drift >= thresholds["drift_frac_fail"]:
        status, rule = FAIL, "drift_backstop_UNVALIDATED"
    elif scale_over:
        status, rule = REVIEW, "scale_range_advisory_TEMPORARY"
    elif (
        scale_range is not None
        and scale_range > thresholds["scale_range_review_when_vlm_unconfirmed"]
        and not vlm_confirmed
    ):
        status, rule = REVIEW, "scale_range_elevated_vlm_unconfirmed"
    return status, {
        "status": status,
        "rule": rule,
        "scale_range": scale_range,
        "translation_drift_frac_of_pet": drift,
        "vlm_confirmed": vlm_confirmed,
        "scale_range_gate": "hard" if scale_hard else "advisory_TEMPORARY",
        "thresholds": thresholds,
    }


def _breathing_business_signals(
    checks: dict[str, str],
    temporal_qa: Optional[dict[str, Any]],
    reasons: list[str],
    *,
    authority_profile: Optional[str] = None,
) -> tuple[dict[str, str], Optional[dict[str, Any]]]:
    """Translate preserved BREATHING evidence into business-v1 authority signals."""

    from .business_qa import BREATHING_AUTHORITY_V1

    temporal = temporal_qa if isinstance(temporal_qa, dict) else {}
    verdict = str(temporal.get("verdict") or "unmeasurable")
    temporal_status = str(checks.get("temporal_breathing") or UNKNOWN)
    legacy_profile = authority_profile == BREATHING_AUTHORITY_V1

    if legacy_profile:
        global_motion = (
            FAIL if verdict == "global_pulse" and temporal_status == FAIL else PASS
        )
    else:
        # breathing-v2: sway / drift / scale_trend / moderate scale change is
        # a quality finding. The catastrophic signal below owns the hard gate.
        global_motion = REVIEW if verdict == "global_pulse" else PASS

    signals = {
        # Absence/uncertainty is a quality concern. The analyzer is primary,
        # but only global corruption is allowed to spend another candidate.
        "breathing_motion_correctness": (
            PASS if verdict == "breathing_detected" else REVIEW
        ),
        "breathing_global_motion_integrity": global_motion,
        # Legacy vlm_composition conflates contamination with presentation.
        # Only the explicit contamination branch retains hard authority.
        "breathing_composition_integrity": (
            FAIL if "vlm_composition_contaminated" in reasons else PASS
        ),
        "breathing_periodicity": PASS,
        "breathing_modulation": PASS,
        "breathing_head_motion": PASS,
    }
    catastrophic_evidence: Optional[dict[str, Any]] = None
    if not legacy_profile:
        status, catastrophic_evidence = _breathing_catastrophic_motion(checks, temporal)
        signals["breathing_catastrophic_motion_integrity"] = status
    advisory_signal = {
        "periodic_score": "breathing_periodicity",
        "torso_energy_modulation": "breathing_modulation",
        "head_to_torso_ratio": "breathing_head_motion",
    }
    for finding in temporal.get("advisories") or []:
        if not isinstance(finding, dict):
            continue
        name = advisory_signal.get(str(finding.get("check") or ""))
        if name:
            signals[name] = REVIEW
    return signals, catastrophic_evidence


def _motion_business_evidence(
    spec_contract: dict[str, Any],
    checks: dict[str, str],
    *,
    temporal_qa: Optional[dict[str, Any]],
    reasons: list[str],
) -> tuple[Optional[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """Materialize the registry-owned class contract beside legacy QA."""

    contract = (
        (((spec_contract.get("requirements") or {}).get("qa") or {}).get("business"))
        if isinstance(spec_contract, dict)
        else None
    )
    if not isinstance(contract, dict):
        return None, {}, {}

    persisted = deepcopy(contract)
    persisted["motion_id"] = spec_contract.get("motion_id")
    signals: dict[str, str] = {
        "motion_temporal_integrity": _business_integrity_signal(
            checks.get("temporal_stability")
        ),
    }
    if bool(contract.get("structural_required")):
        signals["motion_structural_integrity"] = _business_integrity_signal(
            checks.get("structural_morphology_consistency")
        )
    catastrophic_evidence: Optional[dict[str, Any]] = None
    if str(spec_contract.get("motion_id") or "").upper() == "BREATHING":
        breathing_signals, catastrophic_evidence = _breathing_business_signals(
            checks,
            temporal_qa,
            reasons,
            authority_profile=contract.get("authority_profile"),
        )
        signals.update(breathing_signals)

    domain_evidence = {**checks, **signals}
    domains: dict[str, Any] = {}
    for name, domain in dict(contract.get("domains") or {}).items():
        required = [str(check) for check in (domain.get("required_checks") or [])]
        domains[str(name)] = {
            **dict(domain),
            "required_checks": required,
            "status": _domain_status(domain_evidence, required),
        }
    if catastrophic_evidence is not None and "catastrophic_motion_integrity" in domains:
        domains["catastrophic_motion_integrity"]["evidence"] = catastrophic_evidence
    return persisted, signals, domains


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
    ruleset: Optional[str] = None,
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

    motion_class = str(spec_contract.get("motion_class") or "")
    locomotion = motion_class == "LOCOMOTION"
    micro = motion_class == "MICRO"
    interaction = motion_class == "INTERACTION"

    identity_evaluation: dict[str, Any] = {
        "mode": "full_frame",
        "rule": ("locomotion_mean_consistency" if locomotion else "worst_frame"),
    }

    if not valid or start_sig is None:
        checks["identity_over_time"] = "unknown"
        checks["temporal_stability"] = "unknown"
        reasons.append("frame_sampling_unavailable")
    else:
        # ── 신원 드리프트: 모든 샘플 프레임 vs 시작 키프레임 ─────────────
        # v4/v5 — LOCOMOTION·MICRO 는 펫 크롭 정규화 시그니처로 비교한다. 전체
        # 프레임 히스토그램은 양방향으로 구조적 편향이 있다: 다가오기가 성공할수록
        # 시작 키프레임과 멀어지고(v4), MICRO 는 거의 안 움직이는 대신 평평한
        # 배경의 1~2 단계 양자화 이동이 점수를 지배한다(v5). 정규화가 불가능하면
        # (배경 모델 실패 등) 전체 프레임 방식으로 폴백한다. 다른 클래스와
        # 나머지 검사(시간 안정성/루프/끝점)는 계속 전체 프레임 시그니처다.
        #
        # ⚠️ 폴백 조건은 "**모든** 디코딩된 프레임이 정규화됐는가" 다. 정규화
        # 불가 프레임은 sims 에서 조용히 빠지는데, 그게 하필 신원이 사라진
        # 프레임(화이트아웃/장면 전환 — 전경 분할 자체가 실패하는 바로 그 경우)
        # 이라 부분 측정을 허용하면 fail-open 이 된다: 5 프레임 중 3 장이
        # 화이트아웃인 클립이 나머지 2 장만으로 신원 PASS 를 받았다.
        
        use_pet_normalized_identity = locomotion or micro or interaction
        
        id_sigs, id_start = sigs, start_sig
        if use_pet_normalized_identity:
            norm_start = _pet_normalized_signature(start_keyframe_rgb)
            norm_sigs = [
                (_pet_normalized_signature(f) if f is not None else None)
                for f in (frames or [])
            ]
            measurable = sum(1 for s in norm_sigs if s is not None)
            decoded_frames = len(valid)
            identity_evaluation["normalized_frames"] = measurable
            identity_evaluation["decoded_frames"] = decoded_frames
            if norm_start is not None and measurable == decoded_frames:
                id_sigs, id_start = norm_sigs, norm_start
                identity_evaluation["mode"] = "pet_normalized"

        sims = []
        for s in id_sigs:
            if s is None:
                frame_similarities.append(None)
                continue
            sim = signature_similarity(s, id_start)
            v = sim.get("hist_intersection") if sim.get("comparable") else None
            frame_similarities.append(v)
            if v is not None:
                sims.append(v)
        if sims:
            identity_similarity = round(float(np.mean(sims)), 4)
            worst = min(sims)
            if locomotion:
                # 한 프레임의 최악값이 아니라 평균 + 인접 일관성 + VLM same-pet
                # 증거로 판정한다. 임계값은 전역(PHASE6_QA_IDENTITY_*)과 동일 —
                # 무엇에 적용하는지만 다르다. fail-closed 는 유지된다: 평균이
                # FAIL 임계 아래면 FAIL, 크레이터 프레임은 최소 REVIEW, 경계
                # 구간의 PASS 승격은 VLM 확언 + 일관성 증거가 **둘 다** 있을
                # 때만이다.
                id_adjacent = []
                for a, b in zip(id_sigs, id_sigs[1:]):
                    if a and b:
                        sim = signature_similarity(a, b)
                        if sim.get("comparable"):
                            id_adjacent.append(sim["hist_intersection"])
                adj_min = round(float(min(id_adjacent)), 4) if id_adjacent else None
                vlm_same_pet = str((vlm_qa or {}).get("same_pet_all_frames") or "") == "yes"
                mean_v = float(identity_similarity)
                identity_evaluation.update(
                    {
                        "mean": identity_similarity,
                        "worst": round(float(worst), 4),
                        "adjacent_min": adj_min,
                        "vlm_same_pet": vlm_same_pet,
                    }
                )
                if mean_v < id_fail:
                    checks["identity_over_time"] = FAIL
                    reasons.append(f"identity_mean {round(mean_v, 3)} < {id_fail}")
                elif worst < id_fail:
                    # 평균은 살았지만 한 프레임이 FAIL 임계 아래로 꺼졌다 —
                    # 순간 교체 신호일 수 있다. 자동 PASS 는 없다.
                    checks["identity_over_time"] = REVIEW
                    reasons.append(f"identity_crater_frame {round(worst, 3)} < {id_fail}")
                elif mean_v >= id_pass:
                    checks["identity_over_time"] = PASS
                elif vlm_same_pet and adj_min is not None and adj_min >= adj_review:
                    checks["identity_over_time"] = PASS
                    reasons.append(
                        f"locomotion_identity_resolved mean {round(mean_v, 3)} "
                        f"+ adjacent_min {adj_min} + vlm_same_pet"
                    )
                else:
                    checks["identity_over_time"] = REVIEW
                    reasons.append(f"identity borderline mean {round(mean_v, 3)}")
            elif worst < id_fail:
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

    # ── 구조/해부학 도메인 (pinned morphology + sampled frames) ───────────
    structural_evidence = _structural_domain_from_frames(
        list(frames or []),
        spec_contract,
        checks=checks,
        reasons=reasons,
    )

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

    judged = apply_judgement(
        checks=checks,
        reasons=reasons,
        spec_contract=spec_contract,
        vlm_qa=vlm_qa,
        temporal_qa=temporal_qa,
        structural_evidence=structural_evidence,
        ruleset=ruleset,
    )

    return {
        "qa_version": active_qa_version(ruleset),
        "ruleset": judged["ruleset"],
        "sampling_version": FRAME_SAMPLING_VERSION,
        "sample_fractions": list(SAMPLE_FRACTIONS),
        "identity_similarity": identity_similarity,
        # v4 — 신원 검사가 무엇을 어떻게 쟀는지 (mode: full_frame|pet_normalized).
        # LOCOMOTION 이 아니면 rule=worst_frame 의 기존 판정 그대로다.
        "identity_evaluation": identity_evaluation,
        "frame_similarities": frame_similarities,
        "loop_metrics": loop_metrics,
        "checks": judged["checks"],
        "domains": judged["domains"],
        "advisories": judged["advisories"],
        "motion_business_contract": judged["motion_business_contract"],
        "business_signals": judged["business_signals"],
        "business_domains": judged["business_domains"],
        # v10 — 판정 단계가 무엇을 완화/강등했는지 (v9 에서는 비어 있다).
        "judgement": judged["judgement"],
        "reasons": judged["reasons"],
        "decision": judged["decision"],
        # v1 은 source/model 만 남겨 REVIEW 의 실제 설명(notes)과 원 판정을
        # 잃었다. 운영자가 직접 DB 를 추측하지 않도록 구조화 VLM 근거 전체를
        # 후보 QA 결과에 보존한다.
        "vlm": (dict(vlm_qa) if vlm_qa else None),
        # v3 — BREATHING 시간축 증거 전체 (판정·지표·임계). 없으면 None.
        "temporal": (dict(temporal_qa) if temporal_qa else None),
    }


# ══════════════════════════════════════════════════════════════════════════
# 판정 단계 (v10) — 결정론 측정값 + VLM + 시간축 증거 → decision
#
# evaluate_motion_video 의 후반부를 **순수 함수**로 분리했다. 같은 함수가
#   * 라이브 경로 (프레임을 방금 잰 checks) 와
#   * 오프라인 리플레이 (저장된 qa_result 에서 복원한 checks — rescore_stored_qa_result)
# 를 판정한다. 규칙 집합(ruleset) 은 인자 > 환경 변수 > v9 순으로 정해지고,
# v9 는 이전 동작과 바이트 단위로 같다.
# ══════════════════════════════════════════════════════════════════════════

#: 판정 단계가 **만들어 내는** 검사 — 저장된 qa_result 를 재판정할 때 이 키들은
#: 버리고 vlm/temporal 근거에서 다시 만든다. 나머지 검사는 프레임 측정값이다.
JUDGEMENT_CHECKS = frozenset(
    {
        "vlm_same_pet",
        "vlm_anatomy",
        "vlm_motion",
        "vlm_composition",
        "vlm_target_pose",
        "vlm_locomotion_form",
        "vlm_direction_travel",
        "vlm_interaction",
        "vlm_human_hand_policy",
        "temporal_breathing",
    }
)
#: 판정 단계가 만들어 내는 사유의 접두어 — 재판정 시 제거 후 재생성.
_JUDGEMENT_REASON_PREFIXES = (
    "vlm",
    "temporal_",
    "advisory_checks_not_blocking",
    "advisory_structural_fail_not_blocking",
    "structural_heuristic_borderline",
    "output_conformance:",
)

#: BREATHING 시간축의 전역 게이트 — (metric, threshold key, verdict reason tag).
_TEMPORAL_GLOBAL_GATES = (
    ("scale_range", "scale_pulse_max"),
    ("translation_drift_frac_of_pet", "drift_max_frac"),
    ("scale_trend", "sag_trend_max"),
)


def _default_temporal_thresholds() -> dict[str, float]:
    from .breathing_temporal_qa import analyze_frames  # noqa: F401  (same env keys)

    return {
        "scale_pulse_max": _f("BREATHING_QA_SCALE_PULSE_MAX", 0.020),
        "scale_strict": _f("BREATHING_QA_SCALE_STRICT", 0.010),
        "drift_max_frac": _f("BREATHING_QA_DRIFT_MAX_FRAC", 0.020),
        "sag_trend_max": _f("BREATHING_QA_SAG_TREND_MAX", 0.010),
        "visible_osc_min": _f("BREATHING_QA_VISIBLE_OSC_MIN", 0.003),
        "torso_snr_min": _f("BREATHING_QA_TORSO_SNR_MIN", 1.6),
        "periodic_min": _f("BREATHING_QA_PERIODIC_MIN", 0.25),
        "modulation_strong": _f("BREATHING_QA_MODULATION_STRONG", 0.45),
        "head_ratio_max": _f("BREATHING_QA_HEAD_RATIO_MAX", 1.6),
        "head_ratio_max_midband": _f("BREATHING_QA_HEAD_RATIO_MAX_MIDBAND", 1.2),
    }


def temporal_global_gate_ratios(temporal_qa: Optional[dict[str, Any]]) -> Optional[dict[str, float]]:
    """
    전역 게이트별 (측정값 / 한계) 비율. 지표가 없으면 None (판단 보류).

    sag 게이트는 `trend > osc` 일 때만 활성이다(breathing_temporal_qa 와 동일).
    비활성이면 비율을 0 으로 둔다 — 호흡 진폭이 추세보다 크면 침하가 아니다.
    """
    if not isinstance(temporal_qa, dict):
        return None
    metrics = temporal_qa.get("metrics") or {}
    if not isinstance(metrics, dict) or not metrics:
        return None
    thresholds = {**_default_temporal_thresholds(), **dict(temporal_qa.get("thresholds") or {})}
    ratios: dict[str, float] = {}
    for metric, key in _TEMPORAL_GLOBAL_GATES:
        value = metrics.get(metric)
        limit = thresholds.get(key)
        if not isinstance(value, (int, float)) or not isinstance(limit, (int, float)) or limit <= 0:
            continue
        ratio = float(value) / float(limit)
        if metric == "scale_trend":
            osc = metrics.get("scale_oscillation")
            if isinstance(osc, (int, float)) and float(value) <= float(osc):
                ratio = 0.0
        ratios[metric] = round(ratio, 4)
    return ratios or None


def _breathing_evidence_strong(temporal_qa: dict[str, Any], knobs: dict[str, float]) -> tuple[bool, dict[str, Any]]:
    metrics = temporal_qa.get("metrics") or {}
    thresholds = {**_default_temporal_thresholds(), **dict(temporal_qa.get("thresholds") or {})}
    snr = metrics.get("torso_snr")
    osc = metrics.get("scale_oscillation")
    snr_floor = float(thresholds["torso_snr_min"]) * knobs["snr_strong_mult"]
    osc_floor = float(thresholds["visible_osc_min"]) * knobs["osc_strong_mult"]
    ok = (
        isinstance(snr, (int, float))
        and isinstance(osc, (int, float))
        and float(snr) >= snr_floor
        and float(osc) >= osc_floor
    )
    return bool(ok), {
        "torso_snr": snr,
        "torso_snr_strong_min": round(snr_floor, 4),
        "scale_oscillation": osc,
        "scale_oscillation_strong_min": round(osc_floor, 5),
    }


def _v10_knobs() -> dict[str, float]:
    return {
        # 한계 대비 이 비율 안쪽이면 "경계 구간" — 강한 호흡 증거가 있을 때 REVIEW.
        "band": _f("MOTION_QA_V10_BORDERLINE_BAND", 0.15),
        # 한계의 이 배수 이상은 무조건 hard FAIL (경계 구간 규칙 적용 불가).
        "hard_fail_ratio": _f("MOTION_QA_V10_HARD_FAIL_RATIO", 1.5),
        # 강한 호흡 증거: torso_snr ≥ mult × torso_snr_min, osc ≥ mult × visible_osc_min.
        "snr_strong_mult": _f("MOTION_QA_V10_TORSO_SNR_STRONG_MULT", 2.0),
        "osc_strong_mult": _f("MOTION_QA_V10_OSC_STRONG_MULT", 2.0),
    }


def apply_judgement(
    *,
    checks: dict[str, str],
    reasons: list[str],
    spec_contract: dict[str, Any],
    vlm_qa: Optional[dict[str, Any]],
    temporal_qa: Optional[dict[str, Any]],
    structural_evidence: Optional[dict[str, Any]],
    ruleset: Optional[str] = None,
    vlm_checks_fallback: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """
    checks/reasons 는 **복사**해서 다룬다 — 호출자의 측정값은 변하지 않는다.

    vlm_checks_fallback: 구조화 VLM 근거(vlm dict)가 없을 때 대신 쓸 저장된
    vlm_* 검사값 (qa-v1 행은 vlm 을 source/model 만 남겼다). 리플레이 전용 —
    라이브 경로는 항상 vlm_qa 를 넘긴다. v10 의 VLM-대-시간축 규칙은 이 경우
    근거가 없으므로 적용되지 않는다(보수적).

    반환: decision / checks / reasons / domains / advisories / judgement / ruleset.
    """
    ruleset = active_ruleset(ruleset)
    v10 = ruleset == RULESET_V10
    knobs = _v10_knobs() if v10 else {}
    checks = dict(checks)
    reasons = list(reasons)
    structural_evidence = dict(structural_evidence or {"enabled": False})
    compat = spec_contract.get("video_compat") or {}
    motion_class = str(spec_contract.get("motion_class") or "")

    qa_requirements = ((spec_contract.get("requirements") or {}).get("qa") or {})
    structural_required = list(((qa_requirements.get("structural_anatomy") or {}).get("required_checks") or []))
    identity_required = list(((qa_requirements.get("identity") or {}).get("required_checks") or []))
    motion_required = list(((qa_requirements.get("motion_specific") or {}).get("required_checks") or []))

    judgement: dict[str, Any] = {
        "ruleset": ruleset,
        "knobs": knobs,
        "downgrades": [],     # FAIL → REVIEW (여전히 차단)
        "advisories": [],     # REVIEW → advisory (차단 해제, 근거 보존)
        "hard_fails": [],     # 경계 규칙에서 명시적으로 제외된 명백한 위반
    }
    #: FAIL 에서 REVIEW 로 **강등된** 검사 — 자문 규칙이 이것을 다시 풀어 PASS 로
    #: 만들면 안 된다 (fail-open 금지).
    demoted_from_fail: set[str] = set()
    extra_advisory: set[str] = set()
    extra_findings: list[dict[str, Any]] = []

    gate_ratios = temporal_global_gate_ratios(temporal_qa) if v10 else None
    temporal_gates_pass = bool(
        gate_ratios is not None
        and all(r <= 1.0 for r in gate_ratios.values())
        and checks.get("temporal_stability") == PASS
    )

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

        class_specific = {
            "LOCOMOTION": (
                ("vlm_locomotion_form", "locomotion_form_correct"),
                ("vlm_direction_travel", "direction_travel_correct"),
            ),
            "INTERACTION": (
                ("vlm_interaction", "interaction_correct"),
                ("vlm_human_hand_policy", "human_hand_policy_ok"),
            ),
        }
        for check_name, key in class_specific.get(motion_class, ()):
            # Compatibility for stored/test v2 evidence: absence means the old
            # contract did not ask this question. New v3 responses always do.
            if key not in vlm_qa:
                continue
            val = v(key)
            checks[check_name] = (
                PASS if val == "yes" else (FAIL if val == "no" else "unknown")
            )
            if val == "no":
                reasons.append(f"vlm:{key}=no")

        composition = PASS
        human_policy_failed = (
            motion_class == "INTERACTION"
            and "human_hand_policy_ok" in vlm_qa
            and v("human_hand_policy_ok") == "no"
        )
        if (
            v("duplicated_pet") == "yes"
            or v("scene_cut") == "yes"
            or human_policy_failed
            or v("human_present") == "yes" and not (
                (compat.get("allow_generated_hand")) and motion_class == "INTERACTION"
            )
        ):
            composition = FAIL
            reasons.append("vlm_composition_contaminated")
        elif v("unintended_large_motion") == "yes" and motion_class == "MICRO":
            if v10 and temporal_gates_pass:
                # (a) 결정론 전역 게이트(스케일/이동/침하)가 전부 한계 안인데 VLM 만
                # "큰 움직임"이라 한다 — 이 검출기는 바로 그 판단을 재기 위해 만들었다.
                # 그래도 VLM "yes" 를 자동 PASS 로 뒤집지는 않는다: REVIEW 로 강등.
                composition = REVIEW
                reasons.append("vlm_unintended_large_motion_contradicted_by_temporal_metrics")
                demoted_from_fail.add("vlm_composition")
                judgement["downgrades"].append(
                    {"check": "vlm_composition", "from": FAIL, "to": REVIEW,
                     "reason": "unintended_large_motion_vs_temporal_gates", "gate_ratios": gate_ratios}
                )
            else:
                composition = FAIL
                reasons.append("vlm_unintended_large_motion")
        elif v("major_flicker") == "yes" or v("camera_stable") == "no" or v("background_neutral") == "no":
            composition = REVIEW
            if v10 and temporal_gates_pass and v("background_neutral") != "no":
                # (a) 카메라 안정/플리커는 시간축 지표가 직접 재는 항목이다 — 지표가
                # 통과하면 VLM 소견은 자문으로 남긴다 (배경 중립성은 지표가 못 잰다).
                reasons.append("vlm_temporal_or_background_issue_advisory_temporal_metrics_pass")
                extra_advisory.add("vlm_composition")
                extra_findings.append(
                    {"check": "vlm_composition", "status": REVIEW,
                     "reason": "vlm_camera_or_flicker_contradicted_by_temporal_metrics",
                     "camera_stable": v("camera_stable"), "major_flicker": v("major_flicker"),
                     "gate_ratios": gate_ratios}
                )
                judgement["advisories"].append(extra_findings[-1])
            else:
                reasons.append("vlm_temporal_or_background_issue")
        elif v("single_pet") != "yes":
            composition = "unknown"
        checks["vlm_composition"] = composition

        if motion_class == "TRANSITION":
            val = v("ends_in_target_pose")
            checks["vlm_target_pose"] = (
                PASS if val == "yes" else (FAIL if val == "no" else "unknown")
            )
            if val == "no":
                reasons.append("vlm_did_not_reach_target")
    elif vlm_checks_fallback:
        checks.update({k: str(v_) for k, v_ in vlm_checks_fallback.items() if k in JUDGEMENT_CHECKS and k != "temporal_breathing"})
        for name in ("vlm_same_pet", "vlm_anatomy", "vlm_motion", "vlm_composition"):
            checks.setdefault(name, "unknown")
        reasons.append("vlm_evidence_from_stored_checks")
        judgement["vlm_evidence"] = "stored_checks"
    else:
        checks["vlm_same_pet"] = "unknown"
        checks["vlm_anatomy"] = "unknown"
        checks["vlm_motion"] = "unknown"
        checks["vlm_composition"] = "unknown"
        reasons.append("vlm_qa_unavailable")

    # ── BREATHING 시간축 증거 — 사용 규칙이 계약이다 ────────────────────
    #   * VLM "no" 는 이미 위에서 FAIL 이다 — 시간축 증거가 되살리지 못한다.
    #   * breathing_detected 는 vlm_motion 이 **unknown 일 때만** PASS 로 해소한다.
    #   * 전신 펄스/큰 이동/카메라 drift 는 BREATHING 계약 위반이라 hard FAIL.
    #     v10: 위반 게이트가 전부 경계 구간(≤ 1+band) 이고 호흡 증거가 강하면 REVIEW.
    #          한계의 hard_fail_ratio 배 이상은 어떤 경우에도 FAIL.
    #   * no_motion 은 증거 부족 REVIEW, inconclusive/unmeasurable 은 기존 VLM 판정 유지.
    if temporal_qa is not None:
        verdict = str(temporal_qa.get("verdict") or "unmeasurable")
        if verdict == "breathing_detected":
            checks["temporal_breathing"] = PASS
            if checks.get("vlm_motion") == "unknown":
                checks["vlm_motion"] = PASS
                reasons.append("vlm_motion_resolved_by_temporal_evidence")
        elif verdict == "global_pulse":
            borderline = None
            if v10 and gate_ratios:
                violated = {k: r for k, r in gate_ratios.items() if r > 1.0}
                worst = max(violated.values()) if violated else max(gate_ratios.values())
                strong, evidence = _breathing_evidence_strong(temporal_qa, knobs)
                if worst >= knobs["hard_fail_ratio"]:
                    judgement["hard_fails"].append(
                        {"check": "temporal_breathing", "gate_ratios": gate_ratios,
                         "worst_ratio": worst, "rule": f">= {knobs['hard_fail_ratio']}x limit"}
                    )
                elif (
                    violated
                    and worst <= 1.0 + knobs["band"]
                    and strong
                    and checks.get("vlm_motion") != FAIL
                ):
                    borderline = {
                        "check": "temporal_breathing", "from": FAIL, "to": REVIEW,
                        "reason": "global_pulse_borderline", "gate_ratios": gate_ratios,
                        "worst_ratio": worst, "band": knobs["band"], "breathing_evidence": evidence,
                    }
            if borderline:
                checks["temporal_breathing"] = REVIEW
                demoted_from_fail.add("temporal_breathing")
                judgement["downgrades"].append(borderline)
                reasons.append(
                    f"temporal_global_pulse_borderline: {temporal_qa.get('reason')} "
                    f"(worst {borderline['worst_ratio']}x, band {knobs['band']})"
                )
            else:
                checks["temporal_breathing"] = FAIL
                reasons.append(f"temporal_{verdict}: {temporal_qa.get('reason')}")
        elif verdict == "unlocalized_motion":
            # temporal v2 receipt 호환. v3 부터 head ratio 는 advisory 라 새로
            # 생성되지 않지만 과거 결과를 hard FAIL 로 재해석하지 않는다.
            checks["temporal_breathing"] = REVIEW
            reasons.append(f"temporal_{verdict}: {temporal_qa.get('reason')}")
        elif verdict == "no_motion":
            checks["temporal_breathing"] = REVIEW
            reasons.append(f"temporal_no_breathing: {temporal_qa.get('reason')}")
        # inconclusive / unmeasurable: 체크를 **추가하지 않는다** — unknown 으로
        # 넣으면 VLM 이 yes 라고 확언한 클립까지 REVIEW 로 끌어내려, "시간축
        # 증거는 상반된 VLM 증거를 뒤집지 않는다" 규칙을 어기게 된다.

    # ── (v10) 휴리스틱 구조 FAIL 의 경계 구간 — VLM 해부학/동일 개체가 확언할 때만 ──
    # 구조 검사는 마스크 기하 휴리스틱이라(PET_HEAD 의 허용된 손이 전경에 섞이면
    # bbox 종횡비가 뛴다) 한계 근처의 FAIL 은 REVIEW 로 강등한다. VLM 이 해부학
    # 이상을 봤거나(vlm_anatomy=FAIL) 개체가 바뀌었으면 손대지 않는다.
    if v10 and vlm_qa and v("anatomy_plausible_all_frames") == "yes" and v("same_pet_all_frames") == "yes":
        ratios: dict[str, float] = {}
        contradiction_limit = float(structural_evidence.get("strong_contradiction_ratio") or 0.7)
        for trait, summary in (structural_evidence.get("trait_summary") or {}).items():
            if not isinstance(summary, dict) or summary.get("status") != FAIL:
                continue
            support = float(summary.get("support_frames") or 0)
            mism = float(summary.get("mismatch_frames") or 0)
            if support > 0 and contradiction_limit > 0:
                ratios[f"structural_{trait}"] = round((mism / support) / contradiction_limit, 4)
        signals = structural_evidence.get("anatomy_signals") or {}
        deform = signals.get("deformation_ratio_max")
        if checks.get("anatomy_body_deformation") == FAIL and isinstance(deform, (int, float)):
            ratios["anatomy_body_deformation"] = round(float(deform) / 2.0, 4)
        failing = [n for n in ("structural_morphology_consistency", "anatomy_body_deformation") if checks.get(n) == FAIL]
        if failing and ratios:
            worst = max(ratios.values())
            if worst <= 1.0 + knobs["band"]:
                for name in failing:
                    checks[name] = REVIEW
                    demoted_from_fail.add(name)
                judgement["downgrades"].append(
                    {"check": ",".join(failing), "from": FAIL, "to": REVIEW,
                     "reason": "structural_heuristic_borderline_vlm_anatomy_ok",
                     "ratios": ratios, "worst_ratio": worst, "band": knobs["band"]}
                )
                reasons.append(
                    f"structural_heuristic_borderline_vlm_anatomy_ok worst {worst}x band {knobs['band']}"
                )
            elif worst >= knobs["hard_fail_ratio"]:
                judgement["hard_fails"].append(
                    {"check": ",".join(failing), "ratios": ratios, "worst_ratio": worst}
                )

    # ── 전역 판정 ────────────────────────────────────────────────────────
    # 기본은 fail-closed 다: FAIL 하나면 후보 전체가 FAIL. 단 구조 평가가
    # 명시적으로 advisory 로 분류한 REVIEW 는 증거/사유를 보존하되 PASS 자격을
    # 막지 않는다. UNKNOWN 및 비자문 REVIEW 는 계속 REVIEW 로 막는다.
    required_all = set(structural_required) | set(identity_required) | set(motion_required)
    advisory_checks = set(structural_evidence.get("advisory_checks") or [])
    advisory_checks.update(
        name
        for name in _ADVISORY_STRUCTURAL_CHECKS
        if name not in required_all and checks.get(name) == REVIEW
    )
    advisory_checks.update(extra_advisory)
    # FAIL 에서 강등된 REVIEW 는 절대 자문이 아니다.
    advisory_checks -= demoted_from_fail
    advisory_findings = list(structural_evidence.get("advisory_findings") or []) + extra_findings

    failed = [name for name, value in checks.items() if value == FAIL]
    blocking_non_pass = [
        name
        for name, value in checks.items()
        if value != PASS and not (value == REVIEW and name in advisory_checks)
    ]
    if failed:
        decision = FAIL
    elif checks and not blocking_non_pass:
        decision = PASS
        if advisory_checks:
            reasons.append(
                "advisory_checks_not_blocking:" + ",".join(sorted(advisory_checks))
            )
    else:
        decision = REVIEW

    structural_domain_status = _domain_status(
        checks,
        structural_required,
        advisory_reviews=advisory_checks,
    )
    if any(checks.get(name) == FAIL for name in _ADVISORY_STRUCTURAL_CHECKS):
        structural_domain_status = FAIL

    domains = {
        "identity": {
            "required_checks": identity_required,
            "status": _domain_status(checks, identity_required),
        },
        "structural_anatomy": {
            "required_checks": structural_required,
            "status": structural_domain_status,
            "morphology_consistency": structural_evidence,
            "advisory_checks": sorted(advisory_checks),
            "advisory_findings": advisory_findings,
        },
        "motion_execution": {
            "required_checks": motion_required,
            "status": _domain_status(checks, motion_required),
        },
    }
    motion_business_contract, business_signals, business_domains = _motion_business_evidence(
        spec_contract,
        checks,
        temporal_qa=temporal_qa,
        reasons=reasons,
    )
    judgement["demoted_from_fail"] = sorted(demoted_from_fail)
    judgement["temporal_gate_ratios"] = gate_ratios
    judgement["temporal_gates_pass"] = temporal_gates_pass if v10 else None

    return {
        "ruleset": ruleset,
        "decision": decision,
        "checks": checks,
        "reasons": reasons,
        "domains": domains,
        "advisories": {"checks": sorted(advisory_checks), "findings": advisory_findings},
        "judgement": judgement,
        "motion_business_contract": motion_business_contract,
        "business_signals": business_signals,
        "business_domains": business_domains,
    }


def rescore_stored_qa_result(
    qa_result: dict[str, Any],
    *,
    motion_id: str,
    motion_class: Optional[str] = None,
    ruleset: Optional[str] = None,
    reclassify_temporal: bool = True,
    temporal_thresholds: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """
    저장된 qa_result 를 **영상 없이** 지정 규칙 집합으로 다시 판정한다.

    프레임 측정값(identity_over_time / temporal_stability / loop_return / 끝점 /
    구조 검사)은 저장된 그대로 쓰고, 판정 단계가 만든 검사(JUDGEMENT_CHECKS)와
    사유는 버린 뒤 저장된 vlm/temporal 근거로 다시 만든다. output_conformance
    의 강등 규칙(motion_video_service._evaluate_candidate_qa)도 재적용한다.

    reclassify_temporal=True 면 저장된 시간축 **지표**로 현재 분류기
    (breathing_temporal_qa._classify_temporal_metrics)를 다시 돌린다 — 시간축
    임계 변경도 리플레이할 수 있다. 지표가 없거나 키가 부족하면 저장 verdict 유지.
    프로바이더 호출/DB 쓰기 없음.
    """
    from . import motion_spec

    ruleset = active_ruleset(ruleset)
    stored_checks = dict(qa_result.get("checks") or {})
    checks = {k: v for k, v in stored_checks.items() if k not in JUDGEMENT_CHECKS}
    reasons = [
        r for r in (qa_result.get("reasons") or [])
        if isinstance(r, str) and not r.startswith(_JUDGEMENT_REASON_PREFIXES)
    ]
    spec = motion_spec.get_motion(motion_id)
    resolved_class = str(motion_class or (spec.motion_class if spec else "") or "")
    domains = qa_result.get("domains") or {}

    def _required(domain: str) -> list[str]:
        return list(((domains.get(domain) or {}).get("required_checks")) or [])

    qa_requirements = {
        "structural_anatomy": {"required_checks": _required("structural_anatomy")},
        "identity": {"required_checks": _required("identity")},
        "motion_specific": {"required_checks": _required("motion_execution")},
    }
    if spec:
        current_business = (((spec.requirements or {}).get("qa") or {}).get("business"))
        if isinstance(current_business, dict):
            # Offline replay keeps the stored legacy measurement applicability,
            # but evaluates business authority against the current registry
            # contract. This is QA-only and never regenerates a video.
            qa_requirements["business"] = deepcopy(current_business)
    spec_contract = {
        "motion_id": motion_id,
        "motion_class": resolved_class,
        "video_compat": dict(spec.video_compat) if spec else {},
        "requirements": {"qa": qa_requirements},
    }
    structural_evidence = ((domains.get("structural_anatomy") or {}).get("morphology_consistency")) or {"enabled": False}

    temporal = qa_result.get("temporal")
    temporal = dict(temporal) if isinstance(temporal, dict) else None
    temporal_reclassified = False
    if temporal is not None and reclassify_temporal and isinstance(temporal.get("metrics"), dict):
        from . import breathing_temporal_qa

        thresholds = {**_default_temporal_thresholds(), **dict(temporal.get("thresholds") or {}), **dict(temporal_thresholds or {})}
        try:
            verdict, reason, advisories = breathing_temporal_qa._classify_temporal_metrics(
                dict(temporal["metrics"]), thresholds
            )
        except (KeyError, TypeError, ValueError):
            pass
        else:
            temporal.update({"verdict": verdict, "reason": reason, "advisories": advisories, "thresholds": thresholds})
            temporal_reclassified = True

    stored_vlm = qa_result.get("vlm")
    vlm_qa = dict(stored_vlm) if isinstance(stored_vlm, dict) and any(
        k in stored_vlm for k in ("same_pet_all_frames", "anatomy_plausible_all_frames", "requested_motion_occurs")
    ) else None
    vlm_fallback = None
    if vlm_qa is None:
        stored_vlm_checks = {k: v for k, v in stored_checks.items() if k.startswith("vlm_")}
        # v1 행: 구조화 근거 없이 vlm_* 판정만 남았다 — 그 판정을 그대로 쓴다.
        if stored_vlm_checks and any(v != "unknown" for v in stored_vlm_checks.values()):
            vlm_fallback = stored_vlm_checks

    judged = apply_judgement(
        checks=checks,
        reasons=reasons,
        spec_contract=spec_contract,
        vlm_qa=vlm_qa,
        temporal_qa=temporal,
        structural_evidence=structural_evidence,
        ruleset=ruleset,
        vlm_checks_fallback=vlm_fallback,
    )

    decision = judged["decision"]
    out_reasons = list(judged["reasons"])
    conformance = qa_result.get("output_conformance") or {}
    if conformance.get("status") == FAIL:
        decision = FAIL
        out_reasons += [f"output_conformance:{r}" for r in (conformance.get("reasons") or [])]
    elif conformance.get("status") == REVIEW and decision == PASS:
        decision = REVIEW
        out_reasons += [f"output_conformance:{r}" for r in (conformance.get("reasons") or [])]

    return {
        "qa_version": active_qa_version(ruleset),
        "ruleset": ruleset,
        "decision": decision,
        "checks": judged["checks"],
        "reasons": out_reasons,
        "judgement": judged["judgement"],
        "advisories": judged["advisories"],
        "motion_business_contract": judged["motion_business_contract"],
        "business_signals": judged["business_signals"],
        "business_domains": judged["business_domains"],
        "temporal_verdict": (temporal or {}).get("verdict") if temporal else None,
        "temporal_reclassified": temporal_reclassified,
        "stored_decision": qa_result.get("decision"),
        "stored_qa_version": qa_result.get("qa_version"),
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
    """
    ffprobe → {width, height, duration, has_audio, fps}. 실패는 None (unknown).

    fps 는 output_conformance 자체의 판정에는 쓰이지 않는다(종횡비/해상도/
    길이/오디오만 본다) — 여기 담아 두는 이유는 이 probe 가 이미 원본 바이트
    전체를 훑은 결과라서, packed-alpha 포장(motion_delivery_service.decode_video)
    이 **같은 불변 raw 바이트**에 대해 자기만의 ffprobe 를 또 돌리지 않고 이
    dict 를 그대로 재사용할 수 있게 하기 위해서다.
    """
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
        fps = None
        rate = str(video.get("r_frame_rate") or "")
        if "/" in rate:
            num, den = rate.split("/", 1)
            try:
                fps = float(num) / max(1.0, float(den))
            except ValueError:
                fps = None
        return {
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "duration": duration,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "fps": fps,
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


# ══════════════════════════════════════════════════════════════════════════
# 심각도 분류 (severity) — INTEGRITY vs COSMETIC, 그리고 무결성 게이트
#
# "지금은 무결성 문제만 전달을 막는다." QA 판정(PASS/REVIEW/FAIL)과 사유는 그대로
# 저장되고(감사), 그 위에 **별도의** 전달 가능 여부를 계산한다. 결정은 절대 고쳐
# 쓰지 않는다. 사유 문자열 하나하나를 INTEGRITY 또는 COSMETIC 으로 분류한다:
#
#   INTEGRITY — 다른 개체/해부학 붕괴/펫 중복/허용되지 않은 사람/장면 컷/구조
#               강한 모순/출력 규격 위반, 그리고 신원을 **확인하지 못한** 경우
#               (VLM 없음, 비교 가능한 프레임 없음). 하나라도 있으면 절대 전달 불가.
#   COSMETIC  — 전신 펄스/이동/침하, 약하거나 불확실한 호흡 증거, 카메라·구도
#               소견, 의도치 않은 큰 움직임, 루프 이음매, 요청 모션 미확인, 자문
#               구조 신호, 해상도/길이 미달.
#
# 모르는 사유(새 규칙이 추가한 사유 포함)는 INTEGRITY 다 — 기본값은 "막는다".
# 게이트: MOTION_QA_SEVERITY_GATE = off (기본, 이전 동작) | integrity_only.
# ══════════════════════════════════════════════════════════════════════════

import re as _re

SEVERITY_VERSION = "motion-qa-severity-v1"
SEVERITY_INTEGRITY = "INTEGRITY"
SEVERITY_COSMETIC = "COSMETIC"

SEVERITY_GATE_ENV = "MOTION_QA_SEVERITY_GATE"
SEVERITY_GATE_OFF = "off"
SEVERITY_GATE_INTEGRITY_ONLY = "integrity_only"
SEVERITY_GATE_MODES = (SEVERITY_GATE_OFF, SEVERITY_GATE_INTEGRITY_ONLY)

#: (매칭 방식, 패턴, 심각도). 위에서부터 첫 일치가 이긴다. 값이 붙는 사유
#: ("identity_drift worst_frame 0.08 < 0.2")는 접두어로, 트레잇 이름이 끼는 사유
#: ("structural_body_length_class_strong_contradiction …")는 정규식으로 잡는다.
REASON_SEVERITY_RULES: tuple[tuple[str, str, str], ...] = (
    # ── INTEGRITY: 신원 ──────────────────────────────────────────────────
    ("prefix", "identity_drift", SEVERITY_INTEGRITY),
    ("prefix", "identity_mean", SEVERITY_INTEGRITY),
    ("prefix", "identity_crater_frame", SEVERITY_INTEGRITY),
    ("prefix", "identity borderline", SEVERITY_INTEGRITY),        # 신원 미확정 = 막는다
    ("prefix", "no_comparable_frames", SEVERITY_INTEGRITY),
    ("prefix", "frame_sampling_unavailable", SEVERITY_INTEGRITY),
    ("prefix", "vlm:same_pet_all_frames=no", SEVERITY_INTEGRITY),
    ("prefix", "vlm_qa_unavailable", SEVERITY_INTEGRITY),          # 신원/해부학 미검증
    # ── INTEGRITY: 해부학 / 구도 오염 / 장면 컷 ──────────────────────────
    ("prefix", "vlm:anatomy_plausible_all_frames=no", SEVERITY_INTEGRITY),
    ("prefix", "vlm_composition_contaminated", SEVERITY_INTEGRITY),  # 펫 중복 / 장면 컷 / 허용 안 된 사람
    ("prefix", "scene_cut_or_swap", SEVERITY_INTEGRITY),
    ("regex", r"^structural_.+_strong_contradiction", SEVERITY_INTEGRITY),
    ("prefix", "anatomy_limb_count_or_placement_corrupted", SEVERITY_INTEGRITY),
    ("prefix", "anatomy_joint_implausible", SEVERITY_INTEGRITY),
    ("prefix", "anatomy_severe_body_deformation", SEVERITY_INTEGRITY),
    # ── INTEGRITY: 출력 규격 위반 (FAIL 급) ──────────────────────────────
    ("prefix", "output_conformance:aspect_mismatch", SEVERITY_INTEGRITY),
    ("prefix", "output_conformance:audio_stream_present", SEVERITY_INTEGRITY),
    # ── COSMETIC: 호흡 시간축 증거 ───────────────────────────────────────
    ("prefix", "temporal_global_pulse_borderline", SEVERITY_COSMETIC),
    ("prefix", "temporal_global_pulse", SEVERITY_COSMETIC),        # scale_range / drift / sag
    ("prefix", "temporal_unlocalized_motion", SEVERITY_COSMETIC),
    ("prefix", "temporal_no_breathing", SEVERITY_COSMETIC),
    ("prefix", "vlm_motion_resolved_by_temporal_evidence", SEVERITY_COSMETIC),
    # ── COSMETIC: 요청 모션 / 카메라 / 구도 소견 ─────────────────────────
    ("prefix", "vlm:requested_motion_occurs=no", SEVERITY_COSMETIC),
    ("prefix", "vlm_unintended_large_motion", SEVERITY_COSMETIC),  # …_contradicted_by_temporal_metrics 포함
    ("prefix", "vlm_temporal_or_background_issue", SEVERITY_COSMETIC),
    ("prefix", "vlm_did_not_reach_target", SEVERITY_COSMETIC),
    ("prefix", "start_pose_", SEVERITY_COSMETIC),                  # _not_reached / _borderline / _unmeasurable
    ("prefix", "target_pose_", SEVERITY_COSMETIC),
    # ── COSMETIC: 시간 안정성 경계 / 루프 이음매 ─────────────────────────
    ("prefix", "flicker adjacent", SEVERITY_COSMETIC),
    ("prefix", "loop_ssim_below_threshold", SEVERITY_COSMETIC),
    ("prefix", "loop_return_unmeasurable", SEVERITY_COSMETIC),
    ("prefix", "end_pose_far_from_start", SEVERITY_COSMETIC),      # qa-v1 루프 사유
    ("prefix", "locomotion_identity_resolved", SEVERITY_COSMETIC), # 정보성 (신원 PASS 로 해소됨)
    # ── COSMETIC: 자문 구조 신호 ─────────────────────────────────────────
    ("regex", r"^structural_.+_(drift|insufficient_visibility|pose_dependent_change_advisory)", SEVERITY_COSMETIC),
    ("prefix", "structural_evidence_unavailable", SEVERITY_COSMETIC),
    ("prefix", "structural_morphology_evidence_insufficient", SEVERITY_COSMETIC),
    ("prefix", "structural_heuristic_borderline_vlm_anatomy_ok", SEVERITY_COSMETIC),
    ("prefix", "anatomy_joint_borderline", SEVERITY_COSMETIC),
    ("prefix", "anatomy_body_deformation_review", SEVERITY_COSMETIC),
    ("prefix", "anatomy_body_deformation_pose_transition_advisory", SEVERITY_COSMETIC),
    ("prefix", "advisory_checks_not_blocking", SEVERITY_COSMETIC),
    ("prefix", "advisory_structural_fail_not_blocking", SEVERITY_COSMETIC),  # qa-v7 레거시
    ("prefix", "vlm_evidence_from_stored_checks", SEVERITY_COSMETIC),        # 오프라인 재판정 표식
    # ── COSMETIC: 출력 규격 미달 (REVIEW 급) ─────────────────────────────
    ("prefix", "output_conformance:resolution_below_requested", SEVERITY_COSMETIC),
    ("prefix", "output_conformance:duration_off", SEVERITY_COSMETIC),
)

#: 사유 문자열 없이도 FAIL 만으로 무결성 위반인 검사 — 방어적 이중 확인.
INTEGRITY_CHECKS = (
    "identity_over_time",
    "temporal_stability",
    "vlm_same_pet",
    "vlm_anatomy",
    "structural_morphology_consistency",
    "anatomy_limb_count_placement",
    "anatomy_joint_plausibility",
    "anatomy_body_deformation",
)


def classify_reason(reason: Any) -> str:
    """사유 문자열 하나 → INTEGRITY | COSMETIC. 빈 값/모르는 사유는 INTEGRITY."""
    text = str(reason or "").strip()
    if not text:
        return SEVERITY_INTEGRITY
    for kind, pattern, severity in REASON_SEVERITY_RULES:
        if kind == "prefix" and text.startswith(pattern):
            return severity
        if kind == "regex" and _re.match(pattern, text):
            return severity
    return SEVERITY_INTEGRITY


def severity_gate_mode(override: Optional[str] = None) -> str:
    """인자 > 환경 변수 > off. 알 수 없는 값은 off (이전 동작) 로 닫힌다."""
    value = str(override or os.getenv(SEVERITY_GATE_ENV) or SEVERITY_GATE_OFF).strip().lower()
    return value if value in SEVERITY_GATE_MODES else SEVERITY_GATE_OFF


def severity_summary(qa_result: Optional[dict[str, Any]]) -> dict[str, Any]:
    """저장된(또는 방금 만든) qa_result → 무결성/외관 사유 목록. 순수 함수, 부작용 없음."""
    qa = qa_result or {}
    integrity: list[str] = []
    cosmetic: list[str] = []
    for reason in qa.get("reasons") or []:
        (integrity if classify_reason(reason) == SEVERITY_INTEGRITY else cosmetic).append(str(reason))
    checks = qa.get("checks") or {}
    integrity_checks = [name for name in INTEGRITY_CHECKS if checks.get(name) == FAIL]
    for name in integrity_checks:
        marker = f"check:{name}=FAIL"
        if marker not in integrity:
            integrity.append(marker)
    return {
        "version": SEVERITY_VERSION,
        "integrity": integrity,
        "cosmetic": cosmetic,
        "integrity_checks": integrity_checks,
    }


def is_publishable(
    qa_result: Optional[dict[str, Any]],
    *,
    decision: Optional[str] = None,
    mode: Optional[str] = None,
) -> bool:
    """
    전달 가능 여부. PASS 는 항상 참. 그 밖에는 게이트가 integrity_only 이고,
    결정이 REVIEW/FAIL 이며(ERROR 는 QA 결과 자체가 없다), 무결성 사유가 하나도
    없을 때만 참. 게이트 off 에서는 PASS 만 참 — 이전 동작 그대로.
    """
    qa = qa_result or {}
    verdict = str(decision or qa.get("decision") or "").upper()
    if verdict == PASS:
        return True
    if severity_gate_mode(mode) != SEVERITY_GATE_INTEGRITY_ONLY:
        return False
    if verdict not in (REVIEW, FAIL):
        return False
    return not severity_summary(qa)["integrity"]


def severity_receipt(qa_result: Optional[dict[str, Any]], mode: Optional[str] = None) -> dict[str, Any]:
    """qa_result 에 실을 감사 기록 — 분류 결과 + 게이트 모드 + 전달 가능 여부."""
    resolved = severity_gate_mode(mode)
    summary = severity_summary(qa_result)
    return {
        **summary,
        "gate": resolved,
        "publishable": is_publishable(qa_result, mode=resolved),
    }
