"""
모션 비디오 프로바이더 추상화 + 라우팅 정책 (Phase 6).

── 계약 ────────────────────────────────────────────────────────────────────
VideoGenerationProvider.generate(request) → MotionVideoResult(video_bytes …)
프로바이더별 요청 형태는 어댑터 안에만 산다. 비즈니스 로직에는 프로바이더
이름이 등장하지 않는다 — 라우팅은 아래 routing_for_class() 하나가 정본이다.

── 어댑터 (초기 2개 + mock) ────────────────────────────────────────────────
seedance  Seedance 2.5 (BytePlus Ark 태스크 API). first/last frame 이미지 입력.
kling     Kling V3 (image2video + image_tail = start/end 계약, JWT 인증).
mock      개발용 — 시작 이미지를 그대로 돌려준다. 과금 없음.

두 실서비스 API 는 드리프트하는 외부 계약이라 모델 id/베이스 URL 전부 env 다.
유닛 테스트는 이 모듈의 실 어댑터를 호출하지 않는다 (가짜 주입).

── 라이브 안전 (요구 17) ───────────────────────────────────────────────────
PHASE6_LIVE_MODE = off(기본) | allowlist | all
  off        mock 프로바이더만 허용 — 실수로 대량 과금이 불가능하다
  allowlist  PHASE6_LIVE_ALLOWLIST (쉼표 구분 pet_id) 의 펫만 실 호출
  all        전면 허용 (프로덕션 승격 단계에서만)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROVIDER_SEEDANCE = "seedance"
PROVIDER_KLING = "kling"
PROVIDER_MOCK = "mock"


class VideoProviderError(Exception):
    """프로바이더/전송 실패 — QA 실패와 구분 (candidate.decision=ERROR)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MotionVideoRequest:
    prompt: str
    #: 시작 키프레임 (서명 URL + 바이트 둘 다 — 어댑터가 맞는 쪽을 쓴다).
    start_image_url: Optional[str]
    start_image_bytes: Optional[bytes]
    #: 목표 키프레임 — START_END_FRAME 전략에서만. 어댑터는 이것을 **버릴 수 없다**:
    #: supports_end_frame 이 아닌 어댑터에 end 가 오면 예외다 (조용한 강등 금지).
    end_image_url: Optional[str] = None
    end_image_bytes: Optional[bytes] = None
    #: 명시적 출력 사양 — aspect_ratio/resolution/duration_sec/audio/camera_fixed.
    output_spec: dict[str, Any] = field(default_factory=dict)
    #: 추가 신원 레퍼런스 URL (프로바이더별 예산 내에서).
    reference_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MotionVideoResult:
    video_bytes: bytes
    provider: str
    model: str
    external_job_id: Optional[str] = None
    duration_sec: Optional[float] = None
    resolution: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)


class VideoGenerationProvider:
    name: str = "abstract"
    supports_end_frame: bool = False
    supports_motion_reference: bool = False
    #: 신원 레퍼런스 이미지 예산 (시작/끝 프레임 제외).
    reference_budget: int = 0

    def available(self) -> bool:  # pragma: no cover
        return False

    def model_name(self) -> str:  # pragma: no cover
        return ""

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:  # pragma: no cover
        raise NotImplementedError


def _download(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=120.0, follow_redirects=True)
    if r.status_code >= 300 or not r.content:
        raise VideoProviderError("PROVIDER_DOWNLOAD", f"결과 다운로드 실패 ({r.status_code})")
    return r.content


# ══════════════════════════════════════════════════════════════════════════
# Seedance 2.5 (BytePlus Ark)
# ══════════════════════════════════════════════════════════════════════════


class SeedanceProvider(VideoGenerationProvider):
    name = PROVIDER_SEEDANCE
    supports_end_frame = True   # first_frame / last_frame 이미지 역할
    supports_motion_reference = False  # 레퍼런스 영상 워크플로는 라이브 검증 후 개방
    reference_budget = 0

    def _key(self) -> str:
        return (os.getenv("SEEDANCE_API_KEY") or os.getenv("ARK_API_KEY") or "").strip()

    def _base(self) -> str:
        return (
            os.getenv("SEEDANCE_API_BASE")
            or "https://ark.ap-southeast.bytepluses.com/api/v3"
        ).rstrip("/")

    def model_name(self) -> str:
        return (os.getenv("SEEDANCE_MODEL") or "seedance-2-5-pro").strip()

    def available(self) -> bool:
        return bool(self._key())

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:
        import httpx

        if not self.available():
            raise VideoProviderError("PROVIDER_NOT_CONFIGURED", "SEEDANCE_API_KEY 가 없습니다.")
        if not request.start_image_url:
            raise VideoProviderError("NO_START_IMAGE", "시작 키프레임 URL 이 없습니다.")

        spec = request.output_spec
        # 출력 사양을 텍스트 플래그로 **명시**한다 — 프로바이더 기본값 금지.
        flags = (
            f" --ratio {spec.get('aspect_ratio', '9:16')}"
            f" --resolution {spec.get('resolution', '720p')}"
            f" --duration {int(spec.get('duration_sec', 5))}"
            f" --camerafixed {'true' if spec.get('camera_fixed', True) else 'false'}"
            f" --audio {'true' if spec.get('audio', False) else 'false'}"
        )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": request.prompt + flags},
            {
                "type": "image_url",
                "image_url": {"url": request.start_image_url},
                "role": "first_frame",
            },
        ]
        if request.end_image_url:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": request.end_image_url},
                    "role": "last_frame",
                }
            )

        headers = {"Authorization": f"Bearer {self._key()}"}
        try:
            r = httpx.post(
                f"{self._base()}/contents/generations/tasks",
                json={"model": self.model_name(), "content": content},
                headers=headers,
                timeout=60.0,
            )
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"Seedance 요청 실패: {e}") from e
        if r.status_code >= 300:
            raise VideoProviderError("PROVIDER_REJECTED", f"Seedance {r.status_code}: {r.text[:500]}")
        task_id = str((r.json() or {}).get("id") or "")
        if not task_id:
            raise VideoProviderError("PROVIDER_NO_JOB_ID", "Seedance 가 task id 를 주지 않았습니다.")
        logger.info("[motion-receipt] provider=seedance model=%s external_id=%s", self.model_name(), task_id)

        deadline = time.monotonic() + float(os.getenv("SEEDANCE_POLL_MAX_SEC", "600"))
        while time.monotonic() < deadline:
            try:
                s = httpx.get(f"{self._base()}/contents/generations/tasks/{task_id}", headers=headers, timeout=30.0)
            except Exception as e:
                raise VideoProviderError("PROVIDER_TRANSPORT", f"Seedance 폴링 실패: {e}") from e
            body = s.json() if s.status_code < 300 else {}
            status = str(body.get("status") or "").lower()
            if status == "succeeded":
                url = ((body.get("content") or {}).get("video_url")) or ""
                if not url:
                    raise VideoProviderError("PROVIDER_EMPTY", "Seedance 출력이 비어 있습니다.")
                return MotionVideoResult(
                    video_bytes=_download(url),
                    provider=self.name,
                    model=self.model_name(),
                    external_job_id=task_id,
                    usage=dict(body.get("usage") or {}),
                )
            if status in ("failed", "cancelled"):
                raise VideoProviderError("PROVIDER_FAILED", f"Seedance task {status}: {str(body.get('error'))[:300]}")
            time.sleep(float(os.getenv("SEEDANCE_POLL_INTERVAL_SEC", "5")))
        raise VideoProviderError("PROVIDER_TIMEOUT", "Seedance task 폴링 시간 초과")


# ══════════════════════════════════════════════════════════════════════════
# Kling V3
# ══════════════════════════════════════════════════════════════════════════


class KlingProvider(VideoGenerationProvider):
    name = PROVIDER_KLING
    supports_end_frame = True  # image + image_tail
    supports_motion_reference = False
    reference_budget = 0

    def _keys(self) -> tuple[str, str]:
        return (
            (os.getenv("KLING_ACCESS_KEY") or "").strip(),
            (os.getenv("KLING_SECRET_KEY") or "").strip(),
        )

    def _base(self) -> str:
        return (os.getenv("KLING_API_BASE") or "https://api-singapore.klingai.com").rstrip("/")

    def model_name(self) -> str:
        return (os.getenv("KLING_MODEL") or "kling-v3").strip()

    def available(self) -> bool:
        ak, sk = self._keys()
        return bool(ak and sk)

    def _jwt(self) -> str:
        import jwt

        ak, sk = self._keys()
        now = int(time.time())
        return jwt.encode(
            {"iss": ak, "exp": now + 1800, "nbf": now - 5},
            sk,
            algorithm="HS256",
            headers={"alg": "HS256", "typ": "JWT"},
        )

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:
        import httpx

        if not self.available():
            raise VideoProviderError("PROVIDER_NOT_CONFIGURED", "KLING_ACCESS/SECRET_KEY 가 없습니다.")
        if not request.start_image_url:
            raise VideoProviderError("NO_START_IMAGE", "시작 키프레임 URL 이 없습니다.")

        spec = request.output_spec
        payload: dict[str, Any] = {
            "model_name": self.model_name(),
            "image": request.start_image_url,
            "prompt": request.prompt,
            "duration": str(int(spec.get("duration_sec", 5))),
            "aspect_ratio": spec.get("aspect_ratio", "9:16"),
            "mode": os.getenv("KLING_MODE", "pro"),
            "cfg_scale": float(os.getenv("KLING_CFG_SCALE", "0.5")),
        }
        if request.end_image_url:
            payload["image_tail"] = request.end_image_url  # start/end 계약 — 버리지 않는다

        headers = {"Authorization": f"Bearer {self._jwt()}", "Content-Type": "application/json"}
        try:
            r = httpx.post(f"{self._base()}/v1/videos/image2video", json=payload, headers=headers, timeout=60.0)
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"Kling 요청 실패: {e}") from e
        if r.status_code >= 300:
            raise VideoProviderError("PROVIDER_REJECTED", f"Kling {r.status_code}: {r.text[:500]}")
        task_id = str(((r.json() or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise VideoProviderError("PROVIDER_NO_JOB_ID", "Kling 이 task id 를 주지 않았습니다.")
        logger.info("[motion-receipt] provider=kling model=%s external_id=%s", self.model_name(), task_id)

        deadline = time.monotonic() + float(os.getenv("KLING_POLL_MAX_SEC", "600"))
        while time.monotonic() < deadline:
            try:
                s = httpx.get(
                    f"{self._base()}/v1/videos/image2video/{task_id}",
                    headers={"Authorization": f"Bearer {self._jwt()}"},
                    timeout=30.0,
                )
            except Exception as e:
                raise VideoProviderError("PROVIDER_TRANSPORT", f"Kling 폴링 실패: {e}") from e
            data = ((s.json() or {}).get("data") or {}) if s.status_code < 300 else {}
            status = str(data.get("task_status") or "").lower()
            if status == "succeed":
                videos = ((data.get("task_result") or {}).get("videos")) or []
                if not videos or not videos[0].get("url"):
                    raise VideoProviderError("PROVIDER_EMPTY", "Kling 출력이 비어 있습니다.")
                return MotionVideoResult(
                    video_bytes=_download(str(videos[0]["url"])),
                    provider=self.name,
                    model=self.model_name(),
                    external_job_id=task_id,
                    duration_sec=float(videos[0].get("duration") or 0) or None,
                )
            if status == "failed":
                raise VideoProviderError("PROVIDER_FAILED", f"Kling task failed: {str(data.get('task_status_msg'))[:300]}")
            time.sleep(float(os.getenv("KLING_POLL_INTERVAL_SEC", "5")))
        raise VideoProviderError("PROVIDER_TIMEOUT", "Kling task 폴링 시간 초과")


# ══════════════════════════════════════════════════════════════════════════
# Mock (개발 — 과금 없음)
# ══════════════════════════════════════════════════════════════════════════


class MockVideoProvider(VideoGenerationProvider):
    name = PROVIDER_MOCK
    supports_end_frame = True
    reference_budget = 3

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return "mock"

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:
        if not request.start_image_bytes:
            raise VideoProviderError("NO_START_IMAGE", "mock: 시작 키프레임 바이트가 없습니다.")
        return MotionVideoResult(
            video_bytes=request.start_image_bytes,
            provider=self.name,
            model="mock",
            external_job_id="mock",
        )


# ══════════════════════════════════════════════════════════════════════════
# 라우팅 정책 + 라이브 안전
# ══════════════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, VideoGenerationProvider] = {
    PROVIDER_SEEDANCE: SeedanceProvider(),
    PROVIDER_KLING: KlingProvider(),
    PROVIDER_MOCK: MockVideoProvider(),
}

#: 모션 클래스 → (primary, fallback). 이 표가 라우팅의 유일한 정본이다.
#: env 로 클래스별 오버라이드: PHASE6_PROVIDER_<CLASS>, PHASE6_FALLBACK_<CLASS>.
_DEFAULT_ROUTING: dict[str, tuple[str, Optional[str]]] = {
    "MICRO": (PROVIDER_SEEDANCE, PROVIDER_KLING),
    # TRANSITION 폴백은 기본 없음 — start/end 계약을 진짜로 지키는 프로바이더가
    # 설정으로 확인될 때만 PHASE6_FALLBACK_TRANSITION 으로 연다.
    "TRANSITION": (PROVIDER_KLING, None),
    "LOCOMOTION": (PROVIDER_SEEDANCE, PROVIDER_KLING),
    "INTERACTION": (PROVIDER_KLING, PROVIDER_SEEDANCE),
}


def _mock_enabled() -> bool:
    return os.getenv("VIDEO_GENERATION_MOCK", "0").strip().lower() in ("1", "true", "yes")


def get_provider(name: Optional[str]) -> Optional[VideoGenerationProvider]:
    if not name:
        return None
    return _REGISTRY.get(name.strip().lower())


def routing_for_class(motion_class: str) -> list[VideoGenerationProvider]:
    """[primary, fallback?] — mock 모드면 mock 하나만."""
    if _mock_enabled():
        return [_REGISTRY[PROVIDER_MOCK]]
    cls = (motion_class or "").strip().upper()
    primary_name, fallback_name = _DEFAULT_ROUTING.get(cls, (PROVIDER_SEEDANCE, None))
    primary_name = os.getenv(f"PHASE6_PROVIDER_{cls}", primary_name)
    fallback_name = os.getenv(f"PHASE6_FALLBACK_{cls}", fallback_name or "") or None
    out: list[VideoGenerationProvider] = []
    for name in (primary_name, fallback_name):
        p = get_provider(name)
        if p and p not in out:
            out.append(p)
    return out


def live_generation_allowed(pet_id: str, providers: list[VideoGenerationProvider]) -> tuple[bool, str]:
    """
    (허용 여부, 이유). mock 프로바이더만이면 항상 허용. 실 프로바이더는
    PHASE6_LIVE_MODE 가 명시적으로 열어 줄 때만 — 대량 과금 사고 방지 (요구 17).
    """
    if all(p.name == PROVIDER_MOCK for p in providers):
        return True, "mock_only"
    mode = os.getenv("PHASE6_LIVE_MODE", "off").strip().lower()
    if mode == "all":
        return True, "live_mode_all"
    if mode == "allowlist":
        allowed = {
            p.strip() for p in os.getenv("PHASE6_LIVE_ALLOWLIST", "").split(",") if p.strip()
        }
        if pet_id in allowed:
            return True, "allowlisted"
        return False, "pet_not_in_live_allowlist"
    return False, "live_mode_off"
