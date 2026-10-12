"""
EXHIBITION 포즈 적합성 게이트 — 1 장 업로드, 리테이크 없음.

  build_package(원본) → classify_suitability(manifest)
    GOOD_FOR_DEFORMATION      → 원본 그대로 EXHIBITION_PREP_RUN 계속
    NEEDS_AI_NORMALIZATION    → normalize_fn 으로 고정 포즈 재생성
                                → 그 결과로 build_package 를 다시 돌려 계속
                                → 생성기 미설정/실패 시 원본으로 안전 복귀 (REVIEW 로직 불변)

ViTMatte 는 목업한다(matte_fn 주입) — test_exhibition_prep_run.py 와 같은 픽스처.
"""

from __future__ import annotations

import pytest

from backend.services import exhibition_pose_normalizer as pose_mod
from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.tests.test_exhibition_breathing_maps import frontal_dog, round_blob
from backend.tests.test_exhibition_prep_run import _jpeg, fake_matte


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def _run(stores, raw, matte, *, normalize_fn=None, **create_kw):
    """test_exhibition_prep_run._run 과 같지만 normalize_fn 을 process_run 으로 전달한다."""
    store, artifacts = stores
    row = svc.create_run(raw, created_by="ops-1", store=store, artifacts=artifacts, **create_kw)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    assert claimed["id"] == row["id"]
    svc.process_run(
        claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=matte, normalize_fn=normalize_fn
    )
    return store.get(row["id"])


def _normalized_jpeg() -> bytes:
    """AI 가 생성했다고 가정한 '정규화된 포즈' 원본 — 실제로는 frontal_dog(좋은 포즈)."""
    return _jpeg(640, 640)


def _spy_normalizer(generated_png: bytes):
    calls: list[tuple[bytes, dict]] = []

    def _normalize(reference_png: bytes, manifest: dict) -> bytes:
        calls.append((reference_png, manifest))
        return generated_png

    _normalize.calls = calls
    return _normalize


# ── 1. 적합한 이미지 → 원본 그대로 사용 ───────────────────────────────────────


def test_suitable_image_uses_the_original(stores):
    normalize = _spy_normalizer(_normalized_jpeg())
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), normalize_fn=normalize)

    assert final["status"] == st.STATUS_READY
    assert final["manifest_json"]["pose_suitability"] == {
        "decision": svc.SUITABILITY_GOOD,
        "source_used": "original",
    }
    assert normalize.calls == []  # 적합하면 생성기를 호출조차 하지 않는다
    assert "source_normalized.png" not in final["outputs_json"]


# ── 2. 부적합한 포즈 → AI 정규화 트리거 ────────────────────────────────────────


def test_unsuitable_pose_triggers_ai_normalization(stores):
    generated = _normalized_jpeg()
    normalize = _spy_normalizer(generated)
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob), normalize_fn=normalize)

    assert len(normalize.calls) == 1
    called_reference, called_manifest = normalize.calls[0]
    assert called_manifest["anchors"]["head_point"]["tier"] == "NO_PLAUSIBLE_HEAD"
    assert final["manifest_json"]["pose_suitability"]["decision"] == svc.SUITABILITY_NEEDS_NORMALIZATION
    assert final["manifest_json"]["pose_suitability"]["source_used"] == "ai_normalized"
    assert final["manifest_json"]["pose_suitability"]["prompt"] == pose_mod.build_normalization_prompt(
        {"subject_class": "dog"}
    )


# ── 3. 정규화 결과가 기존 EXHIBITION_PREP_RUN 으로 들어간다 ───────────────────


def test_normalized_result_enters_the_existing_prep_pipeline(stores):
    """정규화된 이미지가 실제로 build_package(=기존 CUTOUT→MAPS→PACKAGE)를 다시 거친다."""
    matte = fake_matte(round_blob)
    normalize = _spy_normalizer(_normalized_jpeg())
    final = _run(stores, _jpeg(800, 800), matte, normalize_fn=normalize)

    assert len(matte.calls) == 2  # 1차(적합성 판정용 원본) + 2차(정규화된 이미지) — 둘 다 진짜 build_package
    assert final["manifest_json"]["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    assert final["manifest_json"]["map_method"] == "dt-torso-v2"
    assert set(final["outputs_json"]) >= {
        "subject_rgba.png", "breathing_weight.png", "locked_mask.png", "preview_overlay.png", "manifest.json",
    }
    # 정규화 경로로 실제로 들어갔다는 표시 — manifest 에 기록이 남는다.
    assert final["manifest_json"]["pose_suitability"]["source_used"] == "ai_normalized"


def test_normalize_fn_is_called_exactly_once_matte_runs_twice(stores):
    matte = fake_matte(round_blob)
    normalize = _spy_normalizer(_normalized_jpeg())
    _run(stores, _jpeg(800, 800), matte, normalize_fn=normalize)

    assert len(normalize.calls) == 1  # 생성은 한 번만 — 재시도/리테이크 루프 없음
    assert len(matte.calls) == 2  # 1차(적합성 판정용) + 2차(정규화된 이미지)


# ── 4. 원본 이미지는 감사용으로 그대로 보존 ───────────────────────────────────


def test_original_image_is_preserved_for_audit(stores):
    store, artifacts = stores
    normalize = _spy_normalizer(_normalized_jpeg())
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob), normalize_fn=normalize)
    rid = final["id"]

    original_path = f"exhibition/{rid}/source.png"
    assert original_path in artifacts.objects  # 원본은 항상 있다 (create_run 이 처리 전에 올린다)
    assert final["manifest_json"]["pose_suitability"]["original_source_path"] == original_path

    normalized_path = final["manifest_json"]["pose_suitability"]["normalized_source_path"]
    assert normalized_path in artifacts.objects and normalized_path != original_path
    assert final["outputs_json"]["source_normalized.png"] == normalized_path


def test_original_image_preserved_even_when_normalization_is_not_configured(stores):
    """생성기가 없어도(기본값) 원본은 항상 보존되고, 실행은 원본으로 안전하게 진행된다."""
    store, artifacts = stores
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob))  # normalize_fn 주입 없음
    rid = final["id"]

    assert f"exhibition/{rid}/source.png" in artifacts.objects
    assert final["manifest_json"]["pose_suitability"] == {
        "decision": svc.SUITABILITY_NEEDS_NORMALIZATION,
        "source_used": "original",
        "normalization_error": "NORMALIZATION_NOT_CONFIGURED",
    }
    assert "source_normalized.png" not in final["outputs_json"]
    # 리뷰 로직 자체는 그대로다 — 적합성 게이트가 기존 NEEDS_REVIEW 를 바꾸지 않는다.
    assert final["status"] == st.STATUS_NEEDS_REVIEW
    assert "head_low_confidence" in final["review_reasons"]


def test_normalization_failure_falls_back_to_original_safely(stores):
    def _boom(reference_png, manifest):
        raise pose_mod.PoseNormalizationError("NORMALIZATION_HTTP_ERROR", "simulated failure")

    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob), normalize_fn=_boom)
    assert final["manifest_json"]["pose_suitability"]["source_used"] == "original"
    assert final["manifest_json"]["pose_suitability"]["normalization_error"] == "NORMALIZATION_HTTP_ERROR"
    assert final["status"] == st.STATUS_NEEDS_REVIEW


# ── 5. 리테이크 경로가 없다 ───────────────────────────────────────────────────


def test_no_retake_path_exists():
    """리테이크/2차 업로드 API·플래그가 전혀 없다 — 전부 process_run() 안에서 자동 결정된다."""
    import inspect

    create_run_params = set(inspect.signature(svc.create_run).parameters)
    process_run_params = set(inspect.signature(svc.process_run).parameters)
    assert "retake" not in create_run_params and "retake" not in process_run_params
    assert not hasattr(svc, "retake_run") and not hasattr(svc, "request_retake")
    # create_run 은 사진 1장만 받는다 — 두 번째 사진 파라미터가 없다.
    assert create_run_params == {
        "raw", "created_by", "store", "artifacts", "exhibition_id",
        "head_hint_xy", "allocate_queue_number", "pet_name",
    }


def test_needs_normalization_does_not_block_or_require_a_second_photo(stores):
    """부적합 포즈라도 실행은 (NEEDS_REVIEW 로) 끝까지 자동으로 끝난다 — 추가 입력 요구 없음."""
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob))
    assert final["status"] in (st.STATUS_READY, st.STATUS_NEEDS_REVIEW)
    assert final["status"] != st.STATUS_QUEUED and final["status"] != st.STATUS_RUNNING


# ── 6. 일반 Eternal Beam 파이프라인은 그대로 ──────────────────────────────────


def test_classify_suitability_is_a_pure_function_over_the_manifest():
    """추가 모델 추론 없음 — 이미 계산된 manifest 신호만 읽는다."""
    good_manifest = {
        "flags": {"head_low_confidence": False, "touches_frame_edge": False, "interior_holes": False},
        "diagnostics": {"torso_area_frac": 0.5},
    }
    assert svc.classify_suitability(good_manifest) == svc.SUITABILITY_GOOD

    bad_cases = [
        {"flags": {"head_low_confidence": True, "touches_frame_edge": False, "interior_holes": False},
         "diagnostics": {"torso_area_frac": 0.5}},
        {"flags": {"head_low_confidence": False, "touches_frame_edge": True, "interior_holes": False},
         "diagnostics": {"torso_area_frac": 0.5}},
        {"flags": {"head_low_confidence": False, "touches_frame_edge": False, "interior_holes": True},
         "diagnostics": {"torso_area_frac": 0.5}},
        {"flags": {"head_low_confidence": False, "touches_frame_edge": False, "interior_holes": False},
         "diagnostics": {"torso_area_frac": 0.05}},
        {"flags": {}, "diagnostics": {}},  # 신호 결측 — 안전한 쪽(정규화 필요)으로
    ]
    for manifest in bad_cases:
        assert svc.classify_suitability(manifest) == svc.SUITABILITY_NEEDS_NORMALIZATION


def test_normal_pipeline_untouched_cutout_and_maps_logic_unchanged(stores):
    """적합성 게이트가 있든 없든(normalize_fn 주입 여부) CUTOUT/MAPS 알고리즘 자체는 동일하다."""
    without_gate_context = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))

    stores2 = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    with_normalizer_available = _run(
        stores2, _jpeg(800, 800), fake_matte(frontal_dog), normalize_fn=_spy_normalizer(_normalized_jpeg())
    )

    for key in ("canvas_wh", "map_method", "anchors", "flags", "diagnostics", "subject_class"):
        assert without_gate_context["manifest_json"][key] == with_normalizer_available["manifest_json"][key]

    _, artifacts1 = stores
    _, artifacts2 = stores2
    rid1, rid2 = without_gate_context["id"], with_normalizer_available["id"]
    for name in ("subject_rgba.png", "breathing_weight.png", "locked_mask.png"):
        assert artifacts1.get(f"exhibition/{rid1}/{name}") == artifacts2.get(f"exhibition/{rid2}/{name}")


def test_zip_and_handoff_contract_unaffected_by_the_suitability_gate(stores):
    """ZIP 패키지 포맷·핸드오프 로직은 이 게이트와 무관하게 그대로다 (GOOD 경로)."""
    final = _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        exhibition_id="expo-1", allocate_queue_number=lambda: 28,
    )
    assert final["package_id"] == "visitor_0028"
    assert final["package_zip_path"] == f"exhibition/{final['id']}/visitor_0028.zip"
    import zipfile
    import io as _io

    _, artifacts = stores
    with zipfile.ZipFile(_io.BytesIO(artifacts.get(final["package_zip_path"]))) as zf:
        assert set(zf.namelist()) == {
            "subject_rgba.png", "breathing_weight.png", "locked_mask.png", "manifest.json",
        }
