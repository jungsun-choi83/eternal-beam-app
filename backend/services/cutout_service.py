"""Rembg + Alpha Matting: 배경 제거, 머리카락 한 올까지 정교한 알파"""

import io
import logging
import os
from typing import Optional, Tuple

from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS  # Pillow < 9.1

# 배경 프리셋 (R, G, B, A)
BG_BLACK = (0, 0, 0, 255)
BG_STUDIO = (118, 118, 118, 255)  # 중간 회색 (스튜디오)
BG_PLAIN = (255, 255, 255, 255)  # 흰색 (플레인)

# rembg: u2net_human_seg 가 사람/머리카락에 유리
try:
    from rembg import remove as rembg_remove
    from rembg.session_factory import new_session
    REMBG_AVAILABLE = True
except Exception:
    REMBG_AVAILABLE = False

# 알파 경계 정제 (선택)
try:
    import cv2
    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False


# rembg 세션 캐시 — 요청마다 모델을 새로 로드하지 않도록 재사용.
# (콜드 스타트 후 첫 요청뿐 아니라 매 요청 속도가 빨라짐)
_SESSION_CACHE: dict = {}


def _get_session(model_name: str, providers: list[str]):
    """모델 세션을 캐시에서 가져오거나 1회만 생성한다."""
    key = (model_name, tuple(providers))
    sess = _SESSION_CACHE.get(key)
    if sess is None:
        sess = new_session(model_name, providers=providers)
        _SESSION_CACHE[key] = sess
    return sess


def preload_default_model(model_name: str = "isnet-general-use") -> bool:
    """서버 시작 시 기본 모델을 미리 로드해 첫 요청 지연을 없앤다."""
    if not REMBG_AVAILABLE:
        return False
    try:
        _get_session(model_name, ["CPUExecutionProvider"])
        return True
    except Exception:
        return False


def _alpha_matting_refine(rgba: np.ndarray, feather: bool = True) -> np.ndarray:
    """
    알파 채널 경계 정제.
    feather=True: 기존 (블러 포함). feather=False: 구멍 메우기만, halo 최소화.
    """
    if not CV2_AVAILABLE or rgba.shape[2] != 4:
        return rgba
    alpha = rgba[:, :, 3]
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel)
    if feather:
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0.5)
    rgba[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return rgba


def _apply_bg_color(rgba: np.ndarray, bgcolor: Tuple[int, int, int, int]) -> np.ndarray:
    """RGBA에 배경색 합성. alpha=0 영역을 bgcolor로."""
    r, g, b, a = bgcolor
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    out = rgba.copy()
    out[:, :, 0] = (rgba[:, :, 0] * alpha + r * (1 - alpha)).astype(np.uint8)
    out[:, :, 1] = (rgba[:, :, 1] * alpha + g * (1 - alpha)).astype(np.uint8)
    out[:, :, 2] = (rgba[:, :, 2] * alpha + b * (1 - alpha)).astype(np.uint8)
    out[:, :, 3] = 255
    return out


def _downscale_for_rembg(input_img: Image.Image, max_side: int) -> Image.Image:
    """고해상도 사진에서 alpha matting이 GiB 단위 배열을 쓰며 OOM 나는 것을 방지."""
    w, h = input_img.size
    if max(w, h) <= max_side:
        return input_img
    scale = max_side / float(max(w, h))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return input_img.resize((new_w, new_h), _LANCZOS)


def _rembg_call(
    input_img: Image.Image,
    session,
    *,
    use_alpha_matting: bool,
    foreground_threshold: int,
    background_threshold: int,
    erode_size: int,
    alpha_matting_base_size: int,
):
    """rembg 버전별 인자 이름 차이 흡수."""
    w, h = input_img.size
    am_base = min(alpha_matting_base_size, max(w, h)) if use_alpha_matting else 1000
    try:
        return rembg_remove(
            input_img,
            session=session,
            alpha_matting=use_alpha_matting,
            alpha_matting_foreground_threshold=foreground_threshold,
            alpha_matting_background_threshold=background_threshold,
            alpha_matting_erode_structure_size=erode_size,
            alpha_matting_base_size=am_base,
        )
    except TypeError:
        return rembg_remove(
            input_img,
            session=session,
            alpha_matting=use_alpha_matting,
            alpha_matting_foreground_threshold=foreground_threshold,
            alpha_matting_background_threshold=background_threshold,
            alpha_matting_erode_size=erode_size,
            alpha_matting_base_size=am_base,
        )


def remove_background(
    image_bytes: bytes,
    use_alpha_matting: bool = True,
    model_name: str = "isnet-general-use",
    foreground_threshold: int = 240,
    background_threshold: int = 10,
    erode_size: int = 10,
    alpha_matting_base_size: int = 1000,
    post_refine_feather: bool = True,
    bgcolor: Optional[Tuple[int, int, int, int]] = None,
    meta_out: Optional[dict] = None,
) -> bytes:
    """
    배경 제거 + 알파 매팅.
    - isnet-general-use: 강아지/털/복잡한 실루엣에 최적
    - foreground_threshold: 높을수록 전경만 확실히 (250~255)
    - background_threshold: 낮을수록 배경 완전 제거 (0~5)
    - erode_size: 작을수록 털 디테일 유지, edge feather 최소화 (3~5)
    - post_refine_feather: False면 halo 최소화
    - meta_out: dict를 넘기면 실제로 무슨 일이 있었는지 기록한다
      (alpha_matting_used / downscaled_to / oom_retry). 응답의 `refined` 를
      정직하게 채우기 위한 것 — 요청값이 아니라 실제 수행 여부를 담는다.
    """
    if not REMBG_AVAILABLE:
        raise RuntimeError("rembg가 설치되지 않았습니다. pip install rembg[gpu]")

    # 기본 1280: rembg alpha matting은 CPU에서 픽셀당 큰 배열을 쓰므로 OOM 방지
    max_side = int(os.getenv("CUTOUT_MAX_PIXEL", "1280"))
    # True면 큰 이미지에서도 알파 매팅 시도 (RAM 많은 PC만)
    force_alpha = os.getenv("CUTOUT_FORCE_ALPHA_MATTING", "").lower() in ("1", "true", "yes")
    # 이 픽셀 수 넘으면 알파 매팅 끔 (세그멘테이션만)
    alpha_pixel_budget = int(os.getenv("CUTOUT_ALPHA_MAT_MAX_PIXELS", "1200000"))

    input_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    _decoded_size = input_img.size
    input_img = _downscale_for_rembg(input_img, max_side)
    w, h = input_img.size
    pixels = w * h
    # [IMAGE-TRACE] rembg 경로의 유일한 리사이즈 지점 (CUTOUT_MAX_PIXEL 상한).
    if _decoded_size != (w, h):
        logger.info(
            "[IMAGE-TRACE] rembg downscale: %dx%d -> %dx%d (CUTOUT_MAX_PIXEL=%d)",
            _decoded_size[0],
            _decoded_size[1],
            w,
            h,
            max_side,
        )
    else:
        logger.info("[IMAGE-TRACE] rembg no downscale: %dx%d", w, h)

    use_am = use_alpha_matting
    if use_am and not force_alpha and pixels > alpha_pixel_budget:
        use_am = False

    if meta_out is not None:
        meta_out["alpha_matting_requested"] = bool(use_alpha_matting)
        meta_out["alpha_matting_used"] = bool(use_am)
        meta_out["processing_width"] = int(w)
        meta_out["processing_height"] = int(h)
        meta_out["oom_retry"] = False

    session = _get_session(model_name, ["CUDAExecutionProvider", "CPUExecutionProvider"])
    cpu_session = None

    def _oomish(err: BaseException) -> bool:
        s = str(err).lower()
        return (
            isinstance(err, MemoryError)
            or "allocate" in s
            or "allocation" in s
            or "bad alloc" in s
            or "out of memory" in s
            or "cuda oom" in s
            or "memory" in s
        )

    try:
        out_img = _rembg_call(
            input_img,
            session,
            use_alpha_matting=use_am,
            foreground_threshold=foreground_threshold,
            background_threshold=background_threshold,
            erode_size=erode_size,
            alpha_matting_base_size=alpha_matting_base_size,
        )
    except Exception as first_err:
        if not _oomish(first_err):
            raise
        # CUDA/provider 메모리 이슈일 때 CPU 세션으로 재시도.
        # 이 경로에서는 알파 매팅이 꺼지므로 meta_out 도 실제 값으로 갱신한다.
        cpu_session = _get_session(model_name, ["CPUExecutionProvider"])
        input_img = _downscale_for_rembg(input_img, max(512, max_side // 2))
        use_am = False
        if meta_out is not None:
            meta_out["alpha_matting_used"] = False
            meta_out["oom_retry"] = True
            meta_out["processing_width"] = int(input_img.size[0])
            meta_out["processing_height"] = int(input_img.size[1])
        try:
            out_img = _rembg_call(
                input_img,
                cpu_session,
                use_alpha_matting=False,
                foreground_threshold=foreground_threshold,
                background_threshold=background_threshold,
                erode_size=erode_size,
                alpha_matting_base_size=alpha_matting_base_size,
            )
        except Exception as second_err:
            if not _oomish(second_err):
                raise second_err
            input_img = _downscale_for_rembg(input_img, 512)
            if meta_out is not None:
                meta_out["processing_width"] = int(input_img.size[0])
                meta_out["processing_height"] = int(input_img.size[1])
            out_img = _rembg_call(
                input_img,
                cpu_session,
                use_alpha_matting=False,
                foreground_threshold=foreground_threshold,
                background_threshold=background_threshold,
                erode_size=erode_size,
                alpha_matting_base_size=alpha_matting_base_size,
            )

    if out_img is None:
        raise RuntimeError("rembg 반환값이 비어 있습니다.")

    rgba = np.array(out_img)
    if use_am and rgba.shape[2] == 4:
        rgba = _alpha_matting_refine(rgba, feather=post_refine_feather)
    if bgcolor is not None:
        rgba = _apply_bg_color(rgba, bgcolor)
    out_img = Image.fromarray(rgba)

    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    return buf.getvalue()


def remove_background_high_quality(
    image_bytes: bytes,
    model_name: str = "isnet-general-use",
    bgcolor: Optional[Tuple[int, int, int, int]] = None,
    meta_out: Optional[dict] = None,
) -> bytes:
    """
    고품질 프리셋: 털 디테일 유지, 배경 완전 제거, halo 최소화.
    bgcolor: None=투명, BG_BLACK, BG_STUDIO, BG_PLAIN
    """
    return remove_background(
        image_bytes,
        use_alpha_matting=True,
        model_name=model_name,
        foreground_threshold=255,
        background_threshold=0,
        erode_size=4,
        alpha_matting_base_size=1000,
        post_refine_feather=False,
        bgcolor=bgcolor,
        meta_out=meta_out,
    )
