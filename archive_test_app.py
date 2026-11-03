import os

# Force Eternal Beam services to use their in-memory test stores.
os.environ["HYBRID_USE_SUPABASE"] = "0"

from fastapi import FastAPI

from backend.routers.archive_intake_v1 import router
from backend.services import supabase_assets


async def fake_upload_asset_to_storage(
    object_path: str,
    data: bytes,
    content_type: str,
) -> str:
    # Local test only: do not upload anywhere.
    return f"mock://{object_path}"


supabase_assets.upload_asset_to_storage = fake_upload_asset_to_storage


app = FastAPI()

app.include_router(
    router,
    prefix="/api",
)
from backend.services import archive_intake_service


@app.get("/debug/archive-intake/{application_id}")
async def debug_archive_intake(application_id: str):
    record = archive_intake_service.mock_get(application_id)

    if record is None:
        return {"found": False}

    return {
        "found": True,
        "record": record,
    }