"""
파트너 운영 — Soul Trace 의 partners/partner_codes 를 **서버 대 서버로** 다룬다.

    운영자 브라우저 → (JWT + SHAKER_OPS_USER_IDS) Eternal Beam
                    → (X-EB-Service-Token) Soul Trace → DB

── 왜 이렇게 도는가 ─────────────────────────────────────────────────────────
partners/partner_codes 는 **Soul Trace 프로젝트**에 있다. 두 프로젝트는 DB 를
공유하지 않으며, 공유해서도 안 된다. Eternal Beam 브라우저에 Soul Trace 의
service-role 키를 주면 정산 테이블을 직접 고칠 수 있는 열쇠가 프론트엔드에 놓인다.

그래서 이 모듈은 soul_trace_import 와 **같은 신뢰 경로**를 재사용한다 — 이미
편지 본문이 그 길로 건너오고 있고, 검증된 경로를 두 벌로 늘릴 이유가 없다.

── 이 모듈이 하지 않는 것 ───────────────────────────────────────────────────
코드 문자열을 만들지 않는다(Soul Trace 의 createPartnerCode 가 정본이다).
partner_id 를 만들지 않는다. 귀속을 만들지 않는다. 편지·주문을 건드리지 않는다.
정산을 실행하지 않는다 — 나중에 계산할 수 있을 만큼의 사실만 오간다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .soul_trace_import import api_base, _service_token  # 같은 S2S 설정을 쓴다

logger = logging.getLogger(__name__)

#: Soul Trace 가 이미 쓰는 갈래. **새 개념이 아니다** (lib/letter-mode.ts).
TRACKS = ("living", "memorial")
PARTNER_TYPES = ("HOSPITAL", "FUNERAL")


class PartnerAdminError(Exception):
    """운영 조작 실패. 라우터가 그대로 HTTP 로 옮긴다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class PartnerCode:
    code: str
    track: Optional[str]
    active: bool
    created_at: Optional[str] = None


@dataclass(frozen=True)
class Partner:
    partner_id: str
    partner_type: str
    partner_name: str
    share_rate: float
    active: bool
    created_at: Optional[str] = None
    codes: tuple[PartnerCode, ...] = ()


def _not_configured() -> PartnerAdminError:
    logger.error("SOUL_TRACE_SERVICE_TOKEN 미설정 — 파트너 운영 불가")
    return PartnerAdminError(
        "PARTNER_ADMIN_NOT_CONFIGURED",
        "Soul Trace 연동이 설정되지 않았습니다.",
        status=503,
    )


async def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """
    Soul Trace 내부 API 한 번. **실패를 삼키지 않는다** — 운영 조작이라, 조용히
    실패하면 직원은 QR 이 발급된 줄 알고 인쇄를 넘긴다.
    """
    token = _service_token()
    if not token:
        raise _not_configured()

    import httpx

    url = f"{api_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.request(
                method,
                url,
                json=payload,
                headers={"X-EB-Service-Token": token},
            )
    except Exception as e:
        logger.exception("Soul Trace 파트너 API 호출 실패 (%s %s)", method, path)
        raise PartnerAdminError(
            "PARTNER_ADMIN_UNAVAILABLE", "Soul Trace 에 연결하지 못했습니다.", status=502
        ) from e

    if res.status_code in (401, 503):
        # 우리 쪽 설정 문제다. 운영자에게 "권한 없음"으로 보이면 엉뚱한 곳을 본다.
        logger.error("Soul Trace 파트너 API 거절 — status=%s", res.status_code)
        raise PartnerAdminError(
            "PARTNER_ADMIN_UNAVAILABLE", "Soul Trace 연동이 거부되었습니다.", status=502
        )
    if res.status_code == 404:
        raise PartnerAdminError("PARTNER_NOT_FOUND", "대상을 찾을 수 없습니다.", status=404)
    if res.status_code >= 400:
        detail = ""
        try:
            detail = str((res.json() or {}).get("error") or "")
        except Exception:
            pass
        raise PartnerAdminError(
            "PARTNER_ADMIN_REJECTED", detail or "요청이 거부되었습니다.", status=400
        )

    try:
        return res.json()
    except Exception as e:
        raise PartnerAdminError(
            "PARTNER_ADMIN_UNAVAILABLE", "Soul Trace 응답을 읽지 못했습니다.", status=502
        ) from e


def _as_rate(value: Any) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0.0
    return rate if 0.0 <= rate <= 1.0 else 0.0


def _to_code(row: dict[str, Any]) -> PartnerCode:
    track = str(row.get("track") or "").strip().lower() or None
    return PartnerCode(
        code=str(row.get("code") or ""),
        track=track if track in TRACKS else None,
        active=bool(row.get("active")),
        created_at=(str(row["createdAt"]) if row.get("createdAt") else None),
    )


def _to_partner(row: dict[str, Any]) -> Partner:
    return Partner(
        partner_id=str(row.get("partnerId") or ""),
        partner_type=str(row.get("partnerType") or ""),
        partner_name=str(row.get("partnerName") or ""),
        share_rate=_as_rate(row.get("shareRate")),
        active=bool(row.get("active")),
        created_at=(str(row["createdAt"]) if row.get("createdAt") else None),
        codes=tuple(_to_code(c) for c in (row.get("codes") or [])),
    )


async def list_partners() -> list[Partner]:
    data = await _call("GET", "/api/internal/partners")
    return [_to_partner(p) for p in (data or {}).get("partners", [])]


async def create_partner(
    *,
    partner_name: str,
    partner_type: str,
    share_rate: float,
    active: bool = True,
    initial_track: str | None = None,
) -> Partner:
    """
    파트너 등록. **partner_id 는 Soul Trace 가 만든다** — 여기서도, 브라우저에서도
    고르지 않는다. 고를 수 있으면 남의 병원 id 로 귀속을 만들 수 있다.
    """
    name = (partner_name or "").strip()
    if not name:
        raise PartnerAdminError("PARTNER_NAME_REQUIRED", "파트너 이름이 필요합니다.")
    ptype = (partner_type or "").strip().upper()
    if ptype not in PARTNER_TYPES:
        raise PartnerAdminError(
            "PARTNER_TYPE_INVALID", "파트너 유형은 HOSPITAL 또는 FUNERAL 입니다."
        )
    # 0.15 를 15 로 적는 실수를 여기서도 막는다. Soul Trace 와 DB 가 다시 막지만,
    # 가장 가까운 곳에서 걸러야 운영자가 이유를 읽을 수 있다.
    if not (0.0 <= float(share_rate) <= 1.0):
        raise PartnerAdminError(
            "SHARE_RATE_INVALID", "정산 비율은 0 과 1 사이입니다 (0.15 = 15%)."
        )
    track = (initial_track or "").strip().lower() or None
    if track is not None and track not in TRACKS:
        raise PartnerAdminError("TRACK_INVALID", "갈래는 living 또는 memorial 입니다.")

    data = await _call(
        "POST",
        "/api/internal/partners",
        {
            "partnerName": name,
            "partnerType": ptype,
            "shareRate": float(share_rate),
            "active": bool(active),
            **({"initialTrack": track} if track else {}),
        },
    )
    return _to_partner(data or {})


async def update_partner(
    *,
    partner_id: str,
    active: bool | None = None,
    partner_name: str | None = None,
    share_rate: float | None = None,
) -> Partner:
    pid = (partner_id or "").strip()
    if not pid:
        raise PartnerAdminError("PARTNER_ID_REQUIRED", "partner_id 가 필요합니다.")

    payload: dict[str, Any] = {}
    if active is not None:
        payload["active"] = bool(active)
    if partner_name is not None:
        name = partner_name.strip()
        if not name:
            raise PartnerAdminError("PARTNER_NAME_REQUIRED", "파트너 이름이 필요합니다.")
        payload["partnerName"] = name
    if share_rate is not None:
        if not (0.0 <= float(share_rate) <= 1.0):
            raise PartnerAdminError(
                "SHARE_RATE_INVALID", "정산 비율은 0 과 1 사이입니다 (0.15 = 15%)."
            )
        payload["shareRate"] = float(share_rate)
    if not payload:
        raise PartnerAdminError("NOTHING_TO_UPDATE", "변경할 값이 없습니다.")

    data = await _call("PATCH", f"/api/internal/partners/{pid}", payload)
    return _to_partner(data or {})


async def issue_code(*, partner_id: str, track: str | None) -> PartnerCode:
    """QR 코드 발급. 코드 문자열은 **Soul Trace 가** 만든다(무작위·불투명)."""
    pid = (partner_id or "").strip()
    if not pid:
        raise PartnerAdminError("PARTNER_ID_REQUIRED", "partner_id 가 필요합니다.")
    t = (track or "").strip().lower() or None
    if t is not None and t not in TRACKS:
        raise PartnerAdminError("TRACK_INVALID", "갈래는 living 또는 memorial 입니다.")

    data = await _call(
        "POST", "/api/internal/partner-codes", {"partnerId": pid, "track": t}
    )
    return _to_code(data or {})


async def set_code_active(*, code: str, active: bool) -> PartnerCode:
    """
    코드를 켜고 끈다. 끄면 **새 귀속만** 멈춘다 — 이미 그 코드로 귀속된 편지와
    주문은 그대로다. 그래야 인쇄물을 회수해도 과거 정산 근거가 남는다.
    """
    c = (code or "").strip()
    if not c:
        raise PartnerAdminError("CODE_REQUIRED", "코드가 필요합니다.")
    data = await _call(
        "PATCH", f"/api/internal/partner-codes/{c}", {"active": bool(active)}
    )
    return _to_code(data or {})
