from fastapi import APIRouter, Query

from ..services import supabase_assets

router = APIRouter()


@router.get("/purchased-slots")
async def get_purchased_slots(user_id: str = Query("anonymous")):
    themes = await supabase_assets.get_purchased_themes(user_id)
    return {"theme_ids": themes}
