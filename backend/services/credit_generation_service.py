"""
크레딧 차감형 영상 생성 오케스트레이션.

① 잔액 확인 → ② 차감 → ③ Luma 4건 제출 → (실패 시 환불)
웹훅은 `handle_luma_webhook_for_credit` 에서 처리.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..models.hybrid_business import GenerateWithCreditResponse
from ..scenarios.pet_scenarios import place_public_id, resolve_place_id
from .credit_luma_batch import credit_cost, submit_place_motion_set
from . import generated_motions_service as motions_svc
from .wallet_service import InsufficientCreditsError, deduct_credits, refund_credits


async def generate_with_credit(
  *,
  user_id: str,
  pet_image_url: str,
  selected_place_id: str,
  pet_id: Optional[str] = None,
  webhook_base_url: str,
) -> GenerateWithCreditResponse:
  uid = user_id.strip()
  image_url = pet_image_url.strip()
  if not uid or not image_url:
    raise ValueError("user_id and pet_image_url are required")

  place_key = resolve_place_id(selected_place_id)
  pid = motions_svc.default_pet_id(uid, pet_id)
  cost = credit_cost()

  # ①② 트랜잭션에 가까운 차감 (지갑 Lock)
  wallet = await deduct_credits(uid, cost)

  session_id = await motions_svc.create_credit_session(
    uid, pid, place_key, image_url, cost
  )

  try:
    submitted, errors = await submit_place_motion_set(
      session_id=session_id,
      user_id=uid,
      pet_id=pid,
      place_key=place_key,
      pet_image_url=image_url,
      webhook_base_url=webhook_base_url,
    )
  except Exception:
    await refund_credits(uid, cost)
    raise

  # 4건 모두 제출 실패 → 크레딧 환불
  if submitted == 0:
    wallet = await refund_credits(uid, cost)
    return GenerateWithCreditResponse(
      session_id=session_id,
      user_id=uid,
      pet_id=pid,
      place_id=place_public_id(place_key),
      credits_charged=0,
      credits_remaining=wallet.current_credits,
      submitted=0,
      submit_errors=errors,
      status="failed",
      webhook_path="/api/v1/pet/luma-webhook",
    )

  status = "processing" if submitted == 4 else "partial"
  return GenerateWithCreditResponse(
    session_id=session_id,
    user_id=uid,
    pet_id=pid,
    place_id=place_public_id(place_key),
    credits_charged=cost,
    credits_remaining=wallet.current_credits,
    submitted=submitted,
    submit_errors=errors,
    status=status,
    webhook_path="/api/v1/pet/luma-webhook",
  )


async def handle_luma_webhook_for_credit(
  luma_generation_id: str,
  state: str,
  video_url: Optional[str] = None,
  error: Optional[str] = None,
) -> Optional[dict[str, Any]]:
  """크레딧 세션 Luma 콜백. 배치 파이프라인이 아니면 여기서 처리."""
  job = await motions_svc.resolve_luma_job(luma_generation_id)
  if not job:
    return None

  state_l = (state or "").lower()

  if state_l in ("failed", "error"):
    await motions_svc.mark_job_failed(luma_generation_id, error or "failed")
    return {"session_id": job.session_id, "action_id": job.action_id, "status": "failed"}

  if state_l == "completed" and video_url:
    if str(luma_generation_id).startswith("mock_"):
      mock = (os.getenv("MOCK_LUMA_VIDEO_URL") or "").strip()
      if mock:
        video_url = mock
    motion = await motions_svc.save_completed_motion(job, video_url)
    return {
      "session_id": job.session_id,
      "action_id": job.action_id,
      "status": "completed",
      "video_url": motion.video_url,
      "place_id": motion.place_id,
    }

  return {"session_id": job.session_id, "action_id": job.action_id, "status": state_l or "dreaming"}
