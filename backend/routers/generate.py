import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.dog_image_preprocessing import build_dog_only_nobg_png_bytes
from ..services.luma_keyframe import flatten_rgba_to_jpeg_bytes
from ..services.luma_service import (
    build_idle_action_prompts,
    create_generation_and_get_video_url,
    download_video,
)

router = APIRouter()


@router.post("/generate-pet-video")
async def post_generate_pet_video(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    skip_preprocessing: str = Form("false"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    skip = str(skip_preprocessing).lower() in ("1", "true", "yes")

    try:
        if skip:
            dog_bytes = raw
        else:
            dog_bytes = build_dog_only_nobg_png_bytes(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        dog_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/dog_only_nobg.png", dog_bytes, "image/png"
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Luma용 이미지 URL이 필요합니다. Supabase(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)와 Storage 버킷을 설정하세요: {e}",
        ) from e

    try:
        key_jpeg = flatten_rgba_to_jpeg_bytes(dog_bytes)
        key_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/luma_keyframe.jpg", key_jpeg, "image/jpeg"
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    lum_src = raw if not skip else dog_bytes
    idle_prompt, action_prompt = build_idle_action_prompts(lum_src)

    try:
        idle_remote = await create_generation_and_get_video_url(
            key_url, idle_prompt, poll_max_wait=float(os.getenv("LUMA_POLL_MAX_SEC", "1200"))
        )
        action_remote = await create_generation_and_get_video_url(
            key_url, action_prompt, poll_max_wait=float(os.getenv("LUMA_POLL_MAX_SEC", "1200"))
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Luma 생성 실패: {e}") from e

    idle_local = await download_video(idle_remote)
    action_local = await download_video(action_remote)

    try:
        with open(idle_local, "rb") as f:
            idle_bytes = f.read()
        with open(action_local, "rb") as f:
            action_bytes = f.read()
        idle_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/idle.mp4", idle_bytes, "video/mp4"
        )
        action_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/action.mp4", action_bytes, "video/mp4"
        )
    finally:
        for p in (idle_local, action_local):
            try:
                if p and os.path.isfile(p):
                    os.unlink(p)
            except Exception:
                pass

    return {
        "success": True,
        "content_id": cid,
        "dog_only_nobg_url": dog_url,
        "idle_video_url": idle_url,
        "action_video_url": action_url,
        "prompts": {
            "idle": idle_prompt[:500],
            "action": action_prompt[:500],
        },
    }
