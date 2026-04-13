import os
import tempfile
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..services import supabase_assets
from ..services.compose_video_service import (
    _get_theme_video_path,
    compose_subject_only_rgba,
    compose_video,
)

router = APIRouter()

PREMIUM_THEMES = frozenset({"galaxy_dream", "neon_city"})

_outputs = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "outputs")
)


@router.post("/compose-video")
async def post_compose_video(
    request: Request,
    cutout_file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    theme_id: str = Form(""),
    payment_status: str = Form("false"),
    max_height: str = Form("720"),
    subject_only: str = Form("false"),
):
    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    paid = str(payment_status).lower() in ("1", "true", "yes")
    subj_only = str(subject_only).lower() in ("1", "true", "yes")
    tid = (theme_id or "").strip()
    mh = int(max_height or "720")

    if tid in PREMIUM_THEMES and not paid:
        purchased = await supabase_assets.get_purchased_themes(user_id)
        if tid not in purchased:
            raise HTTPException(
                status_code=402,
                detail="이 테마는 결제 완료 후 이용할 수 있습니다.",
            )

    raw = await cutout_file.read()
    if not raw:
        raise HTTPException(400, detail="Empty cutout")

    os.makedirs(_outputs, exist_ok=True)
    suffix = uuid.uuid4().hex[:10]
    out_name = f"{cid}_compose_{suffix}.mp4"
    out_path = os.path.join(_outputs, out_name)

    tmp_in = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        tmp_in.write(raw)
        tmp_in.close()

        if subj_only or not tid:
            compose_subject_only_rgba(tmp_in.name, out_path, max_height=mh)
            final_theme = tid or "subject_only"
        else:
            theme_path = _get_theme_video_path(tid)
            if not theme_path:
                raise HTTPException(400, detail=f"테마 영상을 찾을 수 없습니다: {tid}")
            compose_video(theme_path, tmp_in.name, out_path, max_height=mh)
            final_theme = tid
    finally:
        try:
            os.unlink(tmp_in.name)
        except Exception:
            pass

    unique_url: str
    try:
        with open(out_path, "rb") as f:
            data = f.read()
        unique_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/composed.mp4", data, "video/mp4"
        )
    except Exception:
        base = str(request.base_url).rstrip("/")
        unique_url = f"{base}/outputs/{out_name}"

    return {
        "success": True,
        "content_id": cid,
        "unique_url": unique_url,
        "nfc_payload": {
            "version": 1,
            "content_id": cid,
            "unique_url": unique_url,
            "theme_id": final_theme,
            "slot_number": None,
        },
    }
