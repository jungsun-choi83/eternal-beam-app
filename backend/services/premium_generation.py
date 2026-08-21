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
from typing import Iterable, Optional

from ..scenarios.pet_scenarios import (
    PREMIUM_ACTIONS,
    THEME_INDEPENDENT_PLACE_KEY,
)
from . import generated_motions_service as motions_svc
from . import generation_queue
from .credit_keyframe import KeyframePreparationError, prepare_black_plate_keyframe
from .generation_safety import log_submission_receipt
from .prompt_factory import build_scenario_prompt
from .video_generation import (
    generation_mock_enabled,
    resolve_action_provider,
    submit_generation,
)

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


def mock_completion_video_url() -> str:
    """
    GENERATION_MOCK 일 때 완료 처리에 쓸 대역 영상 URL.

    왜 필요한가: 프로바이더 호출을 막으면 **완료 콜백도 오지 않는다.** 제출 경로는
    push 방식(프로바이더가 /generation-webhook 을 POST)이라, 부를 프로바이더가
    없으면 작업이 영원히 submitted 로 남고 asset_state 는 계속 GENERATING 이다.
    목업이 파이프라인을 "막기만" 하고 "끝내지" 않으면 그 화면은 절대 READY 가
    되지 않는다.

    그래서 목업은 정상 완료 경로를 **그대로** 태운다. 그러려면 실제로 내려받을 수
    있는 mp4 가 하나 필요하다 — 후보 저장(download_video) → 검증 → 승격이 전부
    진짜 바이트 위에서 돌아야 하기 때문이다.

    미설정이면 제출 **전에** 끊는다. 작업을 만들어 놓고 stuck 시키느니, 사용자에게
    즉시 오류를 보여 주고 상태를 MISSING 으로 남기는 편이 낫다.
    """
    url = (os.getenv("MOCK_LUMA_VIDEO_URL") or "").strip()
    if not url:
        raise PremiumSubmitError(
            "GENERATION_MOCK=1 인데 MOCK_LUMA_VIDEO_URL 이 없습니다. "
            "목업 완료에 쓸 mp4 URL을 설정하거나 GENERATION_MOCK 을 끄십시오.",
            stage="mock_config",
        )
    return url


async def _complete_mocked_generation(external_id: str, video_url: str) -> None:
    """
    목업 작업을 **실제 웹훅과 같은 경로**로 완료시킨다.

    별도 완료 로직을 쓰지 않는 이유: 후보 저장 → 검증 → 승격 → 세션 확정은 이미
    검증된 코드이고, 목업만 다른 길로 가면 목업에서 통과한 것이 실제에서 통과한다는
    보장이 사라진다. 여기서는 프로바이더가 보냈을 콜백을 대신 만들어 줄 뿐이다.

    지연 import: credit_generation_service 가 이 모듈을 import 하므로 모듈 수준에서
    맞물면 순환이 된다 (generation_reconciler 도 같은 이유로 함수 안에서 부른다).
    """
    from .credit_generation_service import handle_luma_webhook_for_credit

    logger.info("GENERATION_MOCK — 완료 콜백을 대신 발행한다 (ext=%s)", external_id)
    await handle_luma_webhook_for_credit(external_id, "completed", video_url=video_url)


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

    # 목업이면 완료를 우리가 만들어야 한다. 대역 영상이 없으면 여기서 끊는다 —
    # 작업 행을 만든 뒤에 알아차리면 그 작업은 영원히 GENERATING 이 된다.
    mocked = generation_mock_enabled()
    mock_video = mock_completion_video_url() if mocked else ""

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

    # 프로바이더가 없으므로 완료 콜백도 없다 → 정상 경로로 직접 끝낸다.
    if mocked:
        await _complete_mocked_generation(job.external_id, mock_video)

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
    allowed_actions: Iterable[str] | None = None,
) -> list[str]:
    """
    이 펫의 큐를 가능한 만큼 전진시킨다. 제출한 action_id 목록을 돌려준다.

    호출 시점: 프리미엄 작업이 **종료**될 때마다(승격/거절/실패). 종료로 슬롯이
    비었으므로 다음 우선순위가 바로 들어갈 수 있다.

    한 번에 여러 건이 들어갈 수 있다 — 상한이 2 이고 두 건이 동시에 끝나면
    두 건을 제출해야 한다. 그래서 루프를 돈다.

    allowed_actions 가 주어지면 **그 안의 액션만** 전진 대상이 된다. 호출부가
    "무엇을 마저 채워야 하는가"를 알고 있을 때 쓴다(예: 아이들 번들 구매는
    IDLE_EVENTS 만 채워야 하고, 사지 않은 COME_CLOSER 까지 만들면 안 된다).
    None 이면 예전 그대로 GENERATION_ORDER 전체가 후보다.

    **절대 예외를 밖으로 내지 않는다.** 이 함수는 웹훅 처리의 부수 작업이다.
    여기서 터지면 이미 성공한 승격이 500 으로 뒤집히고, 프로바이더는 재전송하며,
    그 재전송은 duplicate 로 걸러져 결국 자산이 유실된다.
    """
    allowed = {a.strip().upper() for a in allowed_actions} if allowed_actions is not None else None
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
                    if (allowed is None or a in allowed)
                    and generation_queue.decide(
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
