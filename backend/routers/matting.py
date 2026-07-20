"""
SAM2 + ViTMatte 매팅 API — /api/matting/cutout

기존 /api/cutout(rembg)과 응답 스키마를 동일하게 맞춰 프런트에서 그대로
교체해 쓸 수 있게 했다. 차이는 파이프라인(YOLO bbox → SAM2 박스 프롬프트
(폴백: GrabCut) trimap → ViTMatte)뿐. 자세한 배경은
backend/services/vitmatte_service.py, docs/매팅_및_리깅_AI_조사.md 참고.
"""

import base64
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.cutout_quality import analyze_alpha_fur_edge
from ..services.vitmatte_service import matte_foreground_with_meta

router = APIRouter()


@router.post("/matting/cutout")
async def post_matting_cutout(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    save_to_storage: str = Form("true"),
    model: str | None = Form(None),
    segmenter: str | None = Form(None),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = (content_id or "").strip() or str(uuid.uuid4())
    save = str(save_to_storage).lower() in ("1", "true", "yes")

    try:
        png, vitmatte_meta = matte_foreground_with_meta(raw, model_name=model, segmenter=segmenter)
        quality_meta = {**analyze_alpha_fur_edge(png), **vitmatte_meta, "refined": True}
    except Exception as e:
        return {
            "content_id": cid,
            "cutout_url": None,
            "cutout_png_base64": None,
            "error": str(e),
            "cutout_quality": None,
        }

    cutout_url: str | None = None
    cutout_b64: str | None = None

    if save:
        try:
            path = f"{user_id}/{cid}/cutout_vitmatte.png"
            cutout_url = await supabase_assets.upload_asset_to_storage(path, png, "image/png")
            await supabase_assets.ensure_user_asset_row(user_id, cid, "cutout", cutout_url or "", None)
        except Exception:
            cutout_b64 = base64.b64encode(png).decode("ascii")
    else:
        cutout_b64 = base64.b64encode(png).decode("ascii")

    return {
        "content_id": cid,
        "cutout_url": cutout_url,
        "cutout_png_base64": cutout_b64 if not cutout_url else None,
        "error": None,
        "quality_score": quality_meta.get("quality_score"),
        "cutout_quality": quality_meta,
    }
