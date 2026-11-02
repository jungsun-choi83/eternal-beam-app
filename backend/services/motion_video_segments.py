"""
세그먼트 모션 생성 (PLAY_WITH_ME v2, 2026-10-29) — 하나의 후보 = 양끝 조건부 세그먼트 둘.

    A: STAND_READY ──(짧은 놀이 기대 → 종별 PLAY 포즈)──▶ PLAY_BOW / PLAY_CROUCH 키프레임
    B: PLAY 키프레임 ──(짧은 유지 → 자연 복귀, 되감기 아님)──▶ STAND_READY

둘을 시간 순서로 이어 **하나의** PLAY_WITH_ME 자산(raw_video_path)으로 만든다. 세그먼트
원본은 증거로 함께 저장된다(generation_metadata.segments). 포장/발행/재생은 이어진
자산만 보므로 하류는 바뀌지 않는다.

── 원칙 ─────────────────────────────────────────────────────────────────────
* 세그먼트의 양끝은 **실제 승인된 스틸**이다 — 계약의 start/intermediate/target 키프레임
  플레이트(+ 비디오 앵커). 프롬프트는 그 두 장을 "첫/마지막 프레임"으로 지칭한다.
* 두 세그먼트는 서로 독립이므로 같은 틱에서 **둘 다 제출**한다 (durable 프로바이더가
  A 에서 ProviderWorkPending 을 올려도 B 를 제출한 뒤 대기로 넘어간다 — 벽시계 절반).
* durable 작업 키: DurableVideoProvider 가 metadata.segment/segment_index 로 attempt 축을
  나눈다 (durable_provider_jobs.segment_durable_attempt). 재개 시 같은 영수증을 읽는다.
* 이음매: A 의 마지막 프레임 ≈ PLAY 키프레임 ≈ B 의 첫 프레임. 두 세그먼트가 같은 스틸로
  조건부 생성되므로 이음매에 거의 같은 프레임이 연속으로 둘 생긴다 — B 의 첫 프레임
  하나를 버려 "멈춘 듯한" 중복 프레임을 없앤다 (motion-segment-join-v1). 크로스페이드·
  속도 변경·되감기는 하지 않는다.
* QA (결정론, VLM 과 독립): 세그먼트별 첫/마지막 프레임이 기대 스틸과 맞는지(끝점
  조건부 생성이 실제로 지켜졌는지) + 이음매 연속성(A 마지막 ↔ B 첫 SSIM). 임계는
  기존 motion_video_qa 끝점/루프 임계를 그대로 쓴다 — 새 숫자를 발명하지 않는다.
  REVIEW 는 발행 가능(자문), FAIL 은 명백한 불일치(끝점 미도달·이음매 점프)만.
* 이 모듈은 유료 호출을 하지 않는다 — provider.generate 는 호출자가 넘긴 어댑터다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

import numpy as np

from . import species_motion_variants

logger = logging.getLogger(__name__)

#: 이음매 처리 규칙의 버전 — 바뀌면 PLAY_WITH_ME 생성 스탬프(segment_join)가 바뀐다.
SEGMENT_JOIN_VERSION = "motion-segment-join-v1"
#: COME_PLAY 전용 이음매 규칙 (2026-10-30 v2): v1 의 concat 에 **정지 구간 안에서만** 버리는
#: 모션 인식 트리밍을 더한다 — A 의 정지 리드인, 이음매 양쪽의 과도한 bow 유지
#: (motion_profile.plan_come_play_trim). 끝점 프레임은 보존된다. PLAY_WITH_ME 는 v1 그대로.
COME_PLAY_SEGMENT_JOIN_VERSION = "motion-segment-join-v2-come-play-v1"
#: 결정론 세그먼트 QA 버전 — QA 전용(재생성 사유 아님).
SEGMENT_QA_VERSION = "motion-segment-qa-v1"

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"
UNKNOWN = "unknown"


class MotionVideoSegmentError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ══════════════════════════════════════════════════════════════════════════
# 계획
# ══════════════════════════════════════════════════════════════════════════


def segment_plan(variant: Any) -> list[dict[str, Any]]:
    """계약에 박제되는 세그먼트 계획 — 키프레임 **역할 슬롯**만 가리킨다 (id 는 계약의
    start_keyframe / intermediate_keyframe / target_keyframe 가 쥔다).

    단계 라벨은 변형의 segment_phases, 세그먼트별 길이는 segment_durations_sec 가 있을
    때만 `duration_sec` 로 실린다 (COME_PLAY: A 5 s / B 6 s). 없으면(PLAY_WITH_ME) 두
    세그먼트가 스펙 길이를 같이 쓰고 계획은 이전과 바이트 단위로 같다."""
    pose_role = getattr(variant, "keyframe_role", None) or "INTERMEDIATE"
    phases = tuple(getattr(variant, "segment_phases", None) or ("lead_in_into_pose", "hold_return_to_stand"))
    durations = getattr(variant, "segment_durations_sec", None)
    plan = [
        {
            "id": species_motion_variants.SEGMENT_A,
            "index": 1,
            "start": "start_keyframe",
            "end": "intermediate_keyframe",
            "start_role": "STAND_READY",
            "end_role": pose_role,
            "phase": phases[0],
        },
        {
            "id": species_motion_variants.SEGMENT_B,
            "index": 2,
            "start": "intermediate_keyframe",
            "end": "target_keyframe",
            "start_role": pose_role,
            "end_role": "STAND_READY",
            "phase": phases[1],
        },
    ]
    if durations:
        for entry, duration in zip(plan, durations):
            entry["duration_sec"] = int(duration)
    return plan


def is_segmented_contract(contract: Optional[dict[str, Any]]) -> bool:
    c = contract or {}
    return bool(c.get("intermediate_keyframe")) and bool(c.get("segments"))


def segment_output_spec(joined_spec: dict[str, Any]) -> dict[str, Any]:
    """이어진 자산의 output_spec(총 길이) → 세그먼트 1개의 프로바이더 사양."""
    spec = {k: v for k, v in dict(joined_spec or {}).items() if k != "segments"}
    segments = list((joined_spec or {}).get("segments") or [])
    if segments and segments[0].get("duration_sec") is not None:
        spec["duration_sec"] = segments[0]["duration_sec"]
    return spec


def joined_output_spec(segment_spec: dict[str, Any], plan: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """세그먼트 1개 사양 → 이어진 자산 사양: duration_sec 는 합, segments 에 각 길이.

    계획 항목에 `duration_sec` 가 있으면(COME_PLAY) 그 길이, 없으면 스펙 길이를 쓴다."""
    per = int(segment_spec.get("duration_sec") or 0)
    items = [
        {"id": str(p["id"]), "duration_sec": int(p.get("duration_sec") or per)} for p in plan
    ]
    return {
        **dict(segment_spec),
        "duration_sec": sum(item["duration_sec"] for item in items),
        "segments": items,
    }


def segment_durations(plan: Sequence[dict[str, Any]], joined_spec: dict[str, Any]) -> dict[str, int]:
    """세그먼트 id → 프로바이더에 보낼 길이(초). 이어진 사양의 segments 항목이 정본이고,
    없으면 계획의 duration_sec, 그것도 없으면 세그먼트 기본 길이."""
    base = int(segment_output_spec(joined_spec).get("duration_sec") or 0)
    by_id = {
        str(item.get("id")): int(item.get("duration_sec") or base)
        for item in (joined_spec or {}).get("segments") or []
    }
    out: dict[str, int] = {}
    for entry in plan:
        sid = str(entry["id"])
        out[sid] = int(by_id.get(sid) or entry.get("duration_sec") or base)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 생성 — 두 세그먼트를 같은 틱에 제출한다
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SegmentInput:
    id: str
    index: int
    prompt: str
    start_image_url: Optional[str]
    start_image_bytes: Optional[bytes]
    end_image_url: Optional[str]
    end_image_bytes: Optional[bytes]
    start_keyframe_id: Optional[str]
    end_keyframe_id: Optional[str]
    #: 결정론 QA 기대값 — 세그먼트가 실제로 조건부 생성됐는지 잰다.
    expected_start_rgb: Optional[np.ndarray] = None
    expected_end_rgb: Optional[np.ndarray] = None
    #: 이 세그먼트만의 길이(초). None 이면 호출자의 output_spec 길이를 그대로 쓴다.
    duration_sec: Optional[int] = None


@dataclass
class SegmentGeneration:
    inputs: list[SegmentInput]
    results: list[Any] = field(default_factory=list)


def generate_segments(
    provider: Any,
    inputs: Sequence[SegmentInput],
    *,
    output_spec: dict[str, Any],
    base_metadata: dict[str, Any],
) -> list[Any]:
    """모든 세그먼트에 대해 provider.generate 를 호출한다 (순서 고정).

    durable 어댑터가 ProviderWorkPending 을 올리면 **다음 세그먼트도 제출**한 뒤 첫
    pending 을 다시 올린다 — 재개 틱에서 둘이 함께 수집된다. 계약/프로바이더 오류
    (VideoProviderError)는 pending 보다 우선해 즉시 올라간다 — 잘못된 요청을 기다리지
    않는다. 성공하면 MotionVideoResult 목록(입력 순서)을 돌려준다.
    """
    from .durable_provider_jobs import ProviderWorkPending
    from .video_motion_providers import MotionVideoRequest, VideoProviderError

    results: list[Any] = []
    pending: Optional[ProviderWorkPending] = None
    for seg in inputs:
        request = MotionVideoRequest(
            prompt=seg.prompt,
            start_image_url=seg.start_image_url,
            start_image_bytes=seg.start_image_bytes,
            end_image_url=seg.end_image_url,
            end_image_bytes=seg.end_image_bytes,
            # 세그먼트별 길이(COME_PLAY A 5 s / B 6 s)는 공통 사양 위에 덮어쓴다.
            output_spec={
                **dict(output_spec),
                **({"duration_sec": int(seg.duration_sec)} if seg.duration_sec else {}),
            },
            metadata={
                **dict(base_metadata),
                "segment": seg.id,
                "segment_index": int(seg.index),
                "start_keyframe_id": seg.start_keyframe_id,
                "target_keyframe_id": seg.end_keyframe_id,
            },
        )
        try:
            results.append(provider.generate(request))
        except ProviderWorkPending as exc:
            pending = pending or exc
            results.append(None)
        except VideoProviderError:
            raise
    if pending is not None:
        raise pending
    return results


# ══════════════════════════════════════════════════════════════════════════
# 이어 붙이기 — ffmpeg concat (재인코딩 1회)
# ══════════════════════════════════════════════════════════════════════════


def _probe(path: str) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames:format=duration",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=60,
    )
    meta: dict[str, Any] = {}
    for line in (out.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        meta[key.strip()] = value.strip()
    fps = 0.0
    rate = str(meta.get("r_frame_rate") or "0/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        duration = float(meta.get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    try:
        nb_frames = int(meta.get("nb_frames") or 0)
    except ValueError:
        nb_frames = 0
    return {
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "fps": fps,
        "duration": duration,
        "nb_frames": nb_frames,
    }


def _trim_plan_for_segments(
    segments: Sequence[bytes], trim_policy: Any
) -> dict[str, Any]:
    """두 세그먼트의 모션 시간축 → motion_profile 트리밍 계획 (측정 불가면 applied=False)."""
    from . import motion_profile

    timelines = []
    for data in segments:
        gray = motion_profile.decode_gray_timeline(data)
        timelines.append(motion_profile.motion_timeline(gray) if gray is not None else None)
    return motion_profile.plan_come_play_trim(timelines, policy=trim_policy)


def _select_expression(drop_ranges: Sequence[tuple[int, int]]) -> str:
    """버릴 [start, end) 프레임 구간들 → ffmpeg select 식 (between 은 양끝 포함)."""
    terms = [f"between(n\\,{int(start)}\\,{int(end) - 1})" for start, end in drop_ranges if end > start]
    return "not(" + "+".join(terms) + ")"


def join_segment_videos(
    segments: Sequence[bytes],
    *,
    drop_leading_frame_after_first: bool = True,
    trim_policy: Any = None,
    join_version: Optional[str] = None,
) -> tuple[bytes, dict[str, Any]]:
    """세그먼트 mp4 들 → 하나의 H.264 yuv420p mp4 (오디오 없음) + 메타.

    * 시간 순서 concat. 두 번째 이후 세그먼트의 **첫 프레임 1장**을 버린다 (양끝
      조건부 생성에서 이음매의 중복 프레임 — "멈춘 듯한 한 프레임" 제거).
    * 크기가 다르면 첫 세그먼트 크기로 letterbox(비율 유지, 크롭 없음) 하고 경고로
      남긴다 — 같은 앵커 캔버스에서 나온 세그먼트는 보통 같다.
    * fps 는 첫 세그먼트를 따른다 (CFR 강제).
    * 크로스페이드/속도 변경/되감기 없음 — 결정론 1회 재인코딩.
    * trim_policy (COME_PLAY, motion_profile.TrimPolicy): 두 세그먼트일 때 정지 리드인과
      이음매 양쪽의 과도한 유지 프레임을 **정지 구간 안에서만** 버린다. 버려지는 프레임은
      서로 거의 같은 정지 프레임이라 이음매가 생기지 않고, 각 세그먼트의 첫/마지막
      프레임(조건부 스틸)은 보존된다. 측정 불가면 v1 과 같은 concat 으로 떨어지고 메타에
      그 이유를 남긴다. 트리밍 메타(`trim`, `kept_frames`, `seam_frame`)가 기록된다.
    """
    if not segments or any(not s for s in segments):
        raise MotionVideoSegmentError("SEGMENT_JOIN_INPUT", "이어 붙일 세그먼트가 비어 있습니다.")
    version = join_version or SEGMENT_JOIN_VERSION
    if len(segments) == 1:
        return bytes(segments[0]), {
            "version": version, "segment_count": 1, "dropped_leading_frames": 0,
        }
    trim: Optional[dict[str, Any]] = None
    plans: tuple[Any, ...] = ()
    if trim_policy is not None and len(segments) == 2 and drop_leading_frame_after_first:
        try:
            trim = _trim_plan_for_segments(segments, trim_policy)
        except Exception:
            logger.warning("세그먼트 트리밍 계획 실패 — v1 concat 으로 진행", exc_info=True)
            trim = {"version": getattr(trim_policy, "version", "unknown"), "applied": False,
                    "reason": "plan_error", "segments": []}
        plans = tuple(trim.pop("_plans", ()) or ())
        if not trim.get("applied"):
            plans = ()
    crf = os.getenv("MOTION_SEGMENT_JOIN_CRF", os.getenv("MOTION_DELIVERY_CRF", "16"))
    with tempfile.TemporaryDirectory(prefix="eb_motion_join_") as td:
        paths: list[str] = []
        probes: list[dict[str, Any]] = []
        try:
            for i, data in enumerate(segments):
                path = os.path.join(td, f"seg_{i}.mp4")
                with open(path, "wb") as f:
                    f.write(data)
                paths.append(path)
                probes.append(_probe(path))
        except FileNotFoundError as exc:
            raise MotionVideoSegmentError(
                "SEGMENT_TOOLING_UNAVAILABLE", "ffprobe 가 필요합니다.", status=503
            ) from exc
        first = probes[0]
        if first["width"] <= 0 or first["height"] <= 0 or first["fps"] <= 0:
            raise MotionVideoSegmentError("SEGMENT_JOIN_PROBE", "첫 세그먼트 규격을 읽지 못했습니다.")
        w, h, fps = first["width"], first["height"], first["fps"]
        warnings: list[str] = []
        scaled: list[int] = []
        filters: list[str] = []
        labels: list[str] = []
        kept_frames: list[Optional[int]] = []
        for i, probe in enumerate(probes):
            chain: list[str] = []
            drop_ranges: list[tuple[int, int]] = []
            if plans and i < len(plans):
                drop_ranges = [tuple(r) for r in plans[i].drop_ranges]
            if i > 0 and drop_leading_frame_after_first:
                # 이음매 중복 1장 — 트리밍 계획이 머리 구간 [1, 1+k) 를 더 버리면 [0, 1+k) 로 합친다.
                head = next((r for r in drop_ranges if r[0] == 1), None)
                if head is not None:
                    drop_ranges.remove(head)
                    drop_ranges.append((0, head[1]))
                else:
                    drop_ranges.append((0, 1))
            if drop_ranges:
                chain.append(f"select='{_select_expression(sorted(drop_ranges))}'")
                chain.append("setpts=N/FRAME_RATE/TB")
            dropped_here = sum(end - start for start, end in drop_ranges)
            kept_frames.append((probe["nb_frames"] - dropped_here) if probe["nb_frames"] else None)
            if (probe["width"], probe["height"]) != (w, h):
                scaled.append(i)
                warnings.append(
                    f"segment {i} geometry {probe['width']}x{probe['height']} != {w}x{h} — letterboxed"
                )
                chain.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
                chain.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
            chain.append("format=yuv420p")
            label = f"v{i}"
            filters.append(f"[{i}:v]" + ",".join(chain) + f"[{label}]")
            labels.append(f"[{label}]")
        filters.append("".join(labels) + f"concat=n={len(paths)}:v=1:a=0[v]")
        out_path = os.path.join(td, "joined.mp4")
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for path in paths:
            cmd += ["-i", path]
        cmd += [
            "-filter_complex", ";".join(filters), "-map", "[v]",
            "-r", f"{fps:.6f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-preset", "medium", "-movflags", "+faststart", "-an", out_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=600)
        except FileNotFoundError as exc:
            raise MotionVideoSegmentError(
                "SEGMENT_TOOLING_UNAVAILABLE", "ffmpeg 이 필요합니다.", status=503
            ) from exc
        if proc.returncode != 0 or not os.path.isfile(out_path):
            raise MotionVideoSegmentError(
                "SEGMENT_JOIN_FAILED",
                f"세그먼트 이어 붙이기 실패: {(proc.stderr or b'')[-500:]!r}",
            )
        joined_probe = _probe(out_path)
        with open(out_path, "rb") as f:
            joined = f.read()
    meta: dict[str, Any] = {
        "version": version,
        "segment_count": len(segments),
        "dropped_leading_frames": (len(segments) - 1) if drop_leading_frame_after_first else 0,
        "fps": round(fps, 4),
        "width": w,
        "height": h,
        "segment_durations_sec": [round(p["duration"], 3) for p in probes],
        "segment_frames": [p["nb_frames"] for p in probes],
        "joined_duration_sec": round(joined_probe["duration"], 3),
        "joined_frames": joined_probe["nb_frames"],
        "scaled_segments": scaled,
        "crf": str(crf),
        "warnings": warnings,
    }
    if trim is not None:
        meta["trim"] = trim
        meta["trimmed_sec"] = float(trim.get("trimmed_sec") or 0.0) if trim.get("applied") else 0.0
    if any(k is not None for k in kept_frames):
        meta["kept_frames"] = kept_frames
        meta["seam_frame"] = kept_frames[0]  # 이어진 영상에서 두 번째 세그먼트가 시작하는 프레임
    return joined, meta


# ══════════════════════════════════════════════════════════════════════════
# 결정론 QA — 끝점 조건부 준수 + 이음매 연속성
# ══════════════════════════════════════════════════════════════════════════


def _compare_frames(observed: Optional[np.ndarray], expected: Optional[np.ndarray]) -> dict[str, Optional[float]]:
    """관측 프레임 vs 기대 스틸: 전역 SSIM(형태/포즈) + 펫 정규화 HSV 히스토그램 교차(색/신원).

    SSIM 은 motion_video_qa 의 loop_return 과 같은 측도(첫↔마지막 프레임 동일 포즈 보정값
    PHASE6_QA_LOOP_SSIM_MIN 재사용). 히스토그램은 전체 프레임이 아니라 **펫 픽셀만**
    (_pet_normalized_signature) — 평탄한 중립 배경이 점수를 지배해 다른 펫/엉뚱한 프레임도
    "비슷하다"고 읽는 함정을 피한다. 정규화 불가 시 전체 프레임 시그니처로 폴백한다.
    """
    from . import motion_video_qa
    from .pet_identity_service import signature_similarity

    out: dict[str, Optional[float]] = {"ssim": None, "hist": None}
    if observed is None or expected is None:
        return out
    try:
        from PIL import Image

        from .idle_validation_service import _ssim_rgb

        exp = expected
        if exp.shape != observed.shape:
            exp = np.asarray(
                Image.fromarray(expected).resize((observed.shape[1], observed.shape[0])), dtype=np.uint8
            )
        out["ssim"] = float(_ssim_rgb(observed.astype(np.float64), exp.astype(np.float64)))
    except Exception:
        out["ssim"] = None
    sig_a = motion_video_qa._pet_normalized_signature(observed)
    sig_b = motion_video_qa._pet_normalized_signature(expected)
    if sig_a is None or sig_b is None:
        sig_a = motion_video_qa._frame_signature(observed)
        sig_b = motion_video_qa._frame_signature(expected)
    if sig_a and sig_b:
        sim = signature_similarity(sig_a, sig_b)
        if sim.get("comparable"):
            out["hist"] = float(sim["hist_intersection"])
    return out


def _endpoint_status(observed: Optional[np.ndarray], expected: Optional[np.ndarray],
                     *, label: str, reasons: list[str], metrics: dict[str, Any]) -> str:
    """세그먼트 끝 프레임이 조건 스틸에 도달했는가.

    PASS: SSIM ≥ 루프 임계(같은 포즈·같은 프레이밍). FAIL: 펫 색 분포가 끝점 FAIL 임계
    (PHASE6_QA_ENDPOINT_FAIL) 아래 — 다른 개체/엉뚱한 프레임. 그 사이(같은 펫, 포즈 미도달
    또는 경계)는 REVIEW — 포즈 자체의 판정은 VLM 전용 필드가 맡는다 (발명한 임계 없음).
    """
    from . import motion_video_qa

    cmp = _compare_frames(observed, expected)
    ssim, hist = cmp["ssim"], cmp["hist"]
    if ssim is None and hist is None:
        reasons.append(f"{label}_unmeasurable")
        return UNKNOWN
    metrics[label] = {
        "ssim": round(ssim, 6) if ssim is not None else None,
        "pet_hist_intersection": round(hist, 4) if hist is not None else None,
    }
    ssim_min = motion_video_qa._f("PHASE6_QA_SEAM_SSIM_MIN", motion_video_qa._f("PHASE6_QA_LOOP_SSIM_MIN", 0.65))
    end_fail = motion_video_qa._f("PHASE6_QA_ENDPOINT_FAIL", 0.25)
    if hist is not None and hist < end_fail:
        reasons.append(f"{label}_not_reached pet_hist {round(hist, 3)} < {end_fail}")
        return FAIL
    if ssim is not None and ssim >= ssim_min:
        return PASS
    reasons.append(
        f"{label}_borderline ssim {round(ssim, 3) if ssim is not None else 'n/a'} < {ssim_min}"
    )
    return REVIEW


def _seam_status(last_a: Optional[np.ndarray], first_b: Optional[np.ndarray],
                 *, reasons: list[str], metrics: dict[str, Any]) -> str:
    """A 의 마지막 프레임 ↔ B 의 첫 프레임: 같은 규칙 — SSIM PASS / 펫 색 급변 FAIL / 그 외 REVIEW."""
    from . import motion_video_qa

    cmp = _compare_frames(last_a, first_b)
    ssim, hist = cmp["ssim"], cmp["hist"]
    metrics["segment_seam_ssim"] = round(ssim, 6) if ssim is not None else None
    metrics["segment_seam_hist_intersection"] = round(hist, 4) if hist is not None else None
    if ssim is None and hist is None:
        reasons.append("segment_seam_unmeasurable")
        return UNKNOWN
    seam_min = motion_video_qa._f("PHASE6_QA_SEAM_SSIM_MIN", motion_video_qa._f("PHASE6_QA_LOOP_SSIM_MIN", 0.65))
    end_fail = motion_video_qa._f("PHASE6_QA_ENDPOINT_FAIL", 0.25)
    if hist is not None and hist < end_fail:
        reasons.append(f"segment_seam_jump pet_hist {round(hist, 3)} < {end_fail}")
        return FAIL
    if ssim is not None and ssim >= seam_min:
        return PASS
    reasons.append(
        f"segment_seam_below_threshold ssim {round(ssim, 3) if ssim is not None else 'n/a'} < {seam_min}"
    )
    return REVIEW


def evaluate_segments(
    *,
    segment_videos: Sequence[bytes],
    inputs: Sequence[SegmentInput],
    frame_sampler: Callable[[bytes], Optional[list[Optional[np.ndarray]]]],
    check_names: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """두 세그먼트의 첫/마지막 프레임 vs 기대 스틸 + 이음매. 측정 불가는 unknown(REVIEW 상한).

    check_names: (A 시작, A 끝, B 시작, B 끝, 이음매) 순서의 검사 이름 5개 — 기본은
    species_motion_variants.PLAY_WITH_ME_SEGMENT_CHECK_NAMES, COME_PLAY 는 자기 이름
    (segment_check_names(motion_id)). 레지스트리 required_checks / INTEGRITY_HARD 권위에
    그대로 들어간다. 규칙(임계)은 모션과 무관하게 같다.
    """
    checks: dict[str, str] = {}
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    firsts: list[Optional[np.ndarray]] = []
    lasts: list[Optional[np.ndarray]] = []
    for data in segment_videos:
        frames = None
        try:
            frames = frame_sampler(data) if data else None
        except Exception:
            logger.warning("세그먼트 프레임 샘플링 실패", exc_info=True)
        valid = [f for f in (frames or []) if f is not None]
        firsts.append(valid[0] if valid else None)
        lasts.append(valid[-1] if valid else None)

    names = tuple(check_names or species_motion_variants.PLAY_WITH_ME_SEGMENT_CHECK_NAMES)
    if len(names) != 5:
        raise ValueError("segment check_names must name A-start, A-end, B-start, B-end and the seam")
    # (segment index, first|last, check name)
    slots = (
        (0, "first", names[0]),
        (0, "last", names[1]),
        (1, "first", names[2]),
        (1, "last", names[3]),
    )
    for index, which, name in slots:
        observed = (firsts if which == "first" else lasts)[index] if index < len(segment_videos) else None
        seg = inputs[index] if index < len(inputs) else None
        expected = (
            (seg.expected_start_rgb if which == "first" else seg.expected_end_rgb) if seg else None
        )
        checks[name] = _endpoint_status(
            observed, expected, label=name, reasons=reasons, metrics=metrics
        )
    seam_name = names[4]
    checks[seam_name] = _seam_status(
        lasts[0] if len(lasts) > 0 else None,
        firsts[1] if len(firsts) > 1 else None,
        reasons=reasons, metrics=metrics,
    )
    if any(v == FAIL for v in checks.values()):
        decision = FAIL
    elif all(v == PASS for v in checks.values()):
        decision = PASS
    else:
        decision = REVIEW
    return {
        "version": SEGMENT_QA_VERSION,
        "checks": checks,
        "metrics": metrics,
        "reasons": reasons,
        "decision": decision,
    }


def merge_segment_qa(qa: dict[str, Any], segments_qa: Optional[dict[str, Any]]) -> dict[str, Any]:
    """후보 QA 결과에 결정론 세그먼트 검사를 합친다 (복사본 반환).

    * checks/reasons 에 세그먼트 검사가 들어간다 → business-v1 영수증이 레지스트리
      권위(INTEGRITY_HARD)로 읽는다.
    * decision: FAIL 이 하나라도 있으면 FAIL, PASS 였는데 PASS 아닌 검사가 있으면 REVIEW.
      REVIEW/FAIL 이던 결정은 올라가지 않는다 (fail-open 금지).
    * 세그먼트 검사를 요구하는 business_domains(play_with_me / come_play)의 status 를
      합쳐진 검사로 다시 계산한다.
    """
    from copy import deepcopy

    from . import motion_video_qa

    merged = deepcopy(qa)
    if not segments_qa:
        return merged
    checks = dict(merged.get("checks") or {})
    checks.update({str(k): str(v) for k, v in (segments_qa.get("checks") or {}).items()})
    merged["checks"] = checks
    merged["reasons"] = list(merged.get("reasons") or []) + [
        f"segment:{r}" for r in (segments_qa.get("reasons") or [])
    ]
    merged["segments"] = deepcopy(segments_qa)
    seg_values = [str(v) for v in (segments_qa.get("checks") or {}).values()]
    decision = str(merged.get("decision") or REVIEW)
    if FAIL in seg_values:
        decision = FAIL
    elif decision == PASS and any(v != PASS for v in seg_values):
        decision = REVIEW
    merged["decision"] = decision
    domains = dict(merged.get("business_domains") or {})
    segment_names = set(str(k) for k in (segments_qa.get("checks") or {}))
    touched = False
    for name, domain in list(domains.items()):
        if not isinstance(domain, dict):
            continue
        required = [str(c) for c in (domain.get("required_checks") or [])]
        if not segment_names.intersection(required):
            continue
        domain = dict(domain)
        domain["status"] = motion_video_qa._domain_status(checks, required)
        domains[name] = domain
        touched = True
    if touched:
        merged["business_domains"] = domains
    return merged


def attach_motion_profile(segments_qa: dict[str, Any], profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    """결정론 세그먼트 QA 에 모션 프로파일 진단(COME_PLAY)을 합친다 (복사본 반환).

    * 프로파일 검사는 자문(QUALITY_ADVISORY)이다: PASS/REVIEW/unknown 만 있고 FAIL 은 없다.
    * decision 은 PASS 였을 때만 REVIEW 로 내려간다 — FAIL 로 올라가지도, REVIEW/FAIL 이
      PASS 로 올라가지도 않는다. 측정 불가(status unmeasurable, checks 비움)는 판정을 바꾸지 않는다.
    """
    from copy import deepcopy

    merged = deepcopy(segments_qa)
    if not profile:
        return merged
    merged["profile"] = deepcopy(profile)
    checks = dict(merged.get("checks") or {})
    profile_checks = {str(k): str(v) for k, v in (profile.get("checks") or {}).items()}
    checks.update(profile_checks)
    merged["checks"] = checks
    merged["reasons"] = list(merged.get("reasons") or []) + [
        f"profile:{r}" for r in (profile.get("reasons") or [])
    ]
    if str(merged.get("decision") or REVIEW) == PASS and any(v != PASS for v in profile_checks.values()):
        merged["decision"] = REVIEW
    return merged
