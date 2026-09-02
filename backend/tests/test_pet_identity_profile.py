"""
펫 신원 프로필 (Phase 2) 계약 테스트.

- 유효한 원본+누끼 레퍼런스에서 프로필이 만들어진다 (실제 분석기, 합성 이미지)
- 프로필은 불변 버전으로 쌓인다 / 입력이 안 바뀌면 멱등이다
- 소유권 격리 (빌드·조회 모두)
- 증거가 없으면 unknown 이 기록된다 (누끼 없음, 진단 없음, VLM 꺼짐)
- 빌드는 원본 레퍼런스를 절대 수정하지 않고 스토리지에 쓰지 않는다
- 분석기/모델 버전이 프로필에 기록된다
- 신원 분석 실패가 온보딩(/assets/original)을 깨지 않는다
"""

from __future__ import annotations

import io

import anyio
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw

from backend.routers import assets as assets_router
from backend.routers import pet_identity_v1
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_registry, vlm_identity

from .conftest import ASGITestClient, make_jpeg_bytes


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("IDENTITY_PROFILE_AUTOBUILD", raising=False)
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()
    ids.__reset_for_tests()
    yield
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()
    ids.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


def _run(coro):
    return anyio.run(lambda: coro)


# ── 합성 이미지 ─────────────────────────────────────────────────────────────


def make_pet_cutout_png(*, body=(125, 84, 53), patch=(240, 238, 232), cropped=False) -> bytes:
    """
    강아지 비슷한 실루엣의 RGBA 누끼: 갈색 몸통 타원 + 흰 가슴 패치 + 다리 +
    오른쪽 위 머리. cropped=True 면 몸이 프레임 아래에 닿는다(잘린 사진).
    """
    w, h = 200, 150
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bottom = h if cropped else h - 20
    d.ellipse((30, 55, 150, 110), fill=(*body, 255))            # 몸통
    d.ellipse((130, 20, 180, 70), fill=(*body, 255))            # 머리 (오른쪽, 위로 솟음)
    d.ellipse((45, 80, 85, 108), fill=(*patch, 255))            # 가슴 패치
    for x0 in (45, 70, 105, 130):                               # 다리 4개
        d.rectangle((x0, 100, x0 + 12, bottom - 1), fill=(*body, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def make_striped_cutout_png() -> bytes:
    """전혀 다른 무늬(세로 줄무늬) — 시그니처 상이성 검증용."""
    w, h = 200, 150
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, x0 in enumerate(range(20, 180, 20)):
        color = (20, 20, 20, 255) if i % 2 == 0 else (230, 230, 230, 255)
        d.rectangle((x0, 30, x0 + 19, 120), fill=color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


DIAG = {
    "subject_detected": True,
    "subject_class": "dog",
    "detection_confidence": 0.87,
    "mask_area_fraction": 0.31,
    "rectangle_like_mask": False,
    "quality_score": 0.9,
}


def _seed_pet(uploads, *, content_id="cid1", user_id="alice@test", with_cutout=True, diagnostics=DIAG):
    """원본(+선택적 누끼) 레퍼런스를 대장에 등록하고 fetch 테이블을 돌려준다."""
    cutout_png = make_pet_cutout_png()
    original = _run(
        refs.record_original(
            user_id=user_id,
            content_id=content_id,
            data=make_jpeg_bytes(),
            mime_type="image/jpeg",
            diagnostics=diagnostics,
        )
    )
    bytes_by_path = {original.object_path: make_jpeg_bytes()}
    if with_cutout:
        derived = _run(
            refs.record_derived(
                user_id=user_id,
                content_id=content_id,
                object_path=f"{user_id}/{content_id}/cutout_vitmatte.png",
                derived_kind="cutout_vitmatte",
            )
        )
        bytes_by_path[derived.object_path] = cutout_png

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    return original, fetch


# ══════════════════════════════════════════════════════════════════════════
# 순수 분석기
# ══════════════════════════════════════════════════════════════════════════


def test_visual_identity_measures_coat_and_leaves_semantics_unknown():
    rgba = ids.load_rgba(make_pet_cutout_png())
    visual = ids.analyze_visual_identity(rgba)

    assert visual["status"] == "measured"
    names = [c["name"] for c in visual["coat"]["palette"]]
    assert any(n in ("brown", "dark_brown", "red_brown", "tan") for n in names)
    assert visual["coat"]["tone"] in ("dark", "medium", "light")
    # 잴 수 없는 것은 unknown — 추측 금지.
    for category in ("face", "eyes", "ears", "paws", "tail", "unique_features"):
        assert visual[category]["status"] == ids.UNKNOWN
    assert visual["coat"]["length"]["status"] == ids.UNKNOWN


def test_visual_identity_empty_mask_is_unknown():
    blank = np.zeros((50, 50, 4), dtype=np.uint8)
    visual = ids.analyze_visual_identity(blank)
    assert visual["status"] == ids.UNKNOWN
    assert visual["reason"] == "subject_mask_empty"


def test_structural_identity_measured_vs_low_confidence_split():
    rgba = ids.load_rgba(make_pet_cutout_png())
    structural = ids.analyze_structural_identity(rgba)

    sil = structural["silhouette"]
    assert sil["confidence"] == "measured"
    assert sil["border_contact"] == []
    assert sil["bbox_aspect_ratio"] > 1.0  # 가로로 긴 실루엣
    assert 0 < sil["area_fraction"] < 1

    pose = structural["pose"]
    # 휴리스틱 포즈는 성공해도 절대 measured 로 승격되지 않는다.
    if pose.get("status") != ids.UNKNOWN:
        assert pose["confidence"] == "low"
        assert pose["backend"] == "heuristic_mask_geometry"
        assert len(pose["keypoints"]) == 18
        assert pose["head_side"] in ("left", "right")
    # 실루엣만으로 판정 불가한 항목은 unknown.
    assert structural["tail_visibility"]["status"] == ids.UNKNOWN
    assert structural["leg_proportions"]["status"] == ids.UNKNOWN


def test_border_contact_detected_for_cropped_subject():
    rgba = ids.load_rgba(make_pet_cutout_png(cropped=True))
    assert "bottom" in ids.mask_border_contact(ids.subject_mask(rgba))


def test_signature_deterministic_and_discriminative():
    a = ids.compute_reference_signature(ids.load_rgba(make_pet_cutout_png()))
    a2 = ids.compute_reference_signature(ids.load_rgba(make_pet_cutout_png()))
    b = ids.compute_reference_signature(ids.load_rgba(make_striped_cutout_png()))

    assert a == a2  # 결정론
    same = ids.signature_similarity(a, a2)
    diff = ids.signature_similarity(a, b)
    assert same["phash_hamming"] == 0 and same["hist_intersection"] > 0.99
    assert diff["phash_hamming"] > 0
    assert diff["hist_intersection"] < same["hist_intersection"]


def test_eligibility_reads_stored_diagnostics_and_alpha():
    original, fetch = None, None  # noqa: F841 — 이 테스트는 순수 함수만 쓴다

    class Ref:
        diagnostics = DIAG
        person_detected = None

    rgba = ids.load_rgba(make_pet_cutout_png())
    entry = ids.evaluate_reference_eligibility(Ref(), rgba)
    assert entry["subject_detected"] is True
    assert entry["animal_class"] == "dog"
    assert entry["detection_confidence"] == 0.87
    assert entry["full_body_visible"] == "likely"
    assert entry["usable_for_identity"] is True
    # 근거 없는 뷰 라벨은 추측하지 않는다.
    assert entry["view_label_estimate"] == ids.UNKNOWN
    assert entry["face_usable"] == ids.UNKNOWN


def test_eligibility_without_evidence_is_unknown():
    class Ref:
        diagnostics = None
        person_detected = None

    entry = ids.evaluate_reference_eligibility(Ref(), None)
    assert entry["subject_detected"] == ids.UNKNOWN
    assert entry["detection_confidence"] == ids.UNKNOWN
    assert entry["border_contact"] == ids.UNKNOWN
    assert entry["full_body_visible"] == ids.UNKNOWN
    assert entry["usable_for_identity"] is False
    assert "no_segmentation_available" in entry["reasons"]


# ══════════════════════════════════════════════════════════════════════════
# 프로필 빌드
# ══════════════════════════════════════════════════════════════════════════


def test_build_profile_from_valid_references(uploads):
    original, fetch = _seed_pet(uploads)
    profile = _run(
        ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch)
    )

    assert profile.version == 1
    assert profile.status == ids.STATUS_COMPLETE
    assert profile.source_reference_ids == [original.id]
    assert profile.visual_identity["status"] == "measured"
    assert profile.structural_identity["status"] == "measured"
    assert profile.visual_identity["primary_reference_id"] == original.id

    entry = profile.reference_eligibility[original.id]
    assert entry["usable_for_identity"] is True
    assert entry["signature"]["version"] == ids.SIGNATURE_VERSION

    # 분석기 버전 기록 — 값을 만든 코드가 영구히 남는다.
    assert profile.analyzer_versions == ids.analyzer_versions()
    assert profile.analyzer_versions["visual"] == ids.VISUAL_ANALYZER_VERSION
    assert profile.analyzer_versions["vlm"] is None  # VLM 꺼짐

    assert profile.completeness["visual"]["known"] >= 1
    assert profile.completeness["visual"]["unknown"] >= 1
    assert profile.completeness["semantic"] == "skipped_vlm_disabled"


def test_build_is_idempotent_when_inputs_unchanged(uploads):
    _, fetch = _seed_pet(uploads)
    first = _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    second = _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))

    assert second.deduplicated is True
    assert second.version == first.version == 1


def test_forced_rebuild_appends_new_immutable_version(uploads):
    _, fetch = _seed_pet(uploads)
    v1 = _run(
        ids.build_identity_profile(
            user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch, skip_if_unchanged=False
        )
    )
    v2 = _run(
        ids.build_identity_profile(
            user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch, skip_if_unchanged=False
        )
    )
    assert (v1.version, v2.version) == (1, 2)

    latest = _run(ids.get_profile(user_id="alice@test", pet_id="pet_cid1"))
    assert latest.version == 2
    # 옛 버전은 그대로 남는다 — silent overwrite 없음.
    old = _run(ids.get_profile(user_id="alice@test", pet_id="pet_cid1", version=1))
    assert old is not None and old.id == v1.id


def test_new_reference_triggers_new_version(uploads):
    _, fetch1 = _seed_pet(uploads)
    v1 = _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch1))

    # 새 원본이 추가되면 입력 집합이 달라져 멱등 스킵이 풀린다.
    _run(
        refs.record_original(
            user_id="alice@test",
            content_id="cid1",
            data=make_jpeg_bytes(64, 64),
            mime_type="image/jpeg",
        )
    )
    v2 = _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch1))
    assert v1.version == 1 and v2.version == 2
    assert len(v2.source_reference_ids) == 2


def test_ownership_isolation_for_build_and_get(uploads):
    _, fetch = _seed_pet(uploads)

    with pytest.raises(ids.PetIdentityError) as e:
        _run(ids.build_identity_profile(user_id="mallory@test", pet_id="pet_cid1", fetch_bytes=fetch))
    assert e.value.code == "PET_NOT_OWNED" and e.value.status == 403

    _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    with pytest.raises(ids.PetIdentityError):
        _run(ids.get_profile(user_id="mallory@test", pet_id="pet_cid1"))


def test_profile_without_cutout_is_partial_and_unknown(uploads):
    _, fetch = _seed_pet(uploads, with_cutout=False, diagnostics=None)
    profile = _run(
        ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch)
    )

    assert profile.status == ids.STATUS_PARTIAL
    assert profile.visual_identity["status"] == ids.UNKNOWN
    assert profile.visual_identity["reason"] == "no_analyzable_reference"
    assert profile.structural_identity["status"] == ids.UNKNOWN
    entry = list(profile.reference_eligibility.values())[0]
    assert "no_segmentation_available" in entry["reasons"]
    assert profile.visual_identity["semantic_traits"]["reason"] == "vlm_disabled"


def test_build_never_modifies_references_or_storage(uploads):
    _, fetch = _seed_pet(uploads)
    before_refs = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    uploads_before = list(uploads)

    _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))

    after_refs = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert after_refs == before_refs  # 대장 행이 1바이트도 안 바뀐다
    assert uploads == uploads_before  # 스토리지에 아무것도 올리지 않는다


def test_build_without_originals_is_409(uploads):
    _run(
        refs.record_derived(
            user_id="alice@test",
            content_id="cid1",
            object_path="alice@test/cid1/cutout_vitmatte.png",
            derived_kind="cutout_vitmatte",
        )
    )
    with pytest.raises(ids.PetIdentityError) as e:
        _run(ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1"))
    assert e.value.code == "NO_ORIGINAL_REFERENCES"


# ══════════════════════════════════════════════════════════════════════════
# VLM 격리
# ══════════════════════════════════════════════════════════════════════════


def test_vlm_disabled_returns_none_without_import():
    assert vlm_identity.is_enabled() is False
    assert vlm_identity.analyze_semantic_traits([(b"xx", "image/jpeg")]) is None


def test_vlm_schema_requires_unknown_capable_fields():
    schema = vlm_identity.SEMANTIC_TRAITS_SCHEMA
    assert schema["additionalProperties"] is False
    assert "unknown" in schema["properties"]["ears"]["properties"]["shape"]["enum"]
    assert "unknown" in schema["properties"]["breed_confidence"]["enum"]


def test_vlm_failure_recorded_as_unknown_not_fabricated(uploads, monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setattr(vlm_identity, "analyze_semantic_traits", lambda images: None)
    _, fetch = _seed_pet(uploads)

    profile = _run(
        ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch)
    )
    assert profile.visual_identity["semantic_traits"]["status"] == ids.UNKNOWN
    assert profile.completeness["semantic"] == "failed"
    # 켜져 있었으므로 어떤 모델을 시도했는지는 기록된다.
    assert profile.analyzer_versions["vlm"] == vlm_identity.VLM_ANALYZER_VERSION


# ══════════════════════════════════════════════════════════════════════════
# 라우터 + 온보딩 보존
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def identity_client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(pet_identity_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_build_and_get(identity_client, uploads, monkeypatch):
    _, fetch = _seed_pet(uploads)
    monkeypatch.setattr(ids, "_default_fetch_bytes", fetch)

    res = identity_client.post("/api/v1/pet/identity/pet_cid1/build", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == 1 and body["status"] == "complete"

    res = identity_client.get("/api/v1/pet/identity/pet_cid1", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["analyzer_versions"]["visual"] == ids.VISUAL_ANALYZER_VERSION

    res = identity_client.get(
        "/api/v1/pet/identity/pet_cid1",
        headers={"Authorization": "Bearer test:mallory@test"},
    )
    assert res.status_code == 403


def test_router_missing_profile_is_404(identity_client, uploads):
    _seed_pet(uploads)
    res = identity_client.get("/api/v1/pet/identity/pet_cid1", headers=AUTH)
    assert res.status_code == 404


@pytest.fixture
def assets_client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return ASGITestClient(app)


def test_identity_failure_never_breaks_onboarding(assets_client, monkeypatch):
    """autobuild 가 켜져 있고 분석이 죽어도 원본 인테이크는 200 이다."""
    monkeypatch.setenv("IDENTITY_PROFILE_AUTOBUILD", "1")
    calls: list[str] = []

    async def boom(**kwargs):
        calls.append(kwargs.get("pet_id", ""))
        raise RuntimeError("identity analyzer down")

    monkeypatch.setattr(ids, "build_identity_profile", boom)

    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1"},
    )
    assert res.status_code == 200
    assert res.json()["reference_recorded"] is True
    assert calls == ["pet_cid1"]  # 백그라운드 빌드가 실제로 시도됐고, 실패는 흡수됐다


def test_autobuild_disabled_by_default(assets_client, monkeypatch):
    calls: list[str] = []

    async def recorder(**kwargs):
        calls.append(kwargs.get("pet_id", ""))

    monkeypatch.setattr(ids, "build_identity_profile", recorder)
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1"},
    )
    assert res.status_code == 200
    assert calls == []
