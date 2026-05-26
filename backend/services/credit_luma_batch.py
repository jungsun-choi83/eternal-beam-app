"""
크레딧 1회(4코인) = 장소 1곳 × 행동 4개 Luma 제출.

동시 요청은 Semaphore(3) 로 Luma 레이트 리밋 완화.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from ..scenarios.pet_scenarios import ACTION_ORDER, CREDIT_COST_PER_PLACE_SET
from .prompt_factory import build_scenario_prompt
from . import generated_motions_service as motions_svc
from .luma_service import create_generation


async def _submit_one(
  *,
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  pet_image_url: str,
  action_id: str,
  callback_url: str,
  semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
  async with semaphore:
    prompt = build_scenario_prompt(pet_image_url, place_key, action_id)
    try:
      if os.getenv("LUMA_MOCK", "").strip().lower() in ("1", "true", "yes"):
        gen_id = f"mock_{uuid.uuid4().hex[:12]}"
      else:
        gen_id = await create_generation(
          pet_image_url,
          prompt=prompt,
          model=os.getenv("LUMA_MODEL", "ray-2"),
          resolution=os.getenv("LUMA_RESOLUTION", "720p"),
          callback_url=callback_url,
        )
      await motions_svc.register_luma_job(
        session_id, user_id, pet_id, place_key, action_id, gen_id
      )
      return {"action_id": action_id, "luma_generation_id": gen_id, "ok": True}
    except Exception as e:
      return {"action_id": action_id, "ok": False, "error": str(e)}


async def submit_place_motion_set(
  *,
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  pet_image_url: str,
  webhook_base_url: str,
  max_concurrency: int = 3,
) -> tuple[int, list[dict[str, Any]]]:
  """
  선택 장소에 대해 IDLE/TOUCH/VOICE/NFC 4건 Luma 제출.

  Returns:
    (성공 건수, 실패 목록)
  """
  limit = int(os.getenv("LUMA_CREDIT_CONCURRENCY", str(max_concurrency)))
  sem = asyncio.Semaphore(max(1, min(limit, 10)))
  callback_url = f"{webhook_base_url.rstrip('/')}?session_id={session_id}"

  tasks = [
    _submit_one(
      session_id=session_id,
      user_id=user_id,
      pet_id=pet_id,
      place_key=place_key,
      pet_image_url=pet_image_url,
      action_id=action_id,
      callback_url=callback_url,
      semaphore=sem,
    )
    for action_id in ACTION_ORDER
  ]
  results = await asyncio.gather(*tasks)
  ok = sum(1 for r in results if r.get("ok"))
  errors = [r for r in results if not r.get("ok")]
  return ok, errors


def credit_cost() -> int:
  return int(os.getenv("CREDIT_COST_PER_PLACE", str(CREDIT_COST_PER_PLACE_SET)))
