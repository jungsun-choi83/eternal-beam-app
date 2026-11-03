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

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class StorageScope:
    """Per-generation storage routing; never mutates the process default."""

    bucket: str
    require_private: bool = False


_STORAGE_SCOPE: ContextVar[StorageScope | None] = ContextVar(
    "eb_storage_scope", default=None
)


def active_bucket(explicit: str | None = None) -> str:
    """Resolve an explicit bucket, scoped bucket, or legacy global default."""

    if explicit and str(explicit).strip():
        return str(explicit).strip()
    scope = _STORAGE_SCOPE.get()
    return scope.bucket if scope else BUCKET


def active_requires_private() -> bool:
    scope = _STORAGE_SCOPE.get()
    return bool(scope and scope.require_private)


@contextmanager
def storage_scope(bucket: str, *, require_private: bool = False):
    target = (bucket or "").strip()
    if not target:
        raise ValueError("storage bucket is required")
    token = _STORAGE_SCOPE.set(StorageScope(target, require_private=require_private))
    try:
        yield target
    finally:
        _STORAGE_SCOPE.reset(token)


def _signed_url_from_response(response: object) -> str | None:
    """Accept response shapes used by supported supabase-py versions."""

    pending = [response, getattr(response, "data", None)]
    seen: set[int] = set()
    keys = ("signedURL", "signedUrl", "signed_url", "url")
    while pending:
        value = pending.pop(0)
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
            pending.append(value.get("data"))
        else:
            for key in keys:
                candidate = getattr(value, key, None)
                if isinstance(candidate, str) and candidate:
                    return candidate
    return None


async def upload_asset_to_storage(
    object_path: str,
    data: bytes,
    content_type: str,
    *,
    bucket: str | None = None,
    require_private: bool | None = None,
) -> str:
    """
    Storage에 업로드 후 public URL 또는 signed URL 반환.

    supabase-py 는 동기 클라이언트라, 실제 네트워크 왕복은 `asyncio.to_thread` 로
    스레드에 넘긴다 — 그러지 않으면 이 "async" 함수가 사실 이벤트 루프를 그대로
    막아, 여러 사진을 동시에 올려도 서버 쪽에서 한 장씩 직렬로 처리된다(동시
    인테이크의 실제 병목이 여기였다).
    """
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다.")

    def _sync() -> str:
        target_bucket = active_bucket(bucket)
        private = active_requires_private() if require_private is None else require_private
        storage = supabase.storage.from_(target_bucket)
        if private:
            response = supabase.storage.get_bucket(target_bucket)
            payload = getattr(response, "data", response)
            public = payload.get("public") if isinstance(payload, dict) else getattr(payload, "public", None)
            if not (public is False or str(public).strip().lower() == "false"):
                raise RuntimeError("storage bucket is not verified private")
        storage.upload(
            object_path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        # private bucket일 가능성이 높아 signed URL을 우선 시도.
        # get_public_url()은 private여도 문자열을 반환할 수 있어, 브라우저 GET 시 400/403이 날 수 있다.
        try:
            signed = _signed_url_from_response(storage.create_signed_url(object_path, 604800))
            if signed:
                return signed
        except Exception:
            if private:
                raise

        # 공개 버킷이면 public URL fallback
        if private:
            raise RuntimeError("private storage signed URL unavailable")
        return storage.get_public_url(object_path)

    return await asyncio.to_thread(_sync)


async def upload_asset_for_scope(
    object_path: str,
    data: bytes,
    content_type: str,
    *,
    bucket: str | None = None,
    require_private: bool | None = None,
) -> str:
    """Scoped upload adapter that keeps legacy three-argument mocks/callers valid."""

    target = active_bucket(bucket)
    private = active_requires_private() if require_private is None else require_private
    if target == BUCKET and not private:
        return await upload_asset_to_storage(object_path, data, content_type)
    return await upload_asset_to_storage(
        object_path,
        data,
        content_type,
        bucket=target,
        require_private=private,
    )


async def download_asset_from_storage(
    object_path: str, *, bucket: str | None = None
) -> bytes:
    """Download a private server-side asset without creating a public URL."""
    path = (object_path or "").strip()
    if not path:
        raise ValueError("object_path is required")
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase is not configured")

    def _sync() -> bytes:
        raw = supabase.storage.from_(active_bucket(bucket)).download(path)
        return bytes(raw)

    return await asyncio.to_thread(_sync)


async def verify_private_storage_bucket(bucket: str) -> bool:
    """Return True only when Supabase explicitly reports a private bucket."""

    target_bucket = (bucket or "").strip()
    if not target_bucket:
        return False
    supabase = _client()
    if not supabase:
        return False

    def _sync() -> bool:
        try:
            response = supabase.storage.get_bucket(target_bucket)
            payload = getattr(response, "data", response)
            if isinstance(payload, dict):
                public = payload.get("public")
            else:
                public = getattr(payload, "public", None)
            if isinstance(public, bool):
                return public is False
            return str(public).strip().lower() == "false"
        except Exception:
            return False

    return await asyncio.to_thread(_sync)


async def get_private_asset_info_from_storage(
    object_path: str,
    *,
    bucket: str,
) -> dict:
    """Read private object metadata without producing a URL."""

    path = (object_path or "").strip()
    target_bucket = (bucket or "").strip()
    if not path or not target_bucket:
        raise ValueError("object_path and a verified private bucket are required")
    if not await verify_private_storage_bucket(target_bucket):
        raise RuntimeError("storage bucket is not verified private")
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase is not configured")

    def _sync() -> dict:
        info = supabase.storage.from_(target_bucket).info(path)
        if not isinstance(info, dict):
            raise RuntimeError("storage returned invalid object metadata")
        return info

    return await asyncio.to_thread(_sync)


async def download_private_asset_from_storage(
    object_path: str,
    *,
    bucket: str,
) -> bytes:
    """Download bytes only after verifying the bucket is private."""

    path = (object_path or "").strip()
    target_bucket = (bucket or "").strip()
    if not path or not target_bucket:
        raise ValueError("object_path and a verified private bucket are required")
    if not await verify_private_storage_bucket(target_bucket):
        raise RuntimeError("storage bucket is not verified private")
    return await download_asset_from_storage(path, bucket=target_bucket)


async def upload_private_asset_to_storage(
    object_path: str,
    data: bytes,
    content_type: str,
    *,
    bucket: str | None = None,
    required_prefix: str | None = None,
) -> str:
    """Upload an object without creating or returning a public URL.

    Browser exports are consumed by a later server-to-server handoff.  This
    helper intentionally does not call ``get_public_url`` and does not create a
    signed URL, so a private bucket remains private.
    """

    path = (object_path or "").strip()
    if not path:
        raise ValueError("object_path is required")
    if required_prefix and not path.startswith(required_prefix):
        raise ValueError("object_path is outside the permitted private prefix")
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase is not configured")
    target_bucket = (bucket or "").strip()
    if not target_bucket:
        raise ValueError("a verified private bucket is required")
    if not await verify_private_storage_bucket(target_bucket):
        raise RuntimeError("storage bucket is not verified private")

    def _sync() -> str:
        supabase.storage.from_(target_bucket).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "false"},
        )
        return path

    return await asyncio.to_thread(_sync)


async def replace_private_asset_to_storage(
    object_path: str,
    data: bytes,
    content_type: str,
    *,
    bucket: str | None = None,
) -> str:
    """Replace one private object after the caller has validated new bytes.

    This is intentionally separate from the non-upserting upload helper.  It is
    used only by an explicit operator repair, and never creates a public URL.
    """

    path = (object_path or "").strip()
    target_bucket = (bucket or "").strip()
    if not path:
        raise ValueError("object_path is required")
    if not target_bucket:
        raise ValueError("a verified private bucket is required")
    supabase = _client()
    if not supabase:
        raise RuntimeError("Supabase is not configured")
    if not await verify_private_storage_bucket(target_bucket):
        raise RuntimeError("storage bucket is not verified private")

    def _sync() -> str:
        supabase.storage.from_(target_bucket).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        return path

    return await asyncio.to_thread(_sync)


def _ensure_user_asset_row_sync(supabase: Client, row: dict) -> None:
    try:
        supabase.table("user_assets").insert(row).execute()
    except Exception:
        pass


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
    await asyncio.to_thread(_ensure_user_asset_row_sync, supabase, row)


# ── record_theme_purchase 는 삭제됐다 (Phase 11) ─────────────────────────────
# purchased_slots 에 쓰던 유일한 함수였고, 호출부는 PayPal capture 하나뿐이었다.
# PayPal 이 은퇴하면서 이 함수도 함께 사라진다 — **표는 남는다.**
#
# 표를 지우지 않는 이유: 과거 구매 증거는 새 아키텍처가 생겼다는 이유로 버리는
# 것이 아니다. 조회는 아래 get_purchased_themes 로 계속 가능하고, 쓰기는
# 마이그레이션 20261009000000 이 DB 수준에서 막는다.
# 근거·재검증 방법: docs/PAYPAL_LEGACY.md


def _get_purchased_themes_sync(supabase: Client, user_id: str) -> list[str]:
    r = supabase.table("purchased_slots").select("theme_id").eq("user_id", user_id).eq("payment_status", True).execute()
    if r.data:
        return [x["theme_id"] for x in r.data if x.get("theme_id")]
    return []


async def get_purchased_themes(user_id: str) -> list[str]:
    """purchased_slots에서 해당 유저의 결제 완료된 theme_id 목록."""
    supabase = _client()
    if not supabase:
        return []
    return await asyncio.to_thread(_get_purchased_themes_sync, supabase, user_id)


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
