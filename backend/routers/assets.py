import base64
import binascii
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
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

    누끼(assets/cutout)와 같은 신뢰 계층(무료 파이프라인, user_id 폼 파라미터)에
    둔다 — 인테이크 시점에는 Supabase 세션이 아직 없을 수 있고, backend/auth.py
    는 레거시 무료 경로에 인증을 붙이지 않는다고 명시한다.

    같은 바이트의 재시도는 멱등하다(새 버전을 만들지 않는다). 저장 실패는 502 —
    "durable 하지 않은데 성공"으로 보이면 안 된다. 대장 행 기록 실패는
    reference_recorded=false 로 정직하게 보고한다(바이트는 이미 안전하다).
    """
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    if not uid or not cid:
        raise HTTPException(status_code=400, detail="user_id and content_id are required")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="file is empty")
    if len(raw) > ORIGINAL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="original image exceeds the size limit")

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

    # ── (옵션) 원본별 누끼 첨부 — 파생으로 저장, 원본에는 손대지 않는다 ──
    cutout_recorded: bool | None = None
    if cutout_file is not None:
        cutout_recorded = False
        try:
            cut_raw = await cutout_file.read()
            if cut_raw and ref.recorded and ref.content_hash:
                cut_path = f"{uid}/{cid}/references/cutout_{ref.content_hash[:16]}.png"
                await supabase_assets.upload_asset_to_storage(cut_path, cut_raw, "image/png")
                derived = await pet_reference_service.record_derived(
                    user_id=uid,
                    content_id=cid,
                    object_path=cut_path,
                    derived_kind="cutout_reference",
                    parent_reference_id=ref.id,
                    mime_type="image/png",
                )
                cutout_recorded = derived.recorded
        except Exception:
            logger.warning("persist-original: 누끼 첨부 실패 (cid=%s)", cid, exc_info=True)

    if _identity_autobuild_enabled() and ref.recorded:
        background_tasks.add_task(_autobuild_identity_profile, uid, ref.pet_id)

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
