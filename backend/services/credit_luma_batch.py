"""
크레딧 1회(4코인) = 장소 1곳 × 행동 4개 Luma 제출.

동시 요청은 Semaphore(3) 로 Luma 레이트 리밋 완화.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import logging

from ..scenarios.pet_scenarios import ACTION_ORDER, CREDIT_COST_PER_PLACE_SET

logger = logging.getLogger(__name__)


def resolve_submit_actions() -> tuple[str, ...]:
    """
    실제로 제출할 액션 목록.

    기본값은 프로덕션 그대로 IDLE/TOUCH/VOICE/NFC 4종이다.
    DEV_ACTION_SUBSET 이 설정된 경우에만 그 부분집합으로 좁힌다 — 통제된 실험에서
    원하지 않는 액션에 과금하지 않기 위한 **개발 전용** 장치다.

    ⚠️ 과금에는 절대 영향을 주지 않는다. credit_cost() 는 그대로 4코인이며,
    이 함수는 제출 대상만 줄인다. 4종이 다 없으면 /device/sync 는 계속 404 다.
    """
    raw = (os.getenv("DEV_ACTION_SUBSET") or "").strip()
    if not raw:
        return ACTION_ORDER

    wanted = {a.strip().upper() for a in raw.split(",") if a.strip()}
    subset = tuple(a for a in ACTION_ORDER if a in wanted)
    unknown = wanted - set(ACTION_ORDER)

    if not subset:
        logger.warning(
            "DEV_ACTION_SUBSET=%r 에 유효한 액션이 없습니다 — 프로덕션 기본값(%s)으로 진행합니다.",
            raw, ", ".join(ACTION_ORDER),
        )
        return ACTION_ORDER

    logger.warning(
        "⚠️  TEST ISOLATION MODE 활성 — DEV_ACTION_SUBSET=%r. "
        "제출 액션 %d/%d 개: %s (제외: %s). "
        "과금은 변경되지 않습니다: 여전히 %d 코인이 차감되고, 4종이 모두 없으므로 "
        "/device/sync 는 404 로 남습니다.%s",
        raw, len(subset), len(ACTION_ORDER), ", ".join(subset),
        ", ".join(a for a in ACTION_ORDER if a not in subset) or "없음",
        credit_cost(),
        f" 알 수 없는 값 무시: {', '.join(sorted(unknown))}" if unknown else "",
    )
    return subset
from .prompt_factory import build_scenario_prompt
from . import generated_motions_service as motions_svc
from .generation_safety import log_submission_receipt
from .video_generation import resolve_action_provider, submit_generation


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
      # 액션별 프로바이더. 아무 설정도 없으면 luma — 기존 배포와 동일하다.
      provider = resolve_action_provider(action_id)
      if os.getenv("LUMA_MOCK", "").strip().lower() in ("1", "true", "yes"):
        gen_id = f"mock_{uuid.uuid4().hex[:12]}"
        model = None
      else:
        job = await submit_generation(
          pet_image_url,
          prompt,
          provider=provider,
          callback_url=callback_url,
        )
        gen_id, model = job.external_id, job.model
        # DB 쓰기 **전에** 복구 정보를 먼저 남긴다.
        log_submission_receipt(
          provider=provider, provider_model=model, external_id=gen_id,
          session_id=session_id, action_id=action_id,
        )
      await motions_svc.register_generation_job(
        session_id,
        user_id,
        pet_id,
        place_key,
        action_id,
        gen_id,
        provider=provider,
        provider_model=model,
      )
      return {
        "action_id": action_id,
        "provider": provider,
        "luma_generation_id": gen_id,  # 기존 응답 키 유지
        "external_id": gen_id,
        "ok": True,
      }
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
    for action_id in resolve_submit_actions()
  ]
  results = await asyncio.gather(*tasks)
  ok = sum(1 for r in results if r.get("ok"))
  errors = [r for r in results if not r.get("ok")]
  return ok, errors


async def resubmit_action(
  *,
  session_id: str,
  user_id: str,
  pet_id: str,
  place_key: str,
  action_id: str,
  pet_image_url: str,
  webhook_base_url: str,
  attempt: int,
) -> dict[str, Any]:
  """
  실패/탈락한 **그 액션 하나만** 다시 제출한다.

  크레딧은 추가로 차감하지 않는다 — 4코인은 '시도'가 아니라 '결과'를 산다.
  같은 session_id / 같은 검정 플레이트 키프레임을 재사용하고 attempt 만 올린다.
  """
  prompt = build_scenario_prompt(pet_image_url, place_key, action_id)
  callback_url = f"{webhook_base_url.rstrip('/')}?session_id={session_id}"
  provider = resolve_action_provider(action_id)
  try:
    job = await submit_generation(
      pet_image_url, prompt, provider=provider, callback_url=callback_url
    )
  except Exception as e:
    logger.warning("resubmit_action(%s) 제출 실패: %s", action_id, e)
    return {"action_id": action_id, "ok": False, "error": str(e)}

  log_submission_receipt(
    provider=provider, provider_model=job.model, external_id=job.external_id,
    session_id=session_id, action_id=action_id,
  )
  await motions_svc.register_generation_job(
    session_id, user_id, pet_id, place_key, action_id, job.external_id,
    provider=provider, provider_model=job.model, attempt=attempt,
  )
  logger.info(
    "retry submitted: action=%s attempt=%d provider=%s external_id=%s",
    action_id, attempt, provider, job.external_id,
  )
  return {"action_id": action_id, "ok": True, "external_id": job.external_id, "attempt": attempt}


def credit_cost() -> int:
  return int(os.getenv("CREDIT_COST_PER_PLACE", str(CREDIT_COST_PER_PLACE_SET)))
