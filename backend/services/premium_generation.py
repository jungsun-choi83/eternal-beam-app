"""
프리미엄/아이들 이벤트 생성 제출 + **서버측 큐 전진**.

문제였던 것: 큐는 정상이었지만(슬롯 해제·순서 모두 정확) 다음 액션을 **다시 요청하는
주체가 브라우저의 20초 스윕뿐**이었다. 생성 1건이 45~130초 걸리므로 3~5번째 이벤트는
사용자가 조정 화면에 3~6분 머물러야 제출됐고, 실제로는 화면을 떠나면서 2~3개에서
멈췄다. 자산은 준비됐는데 큐가 "queued/generating" 라벨로 굳어 보였다.

이제 완료/실패/거절 **어느 종료 경로에서든** 서버가 다음 액션을 즉시 제출한다.
브라우저는 상태를 조회할 뿐, 큐를 전진시키는 책임이 없다.

제출 시퀀스는 dev_premium 라우터에서 **그대로 옮겨온 것**이다 — 두 경로가 서로
다르게 제출하면 프롬프트·프로바이더·복구 로그가 갈라진다. 라우터는 이제 이 함수를
호출하고 HTTP 변환만 담당한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from ..scenarios.pet_scenarios import (
    PREMIUM_ACTIONS,
    THEME_INDEPENDENT_PLACE_KEY,
)
from . import generated_motions_service as motions_svc
from . import generation_queue
from .credit_keyframe import KeyframePreparationError, prepare_black_plate_keyframe
from .generation_safety import log_submission_receipt
from .prompt_factory import build_scenario_prompt
from .video_generation import resolve_action_provider, submit_generation

logger = logging.getLogger(__name__)


class PremiumSubmitError(Exception):
    """제출 실패. stage 로 어느 단계에서 깨졌는지 구분한다."""

    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class SubmitResult:
    action_id: str
    session_id: str
    external_id: str
    provider: str
    provider_model: Optional[str]
    keyframe_url: str


def webhook_base_url() -> str:
    """콜백 베이스. 비어 있으면 프로바이더가 우리를 호출할 수 없다(로컬 개발)."""
    return (os.getenv("PUBLIC_API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()


async def submit_premium_action(
    *,
    user_id: str,
    pet_id: str,
    action_id: str,
    pet_image_url: str,
    keyframe_url: str | None = None,
    api_base: str,
) -> SubmitResult:
    """
    테마 독립 액션 1건을 프로바이더에 제출한다. 크레딧 차감 없음.

    **큐 판정은 하지 않는다** — 호출부(라우터 또는 advance_generation_queue)가
    이미 admit 을 결정했다고 본다. 여기서 또 판정하면 판정 지점이 둘로 갈라진다.
    """
    action = (action_id or "").upper()
    place_key = THEME_INDEPENDENT_PLACE_KEY

    # credits_charged=0 — 과금 대상이 아니다. partial/failed 로 끝나도 환불액이 0.
    session_id = await motions_svc.create_credit_session(
        user_id, pet_id, place_key, pet_image_url, 0
    )

    kf = (keyframe_url or "").strip()
    if not kf:
        try:
            kf = await prepare_black_plate_keyframe(pet_image_url, session_id)
        except KeyframePreparationError as e:
            raise PremiumSubmitError(
                "검정 플레이트 키프레임을 만들지 못했습니다.", stage=e.stage
            ) from e

    callback_url = (
        f"{api_base.rstrip('/')}/api/v1/pet/generation-webhook?session_id={session_id}"
        if api_base
        else None
    )
    provider = resolve_action_provider(action)
    prompt = build_scenario_prompt(kf, place_key, action)

    try:
        job = await submit_generation(kf, prompt, provider=provider, callback_url=callback_url)
    except Exception as e:  # noqa: BLE001 — 프로바이더 예외는 종류가 다양하다
        raise PremiumSubmitError(f"submit failed: {e}", stage="submit") from e

    # DB 쓰기 **전에** 복구 정보를 남긴다 (프로바이더는 이미 과금했다).
    log_submission_receipt(
        provider=provider,
        provider_model=job.model,
        external_id=job.external_id,
        session_id=session_id,
        action_id=action,
    )
    await motions_svc.register_generation_job(
        session_id, user_id, pet_id, place_key, action, job.external_id,
        provider=provider, provider_model=job.model, attempt=1,
    )
    return SubmitResult(
        action_id=action,
        session_id=session_id,
        external_id=job.external_id,
        provider=provider,
        provider_model=job.model,
        keyframe_url=kf,
    )


async def advance_generation_queue(
    *,
    user_id: str,
    pet_id: str,
    pet_image_url: str | None,
    api_base: str,
) -> list[str]:
    """
    이 펫의 큐를 가능한 만큼 전진시킨다. 제출한 action_id 목록을 돌려준다.

    호출 시점: 프리미엄 작업이 **종료**될 때마다(승격/거절/실패). 종료로 슬롯이
    비었으므로 다음 우선순위가 바로 들어갈 수 있다.

    한 번에 여러 건이 들어갈 수 있다 — 상한이 2 이고 두 건이 동시에 끝나면
    두 건을 제출해야 한다. 그래서 루프를 돈다.

    **절대 예외를 밖으로 내지 않는다.** 이 함수는 웹훅 처리의 부수 작업이다.
    여기서 터지면 이미 성공한 승격이 500 으로 뒤집히고, 프로바이더는 재전송하며,
    그 재전송은 duplicate 로 걸러져 결국 자산이 유실된다.
    """
    submitted: list[str] = []
    if not pet_image_url:
        logger.info("큐 전진 생략 — pet_image_url 없음 (user=%s pet=%s)", user_id, pet_id)
        return submitted

    try:
        for _ in range(len(generation_queue.GENERATION_ORDER)):
            ready = [
                (m.action_id or "").upper()
                for m in await motions_svc.list_motions_for_pet(user_id, pet_id)
            ]
            active = await motions_svc.list_active_action_ids_for_pet(user_id, pet_id)

            nxt = next(
                (
                    a
                    for a in generation_queue.GENERATION_ORDER
                    if generation_queue.decide(
                        action_id=a, ready_actions=ready, active_actions=active
                    ).allowed
                ),
                None,
            )
            if nxt is None:
                break

            try:
                r = await submit_premium_action(
                    user_id=user_id,
                    pet_id=pet_id,
                    action_id=nxt,
                    pet_image_url=pet_image_url,
                    api_base=api_base,
                )
            except PremiumSubmitError as e:
                # 이 액션은 못 만들었지만 큐를 막지는 않는다. 다음 종료 이벤트에서
                # 다시 시도된다(작업 행이 없으므로 active 를 점유하지도 않는다).
                logger.warning(
                    "큐 전진 중 제출 실패 — %s (stage=%s user=%s pet=%s): %s",
                    nxt, e.stage, user_id, pet_id, e,
                )
                break

            submitted.append(r.action_id)
            logger.info(
                "큐 전진 — %s 제출 (user=%s pet=%s ext=%s)",
                r.action_id, user_id, pet_id, r.external_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("큐 전진 실패 — 웹훅 처리는 계속한다 (user=%s pet=%s)", user_id, pet_id)

    return submitted


def is_queued_action(action_id: str | None) -> bool:
    """큐가 관리하는 액션인가. 레거시 4종은 자기 파이프라인이 따로 있다."""
    return (action_id or "").upper() in PREMIUM_ACTIONS
