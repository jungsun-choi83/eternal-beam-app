"""
펫 형태 프로필 (Phase 3) 집중 테스트.

- accepted original + strict lineage cutout 에서 형태 프로필이 생성된다.
- 다중 레퍼런스 관측치를 융합한다(단일 이미지 의존 금지).
- 형태/신원 분리: profile 본문에 코트/색/무늬를 넣지 않는다.
- 입력 불변 시 멱등 반환.
"""

from __future__ import annotations

import json
import sys
import types

import anyio
import pytest

from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_registry, vlm_identity

from .conftest import make_jpeg_bytes
from .test_pet_identity_profile import make_pet_cutout_png


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    for svc in (refs, pet_registry, ids, morph):
        svc.__reset_for_tests()
    vlm_identity.clear_semantic_cache()
    yield
    for svc in (refs, pet_registry, ids, morph):
        svc.__reset_for_tests()
    vlm_identity.clear_semantic_cache()


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


def _seed_reference(*, idx: int, cutout: bytes):
    original = _run(
        refs.record_original(
            user_id="alice@test",
            content_id="cid1",
            data=make_jpeg_bytes(120 + idx, 90 + idx),
            mime_type="image/jpeg",
            diagnostics={
                "subject_detected": True,
                "subject_class": "dog",
                "detection_confidence": 0.86,
                "mask_area_fraction": 0.29,
                "rectangle_like_mask": False,
                "quality_score": 0.9,
            },
        )
    )
    cut = _run(
        refs.record_derived(
            user_id="alice@test",
            content_id="cid1",
            object_path=f"alice@test/cid1/references/cut_{idx}.png",
            derived_kind="cutout_reference",
            parent_reference_id=original.id,
            mime_type="image/png",
        )
    )
    return original, cut


def test_build_profile_fuses_multiple_references_and_stays_structural_only(monkeypatch, uploads):
    o1, c1 = _seed_reference(idx=1, cutout=make_pet_cutout_png())
    o2, c2 = _seed_reference(idx=2, cutout=make_pet_cutout_png(cropped=True))

    bytes_by_path = {
        c1.object_path: make_pet_cutout_png(),
        c2.object_path: make_pet_cutout_png(cropped=True),
        o1.object_path: make_jpeg_bytes(121, 91),
        o2.object_path: make_jpeg_bytes(122, 92),
    }

    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setattr(
        vlm_identity,
        "analyze_semantic_traits",
        lambda images: {
            "traits": {
                "species": "dog",
                "breed_estimate": "shiba",
                "breed_confidence": "low",
                "face": {"muzzle_color": "brown", "facial_markings": "unknown"},
                "eyes": {"color": "unknown", "surrounding_markings": "unknown"},
                "ears": {"shape": "erect", "color_markings": "brown"},
                "coat": {
                    "dominant_colors": ["brown"],
                    "secondary_colors": ["white"],
                    "length": "short",
                    "texture": "smooth",
                    "marking_distribution": "unknown",
                },
                "body": {"chest_markings": "unknown", "torso_markings": "unknown"},
                "paws": {"colors_markings": "unknown"},
                "tail": {"appearance": "curled", "tip_marking": "unknown"},
                "unique_features": [],
            },
            "model": "test-stub",
            "analyzer": vlm_identity.VLM_ANALYZER_VERSION,
            "image_count": 1,
        },
    )

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    p = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))

    assert p.version == 1
    assert p.status == morph.STATUS_COMPLETE
    assert set(p.source_reference_ids) == {o1.id, o2.id}
    assert p.profile["status"] == "fused"
    traits = p.profile["traits"]
    for key in (
        "body_size",
        "body_build",
        "torso_proportion",
        "leg_proportion",
        "head_proportion",
        "muzzle_proportion",
        "ear_form",
        "tail_form",
    ):
        assert key in traits

    # 형태/신원 분리: 코트/색/무늬 계열 키는 profile 루트에 없다.
    assert "coat" not in p.profile and "colors" not in p.profile and "markings" not in p.profile

    # 다중 레퍼런스 융합 근거 존재.
    torso = traits["torso_proportion"]
    assert torso["status"] == "fused"
    assert len(torso["support_reference_ids"]) >= 2

    # 선택 메타데이터로서의 breed 만 존재.
    assert p.profile["metadata"]["breed_estimate"]["status"] == "fused"


def test_single_reference_heuristic_traits_stay_low_confidence(monkeypatch, uploads):
    """
    한 장에서 한 번 추정한 휴리스틱 관측은 medium 이 될 수 없다 — Phase 6.7 의
    medium 하한이 그런 값을 형태 축으로 승격시키면 안 된다.
    """
    o1, c1 = _seed_reference(idx=1, cutout=make_pet_cutout_png())
    bytes_by_path = {
        c1.object_path: make_pet_cutout_png(),
        o1.object_path: make_jpeg_bytes(121, 91),
    }

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    p = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    traits = p.profile["traits"]

    for key in ("leg_proportion", "head_proportion", "muzzle_proportion"):
        t = traits[key]
        if t.get("status") == "fused":
            assert t["evidence"] == "heuristic"
            assert t["confidence"] == "low", (key, t)

    # 결정론 측정치(누끼 실루엣 기하)는 종전대로 단일 레퍼런스에서도 medium.
    torso = traits["torso_proportion"]
    if torso.get("status") == "fused":
        assert torso["evidence"] == "measured"
        assert torso["confidence"] in ("medium", "high")


def test_frame_occupancy_trait_is_not_advertised_as_body_size(monkeypatch, uploads):
    o1, c1 = _seed_reference(idx=1, cutout=make_pet_cutout_png())
    bytes_by_path = {
        c1.object_path: make_pet_cutout_png(),
        o1.object_path: make_jpeg_bytes(121, 91),
    }

    p = _run(
        morph.build_morphology_profile(
            user_id="alice@test",
            pet_id="pet_cid1",
            fetch_bytes=lambda ref: bytes_by_path.get(ref.object_path),
        )
    )
    body_size = p.profile["traits"]["body_size"]
    assert body_size["measures"] == "frame_occupancy"
    assert body_size["usable_as_body_size_class"] is False


def test_build_profile_is_idempotent_when_inputs_unchanged(monkeypatch, uploads):
    o1, c1 = _seed_reference(idx=1, cutout=make_pet_cutout_png())
    bytes_by_path = {
        c1.object_path: make_pet_cutout_png(),
        o1.object_path: make_jpeg_bytes(121, 91),
    }

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    p1 = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    p2 = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))

    assert p1.version == 1
    assert p2.version == 1
    assert p2.deduplicated is True


def test_cutout_attached_later_rebuilds_instead_of_reusing_a_stale_profile(monkeypatch, uploads):
    """
    형태 프로필은 **엄격 계보 누끼에서만** 구조를 측정한다. 그래서 누끼가
    나중에 붙는 것은 partial → complete 의 차이다 — 재사용으로 덮이면 그 원본은
    세그멘테이션이 생긴 뒤에도 영원히 측정되지 않는다.
    """
    original = _run(
        refs.record_original(
            user_id="alice@test",
            content_id="cid1",
            data=make_jpeg_bytes(121, 91),
            mime_type="image/jpeg",
            diagnostics={
                "subject_detected": True,
                "subject_class": "dog",
                "detection_confidence": 0.86,
                "mask_area_fraction": 0.29,
                "rectangle_like_mask": False,
                "quality_score": 0.9,
            },
        )
    )
    bytes_by_path = {original.object_path: make_jpeg_bytes(121, 91)}

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    p1 = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    assert p1.version == 1
    assert p1.status == morph.STATUS_PARTIAL
    assert p1.profile["status"] == morph.UNKNOWN
    assert morph.lineage_from_observations(p1.reference_observations) == {
        str(original.id): {"cutout_reference_id": None, "strict": False}
    }

    cut = _run(
        refs.record_derived(
            user_id="alice@test",
            content_id="cid1",
            object_path="alice@test/cid1/references/cut_late.png",
            derived_kind="cutout_reference",
            parent_reference_id=original.id,
            mime_type="image/png",
        )
    )
    bytes_by_path[cut.object_path] = make_pet_cutout_png()

    p2 = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    assert p2.deduplicated is False
    assert p2.version == 2
    assert sorted(p2.source_reference_ids) == sorted(p1.source_reference_ids)
    assert p2.status == morph.STATUS_COMPLETE
    assert p2.profile["primary_reference_id"] == str(original.id)
    assert morph.lineage_from_observations(p2.reference_observations) == {
        str(original.id): {"cutout_reference_id": str(cut.id), "strict": True}
    }

    # append-only: v1 은 그대로 남는다.
    old = _run(morph.get_profile(user_id="alice@test", pet_id="pet_cid1", version=1))
    assert old is not None and old.id == p1.id

    # 계보가 안정되면 다시 멱등이다.
    p3 = _run(morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch))
    assert p3.deduplicated is True and p3.version == 2


# ══════════════════════════════════════════════════════════════════════════
# VLM 과금 — 같은 이미지는 한 번만
# ══════════════════════════════════════════════════════════════════════════

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


def _stub_anthropic(monkeypatch) -> list[dict]:
    """가짜 anthropic 모듈을 심고, 실제로 나간 유료 호출을 기록해 돌려준다."""
    calls: list[dict] = []

    class _TextBlock:
        type = "text"
        text = json.dumps(_STUB_TRAITS)

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


def test_semantic_vlm_is_billed_once_per_image_across_both_profiles(monkeypatch, uploads):
    """
    신원 프로필과 형태 프로필은 **같은** 원본 레퍼런스를 각각 훑는다
    (pet_reference_set_service 가 둘을 연달아 빌드한다). 이미지 1장당 유료 VLM
    호출은 한 번이어야 한다 — 내용 주소 캐시가 두 번째를 흡수한다.
    """
    o1, c1 = _seed_reference(idx=1, cutout=make_pet_cutout_png())
    o2, c2 = _seed_reference(idx=2, cutout=make_pet_cutout_png(cropped=True))
    bytes_by_path = {
        c1.object_path: make_pet_cutout_png(),
        c2.object_path: make_pet_cutout_png(cropped=True),
        o1.object_path: make_jpeg_bytes(121, 91),
        o2.object_path: make_jpeg_bytes(122, 92),
    }

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    calls = _stub_anthropic(monkeypatch)

    identity = _run(
        ids.build_identity_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch)
    )
    assert len(calls) == 2, "원본 2장 → 첫 프로필에서 2회"

    morphology = _run(
        morph.build_morphology_profile(user_id="alice@test", pet_id="pet_cid1", fetch_bytes=fetch)
    )
    assert len(calls) == 2, "형태 프로필은 캐시 재사용 — 추가 과금이 없어야 한다"

    # 캐시가 값을 삼키지 않는다: 두 프로필 모두, 두 레퍼런스 모두 결과를 받았다.
    semantic = identity.visual_identity["semantic_traits"]
    assert semantic["status"] == "vlm"
    assert semantic["traits"]["species"] == "dog"
    assert set(semantic["source_reference_ids"]) == {o1.id, o2.id}
    assert identity.completeness["semantic"] == "present"
    assert morphology.profile["metadata"]["breed_estimate"]["status"] == "fused"


def test_semantic_cache_is_keyed_by_image_and_survives_caller_mutation(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    calls = _stub_anthropic(monkeypatch)

    first = vlm_identity.analyze_semantic_traits([(b"image-a", "image/jpeg")])
    assert first is not None and len(calls) == 1

    # 같은 바이트 → 캐시 히트
    assert vlm_identity.analyze_semantic_traits([(b"image-a", "image/jpeg")]) == first
    assert len(calls) == 1

    # 다른 바이트 → 새 호출
    vlm_identity.analyze_semantic_traits([(b"image-b", "image/jpeg")])
    assert len(calls) == 2

    # 호출자가 반환값을 변형해도 캐시 원본은 오염되지 않는다.
    first["traits"]["species"] = "cat"
    again = vlm_identity.analyze_semantic_traits([(b"image-a", "image/jpeg")])
    assert again["traits"]["species"] == "dog"
    assert len(calls) == 2

    # 모델이 바뀌면 키가 바뀐다 — stale 결과가 재사용되지 않는다.
    monkeypatch.setenv("PET_VLM_MODEL", "some-other-model")
    vlm_identity.analyze_semantic_traits([(b"image-a", "image/jpeg")])
    assert len(calls) == 3
