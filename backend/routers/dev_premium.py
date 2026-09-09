"""
개발 전용 프리미엄 액션 트리거 (COME_CLOSER).

⚠️ ENABLE_DEV_PREMIUM_TRIGGER=1 일 때만 마운트된다. 기본값은 꺼짐이라
프로덕션 배포에는 이 라우트가 존재하지 않는다.

레거시 4코인 팩을 절대 건드리지 않는다:
  * ACTION_ORDER 를 쓰지 않는다 — COME_CLOSER 한 건만 제출한다
  * 크레딧을 차감하지 않는다 (credits_charged=0 세션)
  * IDLE/TOUCH/VOICE/NFC 는 제출조차 되지 않는다

그 외 파이프라인은 전부 재사용한다:
  검정 플레이트 키프레임 → 프로바이더 디스패치 → 후보 저장 → 진단 검증 →
  승격 → 재시도 → 세션 확정 → 리컨사일러
완료 처리는 기존 /generation-webhook 이 그대로 담당한다 (별도 구현 없음).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..scenarios.pet_scenarios import (
    PREMIUM_ACTIONS,
    THEME_INDEPENDENT_PLACE_ID,
    THEME_INDEPENDENT_PLACE_KEY,
)
from ..services import generated_motions_service as motions_svc
from ..services import generation_queue
from ..services.credit_keyframe import (
    KeyframePreparationError,
    is_remote_asset_url,
    prepare_black_plate_keyframe,
)
from ..services.generation_safety import log_submission_receipt
from ..services.prompt_factory import build_scenario_prompt
from ..services.video_generation import resolve_action_provider, submit_generation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/dev", tags=["pet-dev"])

#: action_id 를 생략했을 때의 기본값 — 기존 호출부(프론트 come-closer-autogen)가
#: 이 필드를 모르기 때문에 반드시 COME_CLOSER 로 남아야 한다.
ACTION = "COME_CLOSER"


def dev_trigger_enabled() -> bool:
    return os.getenv("ENABLE_DEV_PREMIUM_TRIGGER", "0").strip().lower() in ("1", "true", "yes")


def _resolve_action(raw: str | None) -> str:
    """
    요청된 action_id → 검증된 액션 키.

    허용 목록은 PREMIUM_ACTIONS(= PET_ACTIONS + IDLE_EVENTS)뿐이다. 레거시 4종
    (IDLE/TOUCH/VOICE/NFC)은 **절대 여기로 들어오면 안 된다** — 그쪽은 장소별
    자산이고 4코인/NFC 계약이 그 전제로 돈다.
    """
    key = (raw or ACTION).strip().upper()
    if key not in PREMIUM_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ACTION_NOT_SUPPORTED",
                "message": f"{key} 는 이 엔드포인트가 만들 수 있는 액션이 아니다.",
                "supported": list(PREMIUM_ACTIONS),
            },
        )
    return key


class ComeCloserRequest(BaseModel):
    user_id: str
    pet_image_url: str
    #: 정본 장면 키프레임. 있으면 이 그림에서 출발하고 배경이 구워진 채로 나온다.
    #: (keyframe_url 과 달리 배경 보존 프롬프트가 함께 켜진다.)
    scene_keyframe_url: str | None = None
    scene_id: str | None = None
    #: 하위호환으로 받기만 한다. 테마 독립 액션이라 **무시된다** —
    #: 검정 플레이트 위 펫만 생성하므로 결과물이 배경과 무관하기 때문이다.
    selected_place_id: str | None = None
    pet_id: str | None = None
    #: 이미 만들어 둔 검정 플레이트가 있으면 재사용 (재평탄화·재업로드 생략)
    keyframe_url: str | None = None
    #: 생성할 액션. 생략하면 COME_CLOSER — 기존 호출부와 동작이 같다.
    #: PREMIUM_ACTIONS 안의 값만 허용한다(현재 COME_CLOSER / BLINKING).
    action_id: str | None = None


class ComeCloserResponse(BaseModel):
    #: "ready"      = canonical 이미 존재
    #: "processing" = 프로바이더에 제출됨 (신규 또는 기존 진행 중 작업 재사용)
    #: "queued"     = 펫당 동시 생성 상한에 걸려 **아직 제출하지 않았다**
    #:                → 프론트는 이걸 generating 으로 표시하면 안 된다.
    status: str
    #: 이번 호출이 실제로 프로바이더를 불렀는가. 멱등 호출이면 False.
    generated: bool
    user_id: str
    pet_id: str
    place_id: str
    action_id: str
    #: 액션 중립 필드. 새 호출부는 이쪽을 쓴다.
    video_url: str | None = None
    #: 하위호환 별칭 — 기존 프론트가 이 이름만 읽는다. COME_CLOSER 가 아닐 때도
    #: 같은 값을 담아 준다(호출부가 액션별로 분기하지 않아도 되게).
    come_closer_video_url: str | None = None
    session_id: str | None = None
    provider: str | None = None
    provider_model: str | None = None
    external_id: str | None = None
    keyframe_url: str | None = None
    credits_charged: int = 0
    webhook_path: str = "/api/v1/pet/generation-webhook"
    #: status="queued" 일 때만 의미가 있다.
    queue_reason: str | None = None
    #: 대기열에서 앞에 몇 건이 있는가.
    queue_position: int = 0
    #: 이 펫에서 지금 돌고 있는 프로바이더 작업 수.
    active_generations: int = 0


def _public_api_base(request: Request) -> str:
    explicit = (os.getenv("PUBLIC_API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    return explicit.rstrip("/") if explicit else str(request.base_url).rstrip("/")


@router.post("/come-closer", response_model=ComeCloserResponse)
async def trigger_come_closer(request: Request, body: ComeCloserRequest):
    """테마 독립 액션 1건만 제출한다(기본 COME_CLOSER). 크레딧 차감 없음."""
    if not dev_trigger_enabled():
        raise HTTPException(status_code=404, detail="dev trigger disabled")

    action = _resolve_action(body.action_id)

    uid = (body.user_id or "").strip()
    image_url = (body.pet_image_url or "").strip()
    if not uid or not image_url:
        raise HTTPException(status_code=400, detail="user_id and pet_image_url are required")

    # 세션을 만들기 **전에** 거른다. data: URL 은 예전에 stage="download" 로
    # 떨어져 스토리지 장애처럼 보였고, 게다가 고아 세션만 남겼다.
    if not is_remote_asset_url(image_url):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PET_IMAGE_URL_NOT_REMOTE",
                "message": (
                    "pet_image_url 은 http(s) URL 이어야 한다. data: URL 은 백엔드가 "
                    "가져올 수 없다 — 누끼를 먼저 스토리지에 올려라 "
                    "(POST /api/assets/cutout)."
                ),
                "got_scheme": image_url.split(":", 1)[0][:16],
            },
        )

    # COME_CLOSER 는 테마 독립이다 — 선택된 테마가 무엇이든 같은 클립 하나를 쓴다.
    # 그래서 place 를 해석하지도, 검증하지도 않는다. 결과적으로 fresh_forest 나
    # 커스텀 배경처럼 백엔드 PLACES 에 없는 테마에서도 그대로 동작한다.
    place_key = THEME_INDEPENDENT_PLACE_KEY
    pid = motions_svc.default_pet_id(uid, body.pet_id)

    # ── 멱등성: 서버가 최종 권위 ────────────────────────────────────────────
    # 클라이언트 가드(인플라이트 맵·StrictMode 방어)는 편의일 뿐이다. 새로고침,
    # 다른 탭, 다른 기기에서 온 중복 호출은 여기서만 막을 수 있다.
    #
    # 1) canonical 이 이미 있으면 절대 재생성하지 않는다.
    existing = await motions_svc.find_motion_for_key(uid, pid, place_key, action)
    if existing:
        return ComeCloserResponse(
            status="ready", generated=False,
            user_id=uid, pet_id=pid, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id=action, video_url=existing.video_url,
            come_closer_video_url=existing.video_url,
        )

    # 2) 같은 키로 진행 중인 작업이 있으면 새로 제출하지 않는다.
    active = await motions_svc.find_active_job_for_key(uid, pid, place_key, action)
    if active:
        logger.info(
            "COME_CLOSER 자동생성 멱등 히트 — 진행 중 작업 재사용 (user=%s pet=%s place=%s ext=%s)",
            uid, pid, place_key, active.luma_generation_id,
        )
        return ComeCloserResponse(
            status="processing", generated=False,
            user_id=uid, pet_id=pid, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id=action, session_id=active.session_id,
            provider=active.provider, provider_model=active.provider_model,
            external_id=active.luma_generation_id,
        )

    # ── 3) 펫당 동시 생성 수 제한 ────────────────────────────────────────────
    # 여기까지 왔다는 것은 canonical 도 없고 이 액션의 진행 중 작업도 없다는 뜻이다.
    # 하지만 **다른 액션**이 이미 돌고 있을 수 있다. 예전에는 그걸 세는 곳이 없어
    # 새 펫에서 5건이 한꺼번에 나갔다.
    #
    # 세션을 만들기 **전에** 거른다 — 거절할 요청이 고아 세션을 남기면 안 된다.
    # 프로바이더 호출도 당연히 아직 없다.
    ready_actions = [
        (m.action_id or "").upper()
        for m in await motions_svc.list_motions_for_pet(uid, pid)
    ]
    active_actions = await motions_svc.list_active_action_ids_for_pet(uid, pid)
    decision = generation_queue.decide(
        action_id=action,
        ready_actions=ready_actions,
        active_actions=active_actions,
    )
    if not decision.allowed:
        logger.info(
            "생성 큐 대기 — %s (user=%s pet=%s 이유=%s 앞에=%s 진행중=%s)",
            action, uid, pid, decision.reason, decision.position, sorted(active_actions),
        )
        return ComeCloserResponse(
            status="queued", generated=False,
            user_id=uid, pet_id=pid, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id=action,
            queue_reason=decision.reason,
            queue_position=decision.position,
            active_generations=len(active_actions),
        )

    # credits_charged=0 — 이 세션은 과금 대상이 아니다. partial/failed 로 끝나도
    # 환불액이 0 이라 지갑에 아무 영향이 없다.
    # credits_charged=0 — 이 세션은 과금 대상이 아니다. partial/failed 로 끝나도
    # 환불액이 0 이라 지갑에 아무 영향이 없다.
    session_id = await motions_svc.create_credit_session(uid, pid, place_key, image_url, 0)

    # ── 정본 장면 우선 ──────────────────────────────────────────────────────
    # 장면이 있으면 검정 플레이트를 만들지 않는다. COME_CLOSER 도 BREATHING 과
    # **같은 그림**에서 출발해야 한 아이의 영상들이 같은 배경을 갖는다.
    scene_url = (body.scene_keyframe_url or "").strip()
    background_baked = bool(scene_url)

    keyframe_url = scene_url or (body.keyframe_url or "").strip()
    if not keyframe_url:
        try:
            keyframe_url = await prepare_black_plate_keyframe(image_url, session_id)
        except KeyframePreparationError as e:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "KEYFRAME_PREPARATION_FAILED",
                    "message": "검정 플레이트 키프레임을 만들지 못했습니다.",
                    "stage": e.stage,
                },
            ) from e

    api_base = _public_api_base(request)
    callback_url = f"{api_base}/api/v1/pet/generation-webhook?session_id={session_id}"

    provider = resolve_action_provider(action)
    prompt = build_scenario_prompt(
        keyframe_url, place_key, action, background_baked=background_baked
    )

    try:
        job = await submit_generation(
            keyframe_url, prompt, provider=provider, callback_url=callback_url
        )
    except Exception as e:
        logger.exception("dev premium 제출 실패")
        raise HTTPException(status_code=502, detail=f"submit failed: {e}") from e

    # DB 쓰기 **전에** 복구 정보를 먼저 남긴다 (첫 시도가 여기서 실패해 유실됐다).
    log_submission_receipt(
        provider=provider, provider_model=job.model, external_id=job.external_id,
        session_id=session_id, action_id=action,
    )
    await motions_svc.register_generation_job(
        session_id, uid, pid, place_key, action, job.external_id,
        provider=provider, provider_model=job.model, attempt=1,
    )
    logger.warning(
        "⚠️  DEV PREMIUM TRIGGER — %s 1건만 제출 (레거시 4종 미제출, 크레딧 0). "
        "provider=%s external_id=%s session=%s",
        action, provider, job.external_id, session_id,
    )

    return ComeCloserResponse(
        status="processing",
        generated=True,
        session_id=session_id,
        user_id=uid,
        pet_id=pid,
        place_id=THEME_INDEPENDENT_PLACE_ID,
        action_id=action,
        provider=provider,
        provider_model=job.model,
        external_id=job.external_id,
        keyframe_url=keyframe_url,
        credits_charged=0,
        webhook_path="/api/v1/pet/generation-webhook",
    )


@router.get("/come-closer/{user_id}")
async def get_come_closer(
    user_id: str,
    place_id: str | None = None,
    pet_id: str | None = None,
    action_id: str | None = None,
):
    """
    승격된 테마 독립 자산을 돌려준다. action_id 를 생략하면 COME_CLOSER.

    place_id 는 하위호환으로 받기만 하고 **쓰지 않는다** — 이 액션들은 테마 독립이라
    펫 단위로 하나다. 덕분에 예전에 장소별로 저장된 행도 그대로 찾아진다.
    """
    if not dev_trigger_enabled():
        raise HTTPException(status_code=404, detail="dev trigger disabled")

    action = _resolve_action(action_id)
    motions = await motions_svc.list_motions_for_pet(user_id, pet_id)
    match = next((m for m in motions if (m.action_id or "").upper() == action), None)
    url = match.video_url if match else None
    return {
        "user_id": user_id,
        "pet_id": motions_svc.default_pet_id(user_id, pet_id),
        "place_id": THEME_INDEPENDENT_PLACE_ID,
        "theme_independent": True,
        "action_id": action,
        "video_url": url,
        # 하위호환 별칭 — 기존 프론트가 이 이름만 읽는다.
        "come_closer_video_url": url,
        "ready": bool(match),
        "premium_actions": list(PREMIUM_ACTIONS),
    }
