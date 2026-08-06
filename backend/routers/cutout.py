"""
레거시 rembg 누끼 API — /api/cutout

기본 파이프라인은 /api/matting/cutout (SAM2 + ViTMatte) 이고, 여기는
VITE_CUTOUT_PIPELINE=rembg 로 되돌릴 때 쓰는 경로다.

Phase 1: 실패는 HTTP 200 + {"error": ...} 대신 제대로 된 4xx/5xx 로 나가고,
`refined` 는 실제로 정제 패스가 돌았을 때만 true 다.
"""

import base64
import logging
import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.cutout_errors import CutoutError
from ..services.cutout_quality import analyze_alpha_fur_edge
from ..services.cutout_service import remove_background, remove_background_high_quality
from ..services.vitmatte_service import DEBUG_ARTIFACTS_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter()


def _adaptive_enabled() -> bool:
    return os.getenv("CUTOUT_ADAPTIVE_REFINE", "true").lower() in ("1", "true", "yes")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _run_cutout(raw: bytes, model_name: str, pet_only: bool) -> tuple[bytes, dict]:
    """pet_only=True 면 YOLO 크롭 후 rembg. 피사체 미검출은 그대로 전파한다.

    (예전에는 여기서 모든 예외를 삼키고 전체 프레임 rembg 로 폴백해서, 개를
    못 찾았다는 사실이 응답 어디에도 남지 않았다.)
    """
    meta: dict = {}
    if pet_only:
        from ..services.dog_image_preprocessing import build_dog_only_nobg_png_bytes

        png = build_dog_only_nobg_png_bytes(
            raw,
            bbox_pad_frac=0.15,
            rembg_model=model_name,
        )
        meta.update(
            {
                "cutout_pass": "pet_only",
                "refined": True,
                "refinement_type": "rembg_alpha_matting",
                "second_pass": False,
                "subject_detected": True,
            }
        )
        return png, meta

    png = remove_background(raw, model_name=model_name, meta_out=meta)
    used_am = bool(meta.get("alpha_matting_used"))
    meta.update(
        {
            "cutout_pass": "full",
            "refined": used_am,
            "refinement_type": "rembg_alpha_matting" if used_am else None,
            "second_pass": False,
        }
    )
    return png, meta


def _fast_cutout(raw: bytes, model_name: str, meta_out: dict | None = None) -> bytes:
    return remove_background(
        raw,
        model_name=model_name,
        use_alpha_matting=False,
        post_refine_feather=False,
        meta_out=meta_out,
    )


def _refined_cutout(raw: bytes, model_name: str, meta_out: dict | None = None) -> bytes:
    """장모·털 경계 — alpha matting ON (고품질 프리셋)."""
    return remove_background_high_quality(raw, model_name=model_name, meta_out=meta_out)


def _adaptive_cutout(raw: bytes, model_name: str) -> tuple[bytes, dict]:
    """
    1) fast (매팅 OFF) → 2) 알파 경계 분석 → 3) 필요 시 matting 재처리
    """
    png_fast = _fast_cutout(raw, model_name)
    metrics = analyze_alpha_fur_edge(png_fast)
    needs = bool(metrics.get("needs_refinement"))

    if needs:
        refine_meta: dict = {}
        try:
            png = _refined_cutout(raw, model_name, meta_out=refine_meta)
            used_am = bool(refine_meta.get("alpha_matting_used"))
            metrics = {
                **metrics,
                # 2차 패스는 돌았지만 메모리 예산 때문에 알파 매팅이 꺼졌을 수 있다.
                # 그때는 "정제했다"고 말하지 않는다.
                "refined": used_am,
                "refinement_type": "rembg_alpha_matting" if used_am else None,
                "second_pass": True,
                "cutout_pass": "fast_then_matting",
            }
            return png, metrics
        except Exception as refine_err:
            logger.exception("cutout: adaptive refine pass failed")
            metrics = {
                **metrics,
                "refined": False,
                "refinement_type": None,
                "second_pass": False,
                "cutout_pass": "fast_only",
                "refine_error": str(refine_err),
            }
            return png_fast, metrics

    metrics = {
        **metrics,
        "refined": False,
        "refinement_type": None,
        "second_pass": False,
        "cutout_pass": "fast_only",
    }
    return png_fast, metrics


@router.post("/cutout")
async def post_cutout(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    save_to_storage: str = Form("true"),
    model: str | None = Form(None),
    pet_only: str = Form("false"),
    fast: str = Form("false"),
    auto_refine: str = Form("true"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    # [IMAGE-TRACE] 멀티파트로 실제 도착한 바이트 + PIL 디코드 크기.
    logger.info(
        "[IMAGE-TRACE] POST /api/cutout upload filename=%r content_type=%r bytes=%d",
        file.filename,
        file.content_type,
        len(raw),
    )
    try:
        import io as _io

        from PIL import Image as _Image

        _probe = _Image.open(_io.BytesIO(raw))
        logger.info(
            "[IMAGE-TRACE] /api/cutout decoded %dx%d mode=%s format=%s",
            _probe.size[0],
            _probe.size[1],
            _probe.mode,
            _probe.format,
        )
    except Exception:
        logger.exception("[IMAGE-TRACE] /api/cutout could not decode upload")

    cid = (content_id or "").strip() or str(uuid.uuid4())
    model_name = (model or "isnet-general-use").strip() or "isnet-general-use"
    save = _truthy(save_to_storage)
    only_pet = _truthy(pet_only)

    use_fast = _truthy(fast)
    use_auto = _truthy(auto_refine) and _adaptive_enabled() and not only_pet
    quality_meta: dict = {}

    try:
        if use_auto and not use_fast:
            png, quality_meta = _adaptive_cutout(raw, model_name)
        elif use_fast and not only_pet:
            fast_meta: dict = {}
            png = _fast_cutout(raw, model_name, meta_out=fast_meta)
            quality_meta = {
                **analyze_alpha_fur_edge(png),
                **fast_meta,
                "refined": False,
                "refinement_type": None,
                "second_pass": False,
                "cutout_pass": "fast_forced",
            }
        else:
            png, quality_meta = _run_cutout(raw, model_name, only_pet)
    except CutoutError as e:
        logger.warning(
            "cutout rejected (cid=%s, code=%s): %s | diagnostics=%s",
            cid,
            e.code,
            e.message,
            e.diagnostics,
        )
        detail = e.to_detail(include_diagnostics=DEBUG_ARTIFACTS_ENABLED)
        detail["content_id"] = cid
        raise HTTPException(status_code=e.http_status, detail=detail) from e
    except Exception as e:
        logger.exception("cutout failed unexpectedly (cid=%s)", cid)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "CUTOUT_INTERNAL_ERROR",
                "message": "Background removal failed. Please try again.",
                "content_id": cid,
                **({"error": f"{type(e).__name__}: {e}"} if DEBUG_ARTIFACTS_ENABLED else {}),
            },
        ) from e

    quality_meta.setdefault("subject_detected", True)

    cutout_url: str | None = None
    cutout_b64: str | None = None

    if save:
        try:
            path = f"{user_id}/{cid}/cutout.png"
            cutout_url = await supabase_assets.upload_asset_to_storage(path, png, "image/png")
            await supabase_assets.ensure_user_asset_row(
                user_id, cid, "cutout", cutout_url or "", None
            )
        except Exception:
            logger.exception("cutout: storage upload failed (cid=%s)", cid)
            cutout_b64 = base64.b64encode(png).decode("ascii")
    else:
        cutout_b64 = base64.b64encode(png).decode("ascii")

    return {
        "content_id": cid,
        "cutout_url": cutout_url,
        "cutout_png_base64": cutout_b64 if not cutout_url else None,
        "error": None,
        "quality_score": quality_meta.get("quality_score"),
        "subject_detected": quality_meta.get("subject_detected", True),
        "cutout_quality": quality_meta,
    }
