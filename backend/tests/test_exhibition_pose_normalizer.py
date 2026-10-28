"""
exhibition_pose_normalizer — 모든 전시 업로드를 GptImageProvider 로 1회 생성.

프로바이더는 _gpt_provider() 를 monkeypatch 해 가짜로 바꾼다. 실 OpenAI 어댑터의
요청 모양은 httpx.post 를 가로채 확인한다 — 실제 네트워크/과금은 절대 없다.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from backend.services import exhibition_pose_normalizer as pose_mod
from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.services.canonical_image_providers import CanonicalImageResult, CanonicalProviderError
from backend.tests.test_exhibition_breathing_maps import frontal_dog
from backend.tests.test_exhibition_pose_suitability import _bad_gate, _good_gate
from backend.tests.test_exhibition_prep_run import _jpeg, fake_matte


def _png(w: int = 64, h: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeGptProvider:
    name = "gpt_image"

    def __init__(self, *, available: bool = True, result: bytes | None = None, error: Exception | None = None):
        self._available = available
        self._result = result if result is not None else _png(128, 128)
        self._error = error
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def model_name(self) -> str:
        return "gpt-image-fake"

    def generate(self, references, prompt, output_spec, metadata):
        self.calls.append({"references": list(references), "prompt": prompt, "output_spec": output_spec, "metadata": metadata})
        if self._error is not None:
            raise self._error
        return CanonicalImageResult(image_bytes=self._result, provider=self.name, model=self.model_name(), external_job_id="fake-1")


@pytest.fixture
def gpt_mode(monkeypatch):
    monkeypatch.setenv("EXHIBITION_POSE_NORMALIZATION_MODE", "gpt_image")


def _use(monkeypatch, provider: FakeGptProvider) -> FakeGptProvider:
    monkeypatch.setattr(pose_mod, "_gpt_provider", lambda: provider)
    return provider


_CONTEXT = {"subject_class": "dog", "decision": "NEEDS_AI_NORMALIZATION", "top_label": "top_down"}


# ── default_normalize ──────────────────────────────────────────────────────────


def test_gpt_image_mode_calls_provider_once_with_the_original_as_reference(gpt_mode, monkeypatch):
    provider = _use(monkeypatch, FakeGptProvider())
    source = _png()
    out = pose_mod.default_normalize(source, _CONTEXT)

    assert out == provider._result
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert len(call["references"]) == 1
    ref = call["references"][0]
    assert ref.data == source and ref.mime_type == "image/png" and ref.url is None
    assert call["prompt"] == pose_mod.build_normalization_prompt({"subject_class": "dog"})
    assert call["output_spec"]["size"] == "1024x1024"
    assert call["metadata"]["purpose"] == "exhibition_pose_normalization"
    assert call["metadata"]["subject_class"] == "dog" and call["metadata"]["gate_top_label"] == "top_down"


def test_prompt_requires_stand_ready_pose_and_identity_preservation():
    prompt = pose_mod.build_normalization_prompt({"subject_class": "cat"})
    for phrase in (
        "full-body", "exact same cat", "standardized STAND_READY pose",
        "natural, static standing pose", "approximately 30–45 degrees",
        "all four paws must be visible and firmly grounded", "head upright",
        "eyes looking toward the camera", "entire pet visible",
        "consistent eye-level camera", "feet aligned on one horizontal ground line",
        "plain neutral light-gray solid-color background", "original coat colors",
        "exact marking shapes and positions", "fur length and texture", "body proportions",
        "species-specific anatomy", "Do not lift any paw",
    ):
        assert phrase in prompt
    negative_pose_clause = prompt.split("Do not depict the pet ", 1)[1]
    for forbidden_pose in ("sitting", "lying down", "walking", "running", "jumping", "exaggerated action pose"):
        assert forbidden_pose in negative_pose_clause


def test_gpt_image_mode_without_api_key_is_not_configured(gpt_mode, monkeypatch):
    provider = _use(monkeypatch, FakeGptProvider(available=False))
    with pytest.raises(pose_mod.PoseNormalizationError) as e:
        pose_mod.default_normalize(_png(), _CONTEXT)
    assert e.value.code == "NORMALIZATION_NOT_CONFIGURED"
    assert provider.calls == []


def test_provider_error_maps_to_pose_normalization_error(gpt_mode, monkeypatch):
    _use(monkeypatch, FakeGptProvider(error=CanonicalProviderError("PROVIDER_REJECTED", "OpenAI 400: bad")))
    with pytest.raises(pose_mod.PoseNormalizationError) as e:
        pose_mod.default_normalize(_png(), _CONTEXT)
    assert e.value.code == "PROVIDER_REJECTED" and "OpenAI 400" in e.value.message


def test_unexpected_generator_exception_is_wrapped(gpt_mode, monkeypatch):
    _use(monkeypatch, FakeGptProvider(error=RuntimeError("boom")))
    with pytest.raises(pose_mod.PoseNormalizationError) as e:
        pose_mod.default_normalize(_png(), _CONTEXT)
    assert e.value.code == "NORMALIZATION_GENERATOR_ERROR"


def test_empty_generator_result_is_an_error(gpt_mode, monkeypatch):
    _use(monkeypatch, FakeGptProvider(result=b""))
    with pytest.raises(pose_mod.PoseNormalizationError) as e:
        pose_mod.default_normalize(_png(), _CONTEXT)
    assert e.value.code == "NORMALIZATION_EMPTY_RESULT"


def test_mock_and_off_modes_never_touch_the_provider(monkeypatch):
    provider = _use(monkeypatch, FakeGptProvider())
    monkeypatch.setenv("EXHIBITION_POSE_NORMALIZATION_MODE", "mock")
    assert pose_mod.default_normalize(b"orig", _CONTEXT) == b"orig"
    for value in ("off", "", "banana"):
        monkeypatch.setenv("EXHIBITION_POSE_NORMALIZATION_MODE", value)
        with pytest.raises(pose_mod.PoseNormalizationError) as e:
            pose_mod.default_normalize(b"orig", _CONTEXT)
        assert e.value.code == "NORMALIZATION_NOT_CONFIGURED"
    assert provider.calls == []


def test_generator_info_describes_the_configured_generator(gpt_mode, monkeypatch):
    _use(monkeypatch, FakeGptProvider())
    info = pose_mod.generator_info()
    assert info == {"mode": "gpt_image", "provider": "gpt_image", "model": "gpt-image-fake", "output_spec": pose_mod.OUTPUT_SPEC}
    monkeypatch.setenv("EXHIBITION_POSE_NORMALIZATION_MODE", "off")
    assert pose_mod.generator_info() == {"mode": "off"}


# ── 실제 GptImageProvider 어댑터 — HTTP 만 가로챈다 ──────────────────────────────


def test_real_gpt_image_provider_sends_one_images_edits_request(gpt_mode, monkeypatch):
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.example/v1")
    generated = _png(256, 256)
    seen: list[dict] = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"created": 1700000000, "data": [{"b64_json": base64.b64encode(generated).decode("ascii")}], "usage": {"total_tokens": 1}}

    def _fake_post(url, *, headers=None, data=None, files=None, timeout=None, **kw):
        seen.append({"url": url, "headers": headers, "data": data, "files": files, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)

    source = _png()
    out = pose_mod.default_normalize(source, _CONTEXT)

    assert out == generated
    assert len(seen) == 1
    req = seen[0]
    assert req["url"] == "https://api.openai.example/v1/images/edits"
    assert req["headers"]["Authorization"] == "Bearer sk-test-not-real"
    assert req["data"]["model"] == "gpt-image-2" and req["data"]["size"] == "1024x1024" and req["data"]["n"] == "1"
    assert req["data"]["prompt"] == pose_mod.build_normalization_prompt({"subject_class": "dog"})
    assert len(req["files"]) == 1
    field, (filename, payload, mime) = req["files"][0]
    assert field == "image[]" and filename.endswith(".png") and payload == source and mime == "image/png"


def test_real_gpt_image_provider_http_rejection_becomes_safe_error(gpt_mode, monkeypatch):
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    class _Resp:
        status_code = 429
        text = "rate limited"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(pose_mod.PoseNormalizationError) as e:
        pose_mod.default_normalize(_png(), _CONTEXT)
    assert e.value.code == "PROVIDER_REJECTED"


# ── process_run 끝까지: GOOD/BAD 모두 1회, 실패는 CUTOUT 전 FAILED ────────────


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def _run(stores, matte, *, pose_gate_fn):
    store, artifacts = stores
    row = svc.create_run(_jpeg(800, 800), created_by="ops-1", store=store, artifacts=artifacts)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=matte, pose_gate_fn=pose_gate_fn)
    return store.get(row["id"])


def test_good_pose_also_generates_once(gpt_mode, monkeypatch, stores):
    provider = _use(monkeypatch, FakeGptProvider(result=_jpeg(900, 900)))
    matte = fake_matte(frontal_dog)
    final = _run(stores, matte, pose_gate_fn=_good_gate())
    assert len(provider.calls) == 1
    assert matte.calls == [(900, 900)]
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["source_used"] == "ai_normalized"
    assert ps["generation_policy"] == "always"
    assert ps["generator"]["model"] == "gpt-image-fake"


def test_bad_pose_generates_once_and_preps_only_the_generated_image(gpt_mode, monkeypatch, stores):
    store, artifacts = stores
    provider = _use(monkeypatch, FakeGptProvider(result=_jpeg(900, 900)))
    matte = fake_matte(frontal_dog)
    final = _run(stores, matte, pose_gate_fn=_bad_gate())
    rid = final["id"]

    assert len(provider.calls) == 1
    assert provider.calls[0]["references"][0].data == artifacts.get(f"exhibition/{rid}/source.png")  # 원본이 레퍼런스
    assert matte.calls == [(900, 900)]  # 누끼/맵은 생성본으로 1회
    assert f"exhibition/{rid}/source.png" in artifacts.objects  # 원본 보존
    assert final["outputs_json"]["source_normalized.png"] == f"exhibition/{rid}/source_normalized.png"
    assert artifacts.get(f"exhibition/{rid}/source_normalized.png")  # 생성본 저장
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["source_used"] == "ai_normalized"
    assert ps["generator"] == {"mode": "gpt_image", "provider": "gpt_image", "model": "gpt-image-fake", "output_spec": pose_mod.OUTPUT_SPEC}
    assert ps["prompt"] == provider.calls[0]["prompt"]
    assert final["status"] == st.STATUS_READY


def test_generation_failure_stops_before_cutout_and_records_the_error(gpt_mode, monkeypatch, stores):
    store, artifacts = stores
    provider = _use(monkeypatch, FakeGptProvider(error=CanonicalProviderError("PROVIDER_TRANSPORT", "timeout")))
    matte = fake_matte(frontal_dog)
    final = _run(stores, matte, pose_gate_fn=_bad_gate())
    assert len(provider.calls) == 1  # 재시도 없음
    assert matte.calls == []
    assert final["status"] == st.STATUS_FAILED
    assert final["error_code"] == "PROVIDER_TRANSPORT"
    assert not final.get("outputs_json")
    assert f"exhibition/{final['id']}/source.png" in artifacts.objects


def test_no_api_key_fails_before_cutout(gpt_mode, monkeypatch, stores):
    _use(monkeypatch, FakeGptProvider(available=False))
    final = _run(stores, fake_matte(frontal_dog), pose_gate_fn=_bad_gate())
    assert final["status"] == st.STATUS_FAILED
    assert final["error_code"] == "NORMALIZATION_NOT_CONFIGURED"
