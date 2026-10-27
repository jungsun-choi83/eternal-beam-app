"""
EXHIBITION ZIP HANDOFF PACKAGE — READY 전용 visitor_XXXX.zip (exhibition/{run_id}/).

CUTOUT/MAPS/매니페스트 생성 자체는 바꾸지 않는다 — READY 가 된 뒤 이미 저장된
4개 파일(subject_rgba/breathing_weight/locked_mask/manifest, preview_overlay 제외)을
묶어 하나 더 저장할 뿐이다. ViTMatte 는 목업한다 (matte_fn 주입).
"""

from __future__ import annotations

import io
import json
import zipfile

from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.tests.test_exhibition_breathing_maps import frontal_dog, round_blob
from backend.tests.test_exhibition_prep_run import _jpeg, _run, fake_matte


def _submission(stores, queue_number, **create_kw):
    create_kw.setdefault("allocate_queue_number", lambda: queue_number)
    create_kw.setdefault("exhibition_id", "expo-1")
    return _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), **create_kw)


def _unzip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


# ── 1. ZIP 은 정확히 4개 파일만 ────────────────────────────────────────────────


def test_zip_contains_exactly_the_four_required_files():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run = _submission(stores, 28)
    _, artifacts = stores
    zip_bytes = artifacts.get(run["package_zip_path"])
    with _unzip(zip_bytes) as zf:
        assert set(zf.namelist()) == {
            "subject_rgba.png", "breathing_weight.png", "locked_mask.png", "manifest.json",
        }
        # preview_overlay(운영자용)는 ZIP 에 없다.
        assert "preview_overlay.png" not in zf.namelist()
        # 파일이 루트에 바로 있다 (하위 폴더 없음).
        assert all("/" not in n for n in zf.namelist())


# ── 2. 파일명은 queue_number 로 ────────────────────────────────────────────────


def test_zip_naming_uses_queue_number_zero_padded():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run = _submission(stores, 28)
    rid = run["id"]
    assert run["package_id"] == "visitor_0028"
    assert run["package_zip_path"] == f"exhibition/{rid}/visitor_0028.zip"

    stores2 = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run2 = _submission(stores2, 7)
    assert run2["package_id"] == "visitor_0007"
    assert run2["package_zip_path"].endswith("visitor_0007.zip")

    # run_id 는 그대로 내부 식별자로 남는다 — 저장 경로의 폴더명은 run_id.
    assert run["package_zip_path"].split("/")[1] == rid


def test_zip_is_only_created_when_the_run_reaches_ready():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    needs_review = _run(stores, _jpeg(800, 800), fake_matte(round_blob), exhibition_id="expo-1",
                         allocate_queue_number=lambda: 9)
    assert needs_review["status"] == st.STATUS_NEEDS_REVIEW
    assert needs_review.get("package_id") is None
    assert needs_review.get("package_zip_path") is None
    _, artifacts = stores
    assert not any(p.endswith(".zip") for p in artifacts.objects)


def test_no_zip_without_a_queue_number():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))  # no allocate_queue_number
    assert run["status"] == st.STATUS_READY
    assert run.get("package_id") is None and run.get("package_zip_path") is None


# ── 3. 매니페스트는 ZIP 내부 상대 파일명을 참조 ──────────────────────────────────


def test_manifest_inside_zip_references_relative_filenames_and_has_package_fields():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run = _submission(stores, 28)
    _, artifacts = stores
    with _unzip(artifacts.get(run["package_zip_path"])) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["package_id"] == "visitor_0028"
    assert manifest["queue_number"] == 28
    assert manifest["run_id"] == run["id"]
    assert manifest["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    for key in ("subject_rgba", "breathing_weight", "locked_mask"):
        path = manifest["files"][key]["path"]
        assert "/" not in path and path in {"subject_rgba.png", "breathing_weight.png", "locked_mask.png"}

    # 같이 저장된 standalone manifest.json 도 동일한 package 필드를 갖는다.
    assert run["manifest_json"]["package_id"] == "visitor_0028"
    assert run["manifest_json"]["queue_number"] == 28


# ── 4. RGBA/weight/locked 산출물은 그대로 ─────────────────────────────────────


def test_rgba_weight_and_locked_assets_are_unchanged_by_zip_packaging():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    run = _submission(stores, 28)
    _, artifacts = stores
    rid = run["id"]

    standalone = {
        name: artifacts.get(f"exhibition/{rid}/{name}.png")
        for name in ("subject_rgba", "breathing_weight", "locked_mask")
    }
    with _unzip(artifacts.get(run["package_zip_path"])) as zf:
        zipped = {name: zf.read(f"{name}.png") for name in standalone}

    assert zipped == standalone  # ZIP 안의 바이트가 기존 산출물과 완전히 동일

    expected_outputs = {
        "subject_rgba.png", "breathing_weight.png", "locked_mask.png", "preview_overlay.png", "manifest.json",
        "source_normalized.png",
    }
    assert set(run["outputs_json"]) == expected_outputs  # zip 은 outputs_json 에 섞이지 않는다


# ── 7. 일반 Eternal Beam 파이프라인은 그대로 ──────────────────────────────────


def test_normal_pipeline_untouched_cutout_and_maps_unaffected_by_zip_feature():
    stores = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    with_queue = _submission(stores, 28)
    stores_no_queue = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    without_queue = _run(stores_no_queue, _jpeg(800, 800), fake_matte(frontal_dog))

    # 패키지에 queue_number 가 있든 없든 CUTOUT/MAPS 산출물은 동일한 알고리즘을 쓴다.
    for key in ("canvas_wh", "map_method", "anchors", "flags", "diagnostics", "subject_class"):
        assert with_queue["manifest_json"][key] == without_queue["manifest_json"][key]

    _, artifacts = stores
    _, artifacts_no_queue = stores_no_queue
    rid1, rid2 = with_queue["id"], without_queue["id"]
    for name in ("subject_rgba.png", "breathing_weight.png", "locked_mask.png"):
        assert artifacts.get(f"exhibition/{rid1}/{name}") == artifacts_no_queue.get(f"exhibition/{rid2}/{name}")
