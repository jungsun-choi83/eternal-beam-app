"""Supabase: user_assets 업로드 및 purchased_slots 조회

⚠️ **purchased_slots 는 legacy / dev-only 다** (docs/PAYPAL_LEGACY.md).

이 표는 PayPal(USD) 시절의 테마 소유권 저장소이고, PayPal 은 개발 중에만 쓰였다.
근거: 이 파일의 record_theme_purchase 가 그 표에 쓰는 **유일한** 함수인데,
호출부는 routers/paypal.py 하나뿐이고 그 라우터는 backend/main.py 에 **한 번도
마운트된 적이 없다**(저장소 전체 이력 기준). 즉 배포된 API 는 이 표에 한 줄도
쓸 수 없었다 — 실 고객 결제는 코드 배치상 불가능했다.

따라서:
  * 이 표의 데이터는 크레딧·소유권 마이그레이션에서 **제외한다.**
  * user_theme_entitlements 로 이관하지 않는다. 이관 코드도 만들지 않는다.
  * 새 기능이 이 표를 참조해서는 안 된다. 소유권의 권위는
    services/theme_entitlement.py (user_theme_entitlements) 하나뿐이다.

규칙은 backend/tests/test_paypal_data_excluded.py 가 강제한다.
"""

import os
import re
from typing import Optional

from supabase import create_client, Client

_SUPABASE_HOST = re.compile(
    r"^https://[a-z0-9-]+\.supabase\.co/?$", re.IGNORECASE
)


def _normalize_supabase_url(raw: str) -> str:
    u = (raw or "").strip().strip('"').strip("'")
    if not u:
        return ""
    if not u.startswith("http"):
        u = "https://" + u
    u = u.rstrip("/")
    return u


def get_client() -> Optional[Client]:
    """다른 서비스(예: action_video_jobs.py)가 같은 Supabase 클라이언트 설정을
    재사용할 수 있도록 노출하는 얇은 public 래퍼."""
    return _client()


def _client() -> Optional[Client]:
    url = _normalize_supabase_url(
        os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or ""
    )
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip().strip('"').strip("'")
    if not url or not key:
        return None
    if not _SUPABASE_HOST.match(url):
        return None
    return create_client(url, key)


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
    # private bucket일 가능성이 높아 signed URL을 우선 시도.
    # get_public_url()은 private여도 문자열을 반환할 수 있어, 브라우저 GET 시 400/403이 날 수 있다.
    try:
        res = supabase.storage.from_(BUCKET).create_signed_url(object_path, 604800)
        if isinstance(res, dict):
            for k in ("signedURL", "signedUrl", "signed_url", "url"):
                v = res.get(k)
                if isinstance(v, str) and v:
                    return v
            data = res.get("data")
            if isinstance(data, dict):
                for k in ("signedURL", "signedUrl", "signed_url", "url"):
                    v = data.get(k)
                    if isinstance(v, str) and v:
                        return v
    except Exception:
        pass

    # 공개 버킷이면 public URL fallback
    pub = supabase.storage.from_(BUCKET).get_public_url(object_path)
    return pub


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


# ── record_theme_purchase 는 삭제됐다 (Phase 11) ─────────────────────────────
# purchased_slots 에 쓰던 유일한 함수였고, 호출부는 PayPal capture 하나뿐이었다.
# PayPal 이 은퇴하면서 이 함수도 함께 사라진다 — **표는 남는다.**
#
# 표를 지우지 않는 이유: 과거 구매 증거는 새 아키텍처가 생겼다는 이유로 버리는
# 것이 아니다. 조회는 아래 get_purchased_themes 로 계속 가능하고, 쓰기는
# 마이그레이션 20261009000000 이 DB 수준에서 막는다.
# 근거·재검증 방법: docs/PAYPAL_LEGACY.md


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
