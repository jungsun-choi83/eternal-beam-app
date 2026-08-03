import base64

import os

import uuid



from fastapi import APIRouter, File, Form, HTTPException, UploadFile



from ..services.cutout_quality import analyze_alpha_fur_edge

from ..services.cutout_service import remove_background, remove_background_high_quality

from ..services import supabase_assets





def _adaptive_enabled() -> bool:

    return os.getenv("CUTOUT_ADAPTIVE_REFINE", "true").lower() in ("1", "true", "yes")





def _run_cutout(raw: bytes, model_name: str, pet_only: bool) -> bytes:

    if pet_only:

        try:

            from ..services.dog_image_preprocessing import build_dog_only_nobg_png_bytes



            return build_dog_only_nobg_png_bytes(

                raw,

                bbox_pad_frac=0.15,

                rembg_model=model_name,

            )

        except Exception:

            pass

    return remove_background(raw, model_name=model_name)





def _fast_cutout(raw: bytes, model_name: str) -> bytes:

    return remove_background(

        raw,

        model_name=model_name,

        use_alpha_matting=False,

        post_refine_feather=False,

    )





def _refined_cutout(raw: bytes, model_name: str) -> bytes:

    """장모·털 경계 — alpha matting ON (고품질 프리셋)."""

    return remove_background_high_quality(raw, model_name=model_name)





def _adaptive_cutout(raw: bytes, model_name: str) -> tuple[bytes, dict]:

    """

    1) fast (매팅 OFF) → 2) 알파 경계 분석 → 3) 필요 시 matting 재처리

    """

    png_fast = _fast_cutout(raw, model_name)

    metrics = analyze_alpha_fur_edge(png_fast)

    refined = bool(metrics.get("needs_refinement"))



    if refined:

        try:

            png = _refined_cutout(raw, model_name)

            metrics = {

                **metrics,

                "refined": True,

                "cutout_pass": "fast_then_matting",

            }

            return png, metrics

        except Exception as refine_err:

            metrics = {

                **metrics,

                "refined": False,

                "cutout_pass": "fast_only",

                "refine_error": str(refine_err),

            }

            return png_fast, metrics



    metrics = {**metrics, "refined": False, "cutout_pass": "fast_only"}

    return png_fast, metrics





router = APIRouter()





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



    cid = (content_id or "").strip() or str(uuid.uuid4())

    model_name = (model or "isnet-general-use").strip() or "isnet-general-use"

    save = str(save_to_storage).lower() in ("1", "true", "yes")

    only_pet = str(pet_only).lower() in ("1", "true", "yes")



    use_fast = str(fast).lower() in ("1", "true", "yes")

    use_auto = (

        str(auto_refine).lower() in ("1", "true", "yes")

        and _adaptive_enabled()

        and not only_pet

    )

    quality_meta: dict | None = None



    try:

        if use_auto and not use_fast:

            png, quality_meta = _adaptive_cutout(raw, model_name)

        elif use_fast and not only_pet:

            png = _fast_cutout(raw, model_name)

            quality_meta = {

                **analyze_alpha_fur_edge(png),

                "refined": False,

                "cutout_pass": "fast_forced",

            }

        else:

            png = _run_cutout(raw, model_name, only_pet)

            quality_meta = {

                "cutout_pass": "full" if not only_pet else "pet_only",

                "refined": True,

            }

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

            path = f"{user_id}/{cid}/cutout.png"

            cutout_url = await supabase_assets.upload_asset_to_storage(

                path, png, "image/png"

            )

            await supabase_assets.ensure_user_asset_row(

                user_id, cid, "cutout", cutout_url or "", None

            )

        except Exception:

            cutout_b64 = base64.b64encode(png).decode("ascii")

    else:

        cutout_b64 = base64.b64encode(png).decode("ascii")



    quality_score = None

    if quality_meta:

        quality_score = quality_meta.get("quality_score")



    return {

        "content_id": cid,

        "cutout_url": cutout_url,

        "cutout_png_base64": cutout_b64 if not cutout_url else None,

        "error": None,

        "quality_score": quality_score,

        "cutout_quality": quality_meta,

    }


