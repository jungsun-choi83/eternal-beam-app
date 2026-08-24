"""
QR 생성 — **판매자(Eternal Beam)가 소유하는 서비스.**

물리 제품(편지 / 메모리 카드)에 인쇄될 QR 을 만든다. 고객이 만드는 것이 아니라
판매자·운영이 만든다 — 소유 모델상 QR 생성은 셀러 쪽 자산이다.

── 이 모듈의 단 하나의 안전 규칙 ────────────────────────────────────────────
**Shaker URL 이 아니면 인코딩하지 않는다.**

QR 은 종이에 찍혀 나가면 회수할 수 없다. 실수로 Supabase 서명 URL 이나 원본
영상 주소가 인쇄되면:
  * 그 URL 은 만료된다 (7일) — 인쇄물이 며칠 만에 죽는다
  * 토큰 검증·폐기·레이트 리밋을 **전부 우회**한다
  * 폐기할 방법이 없다 — 이미 인쇄된 종이다

그래서 규칙을 관례가 아니라 **코드**로 둔다. assert_shaker_url() 을 통과하지
못하면 이 모듈은 아무것도 만들지 않는다.

직접 구현하지 않는 이유: QR 은 Reed-Solomon 오류정정과 마스킹 패턴이 미묘하게
틀려도 일부 리더에서는 읽힌다. 테스트에서 통과하고 현장에서 실패하는 종류의
버그이고, 그 실패는 인쇄된 재고 전량이다. 검증된 라이브러리(segno, 순수 파이썬)를 쓴다.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

#: Shaker 경로. app-entry / shaker-entry.ts 의 SHAKER_PATH 와 같아야 한다.
SHAKER_PATH = "/shaker"

#: 절대 QR 에 들어가면 안 되는 호스트/경로 조각. 방어의 2차선이다 —
#: 1차선은 "경로가 /shaker 여야 한다"는 화이트리스트다.
_FORBIDDEN_FRAGMENTS = (
    "supabase.co",
    "/storage/v1/",
    ".mp4",
    ".png",
    ".jpg",
    "token=",       # 스토리지 서명 토큰
    "signedurl",
)

#: 인쇄 품질. 300dpi 기준 약 2.5cm — 편지·카드에 적당하고 스캔 여유가 있다.
DEFAULT_SCALE = 8
DEFAULT_BORDER = 4

#: 오류정정 레벨. M(15%) — 인쇄물이 조금 접히거나 더러워져도 읽힌다.
#: H(30%)는 모듈이 촘촘해져 작은 인쇄에서 오히려 불리하다.
ERROR_LEVEL = "m"


class QrError(Exception):
    """QR 을 만들 수 없다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class QrImage:
    data: bytes
    content_type: str
    #: 다운로드 파일명 제안.
    filename: str


def assert_shaker_url(url: str | None) -> str:
    """
    이 URL 을 QR 로 만들어도 되는가. 아니면 예외.

    허용 조건 **전부**를 만족해야 한다:
      1. http(s) 절대 URL
      2. 경로가 정확히 /shaker
      3. share 쿼리 파라미터가 있다
      4. 금지 조각(스토리지 호스트·영상 확장자·서명 토큰)이 없다

    3번이 있는 이유: share 없는 /shaker 는 QR 로서 쓸모가 없다(열면 "링크가
    없습니다"가 뜬다). 인쇄 전에 잡아야 하는 실수다.
    """
    raw = (url or "").strip()
    if not raw:
        raise QrError("QR_URL_REQUIRED", "QR 로 만들 URL 이 필요합니다.")

    lowered = raw.lower()
    for bad in _FORBIDDEN_FRAGMENTS:
        if bad in lowered:
            raise QrError(
                "QR_URL_NOT_SHAKER",
                "QR 에는 Shaker 링크만 넣을 수 있습니다 (스토리지·영상 주소 금지).",
            )

    try:
        parts = urlsplit(raw)
    except Exception as e:
        raise QrError("QR_URL_INVALID", "URL 을 해석할 수 없습니다.") from e

    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise QrError("QR_URL_INVALID", "http(s) 절대 URL 이어야 합니다.")

    path = (parts.path or "").rstrip("/") or "/"
    if path != SHAKER_PATH:
        raise QrError(
            "QR_URL_NOT_SHAKER",
            f"QR 에는 {SHAKER_PATH} 링크만 넣을 수 있습니다.",
        )

    if not (parse_qs(parts.query).get("share") or [""])[0].strip():
        raise QrError(
            "QR_URL_NO_SHARE",
            "공유 토큰이 없는 링크는 QR 로 만들 수 없습니다.",
        )

    return raw


def _safe_slug(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "").strip()).strip("-")
    return s[:48] or "share"


# ── 파트너 QR (Phase 16) ──────────────────────────────────────────────────────
#
# 제휴 병원·장례식장 벽에 붙는 QR 이다. Shaker QR 과 **다른 화이트리스트**를 쓴다:
# 가리키는 곳이 Shaker 가 아니라 Soul Trace 랜딩이고, 들고 있는 것이 공유 토큰이
# 아니라 불투명 파트너 코드다.
#
# 규칙 자체는 같다 — **아는 모양이 아니면 인코딩하지 않는다.** 인쇄된 QR 은
# 회수할 수 없고, 엉뚱한 주소가 찍힌 인쇄물은 재고 전량이 손실이다.

#: URL 쿼리 파라미터. Soul Trace lib/partner.ts 의 PARTNER_CODE_PARAM 과 같아야 한다.
PARTNER_CODE_PARAM = "p"

_PARTNER_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def soul_trace_base() -> str:
    """
    파트너 QR 이 가리킬 공개 주소. 편지 가져오기와 **같은 설정**을 쓴다
    (SOUL_TRACE_API_BASE) — 두 벌로 두면 한쪽만 바뀌는 날이 온다.
    """
    raw = (os.getenv("SOUL_TRACE_API_BASE") or "https://soultrace.eternalbeam.com").strip()
    return raw.rstrip("/")


def partner_share_url(code: str) -> str:
    """코드 → 인쇄될 공개 URL. 이 함수가 그 주소의 유일한 출처다."""
    c = (code or "").strip()
    if not _PARTNER_CODE_RE.match(c):
        raise QrError("QR_PARTNER_CODE_INVALID", "파트너 코드 모양이 올바르지 않습니다.")
    return f"{soul_trace_base()}/?{PARTNER_CODE_PARAM}={c}"


def assert_partner_url(url: str | None) -> str:
    """
    이 URL 을 파트너 QR 로 만들어도 되는가.

    인쇄용이므로 Shaker QR 과 같은 인쇄 안전 규칙을 적용한다 — localhost 나 API
    도메인을 가리킨 QR 이 벽에 붙는 일이 없어야 한다.
    """
    raw = (url or "").strip()
    if not raw:
        raise QrError("QR_URL_REQUIRED", "QR 로 만들 URL 이 필요합니다.")

    try:
        parts = urlsplit(raw)
    except Exception as e:
        raise QrError("QR_URL_INVALID", "URL 을 해석할 수 없습니다.") from e

    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise QrError("QR_URL_INVALID", "http(s) 절대 URL 이어야 합니다.")

    host = parts.netloc.lower()
    for bad in _UNSAFE_HOSTS:
        if bad in host:
            raise QrError(
                "QR_BASE_UNSAFE",
                "인쇄용 QR 은 공개 도메인만 가리킬 수 있습니다.",
            )

    # 랜딩(`/`)이어야 한다. 갈래는 코드가 들고 있고, 서버가 정한다 —
    # `/living` 을 직접 찍으면 갈래가 종이에 박혀 코드 쪽 track 과 어긋날 수 있다.
    path = (parts.path or "").rstrip("/") or "/"
    if path != "/":
        raise QrError("QR_URL_NOT_PARTNER", "파트너 QR 은 Soul Trace 랜딩만 가리킵니다.")

    code = (parse_qs(parts.query).get(PARTNER_CODE_PARAM) or [""])[0].strip()
    if not _PARTNER_CODE_RE.match(code):
        raise QrError("QR_PARTNER_CODE_INVALID", "파트너 코드가 없거나 모양이 다릅니다.")

    return raw


def render_partner_qr(
    code: str,
    *,
    kind: str = "svg",
    scale: int = DEFAULT_SCALE,
    filename_hint: str = "partner",
) -> QrImage:
    """파트너 코드 → QR. URL 은 서버가 만든다 — 요청이 주소를 고르지 않는다."""
    url = assert_partner_url(partner_share_url(code))
    return _render(url, kind=kind, scale=scale, filename_hint=filename_hint)


def render_qr(
    url: str,
    *,
    kind: str = "svg",
    scale: int = DEFAULT_SCALE,
    border: int = DEFAULT_BORDER,
    filename_hint: str = "shaker",
) -> QrImage:
    """
    Shaker URL → QR 이미지.

    svg 를 기본으로 두는 이유: 인쇄용이다. 벡터라 어떤 크기로 뽑아도 모듈 경계가
    선명하고, 인쇄소에 넘길 때 해상도를 협의할 필요가 없다. png 는 화면 미리보기와
    간단한 붙여넣기용이다.
    """
    safe = assert_shaker_url(url)
    return _render(safe, kind=kind, scale=scale, border=border, filename_hint=filename_hint)


def _render(
    safe: str,
    *,
    kind: str = "svg",
    scale: int = DEFAULT_SCALE,
    border: int = DEFAULT_BORDER,
    filename_hint: str = "shaker",
) -> QrImage:
    """
    이미지 생성만 한다. **검증은 부르는 쪽이 이미 끝냈다.**

    Shaker QR 과 파트너 QR 은 허용 주소가 다르지만(화이트리스트가 다르다) 인코딩
    규칙은 같다. 여기를 나눠 두면 오류정정 레벨·크기 상한 같은 인쇄 품질 결정이
    한 곳에만 있다 — 두 벌이면 한쪽만 고쳐지는 날이 온다.
    """
    k = (kind or "svg").strip().lower()
    if k not in ("svg", "png"):
        raise QrError("QR_KIND_UNSUPPORTED", "svg 또는 png 만 지원합니다.")

    try:
        import segno
    except ImportError as e:  # pragma: no cover - 배포 의존성 누락
        raise QrError(
            "QR_ENGINE_UNAVAILABLE",
            "QR 생성기가 설치되지 않았습니다 (segno).",
            status=503,
        ) from e

    qr = segno.make(safe, error=ERROR_LEVEL)
    buf = io.BytesIO()
    # scale/border 를 상한 없이 받으면 운영 실수 한 번으로 수십 MB 응답이 나온다.
    s = max(1, min(int(scale or DEFAULT_SCALE), 40))
    b = max(0, min(int(border if border is not None else DEFAULT_BORDER), 16))
    qr.save(buf, kind=k, scale=s, border=b)

    return QrImage(
        data=buf.getvalue(),
        content_type="image/svg+xml" if k == "svg" else "image/png",
        filename=f"{_safe_slug(filename_hint)}-qr.{k}",
    )


# ── 인쇄 안전 (Phase 13.1) ────────────────────────────────────────────────────
#
# 인쇄된 QR 은 회수할 수 없다. 그래서 **인쇄용 QR 은 대상 주소가 확실할 때만**
# 만든다. 요청 호스트에서 유도하는 폴백은 화면용으로는 편리하지만 인쇄에는
# 위험하다 — localhost 나 API 도메인을 가리킨 카드가 그대로 찍혀 나간다.

#: 인쇄물로 나가는 용도. 이 목적의 QR 은 base URL 이 확실해야 한다.
PRINT_PURPOSES = ("LETTER", "MEMORY_BOX")

#: 인쇄용으로 절대 허용하지 않는 호스트 조각.
_UNSAFE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver", ".local")


def configured_web_base() -> str:
    """
    QR 이 가리켜야 할 **웹앱** 베이스. 설정되지 않았으면 빈 문자열.

    요청 호스트로 유도하지 않는다 — 그 폴백이 정확히 이 단계에서 위험하다.
    """
    raw = (os.getenv("PUBLIC_WEB_BASE_URL") or os.getenv("VITE_PUBLIC_WEB_URL") or "").strip()
    return raw.rstrip("/")


def is_print_purpose(purpose: str | None) -> bool:
    return (purpose or "").strip().upper() in PRINT_PURPOSES


def assert_printable_base() -> str:
    """
    인쇄용 QR 을 만들어도 되는 base URL — 아니면 예외. **fail closed.**

    막는 것:
      * PUBLIC_WEB_BASE_URL 미설정 (요청 호스트로 유도하면 API 도메인이 찍힌다)
      * localhost / 127.0.0.1 / testserver 등 (개발 장비를 가리킨 카드)
      * http:// (인쇄물은 오래 산다 — 평문 링크를 종이에 남기지 않는다)
    """
    base = configured_web_base()
    if not base:
        raise QrError(
            "PRINT_BASE_URL_MISSING",
            (
                "PUBLIC_WEB_BASE_URL 이 설정되지 않아 인쇄용 QR 을 만들 수 없습니다. "
                "요청 호스트로 유도하면 API 도메인이나 localhost 를 가리킨 QR 이 "
                "인쇄될 수 있습니다."
            ),
            status=409,
        )

    lowered = base.lower()
    if not lowered.startswith("https://"):
        raise QrError(
            "PRINT_BASE_URL_INSECURE",
            "인쇄용 QR 의 대상은 https 여야 합니다.",
            status=409,
        )
    for bad in _UNSAFE_HOSTS:
        if bad in lowered:
            raise QrError(
                "PRINT_BASE_URL_UNSAFE",
                f"인쇄용 QR 의 대상이 개발 호스트입니다 ({base}).",
                status=409,
            )
    return base


def target_host(url: str) -> str:
    """QR 이 가리키는 호스트 — 산출물에 기록해 base URL 변경을 알아차리게 한다."""
    try:
        return urlsplit((url or "").strip()).netloc
    except Exception:
        return ""
