import base64
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services.cutout_service import remove_background
from ..services import supabase_assets

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

router = APIRouter()


@router.post("/cutout")
async def post_cutout(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    save_to_storage: str = Form("true"),
    model: str | None = Form(None),
    pet_only: str = Form("false"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = (content_id or "").strip() or str(uuid.uuid4())
    model_name = (model or "isnet-general-use").strip() or "isnet-general-use"
    save = str(save_to_storage).lower() in ("1", "true", "yes")
    only_pet = str(pet_only).lower() in ("1", "true", "yes")

    try:
        png = _run_cutout(raw, model_name, only_pet)
    except Exception as e:
        return {
            "content_id": cid,
            "cutout_url": None,
            "cutout_png_base64": None,
            "error": str(e),
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

    return {
        "content_id": cid,
        "cutout_url": cutout_url,
        "cutout_png_base64": cutout_b64 if not cutout_url else None,
        "error": None,
    }
