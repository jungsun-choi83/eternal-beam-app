from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.content import ContentDB, ContentMetadata

router = APIRouter()


class ComposeFinalBody(BaseModel):
    background_id: str
    subject_id: str
    scale: float = 1.0
    position_x: int = 0
    position_y: int = 0
    user_id: str = "anonymous"


@router.post("/compose-final")
async def post_compose_final(body: ComposeFinalBody):
    meta = ContentMetadata(
        background_id=body.background_id,
        subject_id=body.subject_id,
        scale=body.scale,
        position_x=body.position_x,
        position_y=body.position_y,
        user_id=body.user_id,
    )
    cid = ContentDB.create_content(meta)
    return {
        "content_id": cid,
        "nfc_payload": {"content_id": cid, "version": "1"},
    }


@router.get("/content/{content_id}")
async def get_content_by_id(content_id: str):
    m = ContentDB.get_content(content_id)
    if not m:
        raise HTTPException(status_code=404, detail="Content not found")
    return {
        "background_url": "",
        "subject_url": "",
        "scale": m.scale,
        "position_x": m.position_x,
        "position_y": m.position_y,
    }
