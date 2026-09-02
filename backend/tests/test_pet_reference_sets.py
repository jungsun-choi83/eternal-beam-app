"""
신뢰 레퍼런스 세트 (Phase 3) 계약 테스트.

시나리오 A~H (요구 15) + 시스템 계약:
  A 우수한 3~5장 커버리지        B 정면 1장만
  C 전신이지만 얼굴 부분 가림     D 꼬리 안 보임
  E 흐린 사진 + 좋은 사진         F 사람이 펫을 가림
  G 다른 펫일 가능성              H 중복 이미지
+ 버전·멱등·결정론·소유권·원본 불변·근거 추적·온보딩 보존.

뷰/포즈 분류는 VLM 계약(구조화 출력) 그대로의 데이터를 주입해 선택 로직을
검증한다 — 결정론 폴백(UNKNOWN)과 VLM 비활성 경로는 실제 코드로 검증한다.
"""

from __future__ import annotations

import io

import anyio
import pytest
from fastapi import FastAPI
from PIL import Image, ImageFilter

from backend.routers import assets as assets_router
from backend.routers import pet_references_v1
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity

from .conftest import ASGITestClient, make_jpeg_bytes
from .test_pet_identity_profile import make_pet_cutout_png, make_striped_cutout_png

USER = "alice@test"
PET = "pet_cid1"
CID = "cid1"

DIAG = {
    "subject_detected": True,
    "subject_class": "dog",
    "detection_confidence": 0.87,
    "mask_area_fraction": 0.29,
    "rectangle_like_mask": False,
    "quality_score": 0.9,
}

VIS_KEYS = (
    "face_visible",
    "full_body_visible",
    "left_side_visible",
    "right_side_visible",
    "paws_visible",
    "tail_visible",
    "ears_visible",
    "distinct_markings_visible",
    "heavy_occlusion",
    "person_obstruction",
)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    for svc in (refs, pet_registry, ids, sets):
        svc.__reset_for_tests()
    yield
    for svc in (refs, pet_registry, ids, sets):
        svc.__reset_for_tests()


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


def blur_png(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    out = im.filter(ImageFilter.GaussianBlur(6))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


class Harness:
    """대장 시딩 + fetch 테이블 + 바이트 기반 분류 주입."""

    def __init__(self):
        self.bytes_by_path: dict[str, bytes] = {}
        self.classification_by_original: dict[bytes, dict] = {}
        self._seq = 0

    def seed(self, *, original: bytes | None = None, cutout: bytes | None = None,
             classification: dict | None = None, diagnostics=DIAG, user=USER):
        self._seq += 1
        orig_bytes = original or make_jpeg_bytes(120 + self._seq, 90 + self._seq)
        ref = _run(
            refs.record_original(
                user_id=user, content_id=CID, data=orig_bytes,
                mime_type="image/jpeg", diagnostics=diagnostics,
            )
        )
        self.bytes_by_path[ref.object_path] = orig_bytes
        if cutout is not None:
            cut_path = f"{user}/{CID}/references/cutout_{ref.content_hash[:16]}.png"
            derived = _run(
                refs.record_derived(
                    user_id=user, content_id=CID, object_path=cut_path,
                    derived_kind="cutout_reference", parent_reference_id=ref.id,
                    mime_type="image/png",
                )
            )
            self.bytes_by_path[derived.object_path] = cutout
        if classification is not None:
            self.classification_by_original[orig_bytes] = classification
        return ref

    def fetch(self, ref):
        return self.bytes_by_path.get(ref.object_path)

    def install_vlm(self, monkeypatch):
        """VLM 계약 형태의 분류 결과를 주입 (구조/키는 실제 스키마와 동일)."""
        monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
        monkeypatch.setattr(vlm_identity, "analyze_semantic_traits", lambda images: None)
        monkeypatch.setattr(
            vlm_identity,
            "classify_reference",
            lambda data, mime="image/jpeg": self.classification_by_original.get(bytes(data)),
        )

    def build(self, *, user=USER, force=False):
        return _run(
            sets.build_reference_set(
                user_id=user, pet_id=PET, fetch_bytes=self.fetch,
                skip_if_unchanged=not force,
            )
        )


def cls(view="UNKNOWN", vconf="high", pose="STANDING", pconf="high", **vis) -> dict:
    v = {k: "unknown" for k in VIS_KEYS}
    v.update(vis)
    return {
        "view_label": view,
        "view_confidence": vconf,
        "pose_label": pose,
        "pose_confidence": pconf,
        "visibility": v,
        "source": vlm_identity.VLM_CLASSIFIER_VERSION,
        "model": "test-stub",
    }


def items_by_role(s) -> dict[str, dict]:
    return {i["role"]: i for i in s.items}


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 A — 우수한 커버리지 (4장)
# ══════════════════════════════════════════════════════════════════════════


def _seed_excellent(h: Harness):
    r_face = h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT", face_visible="yes", ears_visible="yes"),
    )
    r_left = h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(
            view="LEFT", full_body_visible="yes", tail_visible="yes",
            distinct_markings_visible="yes", paws_visible="yes",
        ),
    )
    r_right3q = h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"),
    )
    r_extra = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="UNKNOWN", vconf="low"))
    return r_face, r_left, r_right3q, r_extra


def test_scenario_a_excellent_coverage(uploads, monkeypatch):
    h = Harness()
    r_face, r_left, r_right3q, _ = _seed_excellent(h)
    h.install_vlm(monkeypatch)

    s = h.build()
    roles = items_by_role(s)

    assert roles["PRIMARY_FACE"]["reference_id"] == r_face.id
    assert roles["PRIMARY_FRONT"]["reference_id"] == r_face.id
    assert roles["PRIMARY_LEFT"]["reference_id"] == r_left.id
    assert roles["PRIMARY_FULL_BODY"]["reference_id"] == r_left.id
    assert roles["PRIMARY_TAIL"]["reference_id"] == r_left.id
    assert roles["PRIMARY_MARKINGS"]["reference_id"] == r_left.id
    assert roles["PRIMARY_3Q"]["reference_id"] == r_right3q.id
    assert roles["PRIMARY_RIGHT"]["reference_id"] == r_right3q.id  # 3Q 간접 근거
    assert "PRIMARY_BACK" not in roles

    assert s.coverage["face"] == "GOOD"
    assert s.coverage["full_body"] == "GOOD"
    assert s.coverage["left"] == "GOOD"
    assert s.coverage["back"] == "MISSING"
    assert s.coverage["right"] == "PARTIAL"  # 간접(3Q) 근거는 GOOD 으로 안 올린다
    assert s.completeness_tier == "EXCELLENT"
    assert s.status == "complete"

    # 컴포넌트 점수와 최종 점수가 둘 다 남는다 — 선택 근거 추적 (요구 4).
    face_item = roles["PRIMARY_FACE"]
    assert "sharpness" in face_item["component_scores"]
    assert 0 < face_item["selection_score"] <= 1
    assert face_item["selection_reason"]


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 B/C/D — 부분 증거
# ══════════════════════════════════════════════════════════════════════════


def test_scenario_b_front_only_photo(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)

    s = h.build()
    roles = items_by_role(s)
    assert "PRIMARY_FACE" in roles and "PRIMARY_FRONT" in roles
    for missing in ("left", "right", "back"):
        assert s.coverage[missing] == "MISSING"
    # 뷰가 없어도 실패하지 않는다 — 세트는 만들어진다 (요구 7).
    assert s.status == "complete"
    assert s.completeness_tier in ("MINIMUM", "GOOD")


def test_scenario_c_face_partially_hidden(uploads, monkeypatch):
    h = Harness()
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="LEFT", full_body_visible="yes", face_visible="unknown"),
    )
    h.install_vlm(monkeypatch)

    s = h.build()
    roles = items_by_role(s)
    assert "PRIMARY_FULL_BODY" in roles
    assert "PRIMARY_FACE" not in roles  # 안 보이는 얼굴을 추론하지 않는다
    assert s.coverage["face"] == "MISSING"


def test_scenario_d_tail_hidden(uploads, monkeypatch):
    h = Harness()
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT", face_visible="yes", tail_visible="no"),
    )
    h.install_vlm(monkeypatch)

    s = h.build()
    assert "PRIMARY_TAIL" not in items_by_role(s)
    assert s.coverage["tail"] == "MISSING"


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 E — 흐린 사진보다 선명한 사진
# ══════════════════════════════════════════════════════════════════════════


def test_scenario_e_sharp_beats_blurry(uploads, monkeypatch):
    h = Harness()
    sharp_png = make_pet_cutout_png()
    r_blurry = h.seed(
        cutout=blur_png(sharp_png),
        classification=cls(view="LEFT", full_body_visible="yes"),
    )
    r_sharp = h.seed(
        cutout=sharp_png,
        classification=cls(view="LEFT", full_body_visible="yes"),
    )
    h.install_vlm(monkeypatch)

    s = h.build()
    roles = items_by_role(s)
    assert roles["PRIMARY_FULL_BODY"]["reference_id"] == r_sharp.id
    assert roles["PRIMARY_LEFT"]["reference_id"] == r_sharp.id
    a_sharp = s.reference_analysis[r_sharp.id]["quality"]["components"]["sharpness"]
    a_blur = s.reference_analysis[r_blurry.id]["quality"]["components"]["sharpness"]
    assert a_sharp > a_blur


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 F — 사람이 가림
# ══════════════════════════════════════════════════════════════════════════


def test_scenario_f_person_obstruction_penalized_not_deleted(uploads, monkeypatch):
    h = Harness()
    diag = {**DIAG, "person_boxes": [[1, 2, 3, 4]]}
    r = h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT", face_visible="yes", person_obstruction="yes"),
        diagnostics=diag,
    )
    h.install_vlm(monkeypatch)

    s = h.build()
    analysis = s.reference_analysis[r.id]
    assert analysis["eligibility"]["person_contamination"] is True
    assert analysis["quality"]["components"]["person_free"] == 0.0
    roles = items_by_role(s)
    # 유일한 레퍼런스라 그래도 선택되지만, 커버리지는 GOOD 이 아니다.
    assert roles["PRIMARY_FACE"]["reference_id"] == r.id
    assert s.coverage["face"] == "PARTIAL"


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 G — 다른 펫 의심
# ══════════════════════════════════════════════════════════════════════════


def test_scenario_g_likely_mismatch_preserved_but_excluded(uploads, monkeypatch):
    h = Harness()
    r1 = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    r2 = h.seed(
        cutout=make_pet_cutout_png(patch=(235, 230, 224)),
        classification=cls(view="LEFT", full_body_visible="yes"),
    )
    r_other = h.seed(
        cutout=make_striped_cutout_png(),
        classification=cls(view="RIGHT", full_body_visible="yes"),
    )
    h.install_vlm(monkeypatch)

    s = h.build()
    assert s.reference_analysis[r1.id]["consistency"]["label"] == sets.CONSISTENT
    assert s.reference_analysis[r2.id]["consistency"]["label"] == sets.CONSISTENT
    other = s.reference_analysis[r_other.id]
    assert other["consistency"]["label"] == sets.LIKELY_MISMATCH
    assert other["excluded_reason"] == "excluded_likely_mismatch"

    # 의심 레퍼런스는 선택되지 않지만 **보존**된다 — 대장에서 사라지지 않는다.
    selected = {i["reference_id"] for i in s.items}
    assert r_other.id not in selected
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    assert any(r.id == r_other.id for r in ledger)
    assert s.coverage["right"] == "MISSING"  # 의심 근거로 커버리지를 채우지 않는다


# ══════════════════════════════════════════════════════════════════════════
# 시나리오 H — 중복 이미지
# ══════════════════════════════════════════════════════════════════════════


def test_scenario_h_duplicate_image_collapses(uploads, monkeypatch):
    h = Harness()
    dup = make_jpeg_bytes(140, 100)
    h.seed(original=dup, cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.seed(original=dup)  # 같은 바이트 → Phase 1 이 같은 행으로 멱등 처리
    h.install_vlm(monkeypatch)

    s = h.build()
    assert len(s.source_reference_ids) == 1
    roles = [i["role"] for i in s.items]
    assert len(roles) == len(set(roles))  # 역할당 항목 1개 (한 이미지가 여러 역할은 가능)
    assert {i["reference_id"] for i in s.items} == set(s.source_reference_ids)


# ══════════════════════════════════════════════════════════════════════════
# 시스템 계약
# ══════════════════════════════════════════════════════════════════════════


def test_one_photo_without_vlm_builds_limited_set(uploads):
    """VLM 없이 1장: 뷰는 정직하게 UNKNOWN, 결정론적 전신 근거만으로 제한 세트."""
    h = Harness()
    r = h.seed(cutout=make_pet_cutout_png())

    s = h.build()
    analysis = s.reference_analysis[r.id]
    assert analysis["classification"]["view_label"] == "UNKNOWN"
    assert analysis["classification"]["source"] == sets.DETERMINISTIC_CLASSIFIER_VERSION

    roles = items_by_role(s)
    assert list(roles) == ["PRIMARY_FULL_BODY"]  # 유일하게 근거 있는 역할
    assert roles["PRIMARY_FULL_BODY"]["selection_reason"] == "deterministic:no_border_contact"
    assert s.coverage["full_body"] == "PARTIAL"
    assert s.coverage["face"] == "MISSING"
    assert s.completeness_tier == "LIMITED"
    assert s.status == "complete"
    assert s.analyzer_versions["view_classifier"] == sets.DETERMINISTIC_CLASSIFIER_VERSION


def test_versioning_appends_and_preserves_history(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)

    v1 = h.build()
    assert v1.version == 1

    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="LEFT", full_body_visible="yes"),
    )
    v2 = h.build()  # 새 레퍼런스 → 멱등 스킵 해제 → 새 버전
    assert v2.version == 2 and v2.deduplicated is False
    assert len(v2.source_reference_ids) == 2

    old = _run(sets.get_set(user_id=USER, pet_id=PET, version=1))
    assert old.id == v1.id
    assert len(old.source_reference_ids) == 1  # 역사적 세트는 그대로다


def test_idempotent_when_unchanged(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)

    first = h.build()
    second = h.build()
    assert second.deduplicated is True and second.version == first.version == 1


def test_selection_is_deterministic(uploads, monkeypatch):
    h = Harness()
    _seed_excellent(h)
    h.install_vlm(monkeypatch)

    a = h.build(force=True)
    b = h.build(force=True)
    assert a.items == b.items
    assert a.coverage == b.coverage
    assert a.completeness_tier == b.completeness_tier
    assert a.reference_analysis == b.reference_analysis


def test_ownership_isolation(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png())

    with pytest.raises(sets.PetReferenceSetError) as e:
        h.build(user="mallory@test")
    assert e.value.code == "PET_NOT_OWNED" and e.value.status == 403

    h.build()
    with pytest.raises(sets.PetReferenceSetError):
        _run(sets.list_sets(user_id="mallory@test", pet_id=PET))


def test_build_never_modifies_ledger_or_storage(uploads, monkeypatch):
    h = Harness()
    _seed_excellent(h)
    h.install_vlm(monkeypatch)
    before = _run(refs.list_references(user_id=USER, pet_id=PET))
    uploads_before = list(uploads)

    h.build()

    assert _run(refs.list_references(user_id=USER, pet_id=PET)) == before
    assert uploads == uploads_before


def test_provenance_every_item_traces_to_ledger(uploads, monkeypatch):
    h = Harness()
    _seed_excellent(h)
    h.install_vlm(monkeypatch)

    s = h.build()
    ledger_ids = {r.id for r in _run(refs.list_references(user_id=USER, pet_id=PET))}
    for item in s.items:
        assert item["reference_id"] in ledger_ids
        # 분석 스냅샷이 원본 객체 경로까지 이어 준다.
        assert s.reference_analysis[item["reference_id"]]["object_path"]
    for rid in s.source_reference_ids:
        assert rid in ledger_ids


def test_no_originals_is_409(uploads):
    _run(
        refs.record_derived(
            user_id=USER, content_id=CID,
            object_path=f"{USER}/{CID}/cutout_vitmatte.png",
            derived_kind="cutout_vitmatte",
        )
    )
    with pytest.raises(sets.PetReferenceSetError) as e:
        Harness().build()
    assert e.value.code == "NO_ORIGINAL_REFERENCES"


# ══════════════════════════════════════════════════════════════════════════
# 라우터 + 온보딩 보존
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def refs_client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(pet_references_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_build_list_get(refs_client, uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)
    monkeypatch.setattr(ids, "_default_fetch_bytes", h.fetch)

    res = refs_client.post(f"/api/v1/pet/references/{PET}/build-set", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == 1
    assert body["coverage"]["face"] in ("GOOD", "PARTIAL")
    assert body["items"] and body["items"][0]["selection_reason"]

    res = refs_client.get(f"/api/v1/pet/references/{PET}/sets", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["sets"][0]["item_count"] >= 1

    res = refs_client.get(f"/api/v1/pet/references/{PET}/sets/1", headers=AUTH)
    assert res.status_code == 200
    res = refs_client.get(f"/api/v1/pet/references/{PET}/sets/9", headers=AUTH)
    assert res.status_code == 404

    res = refs_client.get(
        f"/api/v1/pet/references/{PET}/sets",
        headers={"Authorization": "Bearer test:mallory@test"},
    )
    assert res.status_code == 403


@pytest.fixture
def assets_client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return ASGITestClient(app)


def test_original_intake_with_attached_cutout(assets_client, uploads):
    """멀티 레퍼런스 테스트 입력 — 기존 스토리지/대장/소유권을 그대로 쓴다."""
    res = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg"),
            "cutout_file": ("cutout.png", make_pet_cutout_png(), "image/png"),
        },
        data={"user_id": USER, "content_id": CID},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reference_recorded"] is True
    assert body["cutout_recorded"] is True

    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    derived = [r for r in ledger if r.role == refs.ROLE_DERIVED]
    assert len(derived) == 1
    assert derived[0].derived_kind == "cutout_reference"
    assert derived[0].parent_reference_id == body["reference_id"]


def test_original_intake_without_cutout_is_unchanged(assets_client, uploads):
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": USER, "content_id": CID},
    )
    assert res.status_code == 200
    assert "cutout_recorded" not in res.json()


def test_cutout_attach_failure_does_not_break_intake(assets_client, uploads, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("derived ledger down")

    monkeypatch.setattr(refs, "record_derived", boom)
    res = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg"),
            "cutout_file": ("cutout.png", make_pet_cutout_png(), "image/png"),
        },
        data={"user_id": USER, "content_id": CID},
    )
    assert res.status_code == 200
    assert res.json()["reference_recorded"] is True
    assert res.json()["cutout_recorded"] is False
