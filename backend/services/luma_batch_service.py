"""
Luma 40건 비동기 일괄 제출 (웹훅 콜백 방식).

- create 만 asyncio.gather 로 병렬 요청 (폴링은 웹훅이 담당).
- LUMA_MOCK=1 이면 API 호출 없이 가짜 generation_id 반환.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from ..scenarios.pet_scenarios import all_scenario_keys
from .prompt_factory import build_scenario_prompt
from . import pet_generation_store
from .luma_service import create_generation


async def _submit_one(
  *,
  batch_id: str,
  image_url: str,
  place_key: str,
  action_key: str,
  callback_url: str,
  model: str,
  resolution: str,
) -> dict[str, Any]:
  """시나리오 1개 Luma 생성 요청."""
  prompt = build_scenario_prompt(image_url, place_key, action_key)
  try:
    if os.getenv("LUMA_MOCK", "").strip().lower() in ("1", "true", "yes"):
      gen_id = f"mock_{uuid.uuid4().hex[:12]}"
    else:
      gen_id = await create_generation(
        image_url,
        prompt=prompt,
        model=model,
        resolution=resolution,
        callback_url=callback_url,
      )
    await pet_generation_store.register_luma_job(batch_id, place_key, action_key, gen_id)
    return {
      "place_key": place_key,
      "action_key": action_key,
      "luma_generation_id": gen_id,
      "ok": True,
    }
  except Exception as e:
    return {
      "place_key": place_key,
      "action_key": action_key,
      "ok": False,
      "error": str(e),
    }


async def submit_all_scenarios(
  *,
  batch_id: str,
  image_url: str,
  webhook_base_url: str,
) -> tuple[int, list[dict[str, Any]]]:
  """
  40개 시나리오를 Luma에 동시에 제출.

  Args:
    webhook_base_url: 예) https://api.example.com/api/v1/pet/luma-webhook
  """
  model = os.getenv("LUMA_MODEL", "ray-2")
  resolution = os.getenv("LUMA_RESOLUTION", "720p")
  callback_url = f"{webhook_base_url.rstrip('/')}?batch_id={batch_id}"

  tasks = [
    _submit_one(
      batch_id=batch_id,
      image_url=image_url,
      place_key=place_key,
      action_key=action_key,
      callback_url=callback_url,
      model=model,
      resolution=resolution,
    )
    for place_key, action_key in all_scenario_keys()
  ]

  results = await asyncio.gather(*tasks, return_exceptions=False)
  ok = sum(1 for r in results if r.get("ok"))
  errors = [r for r in results if not r.get("ok")]
  return ok, errors
