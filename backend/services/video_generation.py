"""
영상 생성 프로바이더 디스패처.

    VIDEO_PROVIDER=luma  (기본, 미설정 시에도 luma)  → luma_service   (프로덕션)
    VIDEO_PROVIDER=wan                              → wan_service    (개발/테스트)

목적은 개발 중 비용 절감 하나뿐이다. luma_service.py 는 이 작업으로 한 줄도
수정되지 않았고, 환경변수를 되돌리는 것만으로 기존 Luma 경로가 그대로 살아난다.

동기(polling) 아이들 생성 경로만 프로바이더화한다. Luma 웹훅/크레딧 비동기
경로(pet_v1.py, credit_*, motion_generation_jobs.luma_generation_id)는 손대지
않는다 — 그쪽은 DB 스키마까지 Luma 에 묶여 있다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


def generation_mock_enabled() -> bool:
    """
    **프로바이더 중립 생성 차단 스위치.**

    GENERATION_MOCK=1 이면 어떤 프로바이더로도 실제 제출을 하지 않는다.

    왜 필요한가: 기존 목업은 프로바이더마다 따로였다. LUMA_MOCK=1 은 Luma 만
    막고, wan_service 에는 목업 자체가 없다. 그래서 VIDEO_PROVIDER 를 wan 으로
    바꾸는 순간 — 오타 하나로도 — 실제 fal.ai 호출이 나간다. 프로바이더 선택과
    과금 차단이 같은 축에 있으면 안 된다.

    이 스위치는 **디스패처 한 곳**에 있으므로 프로바이더를 무엇으로 두든 유효하다.
    통제된 실 생성 1건을 하려면 이 값을 끄는 것이 유일한 관문이다.
    """
    return os.getenv("GENERATION_MOCK", "0").strip().lower() in ("1", "true", "yes")

PROVIDER_LUMA = "luma"
PROVIDER_WAN = "wan"              # 하위호환 별칭 → wan_turbo
PROVIDER_WAN_TURBO = "wan_turbo"
PROVIDER_WAN_A14B = "wan_a14b"
DEFAULT_PROVIDER = PROVIDER_LUMA

_KNOWN_PROVIDERS = (PROVIDER_LUMA, PROVIDER_WAN, PROVIDER_WAN_TURBO, PROVIDER_WAN_A14B)

#: 별칭 정규화. "wan" 은 예전 .env 호환용으로 남긴다.
_PROVIDER_ALIASES = {PROVIDER_WAN: PROVIDER_WAN_TURBO}

#: fal 모델 경로. turbo 는 duration 파라미터가 없고, a14b(비turbo)는 num_frames 를
#: 노출한다 — 네이티브 짧은 클립 실험은 a14b 로만 가능하다.
_WAN_MODELS = {
    PROVIDER_WAN_TURBO: "fal-ai/wan/v2.2-a14b/image-to-video/turbo",
    PROVIDER_WAN_A14B: "fal-ai/wan/v2.2-a14b/image-to-video",
}

#: 완료 통지 방식. luma 는 callback_url, fal 은 fal_webhook — 둘 다 push.
COMPLETION_PUSH = "push"


class UnknownVideoProviderError(RuntimeError):
    """VIDEO_PROVIDER* 에 알 수 없는 값이 명시된 경우."""


def normalize_provider(raw: str) -> str:
    """별칭 해소 + 검증. 알 수 없는 값이면 즉시 예외(과금 전에 중단)."""
    v = (raw or "").strip().lower()
    v = _PROVIDER_ALIASES.get(v, v)
    if v not in _KNOWN_PROVIDERS:
        raise UnknownVideoProviderError(
            f"video provider={raw!r} 는 알 수 없는 값입니다. "
            f"사용 가능: {', '.join(_KNOWN_PROVIDERS)} (미설정 시 {DEFAULT_PROVIDER}). "
            f"오타로 인한 의도치 않은 과금을 막기 위해 생성 요청 전에 중단합니다."
        )
    return v


def is_wan_provider(provider: str) -> bool:
    return provider in _WAN_MODELS


def provider_model_name(provider: str) -> str:
    """프로바이더 → 실제 모델 식별자."""
    if provider == PROVIDER_LUMA:
        return (os.getenv("LUMA_MODEL") or "ray-2").strip()
    return _WAN_MODELS[provider]


def resolve_action_provider(action_id: str) -> str:
    """
    System B 액션별 프로바이더 결정. 구체적인 설정이 이긴다:

        VIDEO_PROVIDER_<ACTION>   (VIDEO_PROVIDER_TOUCH / _VOICE / _NFC / _IDLE)
        VIDEO_PROVIDER_ACTION     (System B 4종 공통 기본값)
        VIDEO_PROVIDER            (기존 전역 — System A 와 공유)
        luma                      (아무것도 없으면)

    아무것도 설정하지 않으면 luma 다 — 기존 배포와 100% 동일하게 동작한다.
    """
    act = (action_id or "").strip().upper()
    for key in (f"VIDEO_PROVIDER_{act}" if act else None, "VIDEO_PROVIDER_ACTION", "VIDEO_PROVIDER"):
        if not key:
            continue
        raw = (os.getenv(key) or "").strip()
        if raw:
            return normalize_provider(raw)
    return DEFAULT_PROVIDER


def get_video_provider() -> str:
    """
    현재 프로바이더.

    - 미설정/공백 → luma (기존 배포 하위 호환)
    - "luma" | "wan" → 그대로
    - 그 외 명시된 값 → 즉시 예외

    오타(예: VIDEO_PROVIDER=wanna)를 조용히 luma 로 흘려보내지 않는다 —
    개발 중 의도치 않은 Luma 크레딧 소모를 막는 것이 목적이다. 예외는 어떤
    생성 요청보다도 먼저 발생한다.
    """
    raw = (os.getenv("VIDEO_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw not in _KNOWN_PROVIDERS:
        raise UnknownVideoProviderError(
            f"VIDEO_PROVIDER={raw!r} 는 알 수 없는 값입니다. "
            f"사용 가능: {', '.join(_KNOWN_PROVIDERS)} "
            f"(미설정 시 {DEFAULT_PROVIDER}). 오타로 인한 의도치 않은 과금을 막기 위해 "
            f"생성 요청 전에 중단합니다."
        )
    return raw


# ---------------------------------------------------------------------------
# 프로바이더 중립 비동기 제출 (System B 용)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmittedJob:
    """제출 직후의 작업 핸들 — 폴링 없이 반환된다."""

    provider: str
    external_id: str          # luma generation id | fal request_id
    model: str
    completion: str = COMPLETION_PUSH
    poll_url: Optional[str] = None    # fal status_url (재조정용 예비)
    result_url: Optional[str] = None  # fal response_url (재조정용 예비)


@dataclass(frozen=True)
class GenerationOutcome:
    """웹훅 본문을 프로바이더와 무관한 형태로 정규화한 결과."""

    provider: str
    external_id: str
    state: str                # "completed" | "failed" | "pending"
    video_url: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_completed(self) -> bool:
        return self.state == "completed"

    @property
    def is_failed(self) -> bool:
        return self.state == "failed"


async def submit_generation(
    image_url: str,
    prompt: str,
    *,
    provider: str,
    callback_url: Optional[str] = None,
    model: Optional[str] = None,
    resolution: Optional[str] = None,
) -> SubmittedJob:
    """
    제출만 하고 즉시 핸들을 돌려준다 (폴링하지 않는다).

    두 프로바이더 모두 push 완료를 쓴다:
      luma → POST body 의 callback_url
      fal  → 제출 URL 의 fal_webhook 쿼리 파라미터
    """
    provider = normalize_provider(provider)
    mdl = model or provider_model_name(provider)

    # ── 프로바이더 중립 차단 ─────────────────────────────────────────────────
    # 프로바이더 분기보다 **먼저** 본다. 여기서 막으면 luma 든 wan 이든, 키가
    # 설정돼 있든 없든 유료 호출이 나갈 수 없다.
    if generation_mock_enabled():
        fake = f"mock_{uuid.uuid4().hex[:12]}"
        logger.warning(
            "GENERATION_MOCK=1 — 실제 제출을 건너뛴다 (provider=%s model=%s external_id=%s). "
            "통제된 실 생성을 하려면 GENERATION_MOCK 을 끄십시오.",
            provider, mdl, fake,
        )
        return SubmittedJob(provider=provider, external_id=fake, model=mdl)

    # 유료 호출 **직전** 스키마 확인. 제출은 성공했는데 뒤이은 DB 쓰기가 실패해
    # 돈만 나가고 복구 불가가 되는 사고를 원천 차단한다.
    from .generation_safety import ensure_reliability_schema

    ensure_reliability_schema()

    if provider == PROVIDER_LUMA:
        from .luma_service import create_generation as _luma_submit

        gen_id = await _luma_submit(
            image_url,
            prompt=prompt,
            model=mdl,
            resolution=(resolution or os.getenv("LUMA_RESOLUTION", "720p")),
            callback_url=callback_url,
        )
        return SubmittedJob(provider=provider, external_id=gen_id, model=mdl)

    from . import wan_service

    sub = await wan_service.create_generation(
        image_url,
        prompt,
        model=mdl,
        resolution=resolution,
        webhook_url=callback_url,
    )
    return SubmittedJob(
        provider=provider,
        external_id=sub.request_id,
        model=mdl,
        poll_url=sub.status_url,
        result_url=sub.response_url,
    )


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def normalize_webhook(body: dict[str, Any]) -> Optional[GenerationOutcome]:
    """
    Luma / fal 웹훅 본문 → 공통 GenerationOutcome.

    구분은 **키 존재 여부**로 한다 (ID 포맷 추측 금지):
        luma : {"id", "state", "assets": {"video"}, "failure_reason"}
        fal  : {"request_id", "status", "payload": {"video": {"url"}}, "error"}

    어느 쪽도 아니면 None — 호출자가 400 을 돌려준다.
    """
    if not isinstance(body, dict):
        return None

    # ── fal ────────────────────────────────────────────────────────────────
    req_id = _first(body, "request_id", "requestId")
    if req_id and ("status" in body or "payload" in body):
        raw_state = str(_first(body, "status") or "").upper()
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        video_url = None
        if payload:
            vid = payload.get("video")
            if isinstance(vid, dict):
                video_url = vid.get("url")
            elif isinstance(vid, str):
                video_url = vid
        err = _first(body, "error", "detail")
        if isinstance(err, dict):
            err = str(err)
        if raw_state in ("OK", "COMPLETED", "SUCCESS"):
            state = "completed" if video_url else "pending"
        elif raw_state in ("ERROR", "FAILED", "CANCELLED"):
            state = "failed"
        else:
            state = "pending"
        # fal 은 성공이어도 error 필드를 비워 보낸다 — 실패일 때만 채운다.
        return GenerationOutcome(
            provider=PROVIDER_WAN_TURBO,
            external_id=str(req_id),
            state=state,
            video_url=video_url,
            error=str(err) if (err and state == "failed") else None,
        )

    # ── luma ───────────────────────────────────────────────────────────────
    gen_id = _first(body, "id")
    if gen_id:
        raw_state = str(_first(body, "state") or "").lower()
        assets = body.get("assets") if isinstance(body.get("assets"), dict) else {}
        video_url = assets.get("video") if assets else None
        err = _first(body, "failure_reason", "failureReason")
        if raw_state == "completed":
            state = "completed" if video_url else "pending"
        elif raw_state in ("failed", "error"):
            state = "failed"
        else:
            state = "pending"
        return GenerationOutcome(
            provider=PROVIDER_LUMA,
            external_id=str(gen_id),
            state=state,
            video_url=video_url,
            error=str(err) if err else None,
        )

    return None


def is_luma() -> bool:
    return get_video_provider() == PROVIDER_LUMA


def active_model_name() -> str:
    """로그/리포트용 모델 이름 (프로바이더별 기본값 포함)."""
    if is_luma():
        return (os.getenv("LUMA_MODEL") or "ray-2").strip()
    from . import wan_service

    return wan_service.model_name()


async def create_generation_and_get_video_url(
    image_url: str,
    prompt: str,
    model: Optional[str] = None,
    resolution: Optional[str] = None,
    poll_interval: float = 5.0,
    poll_max_wait: Optional[float] = None,
    on_submit=None,
) -> str:
    """
    이미지 URL + 프롬프트 → 완성된 영상 URL (문자열).

    on_submit(provider_job_id) 은 제출 직후 폴링 전에 불린다(선택). 프로바이더가
    지원하지 않으면 호출되지 않으며, 호출부는 그 경우에도 동작해야 한다.

    기존 호출부(luma_idle_pipeline / generate.py / background_video_pipeline)가
    쓰던 luma_service 동명 함수와 계약이 동일하다 — 반환값도 그대로 URL 문자열이라
    호출부의 후처리 코드는 바뀌지 않는다.
    """
    if is_luma():
        from .luma_service import (
            create_generation_and_get_video_url as _luma_generate,
        )

        # model/resolution 이 None 이면 인자를 넘기지 않는다 —
        # luma_service 자신의 기본값(ray-2 / 720p)을 그대로 쓰기 위함.
        kwargs = {"poll_interval": poll_interval}
        if on_submit is not None:
            kwargs["on_submit"] = on_submit
        if model is not None:
            kwargs["model"] = model
        if resolution is not None:
            kwargs["resolution"] = resolution
        if poll_max_wait is not None:
            kwargs["poll_max_wait"] = poll_max_wait
        return await _luma_generate(image_url, prompt, **kwargs)

    from . import wan_service

    return await wan_service.create_generation_and_get_video_url(
        image_url,
        prompt,
        model=model,
        resolution=resolution,
        poll_interval=poll_interval,
        poll_max_wait=poll_max_wait,
    )


# ---------------------------------------------------------------------------
# 결과 URL/컨테이너 판정
# ---------------------------------------------------------------------------


def _url_path(url: str) -> str:
    return (url or "").split("?", 1)[0].split("#", 1)[0].lower()


def looks_like_video_url(url: str, provider: Optional[str] = None) -> bool:
    """
    "이 URL 을 성공한 생성 결과로 받아들일 수 있는가."

    Luma: 기존과 완전히 동일한 .mp4 접미사 검사 (동작 보존).
    그 외: True. fal CDN URL 은 확장자가 없는 경우가 많은데, 단지 접미사가 없다는
    이유로 실패 처리하면 **성공한 유료 생성이 버려지고 재생성이 돌아간다**.
    실제 컨테이너 검증은 내려받은 바이트로 sniff_video_container() 가 한다.
    """
    prov = (provider or get_video_provider()).strip().lower()
    if prov == PROVIDER_LUMA:
        return _url_path(url).endswith(".mp4")
    return bool((url or "").strip())


def sniff_video_container(data: bytes) -> Optional[str]:
    """
    내려받은 바이트가 실제 영상 컨테이너인지 매직바이트로 확인.
    반환: "mp4" | "webm" | None (판별 불가)
    """
    if not data or len(data) < 12:
        return None
    # ISO-BMFF (mp4/mov): 4바이트 박스 크기 뒤에 'ftyp'
    if data[4:8] == b"ftyp":
        return "mp4"
    # Matroska/WebM: EBML 헤더
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None
