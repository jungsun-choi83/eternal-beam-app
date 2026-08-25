import logging
import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services import supabase_assets
from ..services.cutout_errors import CutoutError
from ..services.dog_image_preprocessing import build_dog_only_nobg_png_bytes
from ..services.idle_validation_service import validate_idle_video
from ..services.luma_idle_pipeline import generate_idle_variant
from ..services.luma_idle_templates import IDLE_TEMPLATE_ORDER, is_known_template
from ..services.luma_keyframe import (
    build_keyframe_jpeg,
    flatten_rgba_to_jpeg_bytes,
    resolve_keyframe_bg_rgb,
)
from ..services import scene_generation_jobs, scene_input
from ..services.luma_service import (
    build_idle_action_prompts,
    download_video,
)
from ..services.video_generation import create_generation_and_get_video_url
from ..services.seamless_loop_service import make_seamless_loop_mp4
from ..services.vitmatte_service import DEBUG_ARTIFACTS_ENABLED, validate_cutout_alpha

logger = logging.getLogger(__name__)

router = APIRouter()


def _cutout_error_response(cid: str, exc: CutoutError) -> HTTPException:
    """누끼 실패 → 422. 유료 Luma 생성으로 넘어가기 전에 여기서 멈춘다."""
    logger.warning(
        "generate-pet-video rejected before generation (cid=%s, code=%s): %s | diagnostics=%s",
        cid,
        exc.code,
        exc.message,
        exc.diagnostics,
    )
    detail = exc.to_detail(include_diagnostics=DEBUG_ARTIFACTS_ENABLED)
    detail["content_id"] = cid
    return HTTPException(status_code=exc.http_status, detail=detail)


async def _cutout_to_dog_bytes(raw: bytes, *, skip: bool) -> bytes:
    """skip=True면 이미 누끼딴 파일 그대로. False면 SAM2+ViTMatte로 누끼."""
    if skip:
        return raw
    from ..services.vitmatte_service import matte_foreground_with_meta

    png_bytes, _meta = matte_foreground_with_meta(raw)
    return png_bytes


def _pet_video_seamless_loop_enabled() -> bool:
    """
    /generate-pet-video, /generate-idle-variant 공통: 아이들 영상 후처리
    (ffmpeg seamless loop) on/off.
    Render 512MB 컨테이너에서 ffmpeg 재인코딩(xfade+concat)이 uvicorn 프로세스와
    메모리를 다투다 컨테이너 전체를 OOM으로 죽이는 문제가 확인되어 기본값 off.
    더 큰 플랜으로 옮기거나 ffmpeg 메모리 사용을 낮추면 PET_VIDEO_SEAMLESS_LOOP=1로
    다시 켤 수 있음.
    (2026-07-21: /api/generate-idle-variant도 같은 플래그를 공유하도록 변경 —
    5종 세트는 이 엔드포인트를 5번 순차 호출하므로, 매 호출마다 ffmpeg 재인코딩을
    돌리면 OOM 위험이 5배가 되어 "5종 테스트 패널이 멈춘다"는 증상의 주 원인이었음.)
    """
    return os.getenv("PET_VIDEO_SEAMLESS_LOOP", "false").lower() in ("1", "true", "yes")


def _idempotency_unavailable(scene_id: str) -> HTTPException:
    """
    멱등성 저장소를 못 쓴다 → **제출하지 않고** 명확히 거절한다.

    503 인 이유: 고객 잘못도 장면 잘못도 아니고, 잠시 후 그대로 다시 시도하면
    되는 상태다. 400 이면 클라이언트가 입력을 고치려 들고, 500 이면 재시도를
    포기한다.
    """
    err = scene_generation_jobs.IdempotencyUnavailableError
    return HTTPException(
        status_code=err.status,
        detail={"code": err.code, "message": err.message, "scene_id": scene_id},
    )


async def _recover_provider_job(job) -> str | None:
    """
    이미 제출된 프로바이더 작업을 **폴링해서** 결과를 되찾는다.

    재제출하지 않는 것이 요점이다. 첫 요청이 타임아웃돼도 프로바이더 쪽 작업은
    계속 돌고 있고, 그 id 를 우리가 들고 있으므로 결과만 가져오면 된다 —
    새로 제출하면 같은 그림에 두 번 과금된다.

    아직 안 끝났으면 None. 호출부가 409 로 "진행 중"을 알린다(실패가 아니다).
    """
    from ..services import generation_reconciler

    try:
        outcome = await generation_reconciler.fetch_outcome_by_id(
            job.provider_job_id, provider=job.provider
        )
    except Exception:
        logger.warning(
            "scene job 복구 조회 실패 (provider_job=%s)", job.provider_job_id, exc_info=True
        )
        return None
    if not outcome:
        return None

    if outcome.state == "completed" and outcome.video_url:
        try:
            await scene_generation_jobs.mark_completed(
                user_id=job.user_id,
                scene_id=job.scene_id,
                behavior=job.behavior,
                video_url=outcome.video_url,
            )
        except Exception:
            logger.warning("scene job 완료 기록 실패", exc_info=True)
        return outcome.video_url

    if outcome.state == "failed":
        # 종료 상태다 — 자리를 비워 다음 요청이 정상적으로 재시도하게 한다.
        try:
            await scene_generation_jobs.clear_for_retry(
                user_id=job.user_id, scene_id=job.scene_id, behavior=job.behavior
            )
        except Exception:
            logger.warning("scene job 정리 실패", exc_info=True)
    return None


@router.post("/generate-pet-video")
async def post_generate_pet_video(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    skip_preprocessing: str = Form("false"),
    # 액션(20종)은 이제 Live Portrait가 맡을 예정 — Luma는 아이들(미세 모션) 루프 1건만 생성.
    # 예전 방식(아이들+액션 2건)이 필요하면 idle_only=false로 되돌릴 수 있음.
    idle_only: str = Form("true"),
    # ── 정본 장면 (Phase 19) ──────────────────────────────────────────────
    # 있으면 프로바이더는 **승인된 그림**에서 출발하고 배경이 구워진 영상이 나온다.
    # 없으면 예전 그대로 — 누끼를 단색 판에 눌러 붙인다(레거시 호환).
    scene_id: str | None = Form(None),
    background_type: str | None = Form(None),
    background_id: str | None = Form(None),
    scene_keyframe_url: str | None = Form(None),
    background_baked: str | None = Form(None),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    skip = str(skip_preprocessing).lower() in ("1", "true", "yes")
    only_idle = str(idle_only).lower() in ("1", "true", "yes")

    try:
        if skip:
            # 클라이언트가 이미 누끼를 떴다고 주장하는 경로 — 최소 검증만 한다.
            # 완전 투명한 PNG 가 그대로 Luma(유료)로 넘어가는 걸 막기 위함.
            validate_cutout_alpha(raw)
            dog_bytes = raw
        else:
            dog_bytes = build_dog_only_nobg_png_bytes(raw)
    except CutoutError as e:
        raise _cutout_error_response(cid, e) from e
    except Exception as e:
        logger.exception("generate-pet-video: cutout/preprocessing failed (cid=%s)", cid)
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        dog_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/dog_only_nobg.png", dog_bytes, "image/png"
        )
    except Exception as e:
        logger.exception(
            "generate-pet-video: Supabase upload of dog_only_nobg.png failed (cid=%s)", cid
        )
        raise HTTPException(
            status_code=503,
            detail=f"Luma용 이미지 URL이 필요합니다. Supabase(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)와 Storage 버킷을 설정하세요: {e}",
        ) from e

    # 프롬프트와 같은 판정(is_black_tan_dog)으로 keyframe 배경을 정해야 I2V가
    # 프롬프트가 요구한 배경을 그대로 유지함.
    lum_src = raw if not skip else dog_bytes

    # ── 정본 장면 ────────────────────────────────────────────────────────────
    # 있으면 **승인된 그림 자체**가 키프레임이다. 없으면 예전 단색 판(레거시).
    scene = await scene_input.resolve(
        scene_id=scene_id,
        background_type=background_type,
        background_id=background_id,
        scene_keyframe_url=scene_keyframe_url,
        background_baked=background_baked,
    )
    baked = scene is not None

    # ── 이미 만든 것이 있으면 다시 만들지 않는다 ─────────────────────────────
    # 이 엔드포인트는 동기식이라 클라이언트 타임아웃·새로고침·502 재시도가
    # 그대로 **두 번째 유료 작업**이 됐다. 장면이 있으면 (사용자, 장면, 행동)
    # 으로 기존 결과/진행 중 작업을 먼저 본다.
    if baked:
        # ⚠️ **fail closed.** 멱등성 저장소를 읽지 못하면 아무것도 제출하지 않는다.
        # 예전에는 여기서 None 으로 넘어가 생성을 계속했다 — 테이블이 잠깐 죽은
        # 동안 들어온 재시도가 각각 유료 작업을 만든다. 보호 장치 없이 진행하는
        # 것은 보호 장치가 없는 것과 같다.
        try:
            done = await scene_generation_jobs.get(user_id, scene.scene_id, "IDLE")
        except scene_generation_jobs.IdempotencyUnavailableError as e:
            raise _idempotency_unavailable(scene.scene_id) from e

        if done and done.completed:
            logger.warning(
                "generate-pet-video: 같은 장면의 완료된 IDLE 재사용 (scene=%s)", scene.scene_id
            )
            return {
                "success": True,
                "content_id": cid,
                "dog_only_nobg_url": dog_url,
                "idle_video_url": done.video_url,
                "action_video_url": None,
                "background_baked": True,
                "scene_id": scene.scene_id,
                "reused": True,
            }
        # 죽은 예약(제출 기록 없이 오래 남은 pending)은 한 번 회수한다.
        # 회수하지 않으면 그 장면은 영원히 생성 불가가 된다.
        if done and done.is_stale_reservation:
            try:
                if await scene_generation_jobs.reclaim_stale_reservation(
                    user_id=user_id, scene_id=scene.scene_id, behavior="IDLE"
                ):
                    done = None
            except scene_generation_jobs.IdempotencyUnavailableError as e:
                raise _idempotency_unavailable(scene.scene_id) from e

        if done and done.active and done.provider_job_id:
            recovered = await _recover_provider_job(done)
            if recovered:
                return {
                    "success": True,
                    "content_id": cid,
                    "dog_only_nobg_url": dog_url,
                    "idle_video_url": recovered,
                    "action_video_url": None,
                    "background_baked": True,
                    "scene_id": scene.scene_id,
                    "reused": True,
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "GENERATION_IN_PROGRESS",
                    "message": "같은 장면의 생성이 이미 진행 중입니다. 잠시 후 다시 확인해 주세요.",
                    "scene_id": scene.scene_id,
                },
            )

        # 아직 살아 있는 예약(신선한 pending, 또는 id 없는 submitted)이 남아 있다.
        # **재제출하지 않는다** — 지금 다른 요청이 제출 중일 수 있고, 그것이
        # 이중 과금의 마지막 입구다. 오래되면 위에서 회수된다.
        if done and done.active:
            logger.warning(
                "generate-pet-video: 진행 중인 예약이 있어 제출하지 않는다 "
                "(scene=%s status=%s provider_job=%s)",
                scene.scene_id, done.status, done.provider_job_id,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "GENERATION_IN_PROGRESS",
                    "message": "같은 장면의 생성이 이미 진행 중입니다. 잠시 후 다시 확인해 주세요.",
                    "scene_id": scene.scene_id,
                },
            )

    try:
        key_jpeg = build_keyframe_jpeg(
            dog_bytes,
            scene_bytes=(scene.scene_bytes if scene else None),
            luminance_source=lum_src,
        )
        key_name = f"scene_{scene.scene_id}.jpg" if scene else "luma_keyframe.jpg"
        key_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/{key_name}", key_jpeg, "image/jpeg"
        )
    except Exception as e:
        logger.exception(
            "generate-pet-video: keyframe flatten/upload failed (cid=%s)", cid
        )
        raise HTTPException(status_code=503, detail=str(e)) from e

    idle_prompt, action_prompt = build_idle_action_prompts(
        lum_src, background_baked=baked
    )
    poll_max_wait = float(os.getenv("LUMA_POLL_MAX_SEC", "1200"))

    if baked:
        # 자리를 먼저 잡는다. 제출 뒤에 잡으면 그 사이 요청이 또 제출한다.
        #
        # ⚠️ **예약에 실패하면 제출하지 않는다.** 예전에는 경고만 남기고 계속했다 —
        # 그 경로가 곧 "보호 없이 유료 생성"이다.
        try:
            _job, is_new = await scene_generation_jobs.reserve(
                user_id=user_id, scene_id=scene.scene_id, behavior="IDLE", content_id=cid
            )
        except scene_generation_jobs.IdempotencyUnavailableError as e:
            raise _idempotency_unavailable(scene.scene_id) from e

        if not is_new:
            # 위 검사와 이 예약 사이에 다른 요청이 자리를 잡았다(동시 요청).
            # 이긴 쪽이 제출한다 — 우리는 물러난다.
            logger.warning(
                "generate-pet-video: 예약 경합에서 졌다 — 제출하지 않는다 (scene=%s)",
                scene.scene_id,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "GENERATION_IN_PROGRESS",
                    "message": "같은 장면의 생성이 이미 진행 중입니다. 잠시 후 다시 확인해 주세요.",
                    "scene_id": scene.scene_id,
                },
            )

    idle_validation = None
    idle_validation_history: list = []
    max_idle_retries = int(os.getenv("IDLE_VALIDATION_MAX_RETRIES", "1"))

    async def _record_submit(provider_job_id: str) -> None:
        """제출 **직후** id 를 남긴다. 폴링 중 끊겨도 이 id 로 결과를 되찾는다."""
        if not baked:
            return
        try:
            from ..services.video_generation import get_video_provider

            await scene_generation_jobs.mark_submitted(
                user_id=user_id,
                scene_id=scene.scene_id,
                behavior="IDLE",
                provider=get_video_provider(),
                provider_job_id=str(provider_job_id),
            )
        except Exception:
            logger.warning("scene job 제출 기록 실패 — 복구가 불가능해진다", exc_info=True)

    try:
        idle_remote = None
        for idle_attempt in range(max_idle_retries + 1):
            idle_remote = await create_generation_and_get_video_url(
                key_url,
                idle_prompt,
                poll_max_wait=poll_max_wait,
                on_submit=_record_submit if baked else None,
            )
            idle_local_check = await download_video(idle_remote)
            try:
                with open(idle_local_check, "rb") as f:
                    idle_check_bytes = f.read()
            finally:
                try:
                    os.unlink(idle_local_check)
                except Exception:
                    pass

            # 레퍼런스는 **프로바이더가 실제로 본 그림**이어야 한다. 장면 경로에서
            # 누끼를 레퍼런스로 쓰면 배경이 있는 첫 프레임과 비교하게 되어 SSIM 이
            # 무너지고, 멀쩡한 영상이 실패로 판정돼 유료 재생성을 한 번 더 태운다.
            v = validate_idle_video(
                idle_check_bytes,
                key_jpeg if baked else dog_bytes,
                template_key="IDLE_BREATH",
            )
            idle_validation_history.append(v.to_dict())
            idle_validation = v
            if v.passed or idle_attempt >= max_idle_retries:
                break
            logger.warning(
                "generate-pet-video: idle validation failed (attempt %s/%s): %s",
                idle_attempt + 1,
                max_idle_retries + 1,
                v.message,
            )

        action_remote = (
            None
            if only_idle
            else await create_generation_and_get_video_url(
                key_url, action_prompt, poll_max_wait=poll_max_wait
            )
        )
    except Exception as e:
        # 이 자리에서 나는 502는 OOM이 아니라 Luma 쪽(키/쿼터/모더레이션/타임아웃) 문제일
        # 가능성이 높음 — 프론트의 friendlyPetVideoError()가 "서버 문제"로 뭉뚱그려 보여주므로
        # 실제 원인은 반드시 이 로그(Render 대시보드 Logs)에서 확인해야 함.
        logger.exception("generate-pet-video: Luma generation failed (cid=%s)", cid)
        if baked:
            # 실패는 종료 상태다 — 자리를 비워 다음 요청이 재시도할 수 있게 한다.
            # (진행 중인 작업을 막는 것과 실패한 작업을 막는 것은 다르다.)
            try:
                await scene_generation_jobs.mark_failed(
                    user_id=user_id, scene_id=scene.scene_id, behavior="IDLE", error=str(e)
                )
                await scene_generation_jobs.clear_for_retry(
                    user_id=user_id, scene_id=scene.scene_id, behavior="IDLE"
                )
            except Exception:
                logger.warning("scene job 실패 기록 실패", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Luma 생성 실패: {e}") from e

    idle_local = await download_video(idle_remote)
    action_local = await download_video(action_remote) if action_remote else None

    idle_url: str | None = None
    action_url: str | None = None
    loop_meta = None
    try:
        with open(idle_local, "rb") as f:
            idle_bytes = f.read()
        if _pet_video_seamless_loop_enabled():
            idle_bytes, loop_meta = make_seamless_loop_mp4(idle_bytes)
        else:
            # ffmpeg 재인코딩 OOM 회피 — 루프 없는 원본 Luma 영상을 그대로 사용.
            loop_meta = {"skipped": True, "reason": "pet_video_seamless_loop_disabled"}
        idle_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/idle_loop.mp4", idle_bytes, "video/mp4"
        )
        if action_local:
            with open(action_local, "rb") as f:
                action_bytes = f.read()
            action_url = await supabase_assets.upload_asset_to_storage(
                f"{user_id}/{cid}/action.mp4", action_bytes, "video/mp4"
            )
    except Exception as e:
        logger.exception(
            "generate-pet-video: post-process/upload of generated video failed (cid=%s)", cid
        )
        raise HTTPException(status_code=502, detail=f"영상 업로드 실패: {e}") from e
    finally:
        for p in (idle_local, action_local):
            try:
                if p and os.path.isfile(p):
                    os.unlink(p)
            except Exception:
                pass

    if baked:
        # 완료 기록. 같은 장면·같은 행동의 다음 요청은 여기서 바로 재사용된다.
        try:
            await scene_generation_jobs.mark_completed(
                user_id=user_id, scene_id=scene.scene_id, behavior="IDLE", video_url=idle_url
            )
        except Exception:
            logger.warning("scene job 완료 기록 실패", exc_info=True)

    return {
        "success": True,
        "content_id": cid,
        "dog_only_nobg_url": dog_url,
        "idle_video_url": idle_url,
        "action_video_url": action_url,
        # 재생 쪽이 배경을 **다시 합성하지 말아야** 한다는 신호. 레거시 자산은
        # 이 값이 없거나 false 이고, 그때만 기존 compose-video 경로를 탄다.
        "background_baked": baked,
        "scene_id": (scene.scene_id if scene else None),
        "idle_loop_meta": loop_meta,
        "idle_validation": idle_validation.to_dict() if idle_validation else None,
        "idle_validation_history": idle_validation_history,
        "prompts": {
            "idle": idle_prompt[:500],
            **({} if only_idle else {"action": action_prompt[:500]}),
        },
    }


@router.post("/generate-idle-variant")
async def post_generate_idle_variant(
    file: UploadFile = File(...),
    template_key: str = Form(...),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    # 프론트가 SAM2 누끼(/api/matting/cutout)를 먼저 호출해 이미 누끼딴 파일을
    # 보내는 경우 true(기본). false면 이 엔드포인트가 SAM2+ViTMatte로 직접 누끼.
    skip_preprocessing: str = Form("true"),
    max_retries: int = Form(2),
):
    """
    아이들(Idle) 5종 세트 중 template_key 1개를 생성.
    파이프라인: (SAM2 누끼, 필요 시) → Luma 생성 → mp4/블랙배경 검증(+재시도)
    → seamless loop(PET_VIDEO_SEAMLESS_LOOP=1일 때만) → Supabase 업로드.

    프론트(VideoGenerationService.ts)는 IDLE_TEMPLATE_ORDER 순서대로 이 엔드포인트를
    5번 순차 호출한다(SAM2 누끼는 최초 1회만 하고 나머지 4번은 skip_preprocessing=true).
    """
    if not is_known_template(template_key):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown template_key: {template_key!r}. Valid: {list(IDLE_TEMPLATE_ORDER)}",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="Empty file")

    cid = ((content_id or "").strip() or str(uuid.uuid4()))
    skip = str(skip_preprocessing).lower() in ("1", "true", "yes")

    try:
        if skip:
            validate_cutout_alpha(raw)
        dog_bytes = await _cutout_to_dog_bytes(raw, skip=skip)
    except CutoutError as e:
        raise _cutout_error_response(cid, e) from e
    except Exception as e:
        logger.exception(
            "generate-idle-variant(%s): cutout failed (cid=%s)", template_key, cid
        )
        raise HTTPException(status_code=500, detail=f"누끼 실패: {e}") from e

    try:
        dog_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/dog_only_nobg.png", dog_bytes, "image/png"
        )
        # 아이들 템플릿은 항상 "pure solid black void background"를 요구하므로
        # keyframe도 검정/흰색 중 하나로 맞춘다.
        # - 이 엔드포인트는 coat 기반 자동 판정을 쓰기 때문에 dog_bytes를 넣어 결정.
        # - 프론트 IdleLoopVideo는 near-black OR near-white 배경을 제거하도록 업데이트함.
        key_jpeg = flatten_rgba_to_jpeg_bytes(
            dog_bytes, bg_rgb=resolve_keyframe_bg_rgb(dog_bytes)
        )
        key_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/luma_keyframe.jpg", key_jpeg, "image/jpeg"
        )
    except Exception as e:
        logger.exception(
            "generate-idle-variant(%s): Supabase upload failed (cid=%s)", template_key, cid
        )
        raise HTTPException(
            status_code=503,
            detail=f"Luma용 이미지 URL이 필요합니다. Supabase(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)와 Storage 버킷을 설정하세요: {e}",
        ) from e

    try:
        variant = await generate_idle_variant(
            key_url,
            template_key,
            reference_image_bytes=dog_bytes,
            max_retries=max_retries,
        )
    except Exception as e:
        # OOM이 아니어도 Luma 쪽(키/쿼터/모더레이션/타임아웃) 문제로 502가 날 수 있음 —
        # 실제 원인은 Render 대시보드 Logs에서 이 traceback으로 확인.
        logger.exception(
            "generate-idle-variant(%s): Luma generation failed (cid=%s)", template_key, cid
        )
        raise HTTPException(status_code=502, detail=f"Luma 아이들 생성 실패: {e}") from e

    try:
        if _pet_video_seamless_loop_enabled():
            looped_bytes, loop_meta = make_seamless_loop_mp4(variant.video_bytes)
        else:
            # ffmpeg 재인코딩 OOM 회피 — 루프 없는 원본 Luma 영상을 그대로 사용.
            looped_bytes, loop_meta = variant.video_bytes, {
                "skipped": True,
                "reason": "pet_video_seamless_loop_disabled",
            }
        video_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/idle_{template_key.lower()}.mp4", looped_bytes, "video/mp4"
        )
    except Exception as e:
        logger.exception(
            "generate-idle-variant(%s): post-process/upload failed (cid=%s)", template_key, cid
        )
        raise HTTPException(status_code=502, detail=f"영상 업로드 실패: {e}") from e

    return {
        "success": True,
        "content_id": cid,
        "dog_only_nobg_url": dog_url,
        "template_key": template_key,
        "video_url": video_url,
        "is_mp4": variant.is_mp4,
        "is_black_background": variant.is_black_background,
        "background_luminance": variant.background_luminance,
        "retries_used": variant.retries_used,
        "loop_meta": loop_meta,
        "prompt": variant.prompt[:500],
        "validation": variant.validation.to_dict() if variant.validation else None,
        "validation_history": variant.validation_history,
    }
