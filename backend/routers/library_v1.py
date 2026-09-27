"""
/api/v1/library — 사용자가 실제로 소유·발행한 모션 전체 (Phase 11).

BREATHING(무료 기본)과 프리미엄 구매/멤버십 생성 모션을 하나의 목록으로 합친다.
읽기 전용 — 생성도 발행도, 크레딧 차감도 하지 않는다. 기존 BREATHING 전용 경로
(motion_videos_v1.get_published_breathing)는 하위호환을 위해 그대로 둔다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import library_service

router = APIRouter(prefix="/v1", tags=["library"])


def _http(e: library_service.LibraryError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


class LibraryOwnershipOut(BaseModel):
    #: included | credit_purchase | membership | owned
    type: str
    permanent: bool = True


class LibraryAccessOut(BaseModel):
    #: playable | locked | unavailable
    state: str


class LibraryEntryOut(BaseModel):
    id: str
    pet_id: str
    motion_id: str
    display_name: str | None = None
    publication_id: str | None = None
    motion_version_id: str | None = None
    version: int | None = None
    #: 호출 시점에 새로 서명된 URL — 저장하지 말고 그대로 재생에 쓴다.
    url: str | None = None
    delivery_format: str | None = None
    background_baked: bool = False
    generated_at: str | None = None
    published_at: str | None = None
    ownership: LibraryOwnershipOut
    access: LibraryAccessOut
    #: 정직하게 확인된 값만 싣는다 — 모르는 필드는 비워 둔다.
    provenance: dict = {}


class LibraryResponse(BaseModel):
    pet_id: str
    motions: list[LibraryEntryOut] = []
    #: "active" | "canceled" | "expired" | null — 참고용. 이 목록의 접근권을 정하지 않는다.
    subscription_status: str | None = None
    entitled: bool = False


@router.get("/library", response_model=LibraryResponse)
async def get_library(
    pet_id: str,
    user: AuthedUser = Depends(require_user),
):
    """
    이 펫으로 사용자가 실제로 소유하거나 발행한 모션 전체. **읽기 전용.**

    만료된 멤버십도 이미 만든 자산은 계속 보이고 계속 재생된다 — 접근권은
    소유가 정하지 구독 상태가 정하지 않는다. 아직 한 번도 만들지 않은 멤버십
    모션은 여기 나타나지 않는다(오퍼일 뿐 라이브러리 기억이 아니다).
    """
    try:
        result = await library_service.get_library(user_id=user.user_id, pet_id=pet_id)
    except library_service.LibraryError as e:
        raise _http(e) from e

    return LibraryResponse(
        pet_id=result.pet_id,
        motions=[
            LibraryEntryOut(
                id=m.id,
                pet_id=m.pet_id,
                motion_id=m.motion_id,
                display_name=m.display_name,
                publication_id=m.publication_id,
                motion_version_id=m.motion_version_id,
                version=m.version,
                url=m.url,
                delivery_format=m.delivery_format,
                background_baked=m.background_baked,
                generated_at=m.generated_at,
                published_at=m.published_at,
                ownership=LibraryOwnershipOut(type=m.ownership.type, permanent=m.ownership.permanent),
                access=LibraryAccessOut(state=m.access.state),
                provenance=m.provenance,
            )
            for m in result.motions
        ],
        subscription_status=result.subscription_status,
        entitled=result.entitled,
    )
