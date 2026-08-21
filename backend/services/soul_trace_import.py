"""
Soul Trace → Eternal Beam 편지 가져오기 — **서버 대 서버로만.**

    브라우저: traceId + 불투명 토큰만 들고 온다
    우리 서버: Soul Trace 에 직접 물어 본문을 받는다

── 왜 브라우저에게서 본문을 받지 않는가 ────────────────────────────────────
편지는 **인쇄되어 배송된다.** 브라우저가 본문을 보내는 구조라면, 인증된 사용자
누구나 아무 문장이나 A5 에 찍어 집으로 받을 수 있다. 되돌릴 수 없는 물리 결과라
"그건 사용자 잘못"으로 넘길 수 없다.

그래서 이 모듈이 유일한 본문 출처다: 토큰을 Soul Trace 에 제시하고, Soul Trace
자신의 DB 가 돌려준 값만 쓴다. 요청 본문에 letter_body 를 받는 자리가 **없다.**

── 두 프로젝트는 DB 를 공유하지 않는다 ─────────────────────────────────────
Soul Trace 는 pjoyuvqykggcuvbsnxio, Eternal Beam 은 kdlukiujgclczwqmwvmk 로
서로 다른 Supabase 프로젝트다. 그래서 교차 조회가 불가능하고, 서로의
service-role 키를 나눠 갖지도 않는다. 공유하는 비밀은 이 경로 전용 자격 증명
**하나뿐**이다(SOUL_TRACE_SERVICE_TOKEN).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_API_BASE_ENV = "SOUL_TRACE_API_BASE"
_SERVICE_TOKEN_ENV = "SOUL_TRACE_SERVICE_TOKEN"
_DEFAULT_API_BASE = "https://soultrace.eternalbeam.com"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
#: Soul Trace 가 발급하는 모양 — base64url 43자(256비트).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


class ImportError_(Exception):
    """가져오기 실패. 라우터가 그대로 HTTP 로 옮긴다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class SourceLetter:
    """Soul Trace 가 **자기 DB 에서** 읽어 돌려준 정본."""

    letter_id: str
    letter_body: str
    pet_name: str


def api_base() -> str:
    return (os.getenv(_API_BASE_ENV) or _DEFAULT_API_BASE).strip().rstrip("/")


def _service_token() -> str:
    return (os.getenv(_SERVICE_TOKEN_ENV) or "").strip()


def looks_like_trace_id(value: str | None) -> bool:
    return bool(value and _UUID_RE.match(value.strip()))


def looks_like_handoff(value: str | None) -> bool:
    return bool(value and _TOKEN_RE.match(value.strip()))


async def fetch_source_letter(
    *, trace_id: str, handoff: str, consumed_by: str
) -> SourceLetter:
    """
    핸드오프 토큰을 Soul Trace 에 제시하고 정본 편지를 받는다.

    Soul Trace 쪽에서 토큰은 **이 호출로 소비된다**(1회용). 그래서 재시도는
    안전하지 않다 — 두 번째 호출은 409 를 받는다. 그것이 의도된 동작이다:
    자동 재시도가 가능하면 1회용이 아니게 된다.
    """
    tid = (trace_id or "").strip()
    tok = (handoff or "").strip()
    uid = (consumed_by or "").strip()

    if not looks_like_trace_id(tid) or not looks_like_handoff(tok):
        # 모양이 틀린 값으로 Soul Trace 를 때리지 않는다.
        raise ImportError_("HANDOFF_INVALID", "핸드오프 정보가 올바르지 않습니다.", status=400)

    token = _service_token()
    if not token:
        # 설정이 없으면 **닫는다.** 여기서 인증 없이 부르려 시도하면 Soul Trace 가
        # 401 로 막겠지만, 그 전에 우리가 먼저 멈추는 편이 진단이 쉽다.
        logger.error("%s 가 설정되지 않았습니다 — Soul Trace 가져오기 불가", _SERVICE_TOKEN_ENV)
        raise ImportError_(
            "IMPORT_NOT_CONFIGURED",
            "Soul Trace 연동이 설정되지 않았습니다.",
            status=503,
        )

    import httpx

    url = f"{api_base()}/api/internal/letter"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                url,
                json={"traceId": tid, "handoff": tok, "consumedBy": uid},
                headers={
                    "Content-Type": "application/json",
                    # 이 헤더는 **서버에만** 존재한다. Vite 번들로 나가지 않는다.
                    "X-EB-Service-Token": token,
                },
            )
    except Exception as e:  # noqa: BLE001 — 네트워크 실패를 그대로 노출하지 않는다
        logger.exception("Soul Trace 호출 실패 (trace=%s)", tid)
        raise ImportError_(
            "SOURCE_UNAVAILABLE", "Soul Trace 에 연결하지 못했습니다.", status=502
        ) from e

    if res.status_code == 401 or res.status_code == 503:
        # 우리 자격 증명 문제다. 사용자에게 "링크가 잘못됐다"고 말하면 안 된다 —
        # 링크는 멀쩡하고, 고치는 주체는 운영이다.
        logger.error("Soul Trace 인증/설정 실패 — http=%s", res.status_code)
        raise ImportError_(
            "IMPORT_NOT_CONFIGURED",
            "Soul Trace 연동이 설정되지 않았습니다.",
            status=503,
        )
    if res.status_code == 409:
        # 만료 · 이미 사용 · 없는 토큰 — Soul Trace 가 구분해 주지 않는다.
        raise ImportError_(
            "HANDOFF_CONSUMED",
            "이 링크는 만료되었거나 이미 사용되었습니다. Soul Trace 에서 다시 시작해 주세요.",
            status=409,
        )
    if res.status_code == 404:
        raise ImportError_("SOURCE_LETTER_NOT_FOUND", "편지를 찾을 수 없습니다.", status=404)
    if res.status_code >= 400:
        logger.error("Soul Trace 응답 오류 — http=%s", res.status_code)
        raise ImportError_(
            "SOURCE_UNAVAILABLE", "Soul Trace 에서 편지를 가져오지 못했습니다.", status=502
        )

    try:
        data = res.json()
    except Exception as e:  # noqa: BLE001
        raise ImportError_(
            "SOURCE_UNAVAILABLE", "Soul Trace 응답을 해석하지 못했습니다.", status=502
        ) from e

    body = str(data.get("letterBody") or "").strip()
    letter_id = str(data.get("letterId") or "").strip()

    # 본문이 비어 있으면 **여기서 멈춘다.** 기본 문구로 채우면 그 순간 Eternal Beam
    # 이 편지를 만든 것이 되고, 고객은 Soul Trace 가 쓴 적 없는 문장을 인쇄해 받는다.
    if not body:
        raise ImportError_(
            "SOURCE_BODY_EMPTY", "Soul Trace 편지 본문이 비어 있습니다.", status=409
        )
    # Soul Trace 가 돌려준 letter_id 가 우리가 요청한 것과 달라서는 안 된다.
    if letter_id and letter_id.lower() != tid.lower():
        logger.error("Soul Trace letterId 불일치 — 요청=%s 응답=%s", tid, letter_id)
        raise ImportError_(
            "SOURCE_MISMATCH", "Soul Trace 응답이 요청과 일치하지 않습니다.", status=502
        )

    return SourceLetter(
        letter_id=letter_id or tid,
        letter_body=body,
        pet_name=str(data.get("petName") or "").strip(),
    )


def excerpt_of(body: str, *, limit: int = 120) -> str:
    """
    목록 화면용 짧은 발췌. **새 문장을 만들지 않는다** — 자르기만 한다.

    본문을 요약하거나 다듬으면 그것은 Eternal Beam 이 쓴 문장이 된다.
    """
    flat = " ".join((body or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "…"
