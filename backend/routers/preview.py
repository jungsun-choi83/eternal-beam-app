import os
import tempfile
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile, HTTPException

from ..services.preview_service import generate_preview

router = APIRouter()

_outputs = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "outputs")
)


@router.post("/preview")
async def post_preview(
    request: Request,
    cutout_file: UploadFile = File(...),
    background_id: str = Form(...),
    scale: str = Form("1"),
    position_x: str = Form("0"),
    position_y: str = Form("0"),
):
    raw = await cutout_file.read()
    if not raw:
        raise HTTPException(400, detail="Empty cutout")

    os.makedirs(_outputs, exist_ok=True)
    preview_id = f"preview_{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(_outputs, preview_id)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        generate_preview(
            background_id,
            tmp_path,
            out_path,
            scale=float(scale or "1"),
            position_x=int(position_x or "0"),
            position_y=int(position_y or "0"),
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    base = str(request.base_url).rstrip("/")
    return {
        "preview_url": f"{base}/outputs/{preview_id}",
        "preview_id": preview_id,
    }
