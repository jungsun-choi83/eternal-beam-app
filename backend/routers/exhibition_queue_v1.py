"""
/api/exhibition/staff/* (스태프 전용) · /api/exhibition/queue (공개) — 전시 대기열.

  POST /exhibition/staff/submissions      사진 + 이름(선택) → 번호 배정 + 처리 실행 생성
  GET  /exhibition/staff/queue            스태프 화면 (폴링; 비어 있는 UP_NEXT 를 채운다)
  POST /exhibition/staff/queue/show-next  SHOW NEXT (수동 — 외부 기기 신호가 아직 없다)
  GET  /exhibition/queue                  공개 화면: NOW SHOWING + UP NEXT (번호·이름만, 읽기 전용)

처리(누끼·맵·패키지·핸드오프)는 기존 exhibition_prep 워커가 그대로 한다.
main.py 에서 ENABLE_EXHIBITION_PREP=1 일 때만 등록된다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from ..auth import AuthedUser
from ..services import exhibition_prep_service
from ..services import exhibition_queue_service as queue
from ..services.shaker_ops import require_ops
from .exhibition_prep_v1 import get_stores

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exhibition", tags=["exhibition-queue"])


def _http(e: queue.QueueError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _exhibition_id(value: Optional[str]) -> str:
    try:
        return queue.normalize_exhibition_id(value)
    except queue.QueueError as e:
        raise _http(e) from e


@router.post("/staff/submissions")
async def post_submission(
    file: UploadFile = File(...),
    pet_name: Optional[str] = Form(None),
    exhibition_id: Optional[str] = Form(None),
    ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    store, artifacts = stores
    eid = _exhibition_id(exhibition_id)
    raw = await file.read()
    try:
        row = await asyncio.to_thread(
            queue.submit, raw, created_by=ops.user_id, store=store, artifacts=artifacts,
            exhibition_id=eid, pet_name=pet_name,
        )
    except exhibition_prep_service.ExhibitionInputError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("exhibition staff submission failed")
        raise HTTPException(
            status_code=503, detail={"code": "EXHIBITION_SUBMISSION_FAILED", "message": "Could not submit photo."}
        ) from e
    return {
        "run_id": row["id"],
        "exhibition_id": row["exhibition_id"],
        "queue_number": row["queue_number"],
        "pet_name": row.get("pet_name"),
        "created_at": row.get("created_at"),
        "display_status": row["display_status"],
        "processing_status": queue.processing_status(row),
    }


@router.get("/staff/queue")
async def get_staff_queue(
    response: Response,
    exhibition_id: Optional[str] = Query(None),
    _ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    store, _artifacts = stores
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(queue.staff_view, _exhibition_id(exhibition_id), store=store)


class ShowNextBody(BaseModel):
    exhibition_id: Optional[str] = None
    #: 스태프 화면이 본 NOW SHOWING / UP NEXT 의 run_id (없으면 null). 두 번 누름 방지.
    expected_now_showing_run_id: Optional[str] = None
    expected_up_next_run_id: Optional[str] = None


@router.post("/staff/queue/show-next")
async def post_show_next(
    body: ShowNextBody,
    _ops: AuthedUser = Depends(require_ops),
    stores=Depends(get_stores),
):
    store, _artifacts = stores
    try:
        return await asyncio.to_thread(
            queue.show_next,
            _exhibition_id(body.exhibition_id),
            expected_now_showing=body.expected_now_showing_run_id,
            expected_up_next=body.expected_up_next_run_id,
            store=store,
        )
    except queue.QueueError as e:
        raise _http(e) from e


@router.get("/queue")
async def get_public_queue(
    response: Response,
    exhibition_id: Optional[str] = Query(None),
    stores=Depends(get_stores),
):
    """인증 없음 — 번호와 이름만. 관리 기능 없음."""
    store, _artifacts = stores
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(queue.public_view, _exhibition_id(exhibition_id), store=store)
