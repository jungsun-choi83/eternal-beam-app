"""
전시 핸드오프 클라이언트 — READY 패키지를 외부 전시 시스템에 넘긴다.

── 범위 ─────────────────────────────────────────────────────────────────────
우리 책임은 "패키지가 준비됐다" 를 외부 시스템에 알리고 ACK 를 받는 데까지다.
셰이더 변형·호흡 애니메이션·디바이스 표시는 외부 시스템(다른 개발자 소유)이
한다. 이 모듈은 패키지 파일을 다시 만들지 않는다 — 이미 저장된
exhibition/{run_id}/visitor_XXXX.zip 하나의 서명 URL 만 보낸다.

── 요청 ─────────────────────────────────────────────────────────────────────
  POST {EXHIBITION_HANDOFF_URL}
  Content-Type: application/json
  Idempotency-Key: <run_id>          (같은 run_id 재전송 = 같은 패키지)
  Authorization: Bearer <EXHIBITION_HANDOFF_TOKEN>   (설정된 경우만)

  {"event": "pet_asset", "run_id", "package_id": "visitor_0028",
   "schema_version": "EXHIBITION_RIG_INPUT_V1", "motion": "BREATHING",
   "packed_url": <signed URL to visitor_0028.zip>}

── ACK ──────────────────────────────────────────────────────────────────────
  2xx + {"run_id": <같은 run_id>, "accepted": true}  → 성공
  그 외(타임아웃·네트워크·5xx·4xx·accepted≠true·run_id 불일치) → HandoffError

── device_gateway 모드 ───────────────────────────────────────────────────────
  EXHIBITION_HANDOFF_MODE=device_gateway 면 EXHIBITION_HANDOFF_URL 을 호출하지
  않고, 같은 pet_asset 페이로드를 기존 Device Gateway(backend/services/device_gateway.py)
  로 enqueue 한다 — beam-001 등 하드웨어 전시 기기로 보내는 경로다. 대상 device_id
  는 새 환경변수 없이 기존 ETERNAL_BEAM_DEVICE_TOKENS(또는 ETERNAL_BEAM_DEVICE_ID/
  _TOKEN)에서 고른다: 정확히 하나만 설정돼 있어야 하며, 0개나 2개 이상이면
  (어느 걸 쓸지 안전하게 알 수 없으므로) HandoffError 로 실패하고 새 기기를
  추측하지 않는다 — handoff_error_code 로 원인이 남는다.
  enqueue 성공은 "Device Gateway 가 전달을 맡았다"만 의미한다 — http 모드의
  2xx+accepted 와 같은 레벨이다. Pi 의 실제 ACK/디스플레이 여부는 기존
  device_commands 테이블/WS ACK 로만 추적되며 이 핸드오프 상태표와는 별개다.

── 환경변수 ─────────────────────────────────────────────────────────────────
  EXHIBITION_HANDOFF_MODE         mock | http | device_gateway | off
                                  (기본: URL 이 있으면 http, 없으면 off)
  EXHIBITION_HANDOFF_URL          외부 수신 엔드포인트 (http 모드 필수)
  EXHIBITION_HANDOFF_TOKEN        선택 — Bearer 토큰
  EXHIBITION_HANDOFF_TIMEOUT_SEC  기본 10
  EXHIBITION_HANDOFF_URL_TTL_SEC  서명 URL 유효시간, 기본 86400 (24h)
  EXHIBITION_HANDOFF_STALE_SEC    SENDING 이 이보다 오래되면 재전송 허용, 기본 120
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

HANDOFF_SCHEMA_VERSION = "EXHIBITION_RIG_INPUT_V1"
HANDOFF_MOTION = "BREATHING"
HANDOFF_EVENT = "pet_asset"

MODE_MOCK = "mock"
MODE_HTTP = "http"
MODE_DEVICE_GATEWAY = "device_gateway"
MODE_OFF = "off"

#: row 의 ZIP 패키지 경로 컬럼 (exhibition_prep_service.process_run 이 쓴다).
PACKAGE_ZIP_FIELD = "package_zip_path"


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
        return self.mode in (MODE_MOCK, MODE_HTTP, MODE_DEVICE_GATEWAY)

    @classmethod
    def from_env(cls) -> "HandoffConfig":
        url = (os.getenv("EXHIBITION_HANDOFF_URL") or "").strip() or None
        mode = (os.getenv("EXHIBITION_HANDOFF_MODE") or "").strip().lower()
        if mode not in (MODE_MOCK, MODE_HTTP, MODE_DEVICE_GATEWAY, MODE_OFF):
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
    """이미 저장된 visitor_XXXX.zip 하나의 서명 URL 로 페이로드를 만든다. 파일은 만들지 않는다."""
    manifest = row.get("manifest_json") or {}
    schema = manifest.get("schema_version")
    if schema != HANDOFF_SCHEMA_VERSION:
        raise HandoffError(
            "HANDOFF_SCHEMA_MISMATCH",
            f"Package schema {schema!r} is not {HANDOFF_SCHEMA_VERSION}.",
            retryable=False,
        )
    package_id = row.get("package_id")
    zip_path = row.get(PACKAGE_ZIP_FIELD)
    if not package_id or not zip_path:
        raise HandoffError(
            "HANDOFF_PACKAGE_UNAVAILABLE",
            "ZIP package is missing (run has no queue_number or was not packaged).",
            retryable=True,
        )
    url = artifacts.signed_url(zip_path, url_ttl_sec)
    if not url:
        raise HandoffError(
            "HANDOFF_PACKAGE_UNAVAILABLE", f"ZIP package at {zip_path} could not be signed.", retryable=True
        )
    return {
        "event": HANDOFF_EVENT,
        "run_id": str(row["id"]),
        "package_id": package_id,
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "motion": HANDOFF_MOTION,
        "packed_url": url,
    }


def resolve_single_device_id() -> str:
    """device_gateway 모드가 보낼 단 하나의 device_id 를 고른다.

    새 EXHIBITION_DEVICE_ID 류 설정을 추가하지 않고 기존
    ETERNAL_BEAM_DEVICE_TOKENS(/_ID/_TOKEN)만 본다. 정확히 하나가 아니면
    어느 쪽도 추측하지 않고 실패한다 — 운영자가 설정을 고쳐야 한다.
    """
    from . import device_gateway

    devices = device_gateway.configured_devices()
    if len(devices) == 1:
        return next(iter(devices))
    if not devices:
        raise HandoffError(
            "HANDOFF_DEVICE_GATEWAY_NO_DEVICE",
            "No device is configured (ETERNAL_BEAM_DEVICE_TOKENS / ETERNAL_BEAM_DEVICE_ID+TOKEN is empty).",
            retryable=False,
        )
    raise HandoffError(
        "HANDOFF_DEVICE_GATEWAY_AMBIGUOUS_DEVICE",
        f"{len(devices)} devices are configured ({sorted(devices)}); exhibition handoff cannot "
        "guess which one to use. Configure exactly one device for device_gateway handoff mode.",
        retryable=False,
    )


def redacted_payload(payload: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """DB 기록용 — 서명 URL(토큰 포함) 대신 저장 경로를 남긴다."""
    out = {k: v for k, v in payload.items() if k != "packed_url"}
    out["zip_path"] = row.get(PACKAGE_ZIP_FIELD)
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
        if cfg.mode == MODE_DEVICE_GATEWAY:
            return self._send_via_device_gateway(payload, run_id)
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

    def _send_via_device_gateway(self, payload: dict[str, Any], run_id: str) -> HandoffAck:
        """같은 pet_asset 페이로드를 EXHIBITION_HANDOFF_URL 없이 Device Gateway 로 enqueue 한다.

        enqueue 는 디바이스가 연결돼 있지 않아도 명령을 영속 저장하고 즉시
        반환한다 (device_gateway.py 의 기존 offline-pending/reconnect-replay 그대로).
        accepted=True 는 "Gateway 가 전달을 맡았다"까지다 — Pi 가 실제로 ACK 했는지는
        여기서 보장하지 않는다(기존 device_commands 테이블/ACK 로만 추적된다).
        """
        from . import device_gateway

        device_id = resolve_single_device_id()
        try:
            command = asyncio.run(device_gateway.enqueue(device_id, dict(payload)))
        except HandoffError:
            raise
        except Exception as e:  # noqa: BLE001 — 게이트웨이 내부 오류를 핸드오프 실패로 환산
            raise HandoffError(
                "HANDOFF_DEVICE_GATEWAY_ERROR", f"{type(e).__name__}: {e}"[:500], retryable=True
            ) from e
        logger.info(
            "exhibition handoff [device_gateway] run_id=%s device_id=%s command_id=%s",
            run_id, device_id, command.get("command_id"),
        )
        return HandoffAck(
            run_id=run_id,
            accepted=True,
            raw={
                "run_id": run_id,
                "accepted": True,
                "device_gateway": True,
                "device_id": device_id,
                "command_id": command.get("command_id"),
            },
        )
