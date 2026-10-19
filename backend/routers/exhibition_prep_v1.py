"""
/api/exhibition/prep-runs — 전시 준비 실행 (운영자 전용, 일반 펫 생성과 분리).

POST 는 원본을 exhibition/{run_id}/source.png 에 올리고 QUEUED 행만 만든 뒤
즉시 반환한다. 무거운 처리(ViTMatte 누끼 + 맵)는
`python -m backend.workers.exhibition_prep_worker` 가 가져간다 — 512MB 웹
인스턴스에서 모델을 올리지 않기 위해서다.

POST /{run_id}/handoff 는 READY 패키지를 외부 전시 시스템에 (다시) 보낸다.
워커가 READY 직후 한 번 자동으로 보내고, 실패하면 운영자가 이 엔드포인트로
재시도한다 — 저장된 패키지를 다시 보낼 뿐 CUTOUT/MAPS 는 다시 돌지 않는다.

main.py 에서 ENABLE_EXHIBITION_PREP=1 일 때만 등록된다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..auth import AuthedUser
from ..services import exhibition_prep_service, exhibition_prep_store, exhibition_queue_service
from ..services.shaker_ops import require_ops

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exhibition/prep-runs", tags=["exhibition-prep"])


def get_stores() -> tuple[exhibition_prep_store.RunStore, exhibition_prep_store.ArtifactStore]:
    try:
        return exhibition_prep_store.default_stores()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "EXHIBITION_STORE_UNAVAILABLE", "message": str(e)},
        ) from e


@router.post("")
async def post_create_run(
    file: UploadFile = File(...),
    exhibition_id: Optional[str] = Form(None),
    head_hint_x: Optional[float] = Form(None),
    head_hint_y: Optional[float] = Form(None),
    ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    """사진 1장 → QUEUED 실행. head_hint_x/y 는 원본(EXIF 회전 반영) 픽셀 좌표(선택)."""
    store, artifacts = stores
    raw = await file.read()
    hint = None
    if head_hint_x is not None and head_hint_y is not None:
        hint = (float(head_hint_x), float(head_hint_y))
    try:
        row = await asyncio.to_thread(
            exhibition_prep_service.create_run,
            raw,
            created_by=ops.user_id,
            store=store,
            artifacts=artifacts,
            exhibition_id=exhibition_id,
            head_hint_xy=hint,
        )
    except exhibition_prep_service.ExhibitionInputError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("exhibition prep run creation failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "EXHIBITION_RUN_CREATE_FAILED", "message": "Could not create run."},
        ) from e
    return {"run_id": row["id"], "status": row["status"]}


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    _ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    store, artifacts = stores
    row = await asyncio.to_thread(store.get, run_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "EXHIBITION_RUN_NOT_FOUND"})
    return await asyncio.to_thread(exhibition_prep_service.public_view, row, artifacts)


_HANDOFF_STATE_HTTP = {
    "EXHIBITION_RUN_NOT_FOUND": 404,
    "HANDOFF_PACKAGE_NOT_READY": 409,
    "HANDOFF_IN_PROGRESS": 409,
    "HANDOFF_NOT_CONFIGURED": 503,
}


@router.post("/{run_id}/handoff")
async def post_handoff(
    run_id: str,
    _ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    """READY 패키지 핸드오프 (재시도 포함). 이미 HANDOFF_CONFIRMED 면 다시 보내지 않고 현재 상태를 돌려준다.

    전송 실패(타임아웃·5xx·네트워크)는 200 + handoff.status=HANDOFF_FAILED 로 돌려준다 —
    요청 자체는 처리됐고 결과가 실패다.
    """
    store, artifacts = stores
    try:
        row = await asyncio.to_thread(
            exhibition_prep_service.hand_off_run, run_id, store=store, artifacts=artifacts
        )
    except exhibition_prep_service.HandoffStateError as e:
        raise HTTPException(
            status_code=_HANDOFF_STATE_HTTP.get(e.code, 409), detail={"code": e.code, "message": e.message}
        ) from e
    if row.get("queue_number") is not None and row.get("exhibition_id"):
        # 재시도로 핸드오프가 확인됐으면 전시 대기열 자격이 생겼을 수 있다.
        await asyncio.to_thread(exhibition_queue_service.promote_up_next, row["exhibition_id"], store=store)
    return await asyncio.to_thread(exhibition_prep_service.public_view, row, artifacts)
