"""
전시 핸드오프 클라이언트 — READY 패키지를 외부 전시 시스템에 넘긴다.

── 범위 ─────────────────────────────────────────────────────────────────────
우리 책임은 "패키지가 준비됐다" 를 외부 시스템에 알리고 ACK 를 받는 데까지다.
셰이더 변형·호흡 애니메이션·디바이스 표시는 외부 시스템(다른 개발자 소유)이
한다. 이 모듈은 패키지 파일을 다시 만들지 않는다 — 이미 저장된
exhibition/{run_id}/ 파일 4개의 서명 URL 만 보낸다.

── 요청 ─────────────────────────────────────────────────────────────────────
  POST {EXHIBITION_HANDOFF_URL}
  Content-Type: application/json
  Idempotency-Key: <run_id>          (같은 run_id 재전송 = 같은 패키지)
  Authorization: Bearer <EXHIBITION_HANDOFF_TOKEN>   (설정된 경우만)

  {"run_id", "exhibition_id", "schema_version": "EXHIBITION_RIG_INPUT_V1",
   "motion": "BREATHING", "subject_rgba_url", "breathing_weight_url",
   "locked_mask_url", "manifest_url"}

── ACK ──────────────────────────────────────────────────────────────────────
  2xx + {"run_id": <같은 run_id>, "accepted": true}  → 성공
  그 외(타임아웃·네트워크·5xx·4xx·accepted≠true·run_id 불일치) → HandoffError

── 환경변수 ─────────────────────────────────────────────────────────────────
  EXHIBITION_HANDOFF_MODE         mock | http | off
                                  (기본: URL 이 있으면 http, 없으면 off)
  EXHIBITION_HANDOFF_URL          외부 수신 엔드포인트 (http 모드 필수)
  EXHIBITION_HANDOFF_TOKEN        선택 — Bearer 토큰
  EXHIBITION_HANDOFF_TIMEOUT_SEC  기본 10
  EXHIBITION_HANDOFF_URL_TTL_SEC  서명 URL 유효시간, 기본 86400 (24h)
  EXHIBITION_HANDOFF_STALE_SEC    SENDING 이 이보다 오래되면 재전송 허용, 기본 120
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

HANDOFF_SCHEMA_VERSION = "EXHIBITION_RIG_INPUT_V1"
HANDOFF_MOTION = "BREATHING"

MODE_MOCK = "mock"
MODE_HTTP = "http"
MODE_OFF = "off"

#: 페이로드 키 → 패키지 파일 이름 (exhibition_prep_service.PACKAGE_FILES 와 같은 이름).
PAYLOAD_FILES = {
    "subject_rgba_url": "subject_rgba.png",
    "breathing_weight_url": "breathing_weight.png",
    "locked_mask_url": "locked_mask.png",
    "manifest_url": "manifest.json",
}


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class HandoffConfig:
    mode: str
    url: Optional[str] = None
    token: Optional[str] = field(default=None, repr=False)
    timeout_sec: float = 10.0
    url_ttl_sec: int = 86400
    stale_sec: float = 120.0

    @property
    def enabled(self) -> bool:
        return self.mode in (MODE_MOCK, MODE_HTTP)

    @classmethod
    def from_env(cls) -> "HandoffConfig":
        url = (os.getenv("EXHIBITION_HANDOFF_URL") or "").strip() or None
        mode = (os.getenv("EXHIBITION_HANDOFF_MODE") or "").strip().lower()
        if mode not in (MODE_MOCK, MODE_HTTP, MODE_OFF):
            mode = MODE_HTTP if url else MODE_OFF
        return cls(
            mode=mode,
            url=url,
            token=(os.getenv("EXHIBITION_HANDOFF_TOKEN") or "").strip() or None,
            timeout_sec=_env_float("EXHIBITION_HANDOFF_TIMEOUT_SEC", 10.0, 1.0),
            url_ttl_sec=int(_env_float("EXHIBITION_HANDOFF_URL_TTL_SEC", 86400, 60)),
            stale_sec=_env_float("EXHIBITION_HANDOFF_STALE_SEC", 120.0, 10.0),
        )


class HandoffError(Exception):
    """핸드오프 실패. code 는 handoff_error_code 로 저장된다.

    retryable 은 정보용이다 (타임아웃·네트워크·5xx 는 True). 운영자 재시도는
    HANDOFF_FAILED 이면 항상 허용되며, 같은 패키지를 다시 보낼 뿐이다.
    """

    def __init__(self, code: str, message: str, *, retryable: bool, http_status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class HandoffAck:
    run_id: str
    accepted: bool
    raw: dict[str, Any]


#: (url, json_payload, headers, timeout_sec) → (http_status, parsed_json_or_None, text_snippet)
Transport = Callable[[str, dict[str, Any], dict[str, str], float], tuple[int, Any, str]]


def _requests_transport(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> tuple[int, Any, str]:
    import requests

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.Timeout as e:
        raise HandoffError("HANDOFF_TIMEOUT", f"No response within {timeout:g}s.", retryable=True) from e
    except requests.RequestException as e:
        raise HandoffError("HANDOFF_NETWORK_ERROR", f"{type(e).__name__}: {e}"[:500], retryable=True) from e
    try:
        body = res.json()
    except ValueError:
        body = None
    return res.status_code, body, (res.text or "")[:500]


# ── 페이로드 ─────────────────────────────────────────────────────────────────


def build_payload(row: dict[str, Any], artifacts: Any, *, url_ttl_sec: int) -> dict[str, Any]:
    """이미 저장된 패키지 파일 4개의 서명 URL 로 페이로드를 만든다. 파일은 만들지 않는다."""
    outputs = row.get("outputs_json") or {}
    manifest = row.get("manifest_json") or {}
    schema = manifest.get("schema_version")
    if schema != HANDOFF_SCHEMA_VERSION:
        raise HandoffError(
            "HANDOFF_SCHEMA_MISMATCH",
            f"Package schema {schema!r} is not {HANDOFF_SCHEMA_VERSION}.",
            retryable=False,
        )
    payload: dict[str, Any] = {
        "run_id": str(row["id"]),
        "exhibition_id": row.get("exhibition_id"),
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "motion": HANDOFF_MOTION,
    }
    for key, name in PAYLOAD_FILES.items():
        path = outputs.get(name)
        url = artifacts.signed_url(path, url_ttl_sec) if path else None
        if not url:
            raise HandoffError(
                "HANDOFF_PACKAGE_UNAVAILABLE",
                f"Package file {name} is missing or could not be signed.",
                retryable=True,
            )
        payload[key] = url
    return payload


def redacted_payload(payload: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """DB 기록용 — 서명 URL(토큰 포함) 대신 저장 경로를 남긴다."""
    outputs = row.get("outputs_json") or {}
    out = {k: v for k, v in payload.items() if k not in PAYLOAD_FILES}
    out["files"] = {key: outputs.get(name) for key, name in PAYLOAD_FILES.items()}
    return out


def parse_ack(body: Any, run_id: str) -> HandoffAck:
    if not isinstance(body, dict):
        raise HandoffError("HANDOFF_BAD_ACK", "ACK is not a JSON object.", retryable=True)
    if str(body.get("run_id")) != run_id:
        raise HandoffError(
            "HANDOFF_ACK_MISMATCH", f"ACK run_id {body.get('run_id')!r} does not match {run_id}.", retryable=True
        )
    if body.get("accepted") is not True:
        raise HandoffError("HANDOFF_NOT_ACCEPTED", "External system did not accept the package.", retryable=False)
    return HandoffAck(run_id=run_id, accepted=True, raw=body)


# ── 클라이언트 ───────────────────────────────────────────────────────────────


class ExhibitionHandoffClient:
    def __init__(self, config: Optional[HandoffConfig] = None, *, transport: Optional[Transport] = None) -> None:
        self.config = config or HandoffConfig.from_env()
        self._transport = transport or _requests_transport

    def send(self, payload: dict[str, Any]) -> HandoffAck:
        run_id = str(payload["run_id"])
        cfg = self.config
        if cfg.mode == MODE_MOCK:
            logger.info("exhibition handoff [mock] run_id=%s exhibition_id=%s", run_id, payload.get("exhibition_id"))
            return HandoffAck(run_id=run_id, accepted=True, raw={"run_id": run_id, "accepted": True, "mock": True})
        if cfg.mode != MODE_HTTP or not cfg.url:
            raise HandoffError(
                "HANDOFF_NOT_CONFIGURED",
                "Set EXHIBITION_HANDOFF_URL (or EXHIBITION_HANDOFF_MODE=mock).",
                retryable=False,
            )
        headers = {"Content-Type": "application/json", "Idempotency-Key": run_id}
        if cfg.token:
            headers["Authorization"] = f"Bearer {cfg.token}"
        status, body, text = self._transport(cfg.url, payload, headers, cfg.timeout_sec)
        if status >= 500:
            raise HandoffError("HANDOFF_HTTP_5XX", f"HTTP {status}: {text}"[:500], retryable=True, http_status=status)
        if status in (408, 429):
            raise HandoffError("HANDOFF_HTTP_RETRYABLE", f"HTTP {status}: {text}"[:500], retryable=True,
                               http_status=status)
        if not 200 <= status < 300:
            raise HandoffError("HANDOFF_REJECTED", f"HTTP {status}: {text}"[:500], retryable=False,
                               http_status=status)
        return parse_ack(body, run_id)
