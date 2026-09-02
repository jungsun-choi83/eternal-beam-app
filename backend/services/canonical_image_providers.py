"""
정본 이미지 프로바이더 추상화 (Phase 4).

── 계약 ────────────────────────────────────────────────────────────────────
CanonicalImageProvider.generate(references, prompt, output_spec, metadata)
  → CanonicalImageResult(image_bytes, provider, model, external_job_id, usage …)

빌더(canonical_pet_service)는 이 인터페이스만 본다 — 프로바이더별 세부는 이
파일 밖으로 새지 않는다. 모델 교체/추가는 어댑터 하나를 더하는 일이다.

── 어댑터 (초기 2개 + mock) ────────────────────────────────────────────────
runway     Runway Gen-4 Image / References (PRIMARY).
           POST {base}/text_to_image → task id → GET {base}/tasks/{id} 폴링.
           referenceImages 는 최대 3장 (Phase 4 설계와 일치).
gpt_image  OpenAI GPT-Image-2 (FALLBACK). POST /v1/images/edits 멀티파트.
mock       로컬 개발용 — 첫 레퍼런스 바이트를 그대로 돌려준다. 과금 없음.

두 실서비스 API 는 스키마가 움직이는 외부 계약이므로 모델 id·버전·베이스 URL 을
전부 env 로 열어 둔다. 유닛 테스트는 이 모듈을 호출하지 않는다(가짜 프로바이더
주입) — 실 결제 호출은 테스트에서 절대 일어나지 않는다.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

PROVIDER_RUNWAY = "runway"
PROVIDER_GPT_IMAGE = "gpt_image"
PROVIDER_MOCK = "mock"


class CanonicalProviderError(Exception):
    """프로바이더/전송 실패 — QA 실패와 구분된다 (candidate.decision=ERROR)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CanonicalReference:
    """프로바이더에 넣을 레퍼런스 1장 — id 로 원본 증거까지 추적된다."""

    reference_id: str
    role: str
    url: Optional[str] = None       # Runway 는 URI 를 받는다 (서명 URL)
    data: Optional[bytes] = None    # OpenAI edits 는 바이트를 받는다
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class CanonicalImageResult:
    image_bytes: bytes
    provider: str
    model: str
    model_version: Optional[str] = None
    external_job_id: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)


class CanonicalImageProvider:
    name: str = "abstract"

    def available(self) -> bool:  # pragma: no cover - 인터페이스
        return False

    def model_name(self) -> str:  # pragma: no cover - 인터페이스
        return ""

    def generate(
        self,
        references: Sequence[CanonicalReference],
        prompt: str,
        output_spec: dict[str, Any],
        metadata: dict[str, Any],
    ) -> CanonicalImageResult:  # pragma: no cover - 인터페이스
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════════════════
# Runway Gen-4 Image / References (PRIMARY)
# ══════════════════════════════════════════════════════════════════════════


class RunwayImageProvider(CanonicalImageProvider):
    name = PROVIDER_RUNWAY

    def _key(self) -> str:
        return (os.getenv("RUNWAY_API_KEY") or "").strip()

    def _base(self) -> str:
        return (os.getenv("RUNWAY_API_BASE") or "https://api.dev.runwayml.com/v1").rstrip("/")

    def model_name(self) -> str:
        return (os.getenv("RUNWAY_IMAGE_MODEL") or "gen4_image").strip()

    def available(self) -> bool:
        return bool(self._key())

    def generate(self, references, prompt, output_spec, metadata) -> CanonicalImageResult:
        import httpx

        if not self.available():
            raise CanonicalProviderError("PROVIDER_NOT_CONFIGURED", "RUNWAY_API_KEY 가 없습니다.")

        headers = {
            "Authorization": f"Bearer {self._key()}",
            "X-Runway-Version": os.getenv("RUNWAY_API_VERSION", "2024-11-06"),
            "Content-Type": "application/json",
        }
        # Gen-4 References — 최대 3장, 태그는 역할에서 유도 (프롬프트에서 @tag 로
        # 참조 가능하지만 v1 프롬프트는 "the supplied references" 로 통칭한다).
        ref_payload = [
            {"uri": r.url, "tag": r.role.lower().replace("primary_", "")[:16] or f"ref{i}"}
            for i, r in enumerate(references[:3])
            if r.url
        ]
        if not ref_payload:
            raise CanonicalProviderError("NO_REFERENCE_URLS", "레퍼런스 서명 URL 이 없습니다.")

        payload = {
            "model": self.model_name(),
            "promptText": prompt,
            "ratio": str(output_spec.get("ratio") or "1024:1024"),
            "referenceImages": ref_payload,
        }

        try:
            r = httpx.post(f"{self._base()}/text_to_image", json=payload, headers=headers, timeout=60.0)
        except Exception as e:
            raise CanonicalProviderError("PROVIDER_TRANSPORT", f"Runway 요청 실패: {e}") from e
        if r.status_code >= 300:
            raise CanonicalProviderError(
                "PROVIDER_REJECTED", f"Runway {r.status_code}: {r.text[:500]}"
            )
        task_id = str((r.json() or {}).get("id") or "")
        if not task_id:
            raise CanonicalProviderError("PROVIDER_NO_JOB_ID", "Runway 가 task id 를 주지 않았습니다.")
        logger.info("[canonical-receipt] provider=runway model=%s external_id=%s", self.model_name(), task_id)

        deadline = time.monotonic() + float(os.getenv("RUNWAY_POLL_MAX_SEC", "300"))
        while time.monotonic() < deadline:
            try:
                s = httpx.get(f"{self._base()}/tasks/{task_id}", headers=headers, timeout=30.0)
            except Exception as e:
                raise CanonicalProviderError("PROVIDER_TRANSPORT", f"Runway 폴링 실패: {e}") from e
            body = s.json() if s.status_code < 300 else {}
            status = str(body.get("status") or "").upper()
            if status == "SUCCEEDED":
                outputs = body.get("output") or []
                if not outputs:
                    raise CanonicalProviderError("PROVIDER_EMPTY", "Runway 출력이 비어 있습니다.")
                img = httpx.get(str(outputs[0]), timeout=60.0, follow_redirects=True)
                if img.status_code >= 300 or not img.content:
                    raise CanonicalProviderError("PROVIDER_DOWNLOAD", "Runway 결과 다운로드 실패")
                return CanonicalImageResult(
                    image_bytes=img.content,
                    provider=self.name,
                    model=self.model_name(),
                    external_job_id=task_id,
                    usage={"status": status},
                )
            if status in ("FAILED", "CANCELLED"):
                raise CanonicalProviderError(
                    "PROVIDER_FAILED", f"Runway task {status}: {str(body.get('failure'))[:300]}"
                )
            time.sleep(float(os.getenv("RUNWAY_POLL_INTERVAL_SEC", "4")))
        raise CanonicalProviderError("PROVIDER_TIMEOUT", "Runway task 폴링 시간 초과")


# ══════════════════════════════════════════════════════════════════════════
# OpenAI GPT-Image-2 (FALLBACK)
# ══════════════════════════════════════════════════════════════════════════


class GptImageProvider(CanonicalImageProvider):
    name = PROVIDER_GPT_IMAGE

    def _key(self) -> str:
        return (os.getenv("OPENAI_API_KEY") or "").strip()

    def _base(self) -> str:
        return (os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1").rstrip("/")

    def model_name(self) -> str:
        return (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-2").strip()

    def available(self) -> bool:
        return bool(self._key())

    def generate(self, references, prompt, output_spec, metadata) -> CanonicalImageResult:
        import httpx

        if not self.available():
            raise CanonicalProviderError("PROVIDER_NOT_CONFIGURED", "OPENAI_API_KEY 가 없습니다.")

        files = []
        for i, r in enumerate(references[:3]):
            if r.data:
                ext = "png" if "png" in (r.mime_type or "") else "jpg"
                files.append(("image[]", (f"ref{i}.{ext}", r.data, r.mime_type or "image/jpeg")))
        if not files:
            raise CanonicalProviderError("NO_REFERENCE_BYTES", "레퍼런스 바이트가 없습니다.")

        data = {
            "model": self.model_name(),
            "prompt": prompt,
            "size": str(output_spec.get("size") or "1024x1024"),
            "n": "1",
        }
        try:
            r = httpx.post(
                f"{self._base()}/images/edits",
                headers={"Authorization": f"Bearer {self._key()}"},
                data=data,
                files=files,
                timeout=float(os.getenv("OPENAI_IMAGE_TIMEOUT_SEC", "300")),
            )
        except Exception as e:
            raise CanonicalProviderError("PROVIDER_TRANSPORT", f"OpenAI 요청 실패: {e}") from e
        if r.status_code >= 300:
            raise CanonicalProviderError(
                "PROVIDER_REJECTED", f"OpenAI {r.status_code}: {r.text[:500]}"
            )
        body = r.json() or {}
        items = body.get("data") or []
        b64 = items[0].get("b64_json") if items else None
        if not b64:
            raise CanonicalProviderError("PROVIDER_EMPTY", "OpenAI 출력이 비어 있습니다.")
        return CanonicalImageResult(
            image_bytes=base64.b64decode(b64),
            provider=self.name,
            model=self.model_name(),
            external_job_id=str(body.get("created") or "") or None,
            usage=dict(body.get("usage") or {}),
        )


# ══════════════════════════════════════════════════════════════════════════
# Mock (로컬 개발 — 과금 없음)
# ══════════════════════════════════════════════════════════════════════════


class MockImageProvider(CanonicalImageProvider):
    name = PROVIDER_MOCK

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return "mock"

    def generate(self, references, prompt, output_spec, metadata) -> CanonicalImageResult:
        for r in references:
            if r.data:
                return CanonicalImageResult(
                    image_bytes=r.data, provider=self.name, model="mock", external_job_id="mock"
                )
        raise CanonicalProviderError("NO_REFERENCE_BYTES", "mock: 레퍼런스 바이트가 없습니다.")


# ══════════════════════════════════════════════════════════════════════════
# 레지스트리
# ══════════════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, CanonicalImageProvider] = {
    PROVIDER_RUNWAY: RunwayImageProvider(),
    PROVIDER_GPT_IMAGE: GptImageProvider(),
    PROVIDER_MOCK: MockImageProvider(),
}


def _mock_enabled() -> bool:
    return os.getenv("CANONICAL_GENERATION_MOCK", "0").strip().lower() in ("1", "true", "yes")


def get_provider(name: str) -> Optional[CanonicalImageProvider]:
    return _REGISTRY.get((name or "").strip().lower())


def resolve_providers() -> list[CanonicalImageProvider]:
    """[primary, fallback] — 설정 순서대로. mock 모드면 mock 하나만."""
    if _mock_enabled():
        return [_REGISTRY[PROVIDER_MOCK]]
    primary = get_provider(os.getenv("CANONICAL_IMAGE_PROVIDER", PROVIDER_RUNWAY))
    fallback = get_provider(os.getenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", PROVIDER_GPT_IMAGE))
    out: list[CanonicalImageProvider] = []
    for p in (primary, fallback):
        if p and p not in out:
            out.append(p)
    return out
