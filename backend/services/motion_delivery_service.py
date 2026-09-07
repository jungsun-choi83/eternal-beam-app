"""
Phase 7F — Phase 6 원본 모션을 브라우저가 합성할 수 있는 전달 포맷으로 포장한다.

── 왜 필요한가 ──────────────────────────────────────────────────────────────
Phase 6 산출물은 **중립 회색 배경** 위의 펫 영상이다 (canonical_prompt /
video_anchor 계약). 브라우저 재생기(idle-loop-video)는 세 모드만 안다:

    raw       배경이 구워진 완성 장면
    blackkey  검정 배경 제거 (레거시 Luma)
    packed    vstack — 상단 RGB, 하단 알파 매트 (packed-alpha-canvas.ts)

회색 배경은 셋 중 어디에도 맞지 않는다. blackkey 는 회색을 못 뽑고, raw 는
회색 사각형을 그대로 보여준다. 그래서 이 모듈이 **packed-alpha 파생물**을
만든다 — 테마는 재생 시점에 합성되고, 생성(Phase 1–6)에는 절대 들어가지 않는다.

── 하는 일 / 하지 않는 일 ───────────────────────────────────────────────────
  * raw_video_path 는 생성/QA 증거로 **불변**이다. 절대 덮어쓰지 않는다.
  * 결과는 derived_video_path + delivery_format='packed_alpha' 로 기록된다.
  * 프로바이더 생성/재생성 없음. 후보 decision/selected 를 절대 바꾸지 않는다.
  * REVIEW 후보도 포장할 수 있다(개발/검증용) — 발행 게이트(QA PASS)는
    Phase 7A/RPC 가 그대로 쥔다. FAIL/ERROR 는 포장하지 않는다.

── packed-alpha 계약 (packed-alpha-canvas.ts 와 일치) ───────────────────────
  * 세로 스택: 상단 = RGB(알파 프리멀티플라이), 하단 = 그레이스케일 알파.
  * 높이는 원본의 2배(항상 짝수). 종횡비 h/w ≥ 1 유지(720x1280 → 720x2560).
  * 매트 절반 평균 chroma ≈ 0 (H.264 후 ~4; 브라우저 임계 6.0 미만).
  * 파일명은 `_packed.mp4` 로 끝난다 — isLikelyPackedAlphaSource 빠른 양성.
  * 프리멀티플라이는 배경 오염 제거를 겸한다: out = I − (1−α)·B.
    (α=1 → 펫 원본색, α=0 → 0. 가장자리의 회색 물듦이 정확히 빠진다.)

── 매트 백엔드 ──────────────────────────────────────────────────────────────
  bgmodel  (기본)  프레임별 테두리 중앙값 배경색 대비 거리 키잉. Phase 6 배경이
                   평탄한 중립 회색이라는 **계약**에 기댄다. 결정론·무의존.
                   한계: 배경과 비슷한 회색 털은 약해질 수 있다 — 그때는 vitmatte.
  vitmatte         기존 스틸 매팅 스택(YOLO→SAM2→ViTMatte)을 프레임마다 호출.
                   품질 우선, 무겁다. MOTION_DELIVERY_MATTE_BACKEND=vitmatte.

시간 안정화는 백엔드와 무관하게 적용된다: 3프레임 시간 중앙값(단일 프레임
구멍/깜빡임 제거) + 제한 EMA(가장자리 펌핑 완화).
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

from . import asset_url_refresh, canonical_pet_service, supabase_assets
from . import motion_video_service as motions

logger = logging.getLogger(__name__)

DELIVERY_PACKED_ALPHA = "packed_alpha"
# v2: 행별 배경 모델 — 실제 산출물의 벽→바닥 세로 그라디언트에서 바닥면이
#     전경으로 남던 결함(라이브 v3 실측, 회색 바닥 슬래브) 수정.
PACKAGING_VERSION = "motion-delivery-v2"
BREATHING = "BREATHING"

#: 포장 대상 모션 (Phase 7H 확장). BREATHING + 기존 상용 5종 — 전부 Phase 6 의
#: 중립 회색 배경 산출물이라 같은 packed-alpha 계약으로 포장된다. 새 모션
#: (PET_HEAD, RUN …)은 카탈로그 결정 전까지 여기서도 열지 않는다.
PACKAGEABLE_MOTIONS: tuple[str, ...] = (
    BREATHING,
    "BLINKING",
    "EAR_TWITCHING",
    "HEAD_TILTING",
    "TAIL_WAGGING",
    "COME_CLOSER",
)

#: 브라우저 판정 상수의 서버측 거울 (packed-alpha-canvas.ts). 포장 결과가 이
#: 임계를 만족하지 못하면 재생기가 packed 로 인식하지 못할 수 있다 — 인코딩
#: 전 검증은 실패로, 인코딩 후(압축 노이즈 포함) 검증은 경고로 다룬다.
ALPHA_MATTE_MAX_CHROMA = 6.0
MIN_COLOR_TO_MATTE_RATIO = 2.0


class MotionDeliveryError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class MotionDeliveryResult:
    motion_version_id: str
    candidate_id: str
    pet_id: str
    delivery_format: str
    derived_bucket: str
    derived_video_path: str
    raw_video_path: str
    frame_count: int
    fps: float
    matte_backend: str
    warnings: list[str] = field(default_factory=list)
    deduplicated: bool = False


# HYBRID_USE_SUPABASE=0 (테스트/로컬)에서 업로드된 파생 객체 경로를 기억한다 —
# 멱등 판정과 발행측 존재 확인이 같은 계약으로 동작하기 위함이다.
_MOCK_DELIVERY_OBJECTS: set[str] = set()


def __reset_for_tests() -> None:
    _MOCK_DELIVERY_OBJECTS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# ══════════════════════════════════════════════════════════════════════════
# 행 로드 — 소유권/모션/결정 게이트
# ══════════════════════════════════════════════════════════════════════════


async def _load_version(motion_version_id: str) -> dict[str, Any]:
    row: Optional[dict[str, Any]] = None
    client = motions._supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(motions._versions_table())
                .select("*")
                .eq("id", motion_version_id)
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            row = rows[0] if rows else None
        except Exception as exc:
            raise MotionDeliveryError(
                "DELIVERY_UNAVAILABLE", "Phase 6 버전을 확인하지 못했습니다.", status=503
            ) from exc
    else:
        row = next(
            (r for r in motions._MOCK_VERSIONS if str(r.get("id")) == motion_version_id), None
        )
    if not row:
        raise MotionDeliveryError(
            "MOTION_VERSION_NOT_FOUND", "Phase 6 모션 버전을 찾을 수 없습니다.", status=404
        )
    return row


async def _load_candidate(candidate_id: str) -> dict[str, Any]:
    row: Optional[dict[str, Any]] = None
    client = motions._supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(motions._candidates_table())
                .select("*")
                .eq("id", candidate_id)
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            row = rows[0] if rows else None
        except Exception as exc:
            raise MotionDeliveryError(
                "DELIVERY_UNAVAILABLE", "Phase 6 후보를 확인하지 못했습니다.", status=503
            ) from exc
    else:
        row = next(
            (c for c in motions._MOCK_CANDIDATES if str(c.get("id")) == candidate_id), None
        )
    if not row:
        raise MotionDeliveryError(
            "CANDIDATE_NOT_FOUND", "Phase 6 후보를 찾을 수 없습니다.", status=404
        )
    return row


# ══════════════════════════════════════════════════════════════════════════
# 비디오 코덱 — ffmpeg/ffprobe CLI (repo 관례: compose_video_service 와 동일)
# ══════════════════════════════════════════════════════════════════════════


def _probe_stream(path: str) -> dict[str, Any]:
    """w/h/fps/duration/오디오 유무. ffprobe 없이는 포장할 수 없다."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                "-of", "default=noprint_wrappers=1", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError as exc:
        raise MotionDeliveryError(
            "DELIVERY_TOOLING_UNAVAILABLE", "ffprobe 가 필요합니다.", status=503
        ) from exc
    info: dict[str, Any] = {}
    for line in (out.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        info[k.strip()] = v.strip()
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    fps = 24.0
    rate = str(info.get("r_frame_rate") or "")
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            fps = float(num) / max(1.0, float(den))
        except ValueError:
            pass
    try:
        duration = float(info.get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60,
    )
    has_audio = bool((audio.stdout or "").strip())
    if width <= 0 or height <= 0:
        raise MotionDeliveryError("DELIVERY_DECODE_FAILED", "원본 영상 규격을 읽지 못했습니다.")
    return {"width": width, "height": height, "fps": fps, "duration": duration, "has_audio": has_audio}


def decode_video(video_bytes: bytes) -> tuple[list[np.ndarray], float]:
    """원본 mp4 → (RGB uint8 프레임 목록, fps). 테스트에서는 decode_fn 주입으로 대체."""
    with tempfile.TemporaryDirectory(prefix="eb_delivery_dec_") as td:
        src = os.path.join(td, "input.mp4")
        with open(src, "wb") as f:
            f.write(video_bytes)
        meta = _probe_stream(src)
        w, h = meta["width"], meta["height"]
        try:
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"],
                capture_output=True, timeout=300,
            )
        except FileNotFoundError as exc:
            raise MotionDeliveryError(
                "DELIVERY_TOOLING_UNAVAILABLE", "ffmpeg 이 필요합니다.", status=503
            ) from exc
        raw = proc.stdout or b""
        frame_size = w * h * 3
        n = len(raw) // frame_size
        if n <= 0:
            raise MotionDeliveryError("DELIVERY_DECODE_FAILED", "원본 영상을 디코딩하지 못했습니다.")
        frames = [
            np.frombuffer(raw[i * frame_size:(i + 1) * frame_size], dtype=np.uint8)
            .reshape(h, w, 3)
            .copy()
            for i in range(n)
        ]
        return frames, float(meta["fps"])


def encode_video(frames: list[np.ndarray], fps: float) -> bytes:
    """RGB uint8 프레임 → H.264 yuv420p mp4 (오디오 없음). 테스트에서는 encode_fn 주입."""
    if not frames:
        raise MotionDeliveryError("DELIVERY_ENCODE_FAILED", "인코딩할 프레임이 없습니다.")
    h, w = frames[0].shape[:2]
    crf = os.getenv("MOTION_DELIVERY_CRF", "16")
    with tempfile.TemporaryDirectory(prefix="eb_delivery_enc_") as td:
        raw_path = os.path.join(td, "frames.rgb")
        out_path = os.path.join(td, "out.mp4")
        with open(raw_path, "wb") as f:
            for fr in frames:
                f.write(np.ascontiguousarray(fr, dtype=np.uint8).tobytes())
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                 "-r", f"{fps:.6f}", "-i", raw_path,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", crf,
                 "-preset", "medium", "-movflags", "+faststart", "-an", out_path],
                capture_output=True, timeout=600,
            )
        except FileNotFoundError as exc:
            raise MotionDeliveryError(
                "DELIVERY_TOOLING_UNAVAILABLE", "ffmpeg 이 필요합니다.", status=503
            ) from exc
        if proc.returncode != 0 or not os.path.isfile(out_path):
            raise MotionDeliveryError(
                "DELIVERY_ENCODE_FAILED",
                f"packed 인코딩 실패: {(proc.stderr or b'')[-500:]!r}",
            )
        with open(out_path, "rb") as f:
            return f.read()


# ══════════════════════════════════════════════════════════════════════════
# 매트 추출
# ══════════════════════════════════════════════════════════════════════════


def _border_median_rgb(frame: np.ndarray) -> np.ndarray:
    """1px 테두리 채널별 중앙값 — video_anchor 와 같은 결정론 배경색 모델."""
    edges = np.concatenate([frame[0, :], frame[-1, :], frame[:, 0], frame[:, -1]])
    return np.median(edges.astype(np.float32), axis=0)


def _rowwise_background(frame: np.ndarray) -> np.ndarray:
    """
    행별 배경 모델 (H,3) — 좌우 테두리 열의 행별 중앙값 + 세로 이동평균 (v2).

    단일 테두리 중앙값(v1)은 실제 Seedance 산출물에서 **바닥면을 통째로 전경으로
    남겼다**: 배경이 균일한 한 색이 아니라 벽→바닥 세로 그라디언트이기 때문이다.
    행별 모델은 그라디언트를 따라가므로 바닥은 키잉되고 접지 그림자만 반투명으로
    남는다. 균일 배경에서는 v1 과 같은 값으로 수렴한다 (기존 테스트 불변).
    """
    f = frame.astype(np.float32)
    edges = np.concatenate([f[:, :8, :], f[:, -8:, :]], axis=1)
    bg_row = np.median(edges, axis=1)  # (H, 3)
    k = 15  # 세로 스무딩 — 테두리를 스치는 그림자/노이즈 행의 오염 완화
    pad = np.pad(bg_row, ((k // 2, k // 2), (0, 0)), mode="edge")
    kernel = np.ones(k) / k
    return np.stack(
        [np.convolve(pad[:, c], kernel, mode="valid") for c in range(3)], axis=1
    )


def _box_blur3(a: np.ndarray) -> np.ndarray:
    """3×3 박스 블러 (순수 numpy) — 앨리어싱 완화용. 경계는 엣지 복제."""
    p = np.pad(a, 1, mode="edge")
    return (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
        + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
        + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    ) / 9.0


def matte_bgmodel(frames: list[np.ndarray]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """
    계약 기반 배경 모델 키잉. Phase 6 배경 = 평탄한 중립 회색(프레임 테두리가
    곧 배경 샘플)이라는 사실에 기댄다. 반환 알파는 float32 0..1.
    """
    lo = _env_float("MOTION_DELIVERY_KEY_LO", 10.0)
    hi = _env_float("MOTION_DELIVERY_KEY_HI", 34.0)
    alphas: list[np.ndarray] = []
    backgrounds: list[list[int]] = []
    for frame in frames:
        f = frame.astype(np.float32)
        bg = _rowwise_background(frame)  # v2 — 행별 모델 (그라디언트 배경 대응)
        backgrounds.append([int(c) for c in bg.mean(axis=0)])
        dist = np.abs(f - bg[:, None, :]).max(axis=2)
        alpha = np.clip((dist - lo) / max(1.0, hi - lo), 0.0, 1.0)
        alphas.append(_box_blur3(alpha).astype(np.float32))
    diag = {
        "backend": "bgmodel",
        "key_lo": lo,
        "key_hi": hi,
        "background_rgb_first": backgrounds[0] if backgrounds else None,
    }
    return alphas, diag


def matte_vitmatte(frames: list[np.ndarray]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """기존 스틸 매팅 스택(YOLO→SAM2→ViTMatte)을 프레임마다 호출. 무겁지만 정밀."""
    try:
        from PIL import Image

        from . import vitmatte_service
    except ImportError as exc:
        raise MotionDeliveryError(
            "MATTE_BACKEND_UNAVAILABLE", "vitmatte 백엔드 의존성이 없습니다.", status=503
        ) from exc
    alphas: list[np.ndarray] = []
    for frame in frames:
        buf = io.BytesIO()
        Image.fromarray(frame, "RGB").save(buf, format="PNG")
        try:
            cutout_png = vitmatte_service.matte_foreground(buf.getvalue())
        except Exception as exc:
            raise MotionDeliveryError(
                "MATTE_BACKEND_FAILED", f"vitmatte 프레임 매팅 실패: {exc}", status=503
            ) from exc
        rgba = np.array(Image.open(io.BytesIO(cutout_png)).convert("RGBA"))
        if rgba.shape[:2] != frame.shape[:2]:
            rgba = np.array(
                Image.fromarray(rgba, "RGBA").resize(
                    (frame.shape[1], frame.shape[0]), Image.LANCZOS
                )
            )
        alphas.append((rgba[:, :, 3].astype(np.float32)) / 255.0)
    return alphas, {"backend": "vitmatte"}


def _select_matte_backend() -> tuple[str, Callable[[list[np.ndarray]], tuple[list[np.ndarray], dict[str, Any]]]]:
    name = (os.getenv("MOTION_DELIVERY_MATTE_BACKEND") or "bgmodel").strip().lower()
    if name == "vitmatte":
        return name, matte_vitmatte
    return "bgmodel", matte_bgmodel


# ══════════════════════════════════════════════════════════════════════════
# 시간 안정화
# ══════════════════════════════════════════════════════════════════════════


def stabilize_alpha(alphas: list[np.ndarray]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """
    3프레임 시간 중앙값 → 제한 EMA.

    중앙값이 단일 프레임 구멍/스파이크(깜빡임)를 제거하고, EMA 가 가장자리
    펌핑을 완화한다. EMA 계수는 낮게 둔다 — 크면 실제 움직임에 고스트가 남는다.
    """
    if len(alphas) < 3:
        return alphas, {"temporal_median": False, "ema": 0.0}
    ema_k = min(0.6, max(0.0, _env_float("MOTION_DELIVERY_ALPHA_EMA", 0.25)))
    med: list[np.ndarray] = [alphas[0]]
    for i in range(1, len(alphas) - 1):
        stack = np.stack([alphas[i - 1], alphas[i], alphas[i + 1]])
        med.append(np.median(stack, axis=0).astype(np.float32))
    med.append(alphas[-1])

    out: list[np.ndarray] = [med[0]]
    for i in range(1, len(med)):
        out.append((med[i] * (1.0 - ema_k) + out[i - 1] * ema_k).astype(np.float32))
    flicker_before = float(np.mean([np.abs(alphas[i] - alphas[i - 1]).mean() for i in range(1, len(alphas))]))
    flicker_after = float(np.mean([np.abs(out[i] - out[i - 1]).mean() for i in range(1, len(out))]))
    return out, {
        "temporal_median": True,
        "ema": ema_k,
        "mean_frame_delta_before": round(flicker_before, 5),
        "mean_frame_delta_after": round(flicker_after, 5),
    }


# ══════════════════════════════════════════════════════════════════════════
# packed 프레임 구성 + 검증
# ══════════════════════════════════════════════════════════════════════════


def build_packed_frames(
    frames: list[np.ndarray], alphas: list[np.ndarray]
) -> list[np.ndarray]:
    """
    프레임별 vstack: 상단 = 배경 오염 제거된 프리멀티플라이 RGB, 하단 = 알파.

    out = I − (1−α)·B  (B = 프레임 테두리 중앙값 배경색)
    α=1 이면 원본색 그대로, α=0 이면 0 — 반투명 가장자리에서 배경 회색이
    정확히 빠지므로 어떤 테마 위에서도 회색 후광이 생기지 않는다.
    """
    packed: list[np.ndarray] = []
    for frame, alpha in zip(frames, alphas):
        f = frame.astype(np.float32)
        bg = _rowwise_background(frame)  # 매트와 같은 배경 모델로 오염 제거
        a3 = alpha[:, :, None]
        premult = np.clip(f - (1.0 - a3) * bg[:, None, :], 0.0, 255.0).astype(np.uint8)
        matte = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
        matte_rgb = np.repeat(matte[:, :, None], 3, axis=2)
        packed.append(np.vstack([premult, matte_rgb]))
    return packed


def _avg_chroma(rgb: np.ndarray) -> float:
    """packed-alpha-canvas.ts averageChroma 의 서버측 거울."""
    f = rgb.astype(np.float32)
    r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
    return float((np.abs(r - g) + np.abs(g - b) + np.abs(r - b)).mean())


def validate_packed_frames(packed: list[np.ndarray]) -> list[str]:
    """
    인코딩 전 구조 검증. 실패는 예외 — 브라우저가 packed 로 못 읽는 산출물을
    조용히 저장할 수 없다. 자동 감지 임계(비율)는 경고만 — 무채색 펫은 합법이며
    명시적 delivery_format 이 휴리스틱을 대신한다.
    """
    warnings: list[str] = []
    if not packed:
        raise MotionDeliveryError("PACKED_INVALID", "packed 프레임이 없습니다.")
    h, w = packed[0].shape[:2]
    if h % 2 != 0:
        raise MotionDeliveryError("PACKED_INVALID", "packed 높이는 짝수여야 합니다.")
    if h / max(1, w) < 1.0:
        raise MotionDeliveryError("PACKED_INVALID", "packed 는 세로(h/w≥1)여야 합니다.")
    half = h // 2
    mid = packed[len(packed) // 2]
    top_chroma = _avg_chroma(mid[:half])
    bottom_chroma = _avg_chroma(mid[half:])
    if bottom_chroma > ALPHA_MATTE_MAX_CHROMA:
        raise MotionDeliveryError(
            "PACKED_INVALID",
            f"알파 절반이 무채색이 아닙니다 (chroma={bottom_chroma:.2f} > {ALPHA_MATTE_MAX_CHROMA}).",
        )
    # H.264 후 매트 절반 chroma 는 0 이 아니라 ~4 가 된다(브라우저 실측). 그
    # 노이즈 바닥(1.0)을 깔고 비교해야 인코딩 전 검증이 브라우저 판정과 같은
    # 방향을 본다. 무채색(회색/흰색/검정) 펫은 정상적으로 여기 걸릴 수 있다 —
    # 재생은 명시 포맷/파일명으로 강제되므로 실패가 아니라 기록만 남긴다.
    if top_chroma < max(bottom_chroma, 1.0) * MIN_COLOR_TO_MATTE_RATIO:
        warnings.append("packed_autodetect_uncertain")
    return warnings


def candidate_delivery_format(candidate: dict[str, Any]) -> Optional[str]:
    """
    후보의 전달 포맷 — 배포 순서 내성 판독.

    1순위는 명시 컬럼(delivery_format, 마이그레이션 20261020)이다. 그 마이그레이션
    이전 DB 에서는 컬럼 쓰기가 조용히 떨어지므로, 포장이 함께 기록하는
    generation_metadata.delivery.format 과 파생 경로의 `_packed.mp4` 규칙을
    차례로 본다 — 세 값 모두 포장 코드만 만든다.
    """
    fmt = str(candidate.get("delivery_format") or "").strip().lower()
    if fmt:
        return fmt
    meta = candidate.get("generation_metadata") or {}
    fmt = str(((meta.get("delivery") or {}).get("format")) or "").strip().lower()
    if fmt:
        return fmt
    derived = str(candidate.get("derived_video_path") or "").strip()
    if derived.split("?")[0].endswith("_packed.mp4"):
        return DELIVERY_PACKED_ALPHA
    return None


def _derived_path_for(raw_video_path: str) -> str:
    if raw_video_path.endswith("_raw.mp4"):
        return raw_video_path[: -len("_raw.mp4")] + "_packed.mp4"
    if raw_video_path.endswith(".mp4"):
        return raw_video_path[: -len(".mp4")] + "_packed.mp4"
    return raw_video_path + "_packed.mp4"


# ══════════════════════════════════════════════════════════════════════════
# 저장/존재 확인
# ══════════════════════════════════════════════════════════════════════════


def _download_raw(bucket: str, path: str) -> Optional[bytes]:
    """raw 후보 영상 다운로드 — 읽기 전용. 테스트에서 monkeypatch 지점."""
    client = supabase_assets.get_client() if _use_db() else None
    if not client:
        return None
    try:
        return client.storage.from_(bucket).download(path)
    except Exception:
        return None


async def _upload_derived(path: str, data: bytes) -> None:
    if _use_db() and supabase_assets.get_client():
        await supabase_assets.upload_asset_to_storage(path, data, "video/mp4")
        return
    _MOCK_DELIVERY_OBJECTS.add(path)


def _derived_exists(bucket: str, path: str) -> bool:
    if _use_db() and supabase_assets.get_client():
        return bool(
            asset_url_refresh.sign_object(asset_url_refresh.StorageObject(bucket=bucket, path=path))
        )
    return path in _MOCK_DELIVERY_OBJECTS


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════


async def package_breathing_for_delivery(
    *,
    user_id: str,
    pet_id: str,
    motion_version_id: str,
    candidate_id: Optional[str] = None,
    force: bool = False,
    # 테스트/재사용 주입 — 실코덱/실매팅 없이 계약을 검증하기 위함.
    video_bytes: Optional[bytes] = None,
    decode_fn: Optional[Callable[[bytes], tuple[list[np.ndarray], float]]] = None,
    encode_fn: Optional[Callable[[list[np.ndarray], float], bytes]] = None,
    matte_fn: Optional[Callable[[list[np.ndarray]], tuple[list[np.ndarray], dict[str, Any]]]] = None,
    upload_fn: Optional[Callable[[str, bytes], Any]] = None,
) -> MotionDeliveryResult:
    """
    선택(또는 지정) Phase 6 후보 하나를 packed-alpha 파생물로 포장한다.

    멱등: 이미 같은 포맷으로 포장돼 있고 객체가 존재하면 그대로 돌려준다
    (deduplicated=True). force=True 는 같은 결정론 경로에 다시 포장한다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    version_id = (motion_version_id or "").strip()
    if not uid or not pid or not version_id:
        raise MotionDeliveryError(
            "DELIVERY_INVALID", "user_id, pet_id, motion_version_id 가 필요합니다."
        )

    version = await _load_version(version_id)
    if str(version.get("user_id") or "") != uid or str(version.get("pet_id") or "") != pid:
        raise MotionDeliveryError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)
    if str(version.get("motion_id") or "").upper() not in PACKAGEABLE_MOTIONS:
        raise MotionDeliveryError(
            "MOTION_NOT_PACKAGEABLE",
            "포장 대상 모션(BREATHING + 상용 5종)이 아닙니다.",
            status=409,
        )

    cand_id = (candidate_id or "").strip() or str(version.get("selected_candidate_id") or "").strip()
    if not cand_id:
        raise MotionDeliveryError(
            "SELECTED_CANDIDATE_MISSING", "포장할 Phase 6 후보가 없습니다.", status=409
        )
    candidate = await _load_candidate(cand_id)
    if (
        str(candidate.get("motion_version_id") or "") != version_id
        or str(candidate.get("user_id") or "") != uid
        or str(candidate.get("pet_id") or "") != pid
    ):
        raise MotionDeliveryError(
            "CANDIDATE_MISMATCH", "후보가 이 모션 버전/펫에 속하지 않습니다.", status=409
        )
    decision = str(candidate.get("decision") or "").upper()
    if decision in ("FAIL", "ERROR"):
        raise MotionDeliveryError(
            "CANDIDATE_NOT_PACKAGEABLE",
            "FAIL/ERROR 후보는 포장하지 않습니다.", status=409,
        )

    raw_path = str(candidate.get("raw_video_path") or "").strip()
    if not raw_path:
        raise MotionDeliveryError(
            "CANDIDATE_ASSET_MISSING", "후보에 raw 영상 경로가 없습니다.", status=409
        )
    bucket = str(candidate.get("raw_bucket") or asset_url_refresh.default_bucket()).strip()
    derived_path = _derived_path_for(raw_path)

    # ── 멱등 지름길 ────────────────────────────────────────────────────────
    already = (
        candidate_delivery_format(candidate) == DELIVERY_PACKED_ALPHA
        and str(candidate.get("derived_video_path") or "") == derived_path
    )
    if already and not force and _derived_exists(bucket, derived_path):
        meta = dict(candidate.get("generation_metadata") or {})
        delivery_meta = dict(meta.get("delivery") or {})
        return MotionDeliveryResult(
            motion_version_id=version_id,
            candidate_id=cand_id,
            pet_id=pid,
            delivery_format=DELIVERY_PACKED_ALPHA,
            derived_bucket=bucket,
            derived_video_path=derived_path,
            raw_video_path=raw_path,
            frame_count=int(delivery_meta.get("frame_count") or 0),
            fps=float(delivery_meta.get("fps") or 0.0),
            matte_backend=str(delivery_meta.get("matte_backend") or ""),
            warnings=list(delivery_meta.get("warnings") or []),
            deduplicated=True,
        )

    # ── 원본 로드 (불변 — 읽기만) ──────────────────────────────────────────
    raw = video_bytes if video_bytes is not None else _download_raw(bucket, raw_path)
    if not raw:
        raise MotionDeliveryError(
            "CANDIDATE_ASSET_UNAVAILABLE", "저장된 raw 영상을 불러오지 못했습니다.", status=503
        )

    frames, fps = (decode_fn or decode_video)(raw)
    if not frames:
        raise MotionDeliveryError("DELIVERY_DECODE_FAILED", "원본에서 프레임을 얻지 못했습니다.")

    backend_name, backend = ("injected", matte_fn) if matte_fn else _select_matte_backend()
    alphas, matte_diag = backend(frames)
    if len(alphas) != len(frames):
        raise MotionDeliveryError("MATTE_BACKEND_FAILED", "프레임/알파 수가 일치하지 않습니다.")

    alphas, stab_diag = stabilize_alpha(alphas)
    packed = build_packed_frames(frames, alphas)
    warnings = validate_packed_frames(packed)

    packed_bytes = (encode_fn or encode_video)(packed, fps)
    if upload_fn is not None:
        await _maybe_await(upload_fn(derived_path, packed_bytes))
    else:
        await _upload_derived(derived_path, packed_bytes)

    # ── 후보 갱신 — raw_* 는 절대 만지지 않는다 ────────────────────────────
    meta = dict(candidate.get("generation_metadata") or {})
    meta["delivery"] = {
        "format": DELIVERY_PACKED_ALPHA,
        "packaging_version": PACKAGING_VERSION,
        "matte_backend": matte_diag.get("backend", backend_name),
        "matte": matte_diag,
        "stabilization": stab_diag,
        "fps": round(float(fps), 4),
        "frame_count": len(frames),
        "source_raw_video_path": raw_path,
        "warnings": warnings,
        "packaged_at": _now_iso(),
    }
    # 핵심 필드(파생 경로 + 진단 메타)를 먼저 쓴다. 명시 컬럼(delivery_format,
    # 마이그레이션 20261020)은 **따로** 쓴다 — 컬럼이 아직 없는 DB 에서 한 번의
    # 업데이트로 묶으면 전체가 떨어져 파생 경로까지 사라진다. 판독 쪽은
    # candidate_delivery_format() 이 메타/경로 폴백으로 같은 답을 낸다.
    await canonical_pet_service._update(
        motions._candidates_table(),
        motions._MOCK_CANDIDATES,
        cand_id,
        {"derived_video_path": derived_path, "generation_metadata": meta},
    )
    await canonical_pet_service._update(
        motions._candidates_table(),
        motions._MOCK_CANDIDATES,
        cand_id,
        {"delivery_format": DELIVERY_PACKED_ALPHA},
    )

    return MotionDeliveryResult(
        motion_version_id=version_id,
        candidate_id=cand_id,
        pet_id=pid,
        delivery_format=DELIVERY_PACKED_ALPHA,
        derived_bucket=bucket,
        derived_video_path=derived_path,
        raw_video_path=raw_path,
        frame_count=len(frames),
        fps=float(fps),
        matte_backend=str(matte_diag.get("backend", backend_name)),
        warnings=warnings,
        deduplicated=False,
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


# ══════════════════════════════════════════════════════════════════════════
# Phase 7G — 발행 없는(개발/현재-실행) 재생 해석
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BreathingPlayback:
    """포장된 후보 하나의 재생 해석 — **발행이 아니다.**

    REVIEW 후보를 브라우저에서 확인하기 위한 명시적 개발 경로다. pets 포인터를
    만지지 않고, QA 결정도 바꾸지 않는다. published 는 항상 False — 발행된
    재생은 Phase 7A/하이드레이션(get_published_breathing)이 담당한다.
    """

    motion_version_id: str
    candidate_id: str
    #: 데이터베이스의 실제 QA 결정 그대로 (PASS | REVIEW). 절대 가공하지 않는다.
    qa_decision: str
    url: str
    delivery_format: str
    derived_bucket: str
    derived_video_path: str
    published: bool = False


async def resolve_breathing_playback(
    *,
    user_id: str,
    pet_id: str,
    motion_version_id: str,
    candidate_id: Optional[str] = None,
    sign_fn: Optional[Callable[[asset_url_refresh.StorageObject], Optional[str]]] = None,
) -> BreathingPlayback:
    """
    포장된 BREATHING 후보의 **지금 유효한** 재생 URL 을 만든다. 읽기 전용.

    FAIL/ERROR 후보는 절대 재생으로 해석되지 않는다. 포장되지 않은 후보는
    PLAYBACK_NOT_PACKAGED 로 거절한다 — 회색 원본을 브라우저에 흘리지 않는다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    version_id = (motion_version_id or "").strip()
    if not uid or not pid or not version_id:
        raise MotionDeliveryError(
            "PLAYBACK_INVALID", "user_id, pet_id, motion_version_id 가 필요합니다."
        )

    version = await _load_version(version_id)
    if str(version.get("user_id") or "") != uid or str(version.get("pet_id") or "") != pid:
        raise MotionDeliveryError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)
    if str(version.get("motion_id") or "").upper() not in PACKAGEABLE_MOTIONS:
        raise MotionDeliveryError(
            "MOTION_NOT_PACKAGEABLE", "포장 대상 모션의 재생만 해석합니다.", status=409
        )

    cand_id = (candidate_id or "").strip() or str(version.get("selected_candidate_id") or "").strip()
    if not cand_id:
        raise MotionDeliveryError(
            "PLAYBACK_UNAVAILABLE", "재생 가능한 후보가 없습니다.", status=409
        )
    candidate = await _load_candidate(cand_id)
    if (
        str(candidate.get("motion_version_id") or "") != version_id
        or str(candidate.get("user_id") or "") != uid
    ):
        raise MotionDeliveryError(
            "CANDIDATE_MISMATCH", "후보가 이 모션 버전/펫에 속하지 않습니다.", status=409
        )

    decision = str(candidate.get("decision") or "").upper()
    if decision not in ("PASS", "REVIEW"):
        raise MotionDeliveryError(
            "PLAYBACK_UNAVAILABLE", "FAIL/ERROR 후보는 재생하지 않습니다.", status=409
        )

    derived = str(candidate.get("derived_video_path") or "").strip()
    fmt = candidate_delivery_format(candidate)
    if not derived or fmt != DELIVERY_PACKED_ALPHA:
        raise MotionDeliveryError(
            "PLAYBACK_NOT_PACKAGED",
            "packed-alpha 파생물이 없습니다 — 먼저 포장(Phase 7F)해야 합니다.",
            status=409,
        )
    bucket = str(candidate.get("raw_bucket") or asset_url_refresh.default_bucket()).strip()
    asset = asset_url_refresh.StorageObject(bucket=bucket, path=derived)
    if not _use_db() and derived in _MOCK_DELIVERY_OBJECTS and sign_fn is None:
        signed: Optional[str] = f"mock://{bucket}/{derived}"
    else:
        signed = (sign_fn or asset_url_refresh.sign_object)(asset)
    if not signed:
        raise MotionDeliveryError(
            "PLAYBACK_ASSET_UNAVAILABLE",
            "포장된 스토리지 객체를 확인할 수 없습니다.",
            status=409,
        )

    return BreathingPlayback(
        motion_version_id=version_id,
        candidate_id=cand_id,
        qa_decision=decision,
        url=str(signed),
        delivery_format=DELIVERY_PACKED_ALPHA,
        derived_bucket=bucket,
        derived_video_path=derived,
        published=False,
    )
