"""
편지 배경(히어로 이미지) — **Eternal Beam 이 소유하는 사본.**

── 왜 사본이 필요한가 ───────────────────────────────────────────────────────
인쇄는 결제·생산 이후, 즉 편지 생성으로부터 며칠 뒤일 수 있다. 그때 원본 주소를
다시 받아 오는 설계는 성립하지 않는다 — 어떤 주소든 그때까지 살아 있으리라고
가정할 수 없기 때문이다. 그래서 **편지를 가져오는 순간(claim)** 바이트를 우리
스토리지로 복사하고, 이후에는 우리 것만 본다. 저장하는 값은 서명 URL 이 아니라
**객체 경로**다 — 서명은 만료되지만 경로는 만료되지 않는다.

── 우리가 받는 주소는 둘 중 하나다 ─────────────────────────────────────────
  * (지금) Soul Trace 스토리지의 **서명된 객체**. Soul Trace 가 생성 직후 히어로
    바이트를 자기 비공개 버킷에 보관하고, claim 시점에 짧은 서명을 새로 발급해
    준다. 그래서 고객이 며칠 뒤에 넘어와도 원본이 남아 있다.
  * (레거시) DALL·E 임시 URL. 보관 이전에 생성된 편지들이다. 원본이 아직 살아
    있으면 동작하고, 죽었으면 배경 없이 진행한다.

어느 쪽이든 **수명이 짧다.** 그것이 이 모듈이 존재하는 이유다 — 받는 즉시 복사한다.

── 실패는 조용히 없음이다 ───────────────────────────────────────────────────
복사에 실패해도 편지 가져오기를 막지 않는다. 배경이 없으면 인쇄는 기존
어두운 스크림으로 떨어진다(레거시 편지와 같은 경로). 배경 한 장 때문에 결제된
주문의 편지를 잃는 것이 훨씬 나쁘다.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

#: 원본을 받아 올 때 허용하는 호스트. 오픈 프록시가 되지 않도록 좁게 잡는다.
#: Soul Trace 의 lib/hero-image-proxy.ts 와 **같은 규칙**이다.
_ALLOWED_SUFFIX = ".blob.core.windows.net"
_ALLOWED_PREFIXES = ("oaidalleapiprod", "dalleprod")

#: 인쇄 배경으로 받아들일 최대 크기. A5 전면에 쓰는 한 장이라 이보다 클 이유가 없고,
#: 상한이 없으면 잘못된 URL 하나가 컨테이너 메모리를 먹는다.
MAX_BYTES = int(os.getenv("LETTER_BACKGROUND_MAX_BYTES", str(12 * 1024 * 1024)))


#: Soul Trace 가 자기 스토리지에서 발급한 서명 URL 의 경로 모양.
#: 호스트만 보지 않고 경로까지 보는 이유는 이 허용이 "서명된 객체 하나"에만
#: 열려 있어야 하기 때문이다 — 프로젝트 API 전체가 아니다.
_SIGNED_OBJECT_PREFIX = "/storage/v1/object/sign/"


def _soul_trace_storage_host() -> str:
    """
    Soul Trace 스토리지의 호스트. 설정돼 있으면 **그 하나만** 허용한다.

    설정이 없으면 아래에서 `*.supabase.co` 로 넓게 받는다 — 좁히는 편이 낫지만,
    설정 하나를 빠뜨렸다고 배경이 조용히 사라지는 쪽이 더 나쁘다. 이 경로로 오는
    주소는 이미 공유 비밀로 인증된 Soul Trace 응답이 실어 보낸 것이다.
    """
    from urllib.parse import urlsplit

    raw = (os.getenv("SOUL_TRACE_SUPABASE_URL") or "").strip().lower()
    if not raw:
        return ""
    try:
        return urlsplit(raw).netloc.split(":")[0]
    except Exception:
        return ""


def is_allowed_source(url: str) -> bool:
    """
    이 주소에서 배경을 받아도 되는가.

    셋을 허용한다:
      1. DALL·E 임시 주소 — 레거시 편지(보관 이전에 생성된 것)
      2. Soul Trace 스토리지의 **서명된 객체** — 지금의 정상 경로
      3. Soul Trace 오리진(설정된 것)
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit((url or "").strip())
    except Exception:
        return False
    if parts.scheme != "https" or not parts.netloc:
        return False

    host = parts.netloc.lower().split(":")[0]
    if host.endswith(_ALLOWED_SUFFIX):
        sub = host[: -len(_ALLOWED_SUFFIX)]
        return any(sub.startswith(p) for p in _ALLOWED_PREFIXES)

    # ── Soul Trace 스토리지의 서명 객체 (Phase 24) ─────────────────────────
    # 이제 정상 경로는 이쪽이다. Soul Trace 가 히어로를 자기 버킷에 보관하고,
    # claim 시점에 짧은 서명을 새로 발급해 준다. 그 주소의 호스트는 DALL·E 도
    # Soul Trace 앱 오리진도 아닌 **Supabase 스토리지**다.
    if parts.path.startswith(_SIGNED_OBJECT_PREFIX):
        configured = _soul_trace_storage_host()
        if configured:
            if host == configured:
                return True
        elif host.endswith(".supabase.co"):
            return True

    # Soul Trace 오리진(설정된 것)도 신뢰한다.
    base = (os.getenv("SOUL_TRACE_API_BASE") or "").strip().lower()
    if base:
        try:
            return host == urlsplit(base).netloc.lower().split(":")[0]
        except Exception:
            return False
    return False


def object_path_for(user_id: str, letter_id: str) -> str:
    """
    저장 경로. **letter_id 로 결정적**이라 같은 편지를 두 번 가져와도 한 객체로
    수렴한다(멱등). 확장자를 jpg 로 고정하는 이유는 인쇄 입력이 어차피 평탄화된
    RGB 이기 때문이다.
    """
    return f"{user_id}/letters/{letter_id}/background.jpg"


async def _fetch(url: str) -> Optional[bytes]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            res = await client.get(url, headers={"Accept": "image/*"})
            res.raise_for_status()
            data = res.content
    except Exception:
        logger.warning("편지 배경 원본을 받지 못했다 (%.80s)", url, exc_info=True)
        return None

    if not data:
        return None
    if len(data) > MAX_BYTES:
        logger.warning("편지 배경이 너무 크다 — %d bytes (상한 %d)", len(data), MAX_BYTES)
        return None
    return data


def _to_print_jpeg(data: bytes) -> Optional[bytes]:
    """
    인쇄 입력용으로 평탄화한다.

    알파를 남기면 JPEG 변환에서 투명 영역이 예측 불가한 색이 되고, 원본 PNG 를
    그대로 두면 A5 전면 배경치고 파일이 불필요하게 크다.
    """
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (0, 0, 0))
            flat.paste(im, mask=im.split()[3])
            im = flat
        else:
            im = im.convert("RGB")
        # A5 300dpi 전면(1748×2480)보다 큰 원본은 줄인다 — 더 커도 인쇄에서 보이지 않는다.
        if max(im.size) > 2600:
            im.thumbnail((2600, 2600), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=88)
        return out.getvalue()
    except Exception:
        logger.warning("편지 배경 이미지를 해석하지 못했다", exc_info=True)
        return None


async def import_from_source(
    *, source_url: str | None, user_id: str, letter_id: str
) -> Optional[str]:
    """
    원본 → 우리 스토리지. 돌려주는 값은 **객체 경로**(stable ref)이며 서명 URL 이 아니다.

    실패하면 None — 호출부는 배경 없이 진행한다(스크림 폴백).
    """
    url = (source_url or "").strip()
    if not url:
        return None
    if not is_allowed_source(url):
        logger.warning("허용되지 않은 배경 원본 호스트 — 건너뛴다 (%.60s)", url)
        return None

    raw = await _fetch(url)
    if not raw:
        return None
    jpeg = _to_print_jpeg(raw)
    if not jpeg:
        return None

    path = object_path_for(user_id, letter_id)
    try:
        from . import supabase_assets

        await supabase_assets.upload_asset_to_storage(path, jpeg, "image/jpeg")
    except Exception:
        logger.warning("편지 배경 업로드 실패 (letter=%s)", letter_id, exc_info=True)
        return None

    logger.warning(
        "편지 배경 복사 완료 — letter=%s path=%s (%d bytes)", letter_id, path, len(jpeg)
    )
    return path


async def load_bytes(ref: str | None) -> Optional[bytes]:
    """
    저장된 ref → 실제 바이트. 인쇄 직전에 부른다.

    ref 는 **경로**이므로 만료되지 않는다. 서명은 여기서 그때그때 만든다 —
    그것이 "만료되는 URL 에 의존하지 않는다"의 실제 구현이다.
    """
    path = (ref or "").strip()
    if not path:
        return None

    try:
        from .asset_url_refresh import StorageObject, default_bucket, sign_object

        signed = sign_object(StorageObject(bucket=default_bucket(), path=path))
    except Exception:
        logger.warning("편지 배경 서명 실패 (path=%s)", path, exc_info=True)
        return None
    if not signed:
        logger.warning("편지 배경 객체를 찾지 못했다 (path=%s)", path)
        return None

    return await _fetch(signed) if _looks_remote(signed) else None


def _looks_remote(url: str) -> bool:
    return (url or "").strip().lower().startswith(("http://", "https://"))
