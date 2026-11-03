"""
EXHIBITION 포즈 적합성 게이트 — 1 장 업로드, 리테이크 없음.

  원본 → pose_gate(로컬 YOLO 크롭 → CLIP 제로샷; 여기서는 주입) → 판정
    GOOD/BAD 모두             → normalize_fn 으로 표준 3/4 포즈 재생성
                                → 그 결과로 build_package **1회**
                                → 생성기 미설정/실패 시 FAILED (CUTOUT 안 함)

게이트는 CUTOUT **전에** 돈다 — 누끼/맵은 어느 경로든 한 번만 돈다.
ViTMatte 는 목업한다(matte_fn 주입) — test_exhibition_prep_run.py 와 같은 픽스처.
"""

from __future__ import annotations

import pytest

from backend.services import exhibition_pose_gate as gate_mod
from backend.services import exhibition_pose_normalizer as pose_mod
from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.tests.test_exhibition_breathing_maps import frontal_dog, round_blob
from backend.tests.test_exhibition_prep_run import _jpeg, fake_matte


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def _run(stores, raw, matte, *, normalize_fn=None, pose_gate_fn=None, **create_kw):
    """test_exhibition_prep_run._run 과 같지만 normalize_fn / pose_gate_fn 을 process_run 으로 전달한다."""
    store, artifacts = stores
    row = svc.create_run(raw, created_by="ops-1", store=store, artifacts=artifacts, **create_kw)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    assert claimed["id"] == row["id"]
    svc.process_run(
        claimed, store=store, artifacts=artifacts, worker_id="w1",
        matte_fn=matte, normalize_fn=normalize_fn, pose_gate_fn=pose_gate_fn,
    )
    return store.get(row["id"])


def _gate(decision: str, *, subject_class: str = "dog", log: list | None = None):
    """주입용 게이트 — 실제 exhibition_pose_gate.assess_pose 와 같은 레코드 모양."""
    calls: list[bytes] = []

    def _fn(source_png: bytes) -> dict:
        calls.append(source_png)
        if log is not None:
            log.append("gate")
        return {
            "version": gate_mod.GATE_VERSION, "mode": "clip", "model": "injected",
            "subject_class": subject_class, "decision": decision, "reason": "injected",
            "top_label": "top_down" if decision == svc.SUITABILITY_NEEDS_NORMALIZATION else "front_standing",
        }

    _fn.calls = calls
    return _fn


def _good_gate(**kw):
    return _gate(svc.SUITABILITY_GOOD, **kw)


def _bad_gate(**kw):
    return _gate(svc.SUITABILITY_NEEDS_NORMALIZATION, **kw)


def _normalized_jpeg() -> bytes:
    """AI 가 생성했다고 가정한 '정규화된 포즈' 원본 — 크기로 원본(800×800)과 구분한다 (fake_matte 의 640×520 실루엣이 잘리지 않게 900)."""
    return _jpeg(900, 900)


def _spy_normalizer(generated_png: bytes):
    calls: list[tuple[bytes, dict]] = []

    def _normalize(reference_png: bytes, context: dict) -> bytes:
        calls.append((reference_png, context))
        return generated_png

    _normalize.calls = calls
    return _normalize


# ── 1. 적합한 이미지 → 원본 그대로 사용 ───────────────────────────────────────


def test_suitable_image_is_still_normalized(stores):
    normalize = _spy_normalizer(_normalized_jpeg())
    matte = fake_matte(frontal_dog)
    final = _run(stores, _jpeg(800, 800), matte, normalize_fn=normalize, pose_gate_fn=_good_gate())

    assert final["status"] == st.STATUS_READY
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["decision"] == svc.SUITABILITY_GOOD and ps["source_used"] == "ai_normalized"
    assert ps["generation_policy"] == "always"
    assert ps["gate"]["decision"] == svc.SUITABILITY_GOOD
    assert ps["silhouette_check"] == svc.SUITABILITY_GOOD
    assert "normalization_error" not in ps and "prompt" in ps
    assert len(normalize.calls) == 1
    assert matte.calls == [(900, 900)]  # 누끼는 생성본으로 정확히 1회
    assert "source_normalized.png" in final["outputs_json"]


# ── 2. 부적합한 포즈 → AI 정규화 트리거 ────────────────────────────────────────


def test_unsuitable_pose_triggers_ai_normalization(stores):
    store, artifacts = stores
    generated = _normalized_jpeg()
    normalize = _spy_normalizer(generated)
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), normalize_fn=normalize, pose_gate_fn=_bad_gate())

    assert len(normalize.calls) == 1
    called_reference, called_context = normalize.calls[0]
    assert called_reference == artifacts.get(f"exhibition/{final['id']}/source.png")  # 원본(정규화된 PNG)이 레퍼런스
    assert called_context["decision"] == svc.SUITABILITY_NEEDS_NORMALIZATION
    assert called_context["subject_class"] == "dog"
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["decision"] == svc.SUITABILITY_NEEDS_NORMALIZATION
    assert ps["source_used"] == "ai_normalized"
    assert ps["prompt"] == pose_mod.build_normalization_prompt({"subject_class": "dog"})
    assert final["status"] == st.STATUS_READY


@pytest.mark.parametrize("gate_factory", [_good_gate, _bad_gate], ids=["GOOD", "BAD"])
def test_generator_is_called_once_for_every_gate_result(stores, gate_factory):
    normalize = _spy_normalizer(_normalized_jpeg())
    _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), normalize_fn=normalize, pose_gate_fn=gate_factory())
    assert len(normalize.calls) == 1


# ── 3. 기존 EXHIBITION_PREP_RUN 은 어느 경로든 정확히 한 번 ──────────────────────


def test_exhibition_prep_runs_once_on_the_good_path(stores):
    matte = fake_matte(frontal_dog)
    normalize = _spy_normalizer(_normalized_jpeg())
    _run(stores, _jpeg(800, 800), matte, normalize_fn=normalize, pose_gate_fn=_good_gate())
    assert len(normalize.calls) == 1
    assert matte.calls == [(900, 900)]


def test_exhibition_prep_runs_once_on_the_bad_path_and_only_on_the_generated_image(stores):
    matte = fake_matte(frontal_dog)
    normalize = _spy_normalizer(_normalized_jpeg())
    _run(stores, _jpeg(800, 800), matte, normalize_fn=normalize, pose_gate_fn=_bad_gate())
    assert len(normalize.calls) == 1  # 생성은 한 번만 — 재시도/리테이크 루프 없음
    assert matte.calls == [(900, 900)]  # 누끼/맵은 생성본으로 1회 — 원본으로는 돌지 않는다


def test_gate_runs_before_cutout(stores):
    order: list[str] = []
    matte = fake_matte(frontal_dog)
    inner = matte

    def _matte_logged(image_bytes):
        order.append("matte")
        return inner(image_bytes)

    _matte_logged.calls = inner.calls

    def _normalize_logged(_source, _context):
        order.append("normalize")
        return _normalized_jpeg()

    _run(
        stores, _jpeg(800, 800), _matte_logged,
        normalize_fn=_normalize_logged, pose_gate_fn=_good_gate(log=order),
    )
    assert order == ["gate", "normalize", "matte"]


def test_normalized_result_enters_the_existing_prep_pipeline(stores):
    """정규화된 이미지가 실제로 build_package(=기존 CUTOUT→MAPS→PACKAGE)를 거친다."""
    final = _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        normalize_fn=_spy_normalizer(_normalized_jpeg()), pose_gate_fn=_bad_gate(),
    )
    assert final["manifest_json"]["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    assert final["manifest_json"]["map_method"] == "dt-torso-v3"
    assert set(final["outputs_json"]) >= {
        "subject_rgba.png", "breathing_weight.png", "locked_mask.png", "preview_overlay.png", "manifest.json",
    }
    assert final["manifest_json"]["pose_suitability"]["source_used"] == "ai_normalized"
    # 생성본 크기(900)가 패키지 source 메타에 반영된다.
    assert final["manifest_json"]["source"]["source_wh"] == [900, 900]


# ── 4. 원본 이미지는 감사용으로 그대로 보존 ───────────────────────────────────


def test_original_image_is_preserved_for_audit(stores):
    store, artifacts = stores
    final = _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        normalize_fn=_spy_normalizer(_normalized_jpeg()), pose_gate_fn=_bad_gate(),
    )
    rid = final["id"]

    original_path = f"exhibition/{rid}/source.png"
    assert original_path in artifacts.objects  # 원본은 항상 있다 (create_run 이 처리 전에 올린다)
    assert final["manifest_json"]["pose_suitability"]["original_source_path"] == original_path

    normalized_path = final["manifest_json"]["pose_suitability"]["normalized_source_path"]
    assert normalized_path in artifacts.objects and normalized_path != original_path
    assert final["outputs_json"]["source_normalized.png"] == normalized_path


def test_original_image_preserved_when_normalization_is_not_configured(stores, monkeypatch):
    """생성기가 없으면 FAILED 이지만 원본은 감사용으로 보존된다."""
    monkeypatch.setenv("EXHIBITION_POSE_NORMALIZATION_MODE", "off")
    store, artifacts = stores
    matte = fake_matte(frontal_dog)
    final = _run(stores, _jpeg(800, 800), matte, pose_gate_fn=_bad_gate())  # normalize_fn 주입 없음
    rid = final["id"]

    assert f"exhibition/{rid}/source.png" in artifacts.objects
    assert final["status"] == st.STATUS_FAILED
    assert final["error_code"] == "NORMALIZATION_NOT_CONFIGURED"
    assert not final.get("outputs_json")
    assert matte.calls == []


def test_normalization_failure_stops_before_cutout(stores):
    def _boom(reference_png, context):
        raise pose_mod.PoseNormalizationError("NORMALIZATION_HTTP_ERROR", "simulated failure")

    matte = fake_matte(frontal_dog)
    final = _run(stores, _jpeg(800, 800), matte, normalize_fn=_boom, pose_gate_fn=_bad_gate())
    assert final["status"] == st.STATUS_FAILED
    assert final["error_code"] == "NORMALIZATION_HTTP_ERROR"
    assert not final.get("manifest_json")
    assert matte.calls == []


# ── 5. 실루엣 신호는 생성을 트리거하지 않는다 (진단만) ───────────────────────────


def test_silhouette_flags_do_not_control_generation(stores):
    """생성은 항상 하고 silhouette_check 는 기존 패키지 진단으로만 남는다."""
    normalize = _spy_normalizer(_normalized_jpeg())
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob), normalize_fn=normalize, pose_gate_fn=_good_gate())
    assert len(normalize.calls) == 1
    assert final["status"] == st.STATUS_NEEDS_REVIEW
    assert "head_low_confidence" in final["review_reasons"]
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["decision"] == svc.SUITABILITY_GOOD and ps["source_used"] == "ai_normalized"
    assert ps["silhouette_check"] == svc.SUITABILITY_NEEDS_NORMALIZATION


def test_default_gate_in_tests_is_off_and_answers_good(stores):
    """pose_gate_fn 을 주입하지 않으면 실제 게이트가 돈다 — 테스트 환경(mode=off)에선 모델 없이 GOOD."""
    normalize = _spy_normalizer(_normalized_jpeg())
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), normalize_fn=normalize)
    ps = final["manifest_json"]["pose_suitability"]
    assert ps["decision"] == svc.SUITABILITY_GOOD
    assert ps["gate"]["reason"] == "gate_off" and ps["gate"]["version"] == gate_mod.GATE_VERSION
    assert len(normalize.calls) == 1


# ── 6. 리테이크 경로가 없다 ───────────────────────────────────────────────────


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


def test_every_upload_completes_without_requiring_a_second_photo(stores):
    """부적합 포즈라도 실행은 끝까지 자동으로 끝난다 — 추가 입력 요구 없음."""
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), pose_gate_fn=_bad_gate())
    assert final["status"] in (st.STATUS_READY, st.STATUS_NEEDS_REVIEW)
    assert final["status"] != st.STATUS_QUEUED and final["status"] != st.STATUS_RUNNING


# ── 7. 일반 Eternal Beam 파이프라인·기존 전시 계약은 그대로 ─────────────────────


def test_classify_suitability_is_a_pure_function_over_the_manifest():
    """진단 전용 실루엣 검사 — 추가 모델 추론 없음, 이미 계산된 manifest 신호만 읽는다."""
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
        {"flags": {}, "diagnostics": {}},  # 신호 결측
    ]
    for manifest in bad_cases:
        assert svc.classify_suitability(manifest) == svc.SUITABILITY_NEEDS_NORMALIZATION


def test_normal_pipeline_untouched_cutout_and_maps_logic_unchanged(stores):
    """게이트/생성기 주입 여부와 무관하게 CUTOUT/MAPS 알고리즘 자체는 동일하다."""
    without_gate_context = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))

    stores2 = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    with_normalizer_available = _run(
        stores2, _jpeg(800, 800), fake_matte(frontal_dog),
        normalize_fn=_spy_normalizer(_jpeg(800, 800)), pose_gate_fn=_good_gate(),
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
        stores, _jpeg(800, 800), fake_matte(frontal_dog), pose_gate_fn=_good_gate(),
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
