"""
크레딧 차감형 영상 생성 오케스트레이션.

① 잔액 확인 → ② 차감 → ③ Luma 4건 제출 → (실패 시 환불)
웹훅은 `handle_luma_webhook_for_credit` 에서 처리.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..models.hybrid_business import (
  GenerateWithCreditResponse,
  MotionJobStatus,
  SessionStatus,
)
from ..scenarios.pet_scenarios import IDLE_EVENTS, place_public_id, resolve_place_id
from .credit_keyframe import prepare_black_plate_keyframe
from .credit_luma_batch import credit_cost, submit_place_motion_set
from . import generated_motions_service as motions_svc
from . import premium_generation
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
    # 제출 전에 한 번만 검정 플레이트로 평탄화한다 (4건이 같은 키프레임을 공유).
    # 실패하면 원본 URL 을 그대로 쓴다 — 준비 단계가 생성을 막지 않는다.
    keyframe_url = await prepare_black_plate_keyframe(image_url, session_id)

    submitted, errors = await submit_place_motion_set(
      session_id=session_id,
      user_id=uid,
      pet_id=pid,
      place_key=place_key,
      pet_image_url=keyframe_url,
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
      webhook_path="/api/v1/pet/generation-webhook",
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
    webhook_path="/api/v1/pet/generation-webhook",
  )


async def _maybe_retry_action(job, session: dict, webhook_base_url: str) -> Optional[dict]:
  """시도 횟수가 남아 있으면 **그 액션만** 재제출한다. 추가 과금 없음."""
  from .credit_luma_batch import resubmit_action

  if job.attempt >= motions_svc.MAX_ACTION_ATTEMPTS:
    return None
  if not session:
    return None
  return await resubmit_action(
    session_id=job.session_id,
    user_id=job.user_id,
    pet_id=job.pet_id,
    place_key=job.place_key,
    action_id=job.action_id,
    pet_image_url=session.get("pet_image_url") or "",
    webhook_base_url=webhook_base_url,
    attempt=job.attempt + 1,
  )


async def _finalize_session_if_terminal(session_id: str) -> dict[str, Any]:
  """
  세션 상태를 재계산하고, 종료 상태면 환불 정책을 **한 번만** 적용한다.

  환불 정책 (device/sync 의 all-or-nothing 계약을 그대로 반영):
    completed 4/4 → 환불 없음
    partial 1~3/4 → 전액 환불 (불완전 세트는 404 라 가치가 0)
    failed  0/4  → 전액 환불
  """
  jobs = await motions_svc.list_jobs_for_session(session_id)
  status = motions_svc.compute_session_status(jobs)
  terminal = status is not SessionStatus.processing
  await motions_svc.update_session_status(session_id, status, finalized=terminal)

  refunded = False
  if terminal and status in (SessionStatus.partial, SessionStatus.failed):
    sess = await motions_svc.get_session(session_id)
    if sess and not sess.get("refunded_at"):
      # refunded_at 을 먼저 선점한다 — 동시 웹훅에서도 한 번만 통과한다.
      if await motions_svc.mark_session_refunded(session_id):
        await refund_credits(sess.get("user_id") or "", int(sess.get("credits_charged") or 0))
        refunded = True
  return {"session_status": status.value, "finalized": terminal, "refunded": refunded}


async def _bundle_actions_to_continue(job) -> tuple[str, ...]:
  """
  이 작업의 종료가 **자동으로 이어서 만들어야 할** 액션들.

  자동 전진은 원래 **아이들 번들** 을 위한 장치다: 번들은 한 번의 구매로 등록된
  아이들 이벤트 전체를 잠금 해제하는데, 동시 상한(2) 때문에 한 번에 다 제출할 수
  없다. 그래서 슬롯이 빌 때마다 서버가 나머지를 마저 채운다.

  Behavior Library(행동 단건 생성)에는 "나머지"가 없다. 사용자가 BLINKING 하나를
  골랐으면 만들 것은 BLINKING 하나뿐이다. 그런데도 전진이 돌면 고르지도 않은
  행동이 제출되고 — 실 프로바이더에서는 **클릭 한 번에 최대 5건이 과금된다.**

  구분 기준은 **활성 번들 구매의 존재**다:
    번들 구매 있음 → 아직 못 채운 IDLE_EVENTS 를 마저 만든다 (기존 동작 보존)
    없음           → 아무것도 만들지 않는다 (단건 생성·구독 모델)

  구독 모델에서는 구매 원장 행 자체가 없으므로(Phase 2) 자동으로 후자가 된다.
  COME_CLOSER 는 번들 대상이 아니므로 어느 쪽이든 자동 제출되지 않는다 —
  예전에는 GENERATION_ORDER 첫 항목이라 번들 구매만으로도 딸려 나왔다.

  조회에 실패하면 **전진하지 않는다.** 모르면 돈을 쓰지 않는 쪽으로 닫는다.
  """
  from . import premium_purchase

  try:
    bundle = await premium_purchase.find_active_purchase(
      job.user_id, job.pet_id, premium_purchase.KIND_IDLE_BUNDLE
    )
  except Exception:  # noqa: BLE001 — 판정 실패가 승격을 뒤집으면 안 된다
    import logging

    logging.getLogger(__name__).exception(
      "번들 구매 조회 실패 — 자동 전진을 건너뛴다 (user=%s pet=%s)", job.user_id, job.pet_id
    )
    return ()

  return tuple(IDLE_EVENTS) if bundle else ()


async def _advance_premium_queue(job, session: dict[str, Any]) -> list[str]:
  """
  프리미엄 작업이 종료됐다 → **번들이 남아 있으면** 다음을 즉시 제출한다.

  예전에는 이 일을 브라우저의 20초 스윕이 했다. 생성 1건이 45~130초라 3~5번째
  이벤트는 사용자가 조정 화면에 3~6분 머물러야 제출됐고, 실제로는 2~3개에서 멈췄다.
  이제 브라우저는 상태만 조회하고, 큐 전진은 서버가 책임진다.

  레거시 4종(IDLE/TOUCH/VOICE/NFC)에서는 아무것도 하지 않는다 — 그쪽은 4코인
  파이프라인이 자기 동시성을 따로 관리한다(is_queued_action 이 걸러낸다).

  ⚠️ 단건 생성에서는 전진하지 않는다 — _bundle_actions_to_continue 참고.
  """
  if not premium_generation.is_queued_action(job.action_id):
    return []

  # 종료 환불 판정 — 이 구매의 대상이 **전부 종료됐는데 승격이 0건**이면 환불한다.
  # 큐 전진보다 먼저 부르지 않는다: 전진이 새 작업을 만들면 아직 종료가 아니다.
  continuation = await _bundle_actions_to_continue(job)
  advanced: list[str] = []
  if continuation:
    advanced = await premium_generation.advance_generation_queue(
      user_id=job.user_id,
      pet_id=job.pet_id,
      pet_image_url=(session or {}).get("pet_image_url"),
      api_base=premium_generation.webhook_base_url(),
      allowed_actions=continuation,
    )
  try:
    from . import premium_purchase

    await premium_purchase.reconcile_after_terminal(job.user_id, job.pet_id, job.action_id)
  except Exception:  # noqa: BLE001 — 환불 판정 실패가 승격을 500 으로 뒤집으면 안 된다
    import logging

    logging.getLogger(__name__).exception(
      "프리미엄 환불 판정 실패 (user=%s pet=%s action=%s)",
      job.user_id, job.pet_id, job.action_id,
    )
  return advanced


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

  # ── 재전송 방어 ───────────────────────────────────────────────────────────
  # 이미 승격됐거나 이미 종료 상태인 작업은 다시 처리하지 않는다:
  # 재다운로드·재업로드도, 재시도도, 재환불도 없다.
  if job.promoted_at or job.status == MotionJobStatus.completed:
    return {
      "session_id": job.session_id,
      "action_id": job.action_id,
      "status": "completed",
      "duplicate": True,
      "video_url": job.video_url,
    }
  if job.status in (MotionJobStatus.failed, MotionJobStatus.rejected):
    return {
      "session_id": job.session_id,
      "action_id": job.action_id,
      "status": job.status.value,
      "duplicate": True,
    }

  session = await motions_svc.get_session(job.session_id) or {}
  webhook_base = (os.getenv("PUBLIC_API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
  webhook_base = f"{webhook_base.rstrip('/')}/api/v1/pet/generation-webhook" if webhook_base else ""

  if state_l in ("failed", "error"):
    await motions_svc.mark_job_failed(luma_generation_id, error or "failed")
    retry = await _maybe_retry_action(job, session, webhook_base)
    summary = await _finalize_session_if_terminal(job.session_id)
    advanced = await _advance_premium_queue(job, session)
    return {
      "session_id": job.session_id, "action_id": job.action_id, "status": "failed",
      "retry": retry, "queue_advanced": advanced, **summary,
    }

  if state_l == "completed" and video_url:
    if str(luma_generation_id).startswith("mock_"):
      mock = (os.getenv("MOCK_LUMA_VIDEO_URL") or "").strip()
      if mock:
        video_url = mock

    # 후보 저장 → 검증 → (통과 시) 승격. canonical 은 통과한 후보만 덮어쓴다.
    _cand, mp4 = await motions_svc.save_candidate_motion(job, video_url)
    accepted, meta = motions_svc.validate_candidate(job, mp4)
    await motions_svc._record_validation(job, meta)

    if not accepted:
      await motions_svc.mark_job_rejected(luma_generation_id, meta)
      retry = await _maybe_retry_action(job, session, webhook_base)
      summary = await _finalize_session_if_terminal(job.session_id)
      advanced = await _advance_premium_queue(job, session)
      return {
        "session_id": job.session_id, "action_id": job.action_id, "status": "rejected",
        "candidate_url": _cand, "validation": meta, "retry": retry,
        "queue_advanced": advanced, **summary,
      }

    motion = await motions_svc.promote_candidate(job, mp4)
    summary = await _finalize_session_if_terminal(job.session_id)
    advanced = await _advance_premium_queue(job, session)
    return {
      "session_id": job.session_id,
      "action_id": job.action_id,
      "status": "completed",
      "video_url": motion.video_url,
      "place_id": motion.place_id,
      "candidate_url": _cand,
      "validation": meta,
      "queue_advanced": advanced,
      **summary,
    }

  return {"session_id": job.session_id, "action_id": job.action_id, "status": state_l or "dreaming"}
