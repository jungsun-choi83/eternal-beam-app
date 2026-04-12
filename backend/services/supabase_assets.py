"""Supabase: user_assets 업로드 및 purchased_slots 조회"""

import os
from typing import Optional

from supabase import create_client, Client

def _client() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return create_client(url, key)
    return None


# Create this bucket in Supabase Dashboard → Storage, or set SUPABASE_STORAGE_BUCKET to an existing bucket name.
BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "user-assets")


async def upload_asset_to_storage(object_path: str, data: bytes, content_type: str) -> str:
    """Storage에 업로드 후 public URL 또는 signed URL 반환."""
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다.")

    supabase.storage.from_(BUCKET).upload(
        object_path,
        data,
        {"content-type": content_type, "upsert": "true"},
    )
    # public bucket이면 get_public_url, 아니면 create_signed_url
    try:
        pub = supabase.storage.from_(BUCKET).get_public_url(object_path)
        return pub
    except Exception:
        res = supabase.storage.from_(BUCKET).create_signed_url(object_path, 604800)
        out = res if isinstance(res, dict) else getattr(res, "__dict__", {})
        return out.get("signedUrl") or out.get("signed_url") or ""


async def ensure_user_asset_row(
    user_id: str,
    content_id: str,
    asset_type: str,
    url: str,
    theme_id: Optional[str] = None,
) -> None:
    """user_assets에 행 삽입 (이미 있으면 무시)."""
    supabase = _client()
    if not supabase:
        return
    row = {
        "user_id": user_id,
        "content_id": content_id,
        "asset_type": asset_type,
        "url": url,
        "theme_id": theme_id,
    }
    try:
        supabase.table("user_assets").insert(row).execute()
    except Exception:
        pass


async def get_purchased_themes(user_id: str) -> list[str]:
    """purchased_slots에서 해당 유저의 결제 완료된 theme_id 목록."""
    supabase = _client()
    if not supabase:
        return []

    r = supabase.table("purchased_slots").select("theme_id").eq("user_id", user_id).eq("payment_status", True).execute()
    if r.data:
        return [x["theme_id"] for x in r.data if x.get("theme_id")]
    return []


def check_payment_for_theme(user_id: str, theme_id: str, paid_theme_ids: list[str]) -> bool:
    """
    유료 테마일 때 결제 여부 확인.
    paid_theme_ids: 앱에서 넘긴 이미 확인된 결제 테마 ID 목록.
    서버에서도 purchased_slots 조회해 검증할 수 있음.
    """
    # 앱에서 넘긴 목록으로 먼저 체크 (동기 호출용)
    if theme_id in (paid_theme_ids or []):
        return True
    # TODO: DB에서 재검증하려면 get_purchased_themes(user_id) 사용
    return False
