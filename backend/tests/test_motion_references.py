"""
모션 레퍼런스 라이브러리 + 매칭 (Phase 6.6) 테스트.

영상 생성 API 호출 없음 — 리졸버는 순수 조회다 (프로바이더를 독살해 증명한다).
"""

from __future__ import annotations

import dataclasses

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import motion_references_v1
from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import motion_reference_service as mrs
from backend.services import motion_spec as ms
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry

from .conftest import ASGITestClient
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_motion_video_generation import FakeVideoProvider, _build_motion, _prepare_pipeline
from .test_pet_reference_sets import PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    for m in (refs, pet_registry, ids, sets, canon, kf, mv, mrs):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf, mv, mrs):
        m.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _run(coro):
    return anyio.run(lambda: coro)


def reg(key, *, species="DOG", motion="RUN", size="UNKNOWN", legs="UNKNOWN",
        body="UNKNOWN", view="UNKNOWN", direction="UNKNOWN", speed="UNKNOWN",
        approve=True, commercial=True, pet_id=None, source_type=None,
        license_text="Adobe Stock Standard License #A123"):
    ref = _run(
        mrs.register_reference(
            reference_key=key, species=species, motion_id=motion,
            source_type=source_type or ("PET_OWN_MOTION" if pet_id else "LICENSED_STOCK"),
            license=license_text, provider_name="StockCo",
            body_size_class=size, leg_length_class=legs, body_length_class=body,
            camera_view=view, travel_direction=direction, speed_class=speed,
            object_path=f"motion-references/{species.lower()}/{key.lower()}_v1.mp4",
            pet_id=pet_id, commercial_use_allowed=commercial,
        )
    )
    if approve:
        ref = _run(mrs.set_status(reference_key=key, version=ref.version,
                                  quality_status="APPROVED", enabled=True))
    return ref


def prof(species="DOG", size="UNKNOWN", legs="UNKNOWN", body="UNKNOWN"):
    return {
        "profile_version": mrs.MORPHOLOGY_PROFILE_VERSION,
        "species": species, "body_size_class": size,
        "leg_length_class": legs, "body_length_class": body, "sources": {},
    }


def resolve(profile, motion="RUN", **kw):
    return _run(mrs.resolve_motion_reference(profile=profile, motion_id=motion, **kw))


# ══════════════════════════════════════════════════════════════════════════
# 등록 / 출처 / 버전
# ══════════════════════════════════════════════════════════════════════════


def test_dog_and_cat_reference_registration():
    d = reg("DOG_RUN_FRONT_SMALL_STANDARD", size="SMALL", legs="STANDARD",
            body="STANDARD", view="FRONT", approve=False)
    c = reg("CAT_WALK_SIDE_STANDARD", species="CAT", motion="WALK", view="SIDE", approve=False)
    assert d.version == 1 and d.quality_status == "DRAFT" and d.enabled is False
    assert c.species == "CAT" and c.motion_id == "WALK"
    # 모션 축은 정본 레지스트리 id 만 — 병행 명명 금지.
    with pytest.raises(mrs.MotionReferenceError) as e:
        reg("DOG_ZOOMIES", motion="ZOOMIES", approve=False)
    assert e.value.code == "UNKNOWN_MOTION"


def test_provenance_is_required():
    with pytest.raises(mrs.MotionReferenceError) as e:
        reg("DOG_RUN_X", license_text="", approve=False)
    assert e.value.code == "LICENSE_REQUIRED"
    with pytest.raises(mrs.MotionReferenceError) as e:
        _run(mrs.register_reference(
            reference_key="DOG_RUN_Y", species="DOG", motion_id="RUN",
            source_type="SCRAPED_TIKTOK", license="x", provider_name="p",
        ))
    assert e.value.code == "INVALID_SOURCE_TYPE"  # 무허가 소스는 등록 자체가 불가


def test_no_commercial_license_cannot_become_production():
    ref = reg("DOG_RUN_NC", commercial=False, approve=False)
    with pytest.raises(mrs.MotionReferenceError) as e:
        _run(mrs.set_status(reference_key="DOG_RUN_NC", version=ref.version,
                            quality_status="APPROVED"))
    assert e.value.code == "PROVENANCE_REQUIRED"
    with pytest.raises(mrs.MotionReferenceError):
        _run(mrs.set_status(reference_key="DOG_RUN_NC", version=ref.version, enabled=True))


def test_versioning_appends():
    v1 = reg("DOG_RUN_FRONT_SHORT_LEG", legs="SHORT", view="FRONT", approve=False)
    v2 = reg("DOG_RUN_FRONT_SHORT_LEG", legs="SHORT", view="FRONT", approve=False)
    assert (v1.version, v2.version) == (1, 2)
    listed = _run(mrs.list_references(species="DOG", motion_id="RUN"))
    assert [r.version for r in listed if r.reference_key == "DOG_RUN_FRONT_SHORT_LEG"] == [1, 2]


def test_disabled_or_unapproved_excluded_from_resolution():
    reg("DOG_RUN_DRAFT", approve=False)  # DRAFT + disabled
    assert resolve(prof()) is None


# ══════════════════════════════════════════════════════════════════════════
# 매칭 — 요구된 6가지 예시 포함
# ══════════════════════════════════════════════════════════════════════════


def test_example1_small_standard_dog_run_level1():
    reg("DOG_RUN_FRONT_SMALL_STANDARD", size="SMALL", legs="STANDARD",
        body="STANDARD", view="FRONT", direction="TOWARD_CAMERA")
    reg("DOG_RUN_GENERIC")
    r = resolve(prof(size="SMALL", legs="STANDARD", body="STANDARD"),
                desired_view="FRONT", direction="TOWARD_CAMERA")
    assert r["reference_key"] == "DOG_RUN_FRONT_SMALL_STANDARD"
    assert r["selection_level"] == mrs.LEVEL_1
    assert r["compatibility"] == {
        "species": "EXACT", "motion": "EXACT", "body_size": "EXACT",
        "leg_class": "EXACT", "body_class": "EXACT", "view": "EXACT",
        "direction": "EXACT", "speed": "UNSPECIFIED",
    }


def test_example2_short_leg_dog_beats_generic():
    reg("DOG_RUN_FRONT_SHORT_LEG", size="SMALL", legs="SHORT", body="LONG", view="FRONT")
    reg("DOG_RUN_GENERIC")
    r = resolve(prof(size="SMALL", legs="SHORT", body="LONG"), desired_view="FRONT")
    assert r["reference_key"] == "DOG_RUN_FRONT_SHORT_LEG"
    assert r["selection_level"] == mrs.LEVEL_1
    assert r["compatibility"]["leg_class"] == "EXACT"


def test_example3_large_dog_approach():
    reg("DOG_APPROACH_FRONT_LARGE_STANDARD", motion="COME_CLOSER", size="LARGE",
        legs="STANDARD", body="STANDARD", view="FRONT", direction="TOWARD_CAMERA")
    r = resolve(prof(size="LARGE", legs="STANDARD", body="STANDARD"),
                motion="COME_CLOSER", desired_view="FRONT", direction="TOWARD_CAMERA")
    assert r["reference_key"] == "DOG_APPROACH_FRONT_LARGE_STANDARD"
    assert r["selection_level"] == mrs.LEVEL_1
    assert r["provenance"]["source_type"] == "LICENSED_STOCK"


def test_example4_cat_walk():
    reg("CAT_WALK_SIDE_STANDARD", species="CAT", motion="WALK", size="MEDIUM",
        legs="STANDARD", body="STANDARD", view="SIDE")
    r = resolve(prof(species="CAT", size="MEDIUM", legs="STANDARD", body="STANDARD"),
                motion="WALK", desired_view="SIDE")
    assert r["reference_key"] == "CAT_WALK_SIDE_STANDARD"
    assert r["selection_level"] == mrs.LEVEL_1


def test_example5_missing_exact_morphology_falls_back():
    # 정확한 형태 레퍼런스가 없다: NEAR(인접) → LEVEL_2, generic → LEVEL_3.
    reg("DOG_RUN_FRONT_SMALL_STANDARD", size="SMALL", legs="STANDARD",
        body="STANDARD", view="FRONT")
    reg("DOG_RUN_GENERIC")

    near = resolve(prof(size="MEDIUM", legs="STANDARD", body="STANDARD"), desired_view="FRONT")
    assert near["reference_key"] == "DOG_RUN_FRONT_SMALL_STANDARD"
    assert near["selection_level"] == mrs.LEVEL_2
    assert near["compatibility"]["body_size"] == "NEAR"

    # SMALL↔LARGE 는 먼 불일치 — 그 레퍼런스는 배제되고 generic 만 남는다.
    far = resolve(prof(size="LARGE", legs="STANDARD", body="STANDARD"), desired_view="FRONT")
    assert far["reference_key"] == "DOG_RUN_GENERIC"
    assert far["selection_level"] == mrs.LEVEL_3
    assert far["compatibility"]["body_size"] == "UNVERIFIED"


def test_example6_cat_never_gets_dog_run():
    reg("DOG_RUN_GENERIC")  # DOG 레퍼런스만 존재
    assert resolve(prof(species="CAT")) is None  # 종 교차 금지 — LEVEL_4
    assert resolve(prof(species="UNKNOWN")) is None  # 종 미상도 해석하지 않는다


def test_view_direction_speed_matching_and_degradation():
    reg("DOG_RUN_SIDE_GEN", view="SIDE", direction="LEFT_TO_RIGHT", speed="FAST")
    reg("DOG_RUN_FRONT_GEN", view="FRONT", direction="TOWARD_CAMERA", speed="NORMAL")

    side = resolve(prof(), desired_view="SIDE", direction="LEFT_TO_RIGHT", speed="FAST")
    assert side["reference_key"] == "DOG_RUN_SIDE_GEN"

    # SIDE 만 있는데 FRONT 를 원함 → 명시적 저하 (조용한 동일시 없음).
    _run(mrs.set_status(reference_key="DOG_RUN_FRONT_GEN", version=1, enabled=False))
    degraded = resolve(prof(), desired_view="FRONT")
    assert degraded["reference_key"] == "DOG_RUN_SIDE_GEN"
    assert degraded["selection_level"] == mrs.LEVEL_3
    assert degraded["compatibility"]["view"] == "MISMATCH"


def test_deterministic_resolution():
    reg("DOG_RUN_FRONT_SMALL_STANDARD", size="SMALL", legs="STANDARD",
        body="STANDARD", view="FRONT")
    reg("DOG_RUN_GENERIC")
    p = prof(size="SMALL", legs="STANDARD", body="STANDARD")
    a = resolve(p, desired_view="FRONT", include_candidates=True)
    b = resolve(p, desired_view="FRONT", include_candidates=True)
    assert a == b
    assert [c["reference_key"] for c in a["candidates"]] == ["DOG_RUN_FRONT_SMALL_STANDARD", "DOG_RUN_GENERIC"]


def test_pet_own_motion_takes_priority():
    reg("DOG_RUN_FRONT_SMALL_STANDARD", size="SMALL", legs="STANDARD",
        body="STANDARD", view="FRONT")
    reg("BORI_OWN_RUN", pet_id=PET, source_type="PET_OWN_MOTION")

    own = resolve(prof(size="SMALL", legs="STANDARD", body="STANDARD"),
                  pet_id=PET, desired_view="FRONT")
    assert own["reference_key"] == "BORI_OWN_RUN"
    assert own["selection_level"] == mrs.LEVEL_OWN  # "LEVEL_0_OWN"

    other = resolve(prof(size="SMALL", legs="STANDARD", body="STANDARD"),
                    pet_id="pet_other", desired_view="FRONT")
    assert other["reference_key"] == "DOG_RUN_FRONT_SMALL_STANDARD"


def test_resolver_makes_no_video_provider_calls(monkeypatch):
    from backend.services import video_motion_providers as vp

    def boom(*a, **kw):
        raise AssertionError("리졸버가 비디오 프로바이더를 건드렸다")

    monkeypatch.setattr(vp, "routing_for_class", boom)
    monkeypatch.setattr(vp.SeedanceProvider, "generate", boom)
    monkeypatch.setattr(vp.KlingProvider, "generate", boom)
    reg("DOG_RUN_GENERIC")
    assert resolve(prof())["reference_key"] == "DOG_RUN_GENERIC"


# ══════════════════════════════════════════════════════════════════════════
# 형태 프로필 파생 (신원 속성 미사용)
# ══════════════════════════════════════════════════════════════════════════


def test_profile_derivation_is_structural_only(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    profile_obj = _run(ids.get_profile(user_id=USER, pet_id=PET))
    p = mrs.derive_motion_profile(profile_obj)

    assert p["species"] == "DOG"  # YOLO subject_class 에서 결정론적으로
    assert p["sources"]["species"] == "detector"
    assert p["body_length_class"] in ("COMPACT", "STANDARD", "LONG")
    # 잴 수 없는 것은 추측하지 않는다 — 사진에 절대 스케일/다리 판별이 없다.
    assert p["body_size_class"] == "UNKNOWN"
    assert p["leg_length_class"] == "UNKNOWN"
    # 시각 신원 속성은 프로필에 존재하지 않는다.
    assert not any(k in p for k in ("coat", "markings", "colors", "fur"))

    over = mrs.derive_motion_profile(profile_obj, overrides={"leg_length_class": "SHORT"})
    assert over["leg_length_class"] == "SHORT" and over["sources"]["leg_length"] == "override"


# ══════════════════════════════════════════════════════════════════════════
# Phase 5.1/6 통합
# ══════════════════════════════════════════════════════════════════════════


def test_contract_resolves_reference_with_asset(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    reg("DOG_APPROACH_FRONT_GENERIC", motion="COME_CLOSER", view="FRONT",
        direction="TOWARD_CAMERA")

    contract = _run(ms.resolve_video_generation_spec(
        user_id=USER, pet_id=PET, motion_id="COME_CLOSER"
    ))
    assert contract["contract_version"] == "phase6-contract-v2"
    assert contract["pet_motion_profile"]["species"] == "DOG"
    mr = contract["motion_reference"]
    assert mr["reference_key"] == "DOG_APPROACH_FRONT_GENERIC"
    assert mr["version"] == 1
    assert mr["asset"]["object_path"]
    assert mr["selection_level"] in (mrs.LEVEL_2, mrs.LEVEL_3)
    assert mr["quality"] == "APPROVED"
    # 해석됨 → 저하 경고 없음, 선호 전략 유지.
    assert contract["video_strategy"] == ms.STRATEGY_I2V_MOTION_REF
    assert not any("unavailable" in w for w in contract["warnings"])


def test_required_policy_fails_safely_without_compatible_reference(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setitem(
        ms.MOTIONS, "RUN",
        dataclasses.replace(ms.MOTIONS["RUN"], motion_reference_policy=ms.REF_REQUIRED),
    )
    with pytest.raises(ms.MotionSpecError) as e:
        _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="RUN"))
    assert e.value.code == "MOTION_REFERENCE_REQUIRED" and e.value.status == 409


def test_generation_records_reference_version_immutably(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    reg("DOG_APPROACH_FRONT_GENERIC", motion="COME_CLOSER", view="FRONT",
        direction="TOWARD_CAMERA")

    # Phase 6.7: 레퍼런스를 실제로 소비하는 프로바이더여야 I2V_MOTION_REF 가
    # 유지된다 — 소비 없는 프로바이더는 I2V 강등 (test_motion_reference_consumption).
    class RefCapable(FakeVideoProvider):
        supports_motion_reference = True

    v = _build_motion(
        h, "COME_CLOSER", [RefCapable("wan", [GOOD()])],
        sign_url_fn=lambda obj: f"https://signed.test/{obj.object_path}",
    )
    sel = next(c for c in v.candidates if c.selected)
    snap = sel.generation_metadata["motion_reference"]
    assert snap["id"] == "DOG_APPROACH_FRONT_GENERIC" and snap["version"] == 1
    assert v.video_strategy == ms.STRATEGY_I2V_MOTION_REF

    # 개선판 V2 가 나와도 이미 생성된 후보는 영원히 V1 을 기록한다.
    reg("DOG_APPROACH_FRONT_GENERIC", motion="COME_CLOSER", view="FRONT",
        direction="TOWARD_CAMERA")
    again = _run(mv.get_motion_version(user_id=USER, pet_id=PET, motion_id="COME_CLOSER"))
    sel2 = next(c for c in again.candidates if c.selected)
    assert sel2.generation_metadata["motion_reference"]["version"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 라우터
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(motion_references_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_register_status_list_resolve(client, storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)

    body = {
        "reference_key": "DOG_RUN_FRONT_SHORT_LEG", "species": "DOG", "motion_id": "RUN",
        "source_type": "LICENSED_STOCK", "license": "Stock #1", "provider_name": "StockCo",
        "commercial_use_allowed": True, "leg_length_class": "SHORT", "camera_view": "FRONT",
        "object_path": "motion-references/dog/short_leg/run/front/v1.mp4",
    }
    res = client.post("/api/v1/motion-references/", json=body, headers=AUTH)
    assert res.status_code == 200 and res.json()["quality_status"] == "DRAFT"

    res = client.post(
        "/api/v1/motion-references/DOG_RUN_FRONT_SHORT_LEG/1/status",
        json={"quality_status": "APPROVED", "enabled": True}, headers=AUTH,
    )
    assert res.status_code == 200 and res.json()["enabled"] is True

    res = client.get("/api/v1/motion-references/?species=DOG&motion_id=RUN", headers=AUTH)
    assert res.status_code == 200 and len(res.json()["references"]) == 1

    res = client.get(
        f"/api/v1/motion-references/resolve/{PET}/RUN?view=FRONT&legs=SHORT", headers=AUTH
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["pet_motion_profile"]["leg_length_class"] == "SHORT"
    assert payload["resolution"]["reference_key"] == "DOG_RUN_FRONT_SHORT_LEG"
    assert payload["resolution"]["compatibility"]["leg_class"] == "EXACT"
    assert payload["resolution"]["candidates"]
