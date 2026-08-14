import base64
import binascii
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services import supabase_assets

logger = logging.getLogger(__name__)
router = APIRouter()


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
    return {"user_id": uid, "content_id": cid, "cutout_url": url, "bytes": len(png)}
