"""
결정론 모션 프로파일 (2026-10-30, COME_PLAY 전용 소비자) — 생성된 클립의 **시간축**을 잰다.

첫 라이브 COME_PLAY 런(2026-10-30, run 592ec5b8)을 프레임 단위로 측정한 결과:

    A: 0–1.2 s 정지(놀이 흥분이 아무것도 아닌 채로 렌더됨) → 1.2–3.4 s 트롯 → 4.2–5.0 s bow 유지
    B: 0–1.75 s bow 유지(이음매 양쪽 합계 ≈2.6 s 정지) → 급회전 → 측면 보행 → 마지막 0.5 s 에 축소

끝점/이음매 SSIM 은 전부 0.99 로 통과했고 VLM 도 모든 모션 필드에 yes 를 줬다 — 즉 기존 QA 는
"포즈에 도달했는가"만 보고 **움직임의 시간 배분**은 전혀 보지 못했다. 이 모듈은 그 빈칸을
결정론으로 채운다:

* `decode_gray_timeline`  — ffmpeg 한 번으로 축소 그레이 프레임 전부(원본 fps) 디코딩.
* `motion_timeline`       — 프레임별 펫 영역 운동 에너지 / 펫 면적 / bbox.
* `plan_come_play_trim`   — 이음매 처리용: 정지 리드인·과도한 bow 유지 프레임을 **정지 구간
                            안에서만** 버릴 계획 (끝점 프레임은 항상 보존 — 조건부 스틸과의
                            일치·BREATHING 이음매가 거기 걸려 있다).
* `evaluate_come_play_profile` — 이어진 자산의 정지 시간 / 접근 진행 / bow 유지 / 복귀 진행을
                            PASS / REVIEW / unknown 으로 진단. **FAIL 은 없다** — 움직임의 질은
                            자문(QUALITY_ADVISORY)이고, 측정 불가는 unknown 이다.

이 모듈은 유료 호출을 하지 않고 영상을 바꾸지 않는다(계획만 세운다). 임계값은 전부 env 로
덮어쓸 수 있고 기본값은 위 실측에서 왔다 — 발명한 숫자가 아니라 재현 가능한 기준선이다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

MOTION_PROFILE_VERSION = "motion-profile-v1"
COME_PLAY_TRIM_POLICY_VERSION = "come-play-trim-v1"
COME_PLAY_PROFILE_QA_VERSION = "come-play-motion-profile-v1"

PASS = "PASS"
REVIEW = "REVIEW"
UNKNOWN = "unknown"

#: 프로파일 진단 검사 이름 (전부 QUALITY_ADVISORY — motion_spec 이 권위를 선언한다).
COME_PLAY_PROFILE_CHECK_NAMES: tuple[str, ...] = (
    "come_play_profile_lead_stationary",
    "come_play_profile_approach_growth",
    "come_play_profile_approach_progression",
    "come_play_profile_bow_hold",
    "come_play_profile_return_progression",
)


def _f(env: str, default: float) -> float:
    raw = os.getenv(env)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


# ══════════════════════════════════════════════════════════════════════════
# 디코딩
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GrayTimeline:
    frames: np.ndarray  #: (N, H, W) uint8
    fps: float
    duration_sec: float

    @property
    def frame_count(self) -> int:
        return int(self.frames.shape[0])


def _probe(path: str) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=60,
    )
    meta: dict[str, str] = {}
    for line in (out.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    try:
        num, den = str(meta.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        duration = float(meta.get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    return {
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "fps": fps,
        "duration": duration,
    }


def decode_gray_timeline(video_bytes: bytes, *, width: int = 320) -> Optional[GrayTimeline]:
    """영상 → 원본 fps 의 축소 그레이 프레임 전부. 도구 없음/디코딩 실패는 None (측정 불가)."""
    if not video_bytes or width < 16:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="eb_motion_profile_") as td:
            path = os.path.join(td, "input.mp4")
            with open(path, "wb") as fh:
                fh.write(video_bytes)
            probe = _probe(path)
            if probe["width"] <= 0 or probe["height"] <= 0 or probe["fps"] <= 0:
                return None
            height = max(2, int(round(probe["height"] * width / probe["width"])) // 2 * 2)
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={width}:{height}",
                 "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=300,
            )
            if proc.returncode != 0 or not proc.stdout:
                return None
            data = np.frombuffer(proc.stdout, dtype=np.uint8)
            per_frame = width * height
            count = data.size // per_frame
            if count < 2:
                return None
            frames = data[: count * per_frame].reshape(count, height, width)
            return GrayTimeline(frames=frames, fps=float(probe["fps"]), duration_sec=float(probe["duration"]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:
        logger.warning("모션 프로파일 디코딩 실패", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════════
# 시간축 측정
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MotionTimeline:
    fps: float
    #: 프레임별 운동 에너지 — 이전 프레임과의 평균 절대차(펫 영역 합집합 안). energy[0] = 0.
    energy: np.ndarray
    #: 프레임별 펫 면적 비율 (0..1).
    area: np.ndarray
    #: 프레임별 bbox 높이/너비 (px, 축소 해상도).
    bbox_h: np.ndarray
    bbox_w: np.ndarray
    background_level: float
    #: 축소 그레이 프레임 (N, H, W) — 이음매 컷 지점 탐색(프레임 간 차이)에 쓴다.
    frames: Optional[np.ndarray] = None
    mask_threshold: float = 18.0

    @property
    def frame_count(self) -> int:
        return int(self.energy.shape[0])

    def seconds(self, frames: int) -> float:
        return round(float(frames) / self.fps, 3) if self.fps > 0 else 0.0


def frame_difference(a: MotionTimeline, i: int, b: MotionTimeline, j: int) -> Optional[float]:
    """두 시간축의 프레임 i, j 사이 펫 영역 평균 절대차 (energy 와 같은 측도). 프레임이 없으면 None."""
    if a.frames is None or b.frames is None:
        return None
    if not (0 <= i < a.frames.shape[0] and 0 <= j < b.frames.shape[0]):
        return None
    x = a.frames[i].astype(np.float32)
    y = b.frames[j].astype(np.float32)
    if x.shape != y.shape:
        return None
    union = (np.abs(x - a.background_level) > a.mask_threshold) | (np.abs(y - b.background_level) > b.mask_threshold)
    if not union.any():
        return 0.0
    return float(np.abs(x - y)[union].mean())


def motion_timeline(timeline: GrayTimeline, *, mask_threshold: Optional[float] = None) -> Optional[MotionTimeline]:
    """축소 그레이 프레임들 → 펫 영역 기준 운동 에너지·면적 시간축.

    배경은 첫 프레임 테두리(좌우 8 px)의 중앙값 — 평탄한 중립 배경(생성 계약)이 전제다.
    테두리가 펫으로 덮여 배경을 못 잡으면(면적 비율이 비정상) None.
    """
    frames = timeline.frames.astype(np.float32)
    n, h, w = frames.shape
    if n < 2 or h < 16 or w < 16:
        return None
    edges = np.concatenate([frames[0][:, :8], frames[0][:, -8:]], axis=1)
    background = float(np.median(edges))
    threshold = float(mask_threshold if mask_threshold is not None else _f("MOTION_PROFILE_MASK_DELTA", 18.0))
    masks = np.abs(frames - background) > threshold
    area = masks.reshape(n, -1).mean(axis=1)
    if float(area[0]) < 0.005 or float(area.max()) > 0.9:
        return None
    energy = np.zeros(n, dtype=np.float64)
    bbox_h = np.zeros(n, dtype=np.float64)
    bbox_w = np.zeros(n, dtype=np.float64)
    for i in range(n):
        ys, xs = np.where(masks[i])
        if ys.size:
            bbox_h[i] = float(ys.max() - ys.min() + 1)
            bbox_w[i] = float(xs.max() - xs.min() + 1)
        if i == 0:
            continue
        union = masks[i] | masks[i - 1]
        if union.any():
            energy[i] = float(np.abs(frames[i] - frames[i - 1])[union].mean())
    return MotionTimeline(
        fps=timeline.fps, energy=energy, area=area, bbox_h=bbox_h, bbox_w=bbox_w,
        background_level=background, frames=timeline.frames, mask_threshold=threshold,
    )


def quiet_threshold(energy: np.ndarray) -> float:
    """정지 판정 임계: max(바닥, 비율 × 그 클립의 90 백분위 에너지).

    실측(2026-10-30): 정지 1.0–2.6, 살아 있는 bow 유지(꼬리 흔들기) 2.4–4.4, 트롯/회전 15–38,
    감속 4–9. 바닥 1.5 / 비율 0.15 는 살아 있는 유지까지 정지로 보고 감속부터 움직임으로 본다
    (p90 ≈ 35 → 5.3)."""
    floor = _f("MOTION_PROFILE_QUIET_FLOOR", 1.5)
    ratio = _f("MOTION_PROFILE_QUIET_RATIO", 0.15)
    moving = energy[1:] if energy.shape[0] > 1 else energy
    p90 = float(np.percentile(moving, 90)) if moving.size else 0.0
    return max(floor, ratio * p90)


def leading_quiet(energy: np.ndarray, threshold: float) -> int:
    """앞에서부터 연속으로 정지인 프레임 수 (프레임 0 은 정의상 정지)."""
    count = 0
    for value in energy:
        if value < threshold:
            count += 1
        else:
            break
    return count


def trailing_quiet(energy: np.ndarray, threshold: float) -> int:
    """뒤에서부터 연속으로 정지인 프레임 수."""
    count = 0
    for value in energy[::-1]:
        if value < threshold:
            count += 1
        else:
            break
    return count


# ══════════════════════════════════════════════════════════════════════════
# 이음매 트리밍 계획 (COME_PLAY)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TrimPolicy:
    version: str = COME_PLAY_TRIM_POLICY_VERSION
    #: 첫 세그먼트 앞에 남길 정지 시간(초) — 첫 프레임(= STAND_READY 스틸)은 항상 남는다.
    lead_keep_sec: float = 0.25
    lead_max_trim_sec: float = 2.0
    #: 이음매 양쪽을 합친 정지(bow 유지) 목표 시간(초).
    seam_hold_target_sec: float = 0.6
    seam_max_trim_sec: float = 3.0
    #: 세그먼트 하나에서 버릴 수 있는 최대 비율 — 넘으면 그 세그먼트는 손대지 않는다.
    max_trim_fraction: float = 0.5
    #: 컷으로 이어지는 두 프레임의 허용 차이: max(바닥, 배율 × 정지 임계). 살아 있는 유지
    #: (꼬리 흔들기)는 시간이 갈수록 첫 프레임에서 멀어지므로, 차이가 이 안에 드는 컷만 쓴다 —
    #: 보이는 점프를 만들면서까지 줄이지 않는다.
    cut_bound_floor: float = 8.0
    cut_bound_ratio: float = 2.0


def come_play_trim_policy() -> TrimPolicy:
    return TrimPolicy(
        lead_keep_sec=_f("COME_PLAY_TRIM_LEAD_KEEP_SEC", 0.25),
        lead_max_trim_sec=_f("COME_PLAY_TRIM_LEAD_MAX_SEC", 2.0),
        seam_hold_target_sec=_f("COME_PLAY_TRIM_SEAM_HOLD_SEC", 0.6),
        seam_max_trim_sec=_f("COME_PLAY_TRIM_SEAM_MAX_SEC", 3.0),
        max_trim_fraction=_f("COME_PLAY_TRIM_MAX_FRACTION", 0.5),
        cut_bound_floor=_f("COME_PLAY_TRIM_CUT_BOUND_FLOOR", 8.0),
        cut_bound_ratio=_f("COME_PLAY_TRIM_CUT_BOUND_RATIO", 2.0),
    )


@dataclass(frozen=True)
class SegmentTrim:
    #: 버릴 [start, end) 프레임 구간들 (원본 프레임 번호, 0-based).
    drop_ranges: tuple[tuple[int, int], ...]
    frame_count: int
    fps: float
    lead_quiet_frames: int
    tail_quiet_frames: int
    lead_trim_frames: int
    tail_trim_frames: int
    head_trim_frames: int  #: 두 번째 세그먼트: 이음매 중복 1장 **뒤에** 더 버리는 머리 프레임 수

    @property
    def dropped_frames(self) -> int:
        return sum(end - start for start, end in self.drop_ranges)

    @property
    def kept_frames(self) -> int:
        return self.frame_count - self.dropped_frames

    def as_dict(self) -> dict[str, Any]:
        sec = (lambda n: round(n / self.fps, 3)) if self.fps > 0 else (lambda n: 0.0)
        return {
            "frame_count": self.frame_count,
            "drop_ranges": [list(r) for r in self.drop_ranges],
            "dropped_frames": self.dropped_frames,
            "kept_frames": self.kept_frames,
            "lead_quiet_sec": sec(self.lead_quiet_frames),
            "tail_quiet_sec": sec(self.tail_quiet_frames),
            "lead_trimmed_sec": sec(self.lead_trim_frames),
            "tail_trimmed_sec": sec(self.tail_trim_frames),
            "head_trimmed_sec": sec(self.head_trim_frames),
        }


def plan_come_play_trim(
    timelines: Sequence[Optional[MotionTimeline]],
    *,
    policy: Optional[TrimPolicy] = None,
) -> dict[str, Any]:
    """두 세그먼트(A: 접근→bow, B: bow→복귀)의 정지 구간 트리밍 계획.

    * A 의 리드인 정지: 첫 `lead_keep_sec` 뒤의 정지 프레임을 버린다 (첫 프레임 보존). 재개
      프레임은 마지막으로 남긴 프레임과의 차이가 컷 허용치 안인 것 중 **가장 뒤**를 고른다.
    * 이음매 유지: A 꼬리 정지 + B 머리 정지(중복 1장 제외)가 `seam_hold_target_sec` 를 넘으면
      (A 의 새 마지막 프레임 i, B 의 재개 프레임 j) 쌍을 **정지 구간 안에서** 탐색한다 — 둘의
      차이가 컷 허용치 안인 쌍 중 남는 유지가 가장 짧은 것, 같으면 A 를 더 많이 남기는 것.
      허용치 안의 쌍이 없으면 이음매는 v1 그대로(중복 1장만 버림)다.
    * 정지 구간 **밖**은 절대 건드리지 않는다. B 의 꼬리(STAND_READY 정착)도 손대지 않는다.
    * 측정 불가(timeline None / 프레임 없음)면 applied=False.
    반환: {"applied": bool, "segments": [SegmentTrim.as_dict() | None, ...], "_plans": (A, B), ...}.
    """
    pol = policy or come_play_trim_policy()
    if len(timelines) != 2:
        return {"version": pol.version, "applied": False, "reason": "two_segments_required", "segments": []}
    a, b = timelines
    if a is None or b is None or a.fps <= 0 or b.fps <= 0 or a.frames is None or b.frames is None:
        return {"version": pol.version, "applied": False, "reason": "unmeasurable", "segments": [None, None]}

    thr_a, thr_b = quiet_threshold(a.energy), quiet_threshold(b.energy)
    bound = max(pol.cut_bound_floor, pol.cut_bound_ratio * max(thr_a, thr_b))
    lead_q = leading_quiet(a.energy, thr_a)
    tail_q = trailing_quiet(a.energy, thr_a)
    head_q = leading_quiet(b.energy, thr_b)
    n_a, n_b = a.frame_count, b.frame_count
    fps = a.fps

    # ── A 리드인 ──────────────────────────────────────────────────────────
    keep_lead = int(round(pol.lead_keep_sec * fps))
    lead_trim = 0
    lead_cut_diff: Optional[float] = None
    if lead_q > keep_lead + 1 and keep_lead >= 1:
        max_resume = min(lead_q, keep_lead + int(round(pol.lead_max_trim_sec * fps)))
        for resume in range(max_resume, keep_lead, -1):  # 가장 뒤의 재개 프레임부터
            diff = frame_difference(a, keep_lead - 1, a, resume)
            if diff is not None and diff <= bound:
                lead_trim = resume - keep_lead
                lead_cut_diff = round(diff, 3)
                break

    # ── 이음매 ────────────────────────────────────────────────────────────
    tail_start = n_a - tail_q                      # A 꼬리 정지 구간의 첫 프레임
    avail_head = max(0, head_q - 1)                # B 첫 1장은 이음매 중복으로 버려진다
    target = int(round(pol.seam_hold_target_sec * fps))
    max_seam_trim = int(round(pol.seam_max_trim_sec * fps))
    hold_before = max(0, tail_q - 1) + avail_head  # A 마지막 프레임은 유지로 세지 않는다(끝점)
    # 쌍 (i, j) 의 순서: 목표 유지에 도달한 쌍이 먼저(도달 못 하면 유지가 짧을수록), 그 안에서는
    # A 를 더 많이 남기는 쪽(큰 i — A 의 마지막 프레임 = 조건부 스틸을 되도록 보존), 그다음 더 짧은
    # 유지, 더 작은 j, 더 작은 차이.
    best: Optional[tuple[int, int, int, int, float]] = None
    if hold_before > target:
        i_lo = max(tail_start, n_a - 1 - max_seam_trim, 1)
        for i in range(n_a - 1, i_lo - 1, -1):
            kept_a = i - tail_start if i >= tail_start else 0
            j_hi = min(head_q, 1 + max_seam_trim - (n_a - 1 - i))
            for j in range(1, max(1, j_hi) + 1):
                if j >= n_b:
                    break
                kept_b = max(0, head_q - j)
                hold_after = kept_a + kept_b
                key_head = (max(hold_after, target), -i, hold_after, j)
                if best is not None and key_head > best[:4]:
                    continue  # 순서상 이길 수 없는 쌍 — 프레임 차이를 계산할 필요 없다
                diff = frame_difference(a, i, b, j)
                if diff is None or diff > bound:
                    continue
                key = (*key_head, diff)
                if best is None or key < best:
                    best = key
    tail_trim = 0
    head_trim = 0
    seam_cut_diff: Optional[float] = None
    if best is not None:
        _reached, neg_i, _hold_after, j, diff = best
        tail_trim = n_a - 1 - (-neg_i)
        head_trim = j - 1
        seam_cut_diff = round(diff, 3)

    # ── 계획 조립 + 안전 상한 ───────────────────────────────────────────────
    def _a_plan(lead: int, tail: int) -> SegmentTrim:
        ranges: list[tuple[int, int]] = []
        if lead > 0:
            ranges.append((keep_lead, keep_lead + lead))
        if tail > 0:
            ranges.append((n_a - tail, n_a))  # A 의 새 마지막 프레임은 i = n_a-1-tail
        return SegmentTrim(
            drop_ranges=tuple(ranges), frame_count=n_a, fps=a.fps,
            lead_quiet_frames=lead_q, tail_quiet_frames=tail_q,
            lead_trim_frames=lead, tail_trim_frames=tail, head_trim_frames=0,
        )

    a_plan = _a_plan(lead_trim, tail_trim)
    if a_plan.dropped_frames > pol.max_trim_fraction * n_a:
        # 상한 초과: 리드인 컷을 먼저 포기하고(이음매 수정이 더 중요), 그래도 넘으면 둘 다 포기.
        lead_trim = 0
        a_plan = _a_plan(0, tail_trim)
        if a_plan.dropped_frames > pol.max_trim_fraction * n_a:
            tail_trim = 0
            head_trim = 0  # 꼬리 컷이 취소되면 머리 컷도 같이 취소 — 쌍으로 고른 것이다
            a_plan = _a_plan(0, 0)
    b_ranges: tuple[tuple[int, int], ...] = ((1, 1 + head_trim),) if head_trim > 0 else ()
    b_plan = SegmentTrim(
        drop_ranges=b_ranges, frame_count=n_b, fps=b.fps,
        lead_quiet_frames=head_q, tail_quiet_frames=trailing_quiet(b.energy, thr_b),
        lead_trim_frames=0, tail_trim_frames=0, head_trim_frames=head_trim,
    )
    if b_plan.dropped_frames > pol.max_trim_fraction * n_b:
        b_plan = SegmentTrim(
            drop_ranges=(), frame_count=n_b, fps=b.fps, lead_quiet_frames=head_q,
            tail_quiet_frames=b_plan.tail_quiet_frames, lead_trim_frames=0, tail_trim_frames=0,
            head_trim_frames=0,
        )
    kept_hold = max(0, tail_q - 1 - a_plan.tail_trim_frames) + max(0, avail_head - b_plan.head_trim_frames)
    trimmed = a_plan.dropped_frames + b_plan.dropped_frames
    return {
        "version": pol.version,
        "applied": trimmed > 0,
        "reason": None if trimmed > 0 else ("nothing_to_trim" if hold_before <= target and lead_q <= keep_lead + 1 else "no_cut_within_bound"),
        "quiet_threshold": [round(thr_a, 3), round(thr_b, 3)],
        "cut_bound": round(bound, 3),
        "policy": {
            "lead_keep_sec": pol.lead_keep_sec, "lead_max_trim_sec": pol.lead_max_trim_sec,
            "seam_hold_target_sec": pol.seam_hold_target_sec, "seam_max_trim_sec": pol.seam_max_trim_sec,
            "max_trim_fraction": pol.max_trim_fraction,
            "cut_bound_floor": pol.cut_bound_floor, "cut_bound_ratio": pol.cut_bound_ratio,
        },
        "lead_cut_diff": lead_cut_diff,
        "seam_cut_diff": seam_cut_diff,
        "seam_hold_sec_before": round(hold_before / fps, 3),
        "seam_hold_sec_after": round(kept_hold / fps, 3),
        "trimmed_frames": trimmed,
        "trimmed_sec": round(trimmed / fps, 3),
        "segments": [a_plan.as_dict(), b_plan.as_dict()],
        "_plans": (a_plan, b_plan),
    }


# ══════════════════════════════════════════════════════════════════════════
# 프로파일 진단 (COME_PLAY — 이어진 자산)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ComePlayProfilePolicy:
    lead_stationary_max_sec: float = 0.6
    bow_hold_max_sec: float = 1.2
    approach_growth_min_ratio: float = 1.15
    approach_progression_min: float = 0.6
    #: 홈 면적 도달이 끝에서 이보다 가까우면 복귀가 끝에 구겨진 것 (REVIEW).
    return_arrival_min_sec: float = 1.0


def come_play_profile_policy() -> ComePlayProfilePolicy:
    return ComePlayProfilePolicy(
        lead_stationary_max_sec=_f("COME_PLAY_PROFILE_LEAD_MAX_SEC", 0.6),
        bow_hold_max_sec=_f("COME_PLAY_PROFILE_HOLD_MAX_SEC", 1.2),
        approach_growth_min_ratio=_f("COME_PLAY_PROFILE_GROWTH_MIN", 1.15),
        approach_progression_min=_f("COME_PLAY_PROFILE_PROGRESSION_MIN", 0.6),
        return_arrival_min_sec=_f("COME_PLAY_PROFILE_RETURN_ARRIVAL_MIN_SEC", 1.0),
    )


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    if values.size == 0 or window <= 1:
        return values.astype(np.float64)
    kernel = np.ones(window) / window
    padded = np.pad(values.astype(np.float64), (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def evaluate_come_play_profile(
    timeline: Optional[MotionTimeline],
    *,
    seam_frame: Optional[int],
    policy: Optional[ComePlayProfilePolicy] = None,
) -> dict[str, Any]:
    """이어진 COME_PLAY 자산의 시간축 진단.

    seam_frame: 이어진 영상에서 세그먼트 B 가 시작하는 프레임 번호 (A 가 남긴 프레임 수).
    측정 불가면 status="unmeasurable", checks 비움 — 자문 진단이 판정을 끌어내리지 않는다.
    """
    pol = policy or come_play_profile_policy()
    base = {"version": COME_PLAY_PROFILE_QA_VERSION, "checks": {}, "metrics": {}, "reasons": []}
    if timeline is None or seam_frame is None or timeline.fps <= 0:
        return {**base, "status": "unmeasurable"}
    n = timeline.frame_count
    seam = int(seam_frame)
    if not (2 <= seam <= n - 2):
        return {**base, "status": "unmeasurable", "reasons": ["seam_frame_out_of_range"]}
    fps = timeline.fps
    energy, area = timeline.energy, timeline.area
    thr = quiet_threshold(energy)
    checks: dict[str, str] = {}
    metrics: dict[str, Any] = {"quiet_threshold": round(thr, 3), "seam_frame": seam, "fps": round(fps, 3)}
    reasons: list[str] = []

    # 1) 리드인 정지
    lead = leading_quiet(energy, thr)
    lead_sec = lead / fps
    metrics["lead_stationary_sec"] = round(lead_sec, 3)
    if lead_sec <= pol.lead_stationary_max_sec:
        checks["come_play_profile_lead_stationary"] = PASS
    else:
        checks["come_play_profile_lead_stationary"] = REVIEW
        reasons.append(f"lead_stationary {round(lead_sec, 2)}s > {pol.lead_stationary_max_sec}s")

    # 2) 접근: 면적 성장 + 진행 단조성 (A 구간)
    a_area = _smooth(area[:seam], max(1, int(round(fps / 2))))  # 0.5 s — 트롯의 상하 바운스를 지운다
    growth = float(a_area.max() / a_area[0]) if a_area[0] > 0 else 0.0
    metrics["approach_area_growth_ratio"] = round(growth, 3)
    if growth >= pol.approach_growth_min_ratio:
        checks["come_play_profile_approach_growth"] = PASS
    else:
        checks["come_play_profile_approach_growth"] = REVIEW
        reasons.append(f"approach_growth {round(growth, 2)} < {pol.approach_growth_min_ratio}")
    moving = np.where(energy[:seam] >= thr)[0]
    if moving.size >= int(fps / 2):
        start, end = int(moving.min()), int(moving.max())
        diffs = np.diff(a_area[start:end + 1])
        progression = float((diffs >= 0).mean()) if diffs.size else 0.0
        metrics["approach_progression"] = round(progression, 3)
        metrics["approach_travel_sec"] = round((end - start + 1) / fps, 3)
        if progression >= pol.approach_progression_min:
            checks["come_play_profile_approach_progression"] = PASS
        else:
            checks["come_play_profile_approach_progression"] = REVIEW
            reasons.append(f"approach_progression {round(progression, 2)} < {pol.approach_progression_min}")
    else:
        checks["come_play_profile_approach_progression"] = UNKNOWN
        reasons.append("approach_progression_unmeasurable")

    # 3) bow 유지 — 이음매를 품은 정지 구간
    back = 0
    i = seam - 1
    while i >= 0 and energy[i] < thr:
        back += 1
        i -= 1
    fwd = 0
    i = seam
    while i < n and energy[i] < thr:
        fwd += 1
        i += 1
    hold_sec = (back + fwd) / fps
    metrics["bow_hold_sec"] = round(hold_sec, 3)
    if hold_sec <= pol.bow_hold_max_sec:
        checks["come_play_profile_bow_hold"] = PASS
    else:
        checks["come_play_profile_bow_hold"] = REVIEW
        reasons.append(f"bow_hold {round(hold_sec, 2)}s > {pol.bow_hold_max_sec}s")

    # 4) 복귀 진행 — B 에서 펫 면적이 홈(끝) 수준으로 **언제** 돌아오는가. 실측 결과 bow→기립의
    #    자세 변화 때문에 면적의 "줄어드는 속도"는 깊이를 말해주지 않지만, 홈 면적에 도달하는 시점은
    #    말해준다: 마지막 1 초 안에서야 홈 면적이 되면 복귀가 끝에 구겨 넣어진 것이다(2026-10-30 런).
    b_area = _smooth(area[seam:], max(1, int(round(fps / 2))))
    a_seam, a_end = float(b_area[0]), float(b_area[-1])
    metrics["return_area_start"] = round(a_seam, 4)
    metrics["return_area_end"] = round(a_end, 4)
    b_sec = b_area.shape[0] / fps
    metrics["return_duration_sec"] = round(b_sec, 3)
    if a_end > 0 and a_seam > a_end * 1.08:
        home_level = a_end * 1.08
        # 유지 구간(이음매 앞뒤 정지) 뒤부터 센다 — bow 자체는 복귀가 아니다.
        search_from = min(b_area.shape[0] - 1, fwd)
        arrived = np.where(b_area[search_from:] <= home_level)[0]
        if arrived.size:
            arrival = search_from + int(arrived[0])
            before_end = (b_area.shape[0] - 1 - arrival) / fps
            metrics["return_arrival_before_end_sec"] = round(before_end, 3)
            if before_end >= pol.return_arrival_min_sec:
                checks["come_play_profile_return_progression"] = PASS
            else:
                checks["come_play_profile_return_progression"] = REVIEW
                reasons.append(
                    f"return_arrival_before_end {round(before_end, 2)}s < {pol.return_arrival_min_sec}s"
                )
        else:
            checks["come_play_profile_return_progression"] = UNKNOWN
            reasons.append("return_progression_unmeasurable")
    else:
        checks["come_play_profile_return_progression"] = UNKNOWN
        reasons.append("return_shrink_not_observed")

    status = PASS if all(v == PASS for v in checks.values()) else REVIEW
    return {**base, "status": status, "checks": checks, "metrics": metrics, "reasons": reasons}
