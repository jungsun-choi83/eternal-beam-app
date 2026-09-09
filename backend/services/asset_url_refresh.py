"""
저장된 자산 URL → **지금 유효한** 서명 URL 재발급.

왜 필요한가: 업로드 시점에 만든 서명 URL 은 7일짜리다(supabase_assets 참고).
그런데 QR 은 **종이에 인쇄된다.** 8일째에 QR 을 찍은 사람은 유효한 공유 토큰을
들고 있는데도 영상이 재생되지 않는다 — 링크는 살아 있고 자산도 살아 있는데
그 사이의 서명만 죽은 상태다.

그래서 Shaker 는 **해석할 때마다** 서명을 새로 만든다. 저장된 URL 을 그대로
내보내지 않는다. 저장된 값에서 필요한 것은 **스토리지 객체 경로**뿐이고,
그것은 만료되지 않는다.

    저장된 URL  ──파싱──▶  (bucket, object_path)  ──서명──▶  새 URL

── 이 모듈이 하지 않는 것 ───────────────────────────────────────────────────
생성하지 않는다. 업로드하지 않는다. 스토리지에 **쓰지 않는다.** 이미 있는 객체에
대한 읽기 서명만 만든다 — 없는 객체를 만들어 내지 못하므로 no-generation 보장이
그대로 유지된다.

인식하지 못하는 URL(외부 CDN 등)은 **그대로 통과시킨다.** 재서명할 수 없다는
이유로 재생을 막으면, 지금 잘 돌아가는 자산까지 죽인다.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

#: 재발급 서명의 유효 기간(초). 해석할 때마다 새로 만들므로 길 이유가 없다.
#: 1시간이면 한 번 열어 끝까지 보기에 충분하고, 유출되더라도 창이 좁다.
DEFAULT_TTL_SECONDS = 3600


def ttl_seconds() -> int:
    raw = (os.getenv("SHAKER_SIGNED_URL_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    # 0 이하는 "무제한"이 아니라 설정 실수로 본다.
    return v if v > 0 else DEFAULT_TTL_SECONDS


@dataclass(frozen=True)
class StorageObject:
    bucket: str
    #: 버킷 안의 객체 경로. 이 값은 만료되지 않는다 — 재서명의 근거다.
    path: str


def default_bucket() -> str:
    """버킷을 함께 저장하지 못한 예전 행을 위한 폴백. supabase_assets 와 같은 값."""
    return os.getenv("SUPABASE_STORAGE_BUCKET", "user-assets")


#: /storage/v1/object/<mode>/<bucket>/<object path>
#: mode: sign(서명) | public(공개) | authenticated(인증) — 셋 다 같은 자리에 경로가 있다.
_OBJECT_RE = re.compile(
    r"/storage/v1/object/(?:sign/|public/|authenticated/)?(?P<bucket>[^/]+)/(?P<path>.+)$"
)


def parse_storage_object(url: str | None) -> Optional[StorageObject]:
    """
    Supabase 스토리지 URL → (bucket, object_path). 아니면 None.

    쿼리스트링(?token=…)은 버린다 — 만료된 서명이 바로 그 부분이고, 우리가
    남기려는 것은 그 앞의 경로다.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except Exception:
        return None

    # 절대/상대 URL 모두 받는다. supabase-py 는 버전에 따라 상대 경로를 주기도 한다.
    m = _OBJECT_RE.search(parts.path)
    if not m:
        return None

    bucket = unquote(m.group("bucket")).strip()
    path = unquote(m.group("path")).strip()
    if not bucket or not path:
        return None
    # 경로 탈출 방어 — 우리가 만든 경로에는 나올 수 없는 형태다.
    if ".." in path.split("/"):
        return None
    return StorageObject(bucket=bucket, path=path)


def sign_object(obj: StorageObject, *, ttl: int | None = None) -> Optional[str]:
    """
    스토리지 객체 → 새 서명 URL. 실패하면 None (호출부가 폴백한다).

    supabase-py 의 응답 모양이 버전마다 달라(dict / {"data": dict} / 속성) 여러
    형태를 모두 훑는다. 여기서 하나만 가정하면 라이브러리 업그레이드 때
    조용히 None 이 되고, 증상은 "며칠 뒤부터 영상이 안 나온다"로 나타난다.
    """
    from . import supabase_assets

    client = supabase_assets.get_client()
    if not client:
        return None

    seconds = ttl if ttl and ttl > 0 else ttl_seconds()
    try:
        res = client.storage.from_(obj.bucket).create_signed_url(obj.path, seconds)
    except Exception:
        logger.warning("서명 URL 재발급 실패 (bucket=%s path=%s)", obj.bucket, obj.path)
        return None

    for candidate in (res, getattr(res, "data", None), (res or {}).get("data") if isinstance(res, dict) else None):
        if isinstance(candidate, dict):
            for key in ("signedURL", "signedUrl", "signed_url", "url"):
                v = candidate.get(key)
                if isinstance(v, str) and v:
                    return _absolutise(v)
    return None


def _absolutise(url: str) -> str:
    """상대 경로로 온 서명 URL 을 절대 URL 로. 브라우저가 우리 도메인으로 오해하지 않게."""
    u = (url or "").strip()
    if not u or u.startswith("http://") or u.startswith("https://"):
        return u
    base = (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").strip().rstrip("/")
    if not base:
        return u
    return f"{base}/{u.lstrip('/')}"


def refresh_url(url: str | None, *, ttl: int | None = None) -> str:
    """
    저장된 URL → 지금 유효한 URL.

    재서명할 수 없으면 **원본을 그대로 돌려준다**. 공개 버킷이거나 외부 CDN 이면
    원본이 이미 유효하고, Supabase 설정이 없는 로컬/테스트에서도 동작이 멈추지
    않아야 한다. 재서명은 개선이지 전제가 아니다.
    """
    original = (url or "").strip()
    if not original:
        return original
    obj = parse_storage_object(original)
    if not obj:
        return original
    return sign_object(obj, ttl=ttl) or original


def refresh_urls(urls: dict[str, str], *, ttl: int | None = None) -> dict[str, str]:
    """여러 개를 한 번에. 하나가 실패해도 나머지는 그대로 간다."""
    return {k: refresh_url(v, ttl=ttl) for k, v in (urls or {}).items()}
