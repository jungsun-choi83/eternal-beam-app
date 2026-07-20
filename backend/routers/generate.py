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
from ..services.seamless_loop_service import make_seamless_loop_mp4

router = APIRouter()


@router.post("/generate-pet-video")
async def post_generate_pet_video(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    skip_preprocessing: str = Form("false"),
    # 액션(20종)은 이제 Live Portrait가 맡을 예정 — Luma는 아이들(미세 모션) 루프 1건만 생성.
    # 예전 방식(아이들+액션 2건)이 필요하면 idle_only=false로 되돌릴 수 있음.
    idle_only: str = Form("true"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    skip = str(skip_preprocessing).lower() in ("1", "true", "yes")
    only_idle = str(idle_only).lower() in ("1", "true", "yes")

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
    poll_max_wait = float(os.getenv("LUMA_POLL_MAX_SEC", "1200"))

    try:
        idle_remote = await create_generation_and_get_video_url(
            key_url, idle_prompt, poll_max_wait=poll_max_wait
        )
        action_remote = (
            None
            if only_idle
            else await create_generation_and_get_video_url(
                key_url, action_prompt, poll_max_wait=poll_max_wait
            )
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Luma 생성 실패: {e}") from e

    idle_local = await download_video(idle_remote)
    action_local = await download_video(action_remote) if action_remote else None

    idle_url: str | None = None
    action_url: str | None = None
    loop_meta = None
    try:
        with open(idle_local, "rb") as f:
            idle_bytes = f.read()
        idle_bytes, loop_meta = make_seamless_loop_mp4(idle_bytes)
        idle_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/idle_loop.mp4", idle_bytes, "video/mp4"
        )
        if action_local:
            with open(action_local, "rb") as f:
                action_bytes = f.read()
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
        "idle_loop_meta": loop_meta,
        "prompts": {
            "idle": idle_prompt[:500],
            **({} if only_idle else {"action": action_prompt[:500]}),
        },
    }
