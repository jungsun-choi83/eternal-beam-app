import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ..services import pet_cutout_service, pet_reference_service, supabase_assets

logger = logging.getLogger(__name__)
router = APIRouter()

#: 원본 인테이크 크기 상한. 파이프라인 입력이 아니라 증거 보존이므로 넉넉하게 —
#: 다만 병리적 업로드가 무료 경로를 막지 않도록 상한은 둔다.
ORIGINAL_MAX_BYTES = 40 * 1024 * 1024


@router.get("/purchased-slots")
async def get_purchased_slots(user_id: str = Query("anonymous")):
    themes = await supabase_assets.get_purchased_themes(user_id)
    return {"theme_ids": themes}


class PersistCutoutBody(BaseModel):
    user_id: str
    content_id: str
    #: 이미 만들어진 누끼 PNG 의 data: URL (또는 순수 base64).
    data_url: str


async def _strict_multi_reference_pet(user_id: str, content_id: str) -> bool:
    """
    이 펫이 이미 **엄격한 멀티 레퍼런스 인테이크**를 탔거나 **잠겼는가**.

    원본이 2장 이상이거나, parent 로 묶인 cutout_reference 가 하나라도 있으면
    참이다. 그런 펫에는 부모 없는 누끼를 더 이상 붙이지 않는다 (아래 참조).
    대장을 못 읽으면 보수적으로 참 — 모호한 행을 만드는 쪽이 더 나쁘다.
    """
    try:
        ledger = await pet_reference_service.list_references(
            user_id=user_id, pet_id=pet_reference_service.pet_id_for_content(content_id)
        )
    except pet_reference_service.PetReferenceError:
        return True

    originals = sum(1 for r in ledger if r.role == pet_reference_service.ROLE_ORIGINAL)
    parented = any(
        r.role == pet_reference_service.ROLE_DERIVED and r.parent_reference_id for r in ledger
    )
    if originals > 1 or parented:
        return True
    # 잠긴 펫(생성이 시작됨)에도 새 누끼 행을 남기지 않는다. 판정 불가는 잠김으로 본다.
    try:
        return await pet_reference_service.pet_inputs_locked(
            pet_reference_service.pet_id_for_content(content_id), ledger
        )
    except pet_reference_service.PetReferenceError:
        return True


@router.post("/assets/cutout")
async def post_persist_cutout(body: PersistCutoutBody):
    """
    **이미 만들어진** 누끼 PNG 를 스토리지에 1회 저장하고 원격 URL 을 돌려준다.

    왜 필요한가: 웹 플로우는 누끼를 `save_to_storage=false` 로 뽑아 브라우저
    안에서 data: URL 로만 들고 있었다(ai-processing-screen). 그래서 백엔드가
    가져갈 수 있는 원격 URL 이 존재하지 않았고, COME_CLOSER 제출이
    stage="download" 로 실패했다.

    누끼를 다시 뽑지 않는다 — 바이트를 그대로 올리기만 한다. 경로는
    generate.py 가 쓰는 것과 **같은 규칙**이라 이후 조회가 일관된다.
    """
    uid = (body.user_id or "").strip()
    cid = (body.content_id or "").strip()
    if not uid or not cid:
        raise HTTPException(status_code=400, detail="user_id and content_id are required")

    raw = (body.data_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="data_url is required")
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]

    try:
        png = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"data_url is not valid base64: {e}") from e
    if not png:
        raise HTTPException(status_code=400, detail="decoded image is empty")

    # generate.py:99 과 동일한 경로 규칙 — 같은 펫이 두 경로에서 같은 곳을 가리킨다.
    path = f"{uid}/{cid}/dog_only_nobg.png"
    try:
        url = await supabase_assets.upload_asset_to_storage(path, png, "image/png")
    except Exception as e:
        logger.exception("persist-cutout: storage upload failed (cid=%s)", cid)
        raise HTTPException(status_code=502, detail=f"storage upload failed: {e}") from e
    if not url:
        raise HTTPException(status_code=502, detail="storage returned an empty URL")

    await supabase_assets.ensure_user_asset_row(uid, cid, "cutout", url, None)

    # 파생 레퍼런스 기록 (Durable Pet Identity Intake). 실패해도 기존 플로우를
    # 막지 않는다 — 이 경로의 계약(누끼 원격 URL 확보)은 그대로다.
    #
    # 단, 이 경로는 **부모 없는** 누끼를 남긴다(어떤 원본에서 나왔는지 모른다).
    # 엄격한 멀티 레퍼런스 인테이크가 이미 선 펫에는 기록하지 않는다 — 원본이
    # 여러 장인데 부모 없는 누끼가 섞이면 "원본 N ↔ 누끼 N" 이 무너진다.
    # 바이트 저장과 cutout_url 반환은 그대로이므로 이 경로의 계약은 불변이다.
    try:
        if await _strict_multi_reference_pet(uid, cid):
            logger.info(
                "persist-cutout: 엄격 인테이크 펫이라 부모 없는 누끼는 대장에 남기지 않는다 (cid=%s)",
                cid,
            )
        else:
            await pet_reference_service.record_derived(
                user_id=uid,
                content_id=cid,
                object_path=path,
                derived_kind="cutout_client",
                mime_type="image/png",
            )
    except Exception:
        logger.warning("persist-cutout: 파생 레퍼런스 기록 실패 (cid=%s)", cid, exc_info=True)

    return {"user_id": uid, "content_id": cid, "cutout_url": url, "bytes": len(png)}


def _identity_autobuild_enabled() -> bool:
    """
    인테이크 직후 신원 프로필 자동 빌드 (Phase 2). 기본 꺼짐 — Render 512MB
    관례(무거운 작업은 opt-in). 켜져 있어도 fail-open: 분석 실패가 온보딩을
    절대 막지 않는다.
    """
    return os.getenv("IDENTITY_PROFILE_AUTOBUILD", "0").strip().lower() in ("1", "true", "yes")


async def _autobuild_identity_profile(user_id: str, pet_id: str) -> None:
    try:
        from ..services import pet_identity_service

        await pet_identity_service.build_identity_profile(user_id=user_id, pet_id=pet_id)
    except Exception:
        logger.warning("identity autobuild 실패 (pet=%s) — 온보딩에는 영향 없음", pet_id, exc_info=True)


@router.post("/assets/original")
async def post_persist_original(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Form(...),
    content_id: str = Form(...),
    diagnostics_json: str | None = Form(None),
    phase1_intake: str = Form("false"),
    authorization: str = Header(default=""),
    cutout_file: UploadFile | None = File(None),
):
    """
    원본(+누끼) 인테이크. 본문은 _persist_original 이다.

    요청 전체를 펫 입력 문의 **공유** 구간에서 처리한다: 잠금 확인과 대장 쓰기
    사이에 생성 실행이 만들어질 수 없다(pet_reference_service._PetInputGate).
    같은 펫의 여러 사진은 여전히 동시에 처리된다.
    """
    pet_id = pet_reference_service.pet_id_for_content((content_id or "").strip())
    async with pet_reference_service.pet_input_gate(pet_id).shared():
        return await _persist_original(
            background_tasks=background_tasks,
            file=file,
            user_id=user_id,
            content_id=content_id,
            diagnostics_json=diagnostics_json,
            phase1_intake=phase1_intake,
            authorization=authorization,
            cutout_file=cutout_file,
        )


async def _persist_original(
    *,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    user_id: str,
    content_id: str,
    diagnostics_json: str | None,
    phase1_intake: str,
    authorization: str,
    # (Phase 3, 옵션) 이 원본에 짝지어진 누끼 RGBA PNG. 멀티 레퍼런스에서
    # 원본별 세그멘테이션을 붙이는 최소 메커니즘이다 — 파생 레퍼런스로 저장되고
    # parent_reference_id 로 원본에 연결된다. 없으면 기존 동작과 완전히 같다.
    cutout_file: UploadFile | None,
):
    """
    **사용자 제공 원본**을 영구 보존한다 (Durable Pet Identity Intake, Phase 1).

    왜 필요한가: 원본 사진은 지금까지 브라우저 상태에만 있었다. 서버 누끼가 받는
    파일조차 normalize 로 축소된 사본이라, 원본 해상도 증거는 어디에도 남지
    않았다. 여기서 원본 바이트를 그대로 올리고 pet_reference_images 에 version 1
    레퍼런스로 기록한다 — 이후 신원 파이프라인의 출발점이다.

    Phase 7B 클라이언트는 phase1_intake=true 와 Bearer 토큰을 보내며, 이 경우
    user_id 는 검증된 Eternal Beam 신원과 반드시 같아야 한다. 플래그 없는 요청은
    기존 Phase 1/테스트 호출의 호환 계약으로만 유지한다.

    같은 바이트의 재시도는 멱등하다(새 버전을 만들지 않는다). 저장 실패는 502 —
    "durable 하지 않은데 성공"으로 보이면 안 된다. 대장 행 기록 실패는
    reference_recorded=false 로 정직하게 보고한다(바이트는 이미 안전하다).
    """
    strict_intake = str(phase1_intake or "").strip().lower() in ("1", "true", "yes")
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    if not uid or not cid:
        raise HTTPException(status_code=400, detail="user_id and content_id are required")

    if strict_intake:
        from ..auth import require_user

        authed = await require_user(authorization)
        if uid != authed.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "INTAKE_IDENTITY_MISMATCH",
                    "message": "업로드 신원과 인증된 사용자가 일치하지 않습니다.",
                },
            )
        uid = authed.user_id

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="file is empty")
    if len(raw) > ORIGINAL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="original image exceeds the size limit")

    # 생성이 시작된 펫은 잠겨 있다 — 원본도 누끼도 더 받지 않는다 (409
    # PHASE1_LOCKED). 소유권 확인(list_references)이 먼저다: 남의 펫의 잠금
    # 상태를 알려 주지 않는다. 잠금을 판정할 수 없으면 503 으로 닫는다.
    try:
        existing_refs = await pet_reference_service.list_references(
            user_id=uid,
            pet_id=pet_reference_service.pet_id_for_content(cid),
        )
        await pet_reference_service.assert_pet_inputs_unlocked(
            pet_reference_service.pet_id_for_content(cid), existing_refs
        )
    except pet_reference_service.PetReferenceError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    # A stable content_id represents one pet, not one photo. Intake accepts
    # 1..MAX_ORIGINALS_PER_PET distinct *active* originals for that pet; a retry
    # repeating the same bytes still dedupes to the existing row and does not
    # spend a slot.
    if strict_intake:
        incoming_hash = hashlib.sha256(raw).hexdigest()
        # 살아 있는(accepted) 원본만 자리를 차지한다. 사용자가 뺀 사진은 세지
        # 않고, 뺐던 바이트가 다시 오면 자리가 남아 있는 한 되살아난다.
        active_hashes = {
            r.content_hash
            for r in pet_reference_service.active_originals(existing_refs)
            if r.content_hash
        }
        if (
            incoming_hash not in active_hashes
            and len(active_hashes) >= pet_reference_service.MAX_ORIGINALS_PER_PET
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PHASE1_ORIGINAL_LIMIT",
                    "message": (
                        "한 펫에 등록할 수 있는 원본은 최대 "
                        f"{pet_reference_service.MAX_ORIGINALS_PER_PET}장입니다."
                    ),
                },
            )

    diagnostics = None
    if diagnostics_json:
        try:
            parsed = json.loads(diagnostics_json)
            diagnostics = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            diagnostics = None  # 진단은 부가 정보 — 깨진 JSON 이 인테이크를 막지 않는다

    # 원본 스토리지 업로드+대장 기록(record_original)이 나가는 동안 누끼
    # 멀티파트 바디를 미리 읽어 둔다 — 둘 다 이 핸들러 안의 순수 I/O이고
    # 서로의 결과에 의존하지 않으므로(누끼 저장 경로 자체는 ref.content_hash
    # 가 필요하지만, "바이트를 읽는 것"은 필요 없다) 겹쳐도 안전하다. 실패하면
    # 아래 누끼 처리 블록에서 원래와 똑같이 처리한다(먼저 읽었을 뿐 예외 경로는
    # 그대로다).
    cutout_read_task: "asyncio.Task[bytes] | None" = (
        asyncio.ensure_future(cutout_file.read()) if cutout_file is not None else None
    )

    try:
        ref = await pet_reference_service.record_original(
            user_id=uid,
            content_id=cid,
            data=raw,
            mime_type=(file.content_type or None),
            original_filename=(file.filename or None),
            source=pet_reference_service.SOURCE_APP,
            diagnostics=diagnostics,
        )
    except pet_reference_service.PetReferenceError as e:
        if cutout_read_task is not None:
            cutout_read_task.cancel()
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e
    except Exception as e:
        if cutout_read_task is not None:
            cutout_read_task.cancel()
        logger.exception("persist-original: storage upload failed (cid=%s)", cid)
        raise HTTPException(status_code=502, detail=f"storage upload failed: {e}") from e

    if strict_intake and (not ref.recorded or not ref.id):
        if cutout_read_task is not None:
            cutout_read_task.cancel()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PHASE1_LEDGER_UNAVAILABLE",
                "message": "원본 레퍼런스를 대장에 기록하지 못했습니다.",
            },
        )

    # ── (옵션) 원본별 누끼 첨부 — 파생으로 저장, 원본에는 손대지 않는다 ──
    cutout_recorded: bool | None = None
    cutout_reference_id: str | None = None
    cutout_object_path: str | None = None
    if cutout_file is not None:
        cutout_recorded = False
        try:
            cut_raw = await cutout_read_task
            if cut_raw and ref.recorded and ref.content_hash:
                derived = await pet_cutout_service.record_strict_cutout(
                    user_id=uid,
                    content_id=cid,
                    original=ref,
                    cutout_png=cut_raw,
                    diagnostics=diagnostics,
                )
                cutout_recorded = derived.recorded
                cutout_reference_id = derived.id
                cutout_object_path = derived.object_path
            elif strict_intake:
                raise pet_reference_service.PetReferenceError(
                    "PHASE1_CUTOUT_EMPTY", "누끼 파일이 비어 있습니다.", status=400
                )
        except pet_reference_service.PetReferenceError as e:
            if strict_intake:
                raise HTTPException(
                    status_code=e.status, detail={"code": e.code, "message": e.message}
                ) from e
            logger.warning("persist-original: 누끼 첨부 실패 (cid=%s)", cid, exc_info=True)
        except Exception as e:
            if strict_intake:
                logger.exception("persist-original: 누끼 첨부 실패 (cid=%s)", cid)
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "PHASE1_CUTOUT_PERSIST_FAILED",
                        "message": f"누끼 레퍼런스를 저장하지 못했습니다: {e}",
                    },
                ) from e
            logger.warning("persist-original: 누끼 첨부 실패 (cid=%s)", cid, exc_info=True)

    # Phase 7B ends at intake-ready. The new authoritative path must not invoke
    # Phase 2 even if the legacy opt-in environment switch happens to be on.
    if not strict_intake and _identity_autobuild_enabled() and ref.recorded:
        background_tasks.add_task(_autobuild_identity_profile, uid, ref.pet_id)

    # 응답은 **이번 요청의 원본**을 말해야 한다. 펫 전체의 "아무 짝이나 하나"를
    # 돌려주면 2·3번째 사진이 1번째의 누끼를 자기 것으로 보고받고, 클라이언트의
    # 장별 검증이 거짓으로 통과한다.
    ledger = await pet_reference_service.list_references(user_id=uid, pet_id=ref.pet_id)
    paired = pet_reference_service.strict_cutout_for_original(ledger, ref.id)
    if paired:
        cutout_reference_id = paired.id
        cutout_object_path = paired.object_path
    intake_ready = bool(
        paired
        and ref.recorded
        and ref.acceptance_state == pet_reference_service.STATE_ACCEPTED
    )

    return {
        "user_id": uid,
        "content_id": cid,
        "pet_id": ref.pet_id,
        "reference_id": ref.id,
        "object_path": ref.object_path,
        "version": ref.version,
        "bytes": len(raw),
        "reference_recorded": ref.recorded,
        "deduplicated": ref.deduplicated,
        "reactivated": ref.reactivated,
        "intake_ready": intake_ready,
        "cutout_reference_id": cutout_reference_id,
        "cutout_object_path": cutout_object_path,
        **({"cutout_recorded": cutout_recorded} if cutout_recorded is not None else {}),
    }


class PersistSceneBody(BaseModel):
    user_id: str
    content_id: str
    #: 장면 식별자. 저장 경로에 들어가므로 같은 장면은 같은 객체로 수렴한다.
    scene_id: str
    #: 합성된 장면 PNG 의 data: URL (또는 순수 base64).
    data_url: str


@router.post("/assets/scene")
async def post_persist_scene(body: PersistSceneBody):
    """
    승인된 **정본 장면** 이미지를 저장하고 원격 URL 을 돌려준다.

    프로바이더는 URL 로만 이미지를 받는다(data: URL 을 받지 않는다). 그래서 장면을
    브라우저에서 합성했더라도 생성에 쓰려면 한 번은 올라와야 한다.

    ── 경로에 scene_id 를 넣는 이유 ────────────────────────────────────────
    같은 장면을 두 번 승인하면 **같은 객체**를 덮어쓴다. 승인할 때마다 새 파일이
    쌓이면 어느 것이 생성에 쓰인 그림인지 나중에 알 수 없고, 재인쇄·재생성에서
    "그때 그 그림"을 되찾지 못한다.

    누끼 저장(assets/cutout)과 **같은 규칙**을 쓴다 — 바이트를 그대로 올리기만
    하고, 여기서 합성하거나 다시 그리지 않는다.
    """
    uid = (body.user_id or "").strip()
    cid = (body.content_id or "").strip()
    sid = (body.scene_id or "").strip()
    if not uid or not cid or not sid:
        raise HTTPException(
            status_code=400, detail="user_id, content_id and scene_id are required"
        )

    raw = (body.data_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="data_url is required")
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]

    try:
        png = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"data_url is not valid base64: {e}") from e
    if not png:
        raise HTTPException(status_code=400, detail="decoded image is empty")

    path = f"{uid}/{cid}/scene/{sid}.png"
    try:
        url = await supabase_assets.upload_asset_to_storage(path, png, "image/png")
    except Exception as e:
        logger.exception("persist-scene: storage upload failed (cid=%s scene=%s)", cid, sid)
        raise HTTPException(status_code=502, detail=f"storage upload failed: {e}") from e
    if not url:
        raise HTTPException(status_code=502, detail="storage returned an empty URL")

    await supabase_assets.ensure_user_asset_row(uid, cid, "scene", url, None)
    return {
        "user_id": uid,
        "content_id": cid,
        "scene_id": sid,
        "scene_keyframe_url": url,
        "bytes": len(png),
    }
