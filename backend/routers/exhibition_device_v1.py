"""
/api/exhibition/device/pet/{visitor_number} — Exhibition 전용 PULL 엔드포인트.

워커가 READY 직후 자동으로 보내던 handoff(exhibition_prep_worker.py)는 제거됐다 —
이제 READY 는 "패키지가 준비됐다"만 뜻한다. 물리 디바이스(또는 그 옆의 컨트롤러)가
원하는 시점에 visitor 번호로 직접 당겨간다. 패키지 생성/큐 로직은 그대로다.

인증은 require_ops 가 아니다 — 이 호출자는 운영 직원 웹앱이 아니라 전시 디바이스에
물리적으로 연결된 컨트롤러다. 공개로도 두지 않는다 — 기존 Device Gateway WebSocket
(backend/services/device_gateway.py, backend/routers/device_ws_v1.py)과 **같은**
ETERNAL_BEAM_DEVICE_TOKENS 자격만 재사용한다. 새 디바이스 인증 체계를 만들지 않는다.

main.py 에서 ENABLE_EXHIBITION_PREP=1 일 때만 등록된다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..services import device_gateway
from ..services import exhibition_handoff_client as hc
from ..services import exhibition_prep_store as st
from ..services.exhibition_prep_service import package_id_for
from ..services.exhibition_queue_service import QueueError, normalize_exhibition_id
from .exhibition_prep_v1 import get_stores

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exhibition/device", tags=["exhibition-device"])


def require_device(device_id: str = Query(...), token: str = Query(...)) -> str:
    """Device Gateway WS 와 같은 자격 증명(device_id+token from ETERNAL_BEAM_DEVICE_TOKENS).

    어떤 device_id 가 존재하는지 알려주지 않으려 모르는 device_id/틀린 token 모두
    401 로 같다 (device_gateway.authenticate_device 와 동일한 실패 모드).
    """
    if not device_gateway.authenticate_device(device_id, token):
        raise HTTPException(
            status_code=401,
            detail={"code": "DEVICE_UNAUTHORIZED", "message": "Invalid device credentials."},
        )
    return device_id


def _visitor_number(raw: str) -> int:
    try:
        n = int(raw)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_VISITOR_NUMBER", "message": "visitor_number must be an integer."},
        ) from e
    if n < 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_VISITOR_NUMBER", "message": "visitor_number must not be negative."},
        )
    return n


def _exhibition_id(value: Optional[str]) -> str:
    try:
        return normalize_exhibition_id(value)
    except QueueError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e


@router.get("/pet/{visitor_number}")
async def get_pet_asset(
    visitor_number: str,
    exhibition_id: Optional[str] = Query(None),
    _device_id: str = Depends(require_device),
    stores=Depends(get_stores),
):
    """상태만 보되 아무것도 보내지 않는다 — Device Gateway/handoff 는 여기서 호출하지 않는다.

    NOT_FOUND(404) · WAITING(200) · FAILED(200, 진단용 error_code/review_reasons) ·
    READY(200, pet_asset — 요청 시점에 새로 서명한 packed_url).
    """
    store, artifacts = stores
    n = _visitor_number(visitor_number)
    package_id = package_id_for(n)
    eid = _exhibition_id(exhibition_id)

    row = await asyncio.to_thread(store.get_by_queue_number, eid, n)
    if not row:
        raise HTTPException(status_code=404, detail={"package_id": package_id, "status": "NOT_FOUND"})

    status = row.get("status")
    if status in (st.STATUS_FAILED, st.STATUS_NEEDS_REVIEW):
        return {
            "package_id": package_id,
            "status": "FAILED",
            "error_code": row.get("error_code"),
            "review_reasons": row.get("review_reasons"),
        }
    if status != st.STATUS_READY:
        return {"package_id": package_id, "status": "WAITING"}

    url_ttl_sec = hc.HandoffConfig.from_env().url_ttl_sec
    try:
        payload = await asyncio.to_thread(hc.build_payload, row, artifacts, url_ttl_sec=url_ttl_sec)
    except hc.HandoffError as e:
        if e.code == "HANDOFF_PACKAGE_UNAVAILABLE":
            # READY 인데 zip 이 아직 안 보이는 극히 드문 틈 — 재시도하면 될 일이니 WAITING.
            return {"package_id": package_id, "status": "WAITING"}
        logger.exception("exhibition device pet asset failed: package_id=%s code=%s", package_id, e.code)
        return {"package_id": package_id, "status": "FAILED", "error_code": e.code}

    return {"status": "READY", "pet_asset": payload}
