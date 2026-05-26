"""
/api/v1/pet — 40 시나리오 Luma 배치 파이프라인

POST /generate-all   : 사진 1장 → 40개 Luma 작업 제출
POST /luma-webhook    : Luma 콜백 → Supabase Storage 저장
GET  /batch/{batch_id}: 진행 상황 조회
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..models.pet_generation import BatchStatus, GenerateAllResponse
from ..models.hybrid_business import GenerateWithCreditRequest, GenerateWithCreditResponse
from ..services import supabase_assets
from ..services.credit_generation_service import (
  generate_with_credit,
  handle_luma_webhook_for_credit,
)
from ..services.wallet_service import InsufficientCreditsError
from ..services.luma_batch_service import submit_all_scenarios
from ..services import pet_generation_store
from ..scenarios.pet_scenarios import scenario_count

router = APIRouter(prefix="/v1/pet", tags=["pet-v1"])


def _public_api_base(request: Request) -> str:
  """웹훅 URL 조립용 공개 베이스 (환경변수 우선)."""
  explicit = (os.getenv("PUBLIC_API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
  if explicit:
    return explicit.rstrip("/")
  return str(request.base_url).rstrip("/")


@router.post("/generate-with-credit", response_model=GenerateWithCreditResponse)
async def generate_with_credit_endpoint(
  request: Request,
  body: GenerateWithCreditRequest,
):
  """
  코인 4개 차감 → 선택 장소 1곳에 대해 4가지 행동(IDLE/TOUCH/VOICE/NFC) Luma 생성.

  - 잔액 부족 시 403
  - Luma 제출은 Semaphore(3) 동시 제한
  - 완료 영상은 웹훅 → `generated_motions` 저장
  """
  try:
    api_base = _public_api_base(request)
    webhook_url = f"{api_base}/api/v1/pet/luma-webhook"
    return await generate_with_credit(
      user_id=body.user_id,
      pet_image_url=body.pet_image_url,
      selected_place_id=body.selected_place_id,
      pet_id=body.pet_id,
      webhook_base_url=webhook_url,
    )
  except InsufficientCreditsError as e:
    raise HTTPException(status_code=403, detail=e.message) from e
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/generate-all", response_model=GenerateAllResponse)
async def generate_all(
  request: Request,
  file: UploadFile | None = File(None),
  image_url: str | None = Form(None),
  user_id: str = Form("anonymous"),
  pet_id: str | None = Form(None),
):
  """
  소비자 메인 사진 1장 → 10 장소 × 4 행동 = 40개 Luma I2V 비동기 제출.

  - `file` 또는 `image_url` 중 하나 필수
  - `pet_id` 없으면 새 UUID 발급
  - 완료 영상은 웹훅에서 `{user_id}/{pet_id}/{THEME_KEY}_{ACTION}.mp4` 로 Storage 저장
  """
  uid = (user_id or "anonymous").strip() or "anonymous"
  pid = (pet_id or "").strip() or str(uuid.uuid4())
  batch_id = str(uuid.uuid4())

  public_url = (image_url or "").strip()
  if file and file.filename:
    raw = await file.read()
    if not raw:
      raise HTTPException(400, detail="Empty file")
    ext = ".jpg"
    if file.filename and "." in file.filename:
      ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    try:
      public_url = await supabase_assets.upload_asset_to_storage(
        f"{uid}/{pid}/main_subject{ext}",
        raw,
        file.content_type or "image/jpeg",
      )
    except Exception as e:
      raise HTTPException(
        503,
        detail=f"이미지를 Storage에 올릴 수 없습니다 (Luma는 공개 URL이 필요합니다): {e}",
      ) from e

  if not public_url:
    raise HTTPException(400, detail="file 또는 image_url 이 필요합니다.")

  await pet_generation_store.create_batch(batch_id, uid, pid, public_url)

  api_base = _public_api_base(request)
  webhook_url = f"{api_base}/api/v1/pet/luma-webhook"

  submitted, errors = await submit_all_scenarios(
    batch_id=batch_id,
    image_url=public_url,
    webhook_base_url=webhook_url,
  )

  batch = await pet_generation_store.get_batch(batch_id)
  status = batch.status.value if batch else BatchStatus.processing.value

  return GenerateAllResponse(
    batch_id=batch_id,
    pet_id=pid,
    user_id=uid,
    image_url=public_url,
    total_scenarios=scenario_count(),
    submitted=submitted,
    submit_errors=errors,
    status=status,
    webhook_path="/api/v1/pet/luma-webhook",
  )


@router.post("/luma-webhook")
async def luma_webhook(request: Request):
  """
  Luma Dream Machine 콜백.

  - Body: Generation JSON (`id`, `state`, `assets.video`, …)
  - `state == completed` 이고 video URL 있으면 Supabase Storage 업로드
  """
  try:
    body = await request.json()
  except Exception:
    raise HTTPException(400, detail="Invalid JSON body") from None

  gen_id = body.get("id")
  state = (body.get("state") or "").lower()
  assets = body.get("assets") or {}
  video_url = assets.get("video") if isinstance(assets, dict) else None
  failure = body.get("failure_reason") or body.get("failureReason")

  if not gen_id:
    raise HTTPException(400, detail="Missing generation id")

  # MOCK: 로컬 테스트 시 즉시 완료 처리 (실제 MP4 없으면 스킵)
  if str(gen_id).startswith("mock_"):
    mock_video = (os.getenv("MOCK_LUMA_VIDEO_URL") or "").strip()
    if mock_video and state != "completed":
      state = "completed"
      video_url = mock_video

  if state == "completed" and not video_url:
    return {"ok": True, "note": "completed but no video url yet"}

  # 크레딧 4건 세트 (하이브리드 모델) 우선 처리
  credit_summary = await handle_luma_webhook_for_credit(
    gen_id,
    state,
    video_url=video_url,
    error=str(failure or "") if state in ("failed", "error") else None,
  )
  if credit_summary is not None:
    return {"ok": True, "pipeline": "credit", "summary": credit_summary}

  if state in ("failed", "error"):
    summary = await pet_generation_store.update_scenario_from_luma(
      gen_id, state, error=str(failure or "failed")
    )
    return {"ok": True, "pipeline": "batch", "summary": summary}

  if state == "completed" and video_url:
    summary = await pet_generation_store.update_scenario_from_luma(
      gen_id, state, video_url=video_url
    )
    if not summary:
      raise HTTPException(404, detail="Unknown generation_id (not in batch registry)")
    return {"ok": True, "pipeline": "batch", "summary": summary}

  # dreaming / 기타 중간 상태
  summary = await pet_generation_store.update_scenario_from_luma(gen_id, state or "dreaming")
  return {"ok": True, "pipeline": "batch", "summary": summary}


@router.get("/wallet/{user_id}")
async def get_wallet_balance(user_id: str):
  """디버그·앱용 잔액 조회."""
  from ..services.wallet_service import get_wallet

  w = await get_wallet(user_id.strip(), create_if_missing=True)
  return {"user_id": user_id, "current_credits": w.current_credits if w else 0}


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
  """배치 진행률 (완료/실패/전체)."""
  batch = await pet_generation_store.get_batch(batch_id)
  if not batch:
    raise HTTPException(404, detail="batch not found")
  return {
    "batch_id": batch.batch_id,
    "user_id": batch.user_id,
    "pet_id": batch.pet_id,
    "status": batch.status.value,
    "total": batch.total,
    "completed": batch.completed_count,
    "failed": batch.failed_count,
    "image_url": batch.image_url,
    "scenarios": {
      k: v.model_dump() for k, v in batch.scenarios.items()
    },
  }
