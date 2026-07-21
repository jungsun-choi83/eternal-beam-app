import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.dog_image_preprocessing import build_dog_only_nobg_png_bytes
from ..services.luma_idle_pipeline import generate_idle_variant
from ..services.luma_idle_templates import IDLE_TEMPLATE_ORDER, is_known_template
from ..services.luma_keyframe import flatten_rgba_to_jpeg_bytes
from ..services.luma_service import (
    build_idle_action_prompts,
    create_generation_and_get_video_url,
    download_video,
)
from ..services.seamless_loop_service import make_seamless_loop_mp4

router = APIRouter()


async def _cutout_to_dog_bytes(raw: bytes, *, skip: bool) -> bytes:
    """skip=True면 이미 누끼딴 파일 그대로. False면 SAM2+ViTMatte로 누끼."""
    if skip:
        return raw
    from ..services.vitmatte_service import matte_foreground_with_meta

    png_bytes, _meta = matte_foreground_with_meta(raw)
    return png_bytes


def _pet_video_seamless_loop_enabled() -> bool:
    """
    /generate-pet-video 의 아이들 영상 후처리(ffmpeg seamless loop) on/off.
    Render 512MB 컨테이너에서 ffmpeg 재인코딩(xfade+concat)이 uvicorn 프로세스와
    메모리를 다투다 컨테이너 전체를 OOM으로 죽이는 문제가 확인되어 기본값 off.
    더 큰 플랜으로 옮기거나 ffmpeg 메모리 사용을 낮추면 PET_VIDEO_SEAMLESS_LOOP=1로
    다시 켤 수 있음. (/api/generate-idle-variant 는 이 플래그와 무관하게 그대로 동작.)
    """
    return os.getenv("PET_VIDEO_SEAMLESS_LOOP", "false").lower() in ("1", "true", "yes")


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
        if _pet_video_seamless_loop_enabled():
            idle_bytes, loop_meta = make_seamless_loop_mp4(idle_bytes)
        else:
            # ffmpeg 재인코딩 OOM 회피 — 루프 없는 원본 Luma 영상을 그대로 사용.
            loop_meta = {"skipped": True, "reason": "pet_video_seamless_loop_disabled"}
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


@router.post("/generate-idle-variant")
async def post_generate_idle_variant(
    file: UploadFile = File(...),
    template_key: str = Form(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    # 프론트가 SAM2 누끼(/api/matting/cutout)를 먼저 호출해 이미 누끼딴 파일을
    # 보내는 경우 true(기본). false면 이 엔드포인트가 SAM2+ViTMatte로 직접 누끼.
    skip_preprocessing: str = Form("true"),
    max_retries: int = Form(2),
):
    """
    아이들(Idle) 5종 세트 중 template_key 1개를 생성.
    파이프라인: (SAM2 누끼, 필요 시) → Luma 생성 → mp4/블랙배경 검증(+재시도)
    → seamless loop → Supabase 업로드.

    프론트(VideoGenerationService.ts)는 IDLE_TEMPLATE_ORDER 순서대로 이 엔드포인트를
    5번 순차 호출한다(SAM2 누끼는 최초 1회만 하고 나머지 4번은 skip_preprocessing=true).
    """
    if not is_known_template(template_key):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown template_key: {template_key!r}. Valid: {list(IDLE_TEMPLATE_ORDER)}",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    skip = str(skip_preprocessing).lower() in ("1", "true", "yes")

    try:
        dog_bytes = await _cutout_to_dog_bytes(raw, skip=skip)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"누끼 실패: {e}") from e

    try:
        dog_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/dog_only_nobg.png", dog_bytes, "image/png"
        )
        key_jpeg = flatten_rgba_to_jpeg_bytes(dog_bytes)
        key_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/luma_keyframe.jpg", key_jpeg, "image/jpeg"
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Luma용 이미지 URL이 필요합니다. Supabase(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)와 Storage 버킷을 설정하세요: {e}",
        ) from e

    try:
        variant = await generate_idle_variant(
            key_url, template_key, max_retries=max_retries
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Luma 아이들 생성 실패: {e}") from e

    looped_bytes, loop_meta = make_seamless_loop_mp4(variant.video_bytes)
    video_url = await supabase_assets.upload_asset_to_storage(
        f"{user_id}/{cid}/idle_{template_key.lower()}.mp4", looped_bytes, "video/mp4"
    )

    return {
        "success": True,
        "content_id": cid,
        "dog_only_nobg_url": dog_url,
        "template_key": template_key,
        "video_url": video_url,
        "is_mp4": variant.is_mp4,
        "is_black_background": variant.is_black_background,
        "background_luminance": variant.background_luminance,
        "retries_used": variant.retries_used,
        "loop_meta": loop_meta,
        "prompt": variant.prompt[:500],
    }
