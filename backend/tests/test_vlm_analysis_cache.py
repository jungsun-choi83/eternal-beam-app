"""
VLM 결과 durable 캐시 (reference-analysis 최적화) 계약 테스트.

목표: pet_identity_service/pet_morphology_service/pet_reference_set_service 가
쓰는 두 VLM 호출(analyze_semantic_traits, classify_reference)의 결과가
content_hash + analyzer_version(+model) 로 durable 하게 재사용되고, 프로세스
경계를 넘어도(= in-memory LRU 를 비워도) 재과금되지 않는지 직접 검증한다.

시나리오:
1. 같은 콘텐츠 + 같은 버전 → 재분석/VLM 재호출 없음 (in-memory 캐시 경유)
2. 바이트가 바뀌면(=content hash 변경) → 재계산
3. 분석기/모델 버전이 바뀌면 → 재계산
4. durable 계층이 비어있거나(모의 "새 프로세스") 조회에 실패해도 → 기존 폴백
   (직접 호출)이 그대로 동작한다
5. in-memory 캐시를 비워도(모의 워커 재시작) durable 계층이 재과금을 막는다
6. 두 목적(semantic traits / view-pose classification)은 같은 바이트라도
   서로의 결과를 오염시키지 않는다
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from backend.services import vlm_identity


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    vlm_identity.clear_semantic_cache()
    yield
    vlm_identity.clear_semantic_cache()


_STUB_TRAITS = {
    "species": "dog",
    "breed_estimate": "shiba",
    "breed_confidence": "low",
    "face": {"muzzle_color": "brown", "facial_markings": "unknown"},
    "eyes": {"color": "unknown", "surrounding_markings": "unknown"},
    "ears": {"shape": "erect", "color_markings": "brown"},
    "coat": {
        "dominant_colors": ["brown"],
        "secondary_colors": [],
        "length": "short",
        "texture": "smooth",
        "marking_distribution": "unknown",
    },
    "body": {"chest_markings": "unknown", "torso_markings": "unknown"},
    "paws": {"colors_markings": "unknown"},
    "tail": {"appearance": "curled", "tip_marking": "unknown"},
    "unique_features": [],
}

_STUB_CLASSIFICATION = {
    "view_label": "FRONT",
    "view_confidence": "high",
    "pose_label": "STANDING",
    "pose_confidence": "high",
    "visibility": {
        "face_visible": "yes",
        "full_body_visible": "yes",
        "left_side_visible": "no",
        "right_side_visible": "no",
        "paws_visible": "yes",
        "tail_visible": "no",
        "ears_visible": "yes",
        "distinct_markings_visible": "no",
        "heavy_occlusion": "no",
        "person_obstruction": "no",
    },
}


def _stub_anthropic(monkeypatch, *, payload: dict) -> list[dict]:
    """가짜 anthropic 모듈. 나간 호출을 기록해 돌려준다."""
    calls: list[dict] = []

    class _TextBlock:
        type = "text"
        text = json.dumps(payload)

    class _Response:
        model = "test-stub"
        stop_reason = "end_turn"
        content = [_TextBlock()]

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _Response()

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return calls


# ══════════════════════════════════════════════════════════════════════════
# 1. 같은 콘텐츠 → 재호출 없음 (semantic traits + classification 둘 다)
# ══════════════════════════════════════════════════════════════════════════


def test_semantic_traits_same_content_is_not_recalled(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_TRAITS)

    first = vlm_identity.analyze_semantic_traits([(b"same-bytes", "image/jpeg")])
    second = vlm_identity.analyze_semantic_traits([(b"same-bytes", "image/jpeg")])

    assert first is not None and second == first
    assert len(calls) == 1


def test_classification_same_content_is_not_recalled(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)

    first = vlm_identity.classify_reference(b"same-bytes", "image/jpeg")
    second = vlm_identity.classify_reference(b"same-bytes", "image/jpeg")

    assert first is not None and second == first
    assert len(calls) == 1, "classify_reference 는 전에 캐시가 전혀 없어 매번 과금됐다"


# ══════════════════════════════════════════════════════════════════════════
# 2. 바이트가 바뀌면(content hash 변경) → 재계산
# ══════════════════════════════════════════════════════════════════════════


def test_classification_changed_content_hash_recomputes(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)

    vlm_identity.classify_reference(b"reference-a", "image/jpeg")
    vlm_identity.classify_reference(b"reference-b", "image/jpeg")

    assert len(calls) == 2


# ══════════════════════════════════════════════════════════════════════════
# 3. 분석기/모델 버전이 바뀌면 → 재계산
# ══════════════════════════════════════════════════════════════════════════


def test_classification_changed_model_recomputes(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)

    vlm_identity.classify_reference(b"reference-a", "image/jpeg")
    monkeypatch.setenv("PET_VLM_MODEL", "some-other-model")
    vlm_identity.classify_reference(b"reference-a", "image/jpeg")

    assert len(calls) == 2, "모델이 바뀌면 캐시 키가 바뀌어 stale 결과가 재사용되지 않는다"


def test_semantic_traits_changed_analyzer_version_recomputes(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_TRAITS)

    vlm_identity.analyze_semantic_traits([(b"reference-a", "image/jpeg")])
    monkeypatch.setattr(vlm_identity, "VLM_ANALYZER_VERSION", "vlm-identity-v2-test")
    vlm_identity.analyze_semantic_traits([(b"reference-a", "image/jpeg")])

    assert len(calls) == 2


# ══════════════════════════════════════════════════════════════════════════
# 4. durable 계층이 비어 있거나 죽어 있어도 기존 폴백(직접 호출)이 동작한다
# ══════════════════════════════════════════════════════════════════════════


def test_missing_durable_analysis_falls_back_to_direct_call(monkeypatch):
    """durable 조회가 실패해도(예: DB 잠깐 불가) 호출자는 그냥 다시 계산한다."""
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)

    def _boom(cache_key):
        raise RuntimeError("durable store unavailable")

    monkeypatch.setattr(vlm_identity, "_durable_cache_get", _boom)
    monkeypatch.setattr(vlm_identity, "_durable_cache_put", lambda *a, **k: None)

    result = vlm_identity.classify_reference(b"reference-a", "image/jpeg")

    assert result is not None and len(calls) == 1


# ══════════════════════════════════════════════════════════════════════════
# 5. in-memory 캐시를 비워도(워커 재시작 흉내) durable 계층이 재과금을 막는다
# ══════════════════════════════════════════════════════════════════════════


def test_durable_layer_survives_in_memory_cache_reset(monkeypatch):
    """
    이 테스트가 곧 "durable" 을 증명한다: in-memory LRU 만 있던 예전 캐시는
    프로세스가 재시작되면(여기서는 mem 캐시만 비워 흉내낸다) 사라졌다.
    """
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)

    first = vlm_identity.classify_reference(b"reference-a", "image/jpeg")
    assert len(calls) == 1

    with vlm_identity._result_cache_lock:
        vlm_identity._result_cache.clear()

    second = vlm_identity.classify_reference(b"reference-a", "image/jpeg")

    assert second == first
    assert len(calls) == 1, "durable 캐시가 워커 재시작 이후에도 재과금을 막아야 한다"


def test_semantic_traits_durable_layer_survives_in_memory_cache_reset(monkeypatch):
    calls = _stub_anthropic(monkeypatch, payload=_STUB_TRAITS)

    first = vlm_identity.analyze_semantic_traits([(b"reference-a", "image/jpeg")])
    assert len(calls) == 1

    with vlm_identity._result_cache_lock:
        vlm_identity._result_cache.clear()

    second = vlm_identity.analyze_semantic_traits([(b"reference-a", "image/jpeg")])

    assert second == first
    assert len(calls) == 1


# ══════════════════════════════════════════════════════════════════════════
# 6. 두 목적은 같은 바이트라도 서로의 결과를 오염시키지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_semantic_and_classification_caches_do_not_collide(monkeypatch):
    same_bytes = b"one-reference-photo"

    semantic_calls = _stub_anthropic(monkeypatch, payload=_STUB_TRAITS)
    semantic = vlm_identity.analyze_semantic_traits([(same_bytes, "image/jpeg")])
    assert semantic["traits"]["species"] == "dog"
    assert len(semantic_calls) == 1

    classification_calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)
    classification = vlm_identity.classify_reference(same_bytes, "image/jpeg")
    assert classification["view_label"] == "FRONT"
    assert len(classification_calls) == 1, "같은 바이트라도 분류는 별도로 과금되어야 한다"

    # 캐시가 서로 섞이지 않았는지 재조회로 재확인.
    assert vlm_identity.analyze_semantic_traits([(same_bytes, "image/jpeg")])["traits"]["species"] == "dog"
    assert vlm_identity.classify_reference(same_bytes, "image/jpeg")["view_label"] == "FRONT"
    assert len(semantic_calls) == 1
    assert len(classification_calls) == 1


# ══════════════════════════════════════════════════════════════════════════
# 7. 실패는 절대 캐시되지 않는다 (기존 semantic traits 원칙을 classify_reference 에도)
# ══════════════════════════════════════════════════════════════════════════


def test_classification_failure_is_not_cached(monkeypatch):
    class _RefusedResponse:
        model = "test-stub"
        stop_reason = "refusal"
        content = []

    class _Messages:
        def create(self, **kwargs):
            return _RefusedResponse()

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    assert vlm_identity.classify_reference(b"reference-a", "image/jpeg") is None

    # 거절 다음에 진짜 응답이 오면 여전히 계산되어야 한다 (실패가 굳지 않았다).
    calls = _stub_anthropic(monkeypatch, payload=_STUB_CLASSIFICATION)
    result = vlm_identity.classify_reference(b"reference-a", "image/jpeg")
    assert result is not None and len(calls) == 1
