import base64
import binascii
import hashlib
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ..services import pet_reference_service, supabase_assets

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
    try:
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
    # (Phase 3, 옵션) 이 원본에 짝지어진 누끼 RGBA PNG. 멀티 레퍼런스에서
    # 원본별 세그멘테이션을 붙이는 최소 메커니즘이다 — 파생 레퍼런스로 저장되고
    # parent_reference_id 로 원본에 연결된다. 없으면 기존 동작과 완전히 같다.
    cutout_file: UploadFile | None = File(None),
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

    # A stable content_id represents one upload. A retry may repeat the same
    # bytes, but must not silently turn that identity into a different original.
    if strict_intake:
        try:
            existing_refs = await pet_reference_service.list_references(
                user_id=uid,
                pet_id=pet_reference_service.pet_id_for_content(cid),
            )
        except pet_reference_service.PetReferenceError as e:
            raise HTTPException(
                status_code=e.status, detail={"code": e.code, "message": e.message}
            ) from e
        incoming_hash = hashlib.sha256(raw).hexdigest()
        conflicting = any(
            r.role == pet_reference_service.ROLE_ORIGINAL
            and r.content_hash
            and r.content_hash != incoming_hash
            for r in existing_refs
        )
        if conflicting:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PHASE1_ORIGINAL_CONFLICT",
                    "message": "같은 업로드 식별자에 다른 원본을 연결할 수 없습니다.",
                },
            )

    diagnostics = None
    if diagnostics_json:
        try:
            parsed = json.loads(diagnostics_json)
            diagnostics = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            diagnostics = None  # 진단은 부가 정보 — 깨진 JSON 이 인테이크를 막지 않는다

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
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e
    except Exception as e:
        logger.exception("persist-original: storage upload failed (cid=%s)", cid)
        raise HTTPException(status_code=502, detail=f"storage upload failed: {e}") from e

    if strict_intake and (not ref.recorded or not ref.id):
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
            cut_raw = await cutout_file.read()
            if cut_raw and ref.recorded and ref.content_hash:
                cut_path = f"{uid}/{cid}/references/cutout_{ref.content_hash[:16]}.png"
                cut_hash = hashlib.sha256(cut_raw).hexdigest()
                ledger = await pet_reference_service.list_references(
                    user_id=uid, pet_id=ref.pet_id
                )
                existing_cutout = next(
                    (
                        r
                        for r in ledger
                        if r.role == pet_reference_service.ROLE_DERIVED
                        and r.object_path == cut_path
                    ),
                    None,
                )
                if existing_cutout:
                    prior_hash = str((existing_cutout.diagnostics or {}).get("content_hash") or "")
                    if existing_cutout.parent_reference_id != ref.id or (
                        prior_hash and prior_hash != cut_hash
                    ):
                        raise pet_reference_service.PetReferenceError(
                            "PHASE1_CUTOUT_CONFLICT",
                            "같은 원본에 다른 누끼를 연결할 수 없습니다.",
                            status=409,
                        )
                    derived = existing_cutout
                else:
                    await supabase_assets.upload_asset_to_storage(cut_path, cut_raw, "image/png")
                    derived = await pet_reference_service.record_derived(
                        user_id=uid,
                        content_id=cid,
                        object_path=cut_path,
                        derived_kind="cutout_reference",
                        parent_reference_id=ref.id,
                        mime_type="image/png",
                        diagnostics={
                            **(diagnostics or {}),
                            "content_hash": cut_hash,
                            "bytes_size": len(cut_raw),
                        },
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

    ledger = await pet_reference_service.list_references(user_id=uid, pet_id=ref.pet_id)
    intake_ready, _, ready_cutout = pet_reference_service.intake_readiness(ledger)
    if ready_cutout:
        cutout_reference_id = ready_cutout.id
        cutout_object_path = ready_cutout.object_path

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
