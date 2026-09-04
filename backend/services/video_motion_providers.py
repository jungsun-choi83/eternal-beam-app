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
PROVIDER_WAN = "wan"  # 레퍼런스 조건부 경로 (Phase 6.7) — 클래스 라우팅 표에는 없다
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
    #: 해석된 모션 레퍼런스 **비디오** URL (Phase 6.7) — supports_motion_reference
    #: 프로바이더만 소비한다. 세팅됐다는 것은 "실제로 보낸다"는 뜻이다 —
    #: 메타데이터만 박제하는 가짜 지원 금지.
    motion_reference_url: Optional[str] = None
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
    supports_durable_jobs: bool = False
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
# fal.ai 트랜스포트 (Phase 6.5 라이브 스택) — Seedance/Kling 을 FAL_KEY 하나로
# ══════════════════════════════════════════════════════════════════════════
#
# 직접 API 키(SEEDANCE_API_KEY, KLING_ACCESS/SECRET_KEY)를 사지 않아도 되도록,
# 기존 wan_service 와 같은 fal 큐 프로토콜(POST {queue}/{model} → status_url
# 폴링 → response_url)로 같은 모델들을 호출한다. **비즈니스 로직/라우팅은
# 그대로다** — 프로바이더 이름(seedance/kling)이 같고, 트랜스포트만 다르다.
# 직접 어댑터는 보존되며 SEEDANCE_TRANSPORT/KLING_TRANSPORT=direct 로 선택된다.
#
# fal 모델 id 는 드리프트하는 외부 계약이라 전부 env 다. 필드명/스키마는
# 라이브 검증(BREATHING V2)에서 실 문서와 대조해 고정했다:
#   Seedance 2.5 I2V  bytedance/seedance-2.5/image-to-video
#     입력: prompt, image_url, end_image_url?, resolution(480p|720p|1080p),
#           duration("auto"|4..30, 문자열), generate_audio(기본 true → 명시 false)
#     aspect_ratio 는 I2V 에서 항상 auto — 기하는 **입력 이미지**가 결정한다
#     (video_anchor 가 9:16 을 강제). camera_fixed 필드는 스키마에 없다.
#     출력: {"video": {"url": ...}, "seed": ...}
#   Kling V3 I2V      fal-ai/kling-video/v3/standard/image-to-video
#     입력: prompt, start_image_url, end_image_url?, duration(기본 "5"),
#           generate_audio(기본 true → 명시 false). aspect_ratio 파라미터 없음.
#     출력: {"video": {"url": ...}}
# 문서와 실 응답이 다르면 PROVIDER_SCHEMA 로 멈춘다 — 추측 파싱 금지.


def sanitize_json_shape(obj: Any, depth: int = 0) -> Any:
    """값 없이 키/타입만 — 스키마 divergence 보고용 (URL/시드 등 값 노출 금지)."""
    if depth >= 4:
        return "..."
    if isinstance(obj, dict):
        return {str(k): sanitize_json_shape(v, depth + 1) for k, v in list(obj.items())[:20]}
    if isinstance(obj, list):
        return [sanitize_json_shape(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


def extract_fal_video_url(result: dict[str, Any]) -> str:
    """문서화된 출력 {"video": {"url": ...}} (+ 방어적으로 videos[0].url)."""
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])
    videos = result.get("videos")
    if isinstance(videos, list) and videos and isinstance(videos[0], dict) and videos[0].get("url"):
        return str(videos[0]["url"])
    return ""


class FalVideoProvider(VideoGenerationProvider):
    """fal 큐 공통 트랜스포트. 서브클래스가 모델/페이로드만 정의한다."""

    model_env: str = ""
    default_model: str = ""

    def _key(self) -> str:
        return (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()

    def _queue_base(self) -> str:
        return (os.getenv("WAN_QUEUE_BASE") or "https://queue.fal.run").rstrip("/")

    def _rest_base(self) -> str:
        return (os.getenv("FAL_REST_BASE") or "https://rest.alpha.fal.ai").rstrip("/")

    def _input_transport(self) -> str:
        """
        입력 이미지 전달 방식:
          signed_url  (기본) Supabase 서명 URL 을 그대로 image_url 로 보낸다
          fal_storage 이미지 바이트를 fal 스토리지에 올리고 fal 호스팅 URL 을 쓴다
                      — 파트너 모더레이션이 외부 서명 URL 에서 오탐하는지 진단용
        """
        return (os.getenv("FAL_INPUT_TRANSPORT") or "signed_url").strip().lower()

    def upload_input_image(
        self, data: bytes, *, content_type: str = "image/png", file_name: str = "input.png"
    ) -> str:
        """문서화된 fal 스토리지 계약: initiate → PUT → file_url."""
        import httpx

        headers = {"Authorization": f"Key {self._key()}"}
        try:
            r = httpx.post(
                f"{self._rest_base()}/storage/upload/initiate?storage_type=fal-cdn-v3",
                json={"content_type": content_type, "file_name": file_name},
                headers=headers,
                timeout=30.0,
            )
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"fal 스토리지 initiate 실패: {e}") from e
        if r.status_code >= 300:
            raise VideoProviderError(
                "PROVIDER_TRANSPORT", f"fal 스토리지 initiate HTTP {r.status_code}: {r.text[:300]}"
            )
        body = r.json() or {}
        upload_url, file_url = body.get("upload_url"), body.get("file_url")
        if not upload_url or not file_url:
            raise VideoProviderError(
                "PROVIDER_SCHEMA",
                f"fal 스토리지 initiate 응답이 문서와 다릅니다 — shape={sanitize_json_shape(body)}",
            )
        try:
            put = httpx.put(
                str(upload_url), content=data, headers={"Content-Type": content_type}, timeout=120.0
            )
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"fal 스토리지 업로드 실패: {e}") from e
        if put.status_code >= 300:
            raise VideoProviderError(
                "PROVIDER_TRANSPORT", f"fal 스토리지 업로드 HTTP {put.status_code}: {put.text[:300]}"
            )
        logger.info("[motion-receipt] provider=%s(fal) input_uploaded url=%s", self.name, file_url)
        return str(file_url)

    def _upload_inputs(self, request: MotionVideoRequest) -> MotionVideoRequest:
        from dataclasses import replace

        if request.start_image_bytes:
            request = replace(
                request,
                start_image_url=self.upload_input_image(
                    request.start_image_bytes, file_name="start.png"
                ),
            )
        if request.end_image_bytes:
            request = replace(
                request,
                end_image_url=self.upload_input_image(request.end_image_bytes, file_name="end.png"),
            )
        return request

    def model_name(self) -> str:
        return (os.getenv(self.model_env) or self.default_model).strip().strip("/")

    def available(self) -> bool:
        return bool(self._key())

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:
        import httpx

        if not request.start_image_url:
            raise VideoProviderError("NO_START_IMAGE", "시작 키프레임 URL 이 없습니다.")
        if request.end_image_url and not self.supports_end_frame:
            raise VideoProviderError(
                "END_FRAME_UNSUPPORTED", f"{self.name}(fal) 트랜스포트는 end frame 을 지원하지 않습니다."
            )
        # 페이로드는 HTTP **이전에** 만든다 — 계약 위반(PROVIDER_CONTRACT)은
        # 로컬에서 잡히고 과금 호출이 나가지 않는다.
        payload = self.build_payload(request)
        if not self.available():
            raise VideoProviderError("PROVIDER_NOT_CONFIGURED", "FAL_KEY 가 없습니다.")
        if self._input_transport() == "fal_storage":
            # 계약 검증 통과 후에만 업로드 — 그리고 fal 호스팅 URL 로 페이로드 재구성.
            request = self._upload_inputs(request)
            payload = self.build_payload(request)

        started = time.monotonic()
        headers = {"Authorization": f"Key {self._key()}", "Content-Type": "application/json"}
        mdl = self.model_name()
        logger.info(
            "[motion-receipt] provider=%s(fal) model=%s payload_keys=%s",
            self.name, mdl, sorted(payload.keys()),
        )
        try:
            r = httpx.post(
                f"{self._queue_base()}/{mdl}",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"fal 요청 실패: {e}") from e
        if r.status_code >= 300:
            raise VideoProviderError("PROVIDER_REJECTED", f"fal {r.status_code}: {r.text[:500]}")
        body = r.json() or {}
        request_id = str(body.get("request_id") or body.get("requestId") or "")
        if not request_id:
            raise VideoProviderError("PROVIDER_NO_JOB_ID", "fal 이 request_id 를 주지 않았습니다.")
        # fal 이 준 URL 을 그대로 쓴다 (wan_service 와 같은 방어).
        status_url = body.get("status_url") or f"{self._queue_base()}/{mdl}/requests/{request_id}/status"
        response_url = body.get("response_url") or f"{self._queue_base()}/{mdl}/requests/{request_id}"
        logger.info("[motion-receipt] provider=%s(fal) model=%s external_id=%s", self.name, mdl, request_id)

        deadline = time.monotonic() + float(os.getenv("FAL_VIDEO_POLL_MAX_SEC", "600"))
        while time.monotonic() < deadline:
            try:
                s = httpx.get(status_url, headers=headers, timeout=30.0)
            except Exception as e:
                raise VideoProviderError("PROVIDER_TRANSPORT", f"fal 폴링 실패: {e}") from e
            sb = s.json() if s.status_code < 300 else {}
            status = str(sb.get("status") or "").upper()
            if status == "COMPLETED":
                try:
                    res = httpx.get(response_url, headers=headers, timeout=60.0)
                except Exception as e:
                    raise VideoProviderError("PROVIDER_TRANSPORT", f"fal 결과 조회 실패: {e}") from e
                # 결과 조회 실패를 빈 결과({})로 삼키지 않는다 — BREATHING V2 의
                # PROVIDER_EMPTY 오진 원인. 4xx 는 프로바이더가 요청/입력을 거절한
                # 것(예: 콘텐츠 모더레이션 422)이고, 그 외는 전송 장애다.
                if res.status_code >= 300:
                    code = "PROVIDER_REJECTED" if 400 <= res.status_code < 500 else "PROVIDER_TRANSPORT"
                    raise VideoProviderError(
                        code,
                        f"fal 결과 조회 HTTP {res.status_code}: {res.text[:300]}",
                    )
                result = res.json() or {}
                logger.info(
                    "[motion-receipt] provider=%s(fal) result_shape=%s",
                    self.name, sanitize_json_shape(result),
                )
                url = extract_fal_video_url(result)
                if not url:
                    # 문서화된 스키마({"video":{"url":...}})와 다르다 — 추측하지
                    # 않고 sanitize 된 형태를 남기고 멈춘다 (어댑터 계약 실패).
                    raise VideoProviderError(
                        "PROVIDER_SCHEMA",
                        f"fal 결과가 문서화된 스키마와 다릅니다 — shape={sanitize_json_shape(result)}",
                    )
                usage: dict[str, Any] = {"latency_sec": round(time.monotonic() - started, 1)}
                if result.get("seed") is not None:
                    usage["seed"] = result["seed"]
                return MotionVideoResult(
                    video_bytes=_download(url),
                    provider=self.name,
                    model=mdl,
                    external_job_id=request_id,
                    usage=usage,
                )
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise VideoProviderError(
                    "PROVIDER_FAILED", f"fal {status}: {str(sb.get('error') or sb.get('detail'))[:300]}"
                )
            time.sleep(float(os.getenv("FAL_VIDEO_POLL_INTERVAL_SEC", "5")))
        raise VideoProviderError("PROVIDER_TIMEOUT", "fal 폴링 시간 초과")


class FalSeedanceProvider(FalVideoProvider):
    name = PROVIDER_SEEDANCE
    model_env = "FAL_SEEDANCE_MODEL"
    default_model = "bytedance/seedance-2.5/image-to-video"

    #: 문서화된 duration 하한 — 이보다 짧은 요청은 계약 위반이다 (몰래 늘리지 않는다).
    MIN_DURATION_SEC = 4

    @property
    def supports_end_frame(self) -> bool:  # type: ignore[override]
        return os.getenv("FAL_SEEDANCE_SUPPORTS_END_FRAME", "1").strip() == "1"

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:
        spec = request.output_spec
        duration = int(spec.get("duration_sec", self.MIN_DURATION_SEC))
        if duration < self.MIN_DURATION_SEC:
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                f"Seedance 2.5 duration 하한은 {self.MIN_DURATION_SEC}s 다 — "
                f"{duration}s 요청은 계약 위반 (모션 스펙/프로파일을 조정하라).",
            )
        # aspect_ratio 는 I2V 에서 항상 auto — 파라미터가 아니라 시작 이미지
        # (video_anchor)가 기하를 결정한다. camera_fixed 는 스키마에 없다.
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "image_url": request.start_image_url,
            "resolution": spec.get("resolution", "720p"),
            "duration": str(duration),
            "generate_audio": bool(spec.get("audio", False)),  # 기본 true — 명시적으로 끈다
        }
        if request.end_image_url:
            payload["end_image_url"] = request.end_image_url
        return payload


class FalKlingProvider(FalVideoProvider):
    name = PROVIDER_KLING
    model_env = "FAL_KLING_MODEL"
    default_model = "fal-ai/kling-video/v3/standard/image-to-video"
    supports_end_frame = True

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:
        spec = request.output_spec
        # aspect_ratio 파라미터는 V3 I2V 에 없다 — 기하는 시작 이미지가 결정한다.
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "start_image_url": request.start_image_url,
            "duration": str(int(spec.get("duration_sec", 5))),
            "generate_audio": bool(spec.get("audio", False)),  # 기본 true — 명시적으로 끈다
        }
        if request.end_image_url:
            payload["end_image_url"] = request.end_image_url  # start/end 계약 유지
        return payload


# ══════════════════════════════════════════════════════════════════════════
# Runway 트랜스포트 — Seedance 2.5 를 Runway Dev API 로 (RUNWAY_API_KEY 재사용)
# ══════════════════════════════════════════════════════════════════════════
#
# fal 경로의 Seedance 2.5 가 partner_validation_failed 로 전면 차단된 뒤의 정본
# 경로다. 계약은 docs.dev.runwayml.com/openapi.json 에서 검증했다 (추측 없음):
#   POST {base}/image_to_video  (base 기본 https://api.dev.runwayml.com/v1)
#     headers: Authorization Bearer + X-Runway-Version 2024-11-06
#     body(model="seedance2_5"): promptImage(단일 URI | [{uri,position:first|last}]),
#       promptText?(≤15000자), ratio(명시적 enum — 480:854/720:1280/1080:1920 등),
#       duration(정수 4..30), audio(기본 true → 명시 false)
#   폴링: GET {base}/tasks/{id} → PENDING|THROTTLED|RUNNING|SUCCEEDED|FAILED|CANCELLED
#     SUCCEEDED: {"output": [url, ...], "cost": {...}} / FAILED: failure+failureCode
# Runway 는 ratio 가 **명시적 파라미터**라 기하 제어가 계약으로 보장된다 —
# 9:16 앵커 입력과 함께 이중으로 잠긴다.

#: (aspect_ratio, resolution) → Runway seedance2_5 ratio enum. 스키마에 없는
#: 조합은 만들어내지 않는다 — 매핑 실패는 계약 위반이다.
_RUNWAY_SEEDANCE_RATIOS: dict[tuple[str, str], str] = {
    ("9:16", "480p"): "480:854",
    ("9:16", "720p"): "720:1280",
    ("9:16", "1080p"): "1080:1920",
    ("16:9", "480p"): "854:480",
    ("16:9", "720p"): "1280:720",
    ("16:9", "1080p"): "1920:1080",
    ("1:1", "480p"): "640:640",
    ("1:1", "720p"): "960:960",
    ("1:1", "1080p"): "1440:1440",
}


class RunwayVideoProvider(VideoGenerationProvider):
    """Runway Dev API 공통 태스크 플로 — 서브클래스가 모델/페이로드만 정의한다."""

    supports_durable_jobs = True

    def _key(self) -> str:
        return (os.getenv("RUNWAY_API_KEY") or "").strip()

    def _base(self) -> str:
        return (os.getenv("RUNWAY_API_BASE") or "https://api.dev.runwayml.com/v1").rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key()}",
            "X-Runway-Version": os.getenv("RUNWAY_API_VERSION", "2024-11-06"),
            "Content-Type": "application/json",
        }

    def available(self) -> bool:
        return bool(self._key())

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def submit(self, request: MotionVideoRequest):
        from .provider_job_contract import ProviderSubmission

        import httpx

        if not request.start_image_url:
            raise VideoProviderError("NO_START_IMAGE", "시작 키프레임 URL 이 없습니다.")
        # 페이로드는 HTTP 이전에 만든다 — 계약 위반은 로컬에서, 과금 전에.
        payload = self.build_payload(request)
        if not self.available():
            raise VideoProviderError("PROVIDER_NOT_CONFIGURED", "RUNWAY_API_KEY 가 없습니다.")

        logger.info(
            "[motion-receipt] provider=%s(runway) model=%s payload_keys=%s",
            self.name, self.model_name(), sorted(payload.keys()),
        )
        try:
            r = httpx.post(
                f"{self._base()}/image_to_video", json=payload, headers=self._headers(), timeout=60.0
            )
        except Exception as e:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"Runway 요청 실패: {e}") from e
        if r.status_code >= 300:
            code = "PROVIDER_REJECTED" if 400 <= r.status_code < 500 else "PROVIDER_TRANSPORT"
            raise VideoProviderError(code, f"Runway {r.status_code}: {r.text[:500]}")
        task_id = str((r.json() or {}).get("id") or "")
        if not task_id:
            raise VideoProviderError("PROVIDER_NO_JOB_ID", "Runway 가 task id 를 주지 않았습니다.")
        logger.info(
            "[motion-receipt] provider=%s(runway) model=%s external_id=%s",
            self.name, self.model_name(), task_id,
        )
        return ProviderSubmission(external_job_id=task_id)

    def check(self, external_job_id: str):
        from .provider_job_contract import FAILED, PENDING, SUCCEEDED, ProviderJobCheck

        import httpx

        try:
            response = httpx.get(
                f"{self._base()}/tasks/{external_job_id}",
                headers=self._headers(),
                timeout=30.0,
            )
        except Exception as exc:
            raise VideoProviderError("PROVIDER_TRANSPORT", f"Runway 폴링 실패: {exc}") from exc
        if response.status_code >= 300:
            raise VideoProviderError(
                "PROVIDER_TRANSPORT",
                f"Runway 폴링 HTTP {response.status_code}: {response.text[:300]}",
            )
        body = response.json() or {}
        provider_status = str(body.get("status") or "").upper()
        if provider_status == "SUCCEEDED":
            return ProviderJobCheck(SUCCEEDED, provider_status, metadata=body)
        if provider_status in ("FAILED", "CANCELLED"):
            error = f"code={body.get('failureCode')} {str(body.get('failure'))[:300]}"
            return ProviderJobCheck(FAILED, provider_status, error=error, metadata=body)
        return ProviderJobCheck(PENDING, provider_status or "PENDING", metadata=body)

    def collect(self, external_job_id: str) -> MotionVideoResult:
        check = self.check(external_job_id)
        if check.status != "SUCCEEDED":
            raise VideoProviderError("PROVIDER_NOT_READY", "Runway 비디오 태스크가 완료되지 않았습니다.")
        logger.info(
            "[motion-receipt] provider=%s(runway) result_shape=%s",
            self.name,
            sanitize_json_shape(check.metadata),
        )
        output = check.metadata.get("output") or []
        url = str(output[0]) if isinstance(output, list) and output else ""
        if not url:
            raise VideoProviderError(
                "PROVIDER_SCHEMA",
                "Runway SUCCEEDED 인데 output 이 문서와 다릅니다 — "
                f"shape={sanitize_json_shape(check.metadata)}",
            )
        usage: dict[str, Any] = {}
        if isinstance(check.metadata.get("cost"), dict):
            usage["cost"] = check.metadata["cost"]
        return MotionVideoResult(
            video_bytes=_download(url),
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
            usage=usage,
        )

    def generate(self, request: MotionVideoRequest) -> MotionVideoResult:
        started = time.monotonic()
        submission = self.submit(request)

        deadline = time.monotonic() + float(os.getenv("RUNWAY_VIDEO_POLL_MAX_SEC", "600"))
        while time.monotonic() < deadline:
            check = self.check(submission.external_job_id)
            if check.status == "SUCCEEDED":
                result = self.collect(submission.external_job_id)
                return MotionVideoResult(
                    video_bytes=result.video_bytes,
                    provider=result.provider,
                    model=result.model,
                    external_job_id=result.external_job_id,
                    duration_sec=result.duration_sec,
                    resolution=result.resolution,
                    usage={**result.usage, "latency_sec": round(time.monotonic() - started, 1)},
                )
            if check.status == "FAILED":
                raise VideoProviderError(
                    "PROVIDER_FAILED",
                    f"Runway task {check.provider_status}: {check.error}",
                )
            # PENDING | THROTTLED | RUNNING → 계속 폴링
            time.sleep(float(os.getenv("RUNWAY_VIDEO_POLL_INTERVAL_SEC", "5")))
        raise VideoProviderError("PROVIDER_TIMEOUT", "Runway task 폴링 시간 초과")


class RunwaySeedanceProvider(RunwayVideoProvider):
    name = PROVIDER_SEEDANCE
    supports_end_frame = True  # promptImage position first/last (키프레임 모드)
    supports_motion_reference = False
    reference_budget = 0

    MIN_DURATION_SEC = 4
    MAX_DURATION_SEC = 30

    def model_name(self) -> str:
        return (os.getenv("RUNWAY_SEEDANCE_MODEL") or "seedance2_5").strip()

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:
        spec = request.output_spec
        duration = int(spec.get("duration_sec", self.MIN_DURATION_SEC))
        if not (self.MIN_DURATION_SEC <= duration <= self.MAX_DURATION_SEC):
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                f"Runway seedance2_5 duration 은 {self.MIN_DURATION_SEC}..{self.MAX_DURATION_SEC}s 다 "
                f"— {duration}s 요청은 계약 위반.",
            )
        aspect = str(spec.get("aspect_ratio") or "9:16")
        resolution = str(spec.get("resolution") or "720p")
        ratio = _RUNWAY_SEEDANCE_RATIOS.get((aspect, resolution))
        if not ratio:
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                f"Runway seedance2_5 ratio 매핑 없음: aspect={aspect} resolution={resolution} "
                f"— 스키마에 없는 조합을 만들어내지 않는다.",
            )
        prompt_image: Any = request.start_image_url
        if request.end_image_url:
            # 키프레임 모드 — end frame 계약을 버리지 않는다 (위치 명시).
            prompt_image = [
                {"uri": request.start_image_url, "position": "first"},
                {"uri": request.end_image_url, "position": "last"},
            ]
        return {
            "model": self.model_name(),
            "promptImage": prompt_image,
            "promptText": request.prompt,
            "ratio": ratio,
            "duration": duration,
            "audio": bool(spec.get("audio", False)),  # 기본 true — 명시적으로 끈다
        }


# ── Runway Wan 3 — 실제 모션 레퍼런스 소비 경로 (Phase 6.7) ─────────────────
# openapi.json 검증: image_to_video model="wan3" 는 referenceVideos
# ([{type:"video", uri}], ≤5개, 합계 ≤15초) 를 **실제로** 입력으로 받는다.
# 이 어댑터는 레퍼런스 조건부다: motion_reference_url 없이는 호출 자체가
# 계약 위반이다 — 메타데이터만 박제하는 가짜 지원을 허용하지 않는다.

_RUNWAY_WAN_RATIOS: dict[tuple[str, str], str] = {
    ("9:16", "480p"): "480:832",
    ("9:16", "720p"): "720:1280",
    ("9:16", "1080p"): "1080:1920",
    ("16:9", "480p"): "832:480",
    ("16:9", "720p"): "1280:720",
    ("16:9", "1080p"): "1920:1080",
    ("1:1", "480p"): "480:480",
    ("1:1", "720p"): "720:720",
    ("1:1", "1080p"): "1080:1080",
}

#: wan3 레퍼런스 비디오 합계 상한 (문서화된 계약).
RUNWAY_WAN_MAX_REFERENCE_SEC = 15.0


class RunwayWanMotionRefProvider(RunwayVideoProvider):
    name = PROVIDER_WAN
    supports_end_frame = False
    supports_motion_reference = True  # 유일하게 레퍼런스 비디오를 소비한다
    reference_budget = 0

    MIN_DURATION_SEC = 2
    MAX_DURATION_SEC = 30

    def model_name(self) -> str:
        return (os.getenv("RUNWAY_WAN_MODEL") or "wan3").strip()

    def build_payload(self, request: MotionVideoRequest) -> dict[str, Any]:
        spec = request.output_spec
        if not request.motion_reference_url:
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                "wan3 는 레퍼런스 조건부 경로다 — motion_reference_url 없이 호출할 수 없다 "
                "(레퍼런스 없는 생성은 기존 I2V 경로를 쓴다).",
            )
        duration = int(spec.get("duration_sec", 4))
        if not (self.MIN_DURATION_SEC <= duration <= self.MAX_DURATION_SEC):
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                f"Runway wan3 duration 은 {self.MIN_DURATION_SEC}..{self.MAX_DURATION_SEC}s 다 "
                f"— {duration}s 요청은 계약 위반.",
            )
        aspect = str(spec.get("aspect_ratio") or "9:16")
        resolution = str(spec.get("resolution") or "720p")
        ratio = _RUNWAY_WAN_RATIOS.get((aspect, resolution))
        if not ratio:
            raise VideoProviderError(
                "PROVIDER_CONTRACT",
                f"Runway wan3 ratio 매핑 없음: aspect={aspect} resolution={resolution}",
            )
        return {
            "model": self.model_name(),
            # 라이브 검증(Runway 400): 레퍼런스 비디오와 결합할 때 promptImage 는
            # position 없는 배열이어야 한다 — bare 문자열은 first-frame 키프레임
            # 모드로 해석돼 레퍼런스와 결합이 거부된다. 펫 이미지는 여기서
            # 외형(appearance) 레퍼런스로 작동한다 (픽셀 고정 시작 프레임 아님).
            "promptImage": [{"uri": request.start_image_url}],
            "promptText": request.prompt,
            "ratio": ratio,
            "duration": duration,
            "audio": bool(spec.get("audio", False)),  # 명시적으로 끈다
            "referenceVideos": [{"type": "video", "uri": request.motion_reference_url}],
        }


# ══════════════════════════════════════════════════════════════════════════
# Mock (개발 — 과금 없음)
# ══════════════════════════════════════════════════════════════════════════


class MockVideoProvider(VideoGenerationProvider):
    name = PROVIDER_MOCK
    supports_end_frame = True
    supports_motion_reference = True  # mock 모드에서도 레퍼런스 경로가 통과되게
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

#: 트랜스포트별 인스턴스. 프로바이더 **이름**(seedance/kling)은 동일하다 —
#: 라우팅 표는 트랜스포트를 모르고, 트랜스포트는 아래 transport_for() 가 정한다.
_DIRECT: dict[str, VideoGenerationProvider] = {
    PROVIDER_SEEDANCE: SeedanceProvider(),
    PROVIDER_KLING: KlingProvider(),
}
_FAL: dict[str, VideoGenerationProvider] = {
    PROVIDER_SEEDANCE: FalSeedanceProvider(),
    PROVIDER_KLING: FalKlingProvider(),
}
_RUNWAY: dict[str, VideoGenerationProvider] = {
    PROVIDER_SEEDANCE: RunwaySeedanceProvider(),  # Kling 은 Runway 에 없다 — fal 유지
}
_TRANSPORTS: dict[str, dict[str, VideoGenerationProvider]] = {
    "runway": _RUNWAY,
    "fal": _FAL,
    "direct": _DIRECT,
}
#: auto 우선순위 — 프로바이더별. Seedance 는 Runway 가 정본 경로다 (fal 경로는
#: partner_validation_failed 전면 차단 — 어댑터는 보존, 기본 사용 안 함).
_AUTO_ORDER: dict[str, tuple[str, ...]] = {
    PROVIDER_SEEDANCE: ("runway", "fal", "direct"),
    PROVIDER_KLING: ("fal", "direct"),
}
_MOCK = MockVideoProvider()


def transport_for(name: str) -> str:
    """
    'runway' | 'fal' | 'direct'. 우선순위:
      1. <NAME>_TRANSPORT (예: SEEDANCE_TRANSPORT=fal)
      2. PHASE6_VIDEO_TRANSPORT (전역)
      3. auto — _AUTO_ORDER 에서 자격 증명이 있는 첫 트랜스포트
         (seedance: runway → fal → direct / kling: fal → direct)
    """
    key = (name or "").strip().lower()
    explicit = (
        os.getenv(f"{key.upper()}_TRANSPORT")
        or os.getenv("PHASE6_VIDEO_TRANSPORT")
        or "auto"
    ).strip().lower()
    if explicit in _TRANSPORTS and key in _TRANSPORTS[explicit]:
        return explicit
    for t in _AUTO_ORDER.get(key, ("direct",)):
        p = _TRANSPORTS[t].get(key)
        if p and p.available():
            return t
    return _AUTO_ORDER.get(key, ("direct",))[-1]

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
    key = name.strip().lower()
    if key == PROVIDER_MOCK:
        return _MOCK
    if key not in _DIRECT:
        return None
    return _TRANSPORTS[transport_for(key)][key]


#: 레퍼런스 비디오를 실제로 소비하는 프로바이더 (Phase 6.7). 클래스 라우팅
#: 표와 별개다 — I2V_MOTION_REF 전략일 때만 빌더가 이 목록을 조회한다.
_REFERENCE_CAPABLE: tuple[VideoGenerationProvider, ...] = (RunwayWanMotionRefProvider(),)


def reference_capable_providers() -> list[VideoGenerationProvider]:
    """supports_motion_reference ∧ 자격 증명 보유. mock 모드면 mock 하나."""
    if _mock_enabled():
        return [_MOCK]
    return [p for p in _REFERENCE_CAPABLE if p.available()]


def routing_for_class(motion_class: str) -> list[VideoGenerationProvider]:
    """[primary, fallback?] — mock 모드면 mock 하나만."""
    if _mock_enabled():
        return [_MOCK]
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
