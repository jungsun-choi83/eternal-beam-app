"""
/api/v1/pet/registry — canonical 펫 등록 (Phase 13.2).

    POST /register        고객 앱이 BREATHING 파이프라인 완료 후 호출
    GET  /mine            내 펫들

── 왜 별도 라우터인가 ──────────────────────────────────────────────────────
생성 라우터(routers/generate.py)를 건드리지 않기 위해서다. 생성 로직은 그대로 두고,
**완료된 뒤** 앱이 결과를 등록한다. 그래서 프로바이더·큐·프롬프트가 한 줄도
바뀌지 않는다.

이 라우터는 생성하지 않는다. 프리미엄도 구독도 요구하지 않는다 —
**BREATHING 하나만 있어도 펫은 등록된다.**
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import pet_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/registry", tags=["pet-registry"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


class RegisterRequest(BaseModel):
    #: canonical petId ("pet_" + content_id).
    pet_id: str
    content_id: str | None = None
    #: 방금 생성된 BREATHING URL. 서버가 여기서 **스토리지 경로만** 뽑아 쓴다 —
    #: 서명 URL 자체는 만료되므로 저장하지 않는다.
    breathing_url: str


class PetOut(BaseModel):
    pet_id: str
    content_id: str | None = None
    #: BREATHING 이 확인됐는가 (등록된 펫은 항상 true).
    breathing_registered: bool = True


class PetsResponse(BaseModel):
    pets: list[PetOut] = []


@router.post("/register", response_model=PetOut)
async def register_pet(
    body: RegisterRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    펫을 canonical 레지스트리에 등록한다. **멱등하다.**

    이 호출이 있어야 운영 콘솔에서 펫이 보인다 — 멤버십도 프리미엄 모션도 필요 없다.
    BREATHING 이 스토리지에 실제로 없으면 거절한다(없는 펫에 QR 이 붙지 않도록).

    소유자는 **토큰에서** 확정한다. 바디의 값은 쓰지 않는다.
    """
    try:
        pet = await pet_registry.register(
            user_id=user.user_id,
            pet_id=body.pet_id,
            content_id=body.content_id,
            breathing_url=body.breathing_url,
            source=pet_registry.SOURCE_APP,
        )
    except pet_registry.PetRegistryError as e:
        raise _http(e) from e

    return PetOut(pet_id=pet.pet_id, content_id=pet.content_id)


@router.get("/mine", response_model=PetsResponse)
async def list_my_pets(user: AuthedUser = Depends(require_user)):
    """내 펫만. 남의 펫은 조회되지 않는다."""
    try:
        rows = await pet_registry.search(user.user_id)
    except pet_registry.PetRegistryError as e:
        raise _http(e) from e
    return PetsResponse(
        pets=[
            PetOut(pet_id=r.pet_id, content_id=r.content_id)
            for r in rows
            if r.user_id == user.user_id
        ]
    )
