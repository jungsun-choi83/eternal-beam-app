#!/usr/bin/env python3
"""
소비자 사진 → Luma AI (Image-to-Video) → 폴링으로 완료 대기 → RemBG 누끼 → 스토리지 업로드

흐름 (백엔드 /generate-pet-video 와 동일한 단계):
  1) 입력 이미지를 Supabase/S3/Firebase 중 설정된 스토리지에 올려 공개 URL 확보 (Luma 필수)
  2) Luma generations 생성 → generations/{id} 폴링 → state==completed 일 때 video URL 확보
  3) 비디오 다운로드 후 rembg(프레임 처리)로 RGBA MP4 생성
  4) 결과 MP4를 스토리지에 업로드

사용법 (저장소 루트에서):
  pip install -r backend/requirements.txt
  pip install requests boto3  # S3 시 / requests는 Luma용
  set PYTHONPATH=backend   # Windows: set PYTHONPATH=backend
  python -m scripts.luma_rembg_pipeline --image path/to/photo.jpg

또는:
  cd backend && set PYTHONPATH=. && python -m scripts.luma_rembg_pipeline --image ../photo.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile

# backend 패키지를 import 경로에 포함 (저장소 루트에서 실행 시)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv

load_dotenv()
for _p in (
    os.path.join(_ROOT, "env.local"),
    os.path.join(_ROOT, ".env.local"),
    os.path.join(_ROOT, ".env.local", "env.local"),
):
    if os.path.isfile(_p):
        load_dotenv(_p)


async def _run(
    image_path: str,
    user_id: str,
    content_id: str | None,
    dog_breed: str,
    prompt_override: str,
) -> dict:
    from services.luma_service import (
        build_luma_prompt,
        create_generation,
        poll_until_complete,
        download_video,
        is_black_tan_dog,
    )
    from services.video_cutout_service import process_video_to_rgba
    from services.object_storage import upload_bytes, new_content_id, upload_input_image_for_luma
    from services.supabase_assets import ensure_user_asset_row
    from services.modal_cutout_client import process_video_via_modal, is_modal_available

    with open(image_path, "rb") as f:
        raw = f.read()

    cid = content_id or new_content_id()
    tmp_dir = tempfile.mkdtemp(prefix=f"luma_cli_{cid}_")

    # 1) 공개 URL (Luma는 image URL 필수)
    image_url = await upload_input_image_for_luma(raw, user_id, cid, ext=os.path.splitext(image_path)[1] or ".png")

    # 2) Luma: 생성 요청 → 폴링으로 완료까지 대기 → 즉시 다운로드
    final_prompt = prompt_override.strip() if prompt_override else build_luma_prompt(raw, dog_breed)
    print(f"[Luma] 프롬프트: {final_prompt[:120]}...")
    gen_id = await create_generation(image_url, prompt=final_prompt)
    print(f"[Luma] generation_id={gen_id} → 폴링 시작 (완료 시까지 대기)")
    video_url = await poll_until_complete(gen_id)
    print(f"[Luma] 완료, video URL 확보 → 다운로드")
    luma_local = await download_video(video_url)

    replace_bg = "white" if is_black_tan_dog(raw) else "black"
    mp4_data: bytes | None = None
    rgba_path: str | None = None

    # 3) RemBG / Modal GPU
    if is_modal_available():
        try:
            with open(luma_local, "rb") as f:
                video_bytes = f.read()
            mp4_data = await process_video_via_modal(
                video_bytes,
                replace_bg=replace_bg,
                max_frames=120,
                use_alpha_matting=False,
            )
            print("[누끼] Modal GPU 경로로 RGBA MP4 생성")
        except Exception as e:
            print(f"[누끼] Modal 실패, 로컬 rembg로 폴백: {e}")
            mp4_data = None

    if mp4_data is None:
        result = process_video_to_rgba(
            luma_local,
            tmp_dir,
            output_format="rgba_video",
            model_name="isnet-general-use",
            use_alpha_matting=False,
            max_frames=120,
            output_resolution=(1280, 720),
            replace_bg_before_rembg=replace_bg,
        )
        rgba_path = result[0] if result else None
        if rgba_path and os.path.isfile(rgba_path):
            with open(rgba_path, "rb") as f:
                mp4_data = f.read()
            print(f"[누끼] 로컬 rembg RGBA MP4: {rgba_path}")
        else:
            raise RuntimeError("RGBA MP4 생성 실패")

    # 4) 업로드
    object_path = f"{user_id}/{cid}_pet_rgba.mp4"
    pet_url = await upload_bytes(object_path, mp4_data, "video/mp4")
    try:
        await ensure_user_asset_row(
            user_id=user_id,
            content_id=cid,
            asset_type="pet_video_rgba",
            url=pet_url,
        )
    except Exception:
        pass

    # 정리
    try:
        if os.path.isfile(luma_local):
            os.unlink(luma_local)
        if rgba_path and os.path.isfile(rgba_path):
            os.unlink(rgba_path)
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    return {
        "content_id": cid,
        "pet_video_url": pet_url,
        "prompt_used": final_prompt,
        "replace_bg": replace_bg,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Luma → 폴링 → RemBG → 스토리지")
    ap.add_argument("--image", required=True, help="입력 사진 경로")
    ap.add_argument("--user-id", default="anonymous", help="사용자 ID (객체 경로)")
    ap.add_argument("--content-id", default="", help="비우면 자동 생성")
    ap.add_argument("--dog-breed", default="dog", help="프롬프트용 견종")
    ap.add_argument("--prompt", default="", help="비우면 이미지 분석 프롬프트 사용")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        print("파일 없음:", args.image)
        sys.exit(1)

    if not os.getenv("LUMA_API_KEY"):
        print("LUMA_API_KEY가 필요합니다.")
        sys.exit(1)

    out = asyncio.run(
        _run(
            args.image,
            args.user_id,
            args.content_id or None,
            args.dog_breed,
            args.prompt,
        )
    )
    print("--- 완료 ---")
    print("content_id:", out["content_id"])
    print("pet_video_url:", out["pet_video_url"])
    print("replace_bg:", out["replace_bg"])


if __name__ == "__main__":
    main()
