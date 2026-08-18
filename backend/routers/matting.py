"""
SAM2 + ViTMatte 매팅 API — /api/matting/cutout

기존 /api/cutout(rembg)과 응답 스키마를 동일하게 맞춰 프런트에서 그대로
교체해 쓸 수 있게 했다. 차이는 파이프라인(YOLO bbox → SAM2 박스 프롬프트
(폴백: GrabCut) trimap → ViTMatte)뿐. 자세한 배경은
backend/services/vitmatte_service.py, docs/매팅_및_리깅_AI_조사.md 참고.

Phase 1: 실패는 더 이상 HTTP 200 + {"error": ...} 로 나가지 않는다.
피사체 미검출/마스크 품질 미달은 HTTP 422 + {"detail": {"code", "message"}} 다.
"""

import base64
import logging
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.cutout_errors import CutoutError
from ..services.cutout_quality import analyze_alpha_fur_edge
from ..services.debug_artifacts import store_debug_artifacts
from ..services.vitmatte_service import (
    DEBUG_ARTIFACTS_ENABLED,
    matte_foreground_with_meta,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes")


@router.post("/matting/cutout")
async def post_matting_cutout(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    save_to_storage: str = Form("true"),
    model: str | None = Form(None),
    segmenter: str | None = Form(None),
    debug: str = Form("false"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    # [IMAGE-TRACE] 멀티파트로 실제 도착한 바이트. 여기 크기가 곧 input_width/height.
    logger.info(
        "[IMAGE-TRACE] POST /api/matting/cutout upload filename=%r content_type=%r bytes=%d",
        file.filename,
        file.content_type,
        len(raw),
    )

    cid = (content_id or "").strip() or str(uuid.uuid4())
    save = _truthy(save_to_storage)
    want_debug = _truthy(debug) and DEBUG_ARTIFACTS_ENABLED
    artifacts: dict[str, bytes] | None = {} if want_debug else None

    try:
        png, vitmatte_meta = matte_foreground_with_meta(
            raw,
            model_name=model,
            segmenter=segmenter,
            debug_artifacts=artifacts,
        )
    except CutoutError as e:
        # 진단은 서버 로그로. 클라이언트에는 code/message 만 (트레이스백 노출 금지).
        logger.warning(
            "matting/cutout rejected (cid=%s, code=%s): %s | diagnostics=%s",
            cid,
            e.code,
            e.message,
            e.diagnostics,
        )
        detail = e.to_detail(include_diagnostics=DEBUG_ARTIFACTS_ENABLED)
        detail["content_id"] = cid
        if artifacts:
            detail["debug_artifacts"] = store_debug_artifacts(cid, artifacts)
        raise HTTPException(status_code=e.http_status, detail=detail) from e
    except Exception as e:
        logger.exception("matting/cutout failed unexpectedly (cid=%s)", cid)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "CUTOUT_INTERNAL_ERROR",
                "message": "Background removal failed. Please try again.",
                "content_id": cid,
                **({"error": f"{type(e).__name__}: {e}"} if DEBUG_ARTIFACTS_ENABLED else {}),
            },
        ) from e

    # ViTMatte 는 트라이맵 unknown 영역을 단일 패스로 매팅한다 — rembg 처럼
    # "1차 후 재처리"하는 2패스가 아니므로 second_pass=False 로 정직하게 적는다.
    quality_meta = {
        **analyze_alpha_fur_edge(png),
        **vitmatte_meta,
        "refined": True,
        "refinement_type": "vitmatte",
        "second_pass": False,
    }

    cutout_url: str | None = None
    cutout_b64: str | None = None

    if save:
        try:
            path = f"{user_id}/{cid}/cutout_vitmatte.png"
            cutout_url = await supabase_assets.upload_asset_to_storage(path, png, "image/png")
            await supabase_assets.ensure_user_asset_row(user_id, cid, "cutout", cutout_url or "", None)
        except Exception:
            logger.exception("matting/cutout: storage upload failed (cid=%s)", cid)
            cutout_b64 = base64.b64encode(png).decode("ascii")
    else:
        cutout_b64 = base64.b64encode(png).decode("ascii")

    response = {
        "content_id": cid,
        "cutout_url": cutout_url,
        "cutout_png_base64": cutout_b64 if not cutout_url else None,
        "error": None,
        "quality_score": quality_meta.get("quality_score"),
        "subject_detected": quality_meta.get("subject_detected", True),
        "cutout_quality": quality_meta,
    }
    if artifacts:
        response["debug_artifacts"] = store_debug_artifacts(cid, artifacts)
    return response
