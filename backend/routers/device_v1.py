"""
/api/v1/device — Unity 하드웨어 조회
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from ..models.hybrid_business import DeviceSyncResponse
from ..services import generated_motions_service as motions_svc
from ..services import subscription_store_service as sub_store

router = APIRouter(prefix="/v1/device", tags=["device-v1"])

_DEVICE_404_DETAIL = "이 장소에 충전된 영혼(모션)이 없습니다. 앱에서 코인을 사용해 충전하세요."
_SUBSCRIPTION_403_DETAIL = (
  "subscription_not_entitled: 구독이 없거나 만료되었습니다. "
  "앱에서 스탠다드 구독을 갱신한 뒤 다시 시도하세요."
)


def _subscription_gate_enabled() -> bool:
  return os.getenv("SUBSCRIPTION_GATE_DEVICE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
  )


@router.get("/sync", response_model=DeviceSyncResponse)
async def device_sync(
  user_id: str = Query(..., description="NFC/계정에 연결된 사용자 ID"),
  place_id: str = Query(..., description="장착된 장소 카드 ID (slot·theme_key·place_key)"),
  pet_id: str | None = Query(None, description="반려동물 ID (없으면 user 기본 pet)"),
):
  """
  NFC 슬롯에 장소 카드가 꽂혔을 때 Unity가 호출.

  - 해당 장소에 IDLE/TOUCH/VOICE/NFC 4개 영상이 모두 있으면 JSON 배열 반환
  - 하나라도 없으면 404 → 앱에서 코인 충전 유도
  """
  uid = (user_id or "").strip()
  if not uid:
    raise HTTPException(400, detail="user_id is required")

  if _subscription_gate_enabled():
    sub = await sub_store.get_subscription(uid)
    if not sub_store.is_entitled(sub):
      raise HTTPException(status_code=403, detail=_SUBSCRIPTION_403_DETAIL)

  items = await motions_svc.get_device_sync_payload(uid, place_id, pet_id)
  if not items:
    raise HTTPException(status_code=404, detail=_DEVICE_404_DETAIL)

  pid = motions_svc.default_pet_id(uid, pet_id)
  from ..scenarios.pet_scenarios import place_public_id, resolve_place_id

  try:
    public_place = place_public_id(resolve_place_id(place_id))
  except ValueError:
    public_place = place_id.strip()

  return DeviceSyncResponse(
    user_id=uid,
    pet_id=pid,
    place_id=public_place,
    motions=items,
  )
