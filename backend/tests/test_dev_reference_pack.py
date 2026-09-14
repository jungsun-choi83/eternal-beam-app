"""
DEV 레퍼런스 팩 (scripts/dev_reference_pack) — 마스터 5장 준비 계약.

프로바이더 전부 가짜 — 실 결제 호출 없음. 지키려는 것:
  * 정본 1 + 키프레임 4역할 = 마스터 이미지 5장이 준비된다.
  * 그동안 모션 스펙 해석·영상 잡·영상 프로바이더 호출이 **0건**이다.
  * 유효한 기존 COMPLETE 는 재사용된다 (추가 프로바이더 호출 0).
  * 첫 QA PASS 중단(후보 정책)이 그대로다 — 마스터 5장 = 프로바이더 호출 5회.
  * 이후 모션 생성 런이 같은 COMPLETE 키프레임을 역할로 재사용할 수 있다.
"""

from __future__ import annotations

import os

import anyio
import pytest

from backend.scripts.dev_reference_pack import (
    REFERENCE_PACK_ROLES,
    prepare_reference_pack,
)
from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, video_motion_providers, vlm_identity

from .test_action_keyframes import VLM_KF_OK, install_kf_vlm
from .test_canonical_pet_builder import (
    GOOD,
    VLM_QA_OK,
    FakeProvider,
    _seed_three_ref_pet,
    install_vlm_qa,
)
from .test_pet_reference_sets import PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, sets, canon, kf):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf):
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


def _install_motion_bomb(monkeypatch):
    """모션/영상 경로가 단 한 번이라도 불리면 즉시 실패한다."""

    def bomb(*args, **kwargs):
        raise AssertionError("레퍼런스 팩이 모션/영상 경로를 건드렸다 — 계약 위반")

    monkeypatch.setattr(mv, "build_motion_video", bomb)
    monkeypatch.setattr(video_motion_providers, "get_provider", bomb)
    monkeypatch.setattr(video_motion_providers, "routing_for_class", bomb)


def _prepare(monkeypatch, storage):
    """레퍼런스 시딩 + 두 VLM 스텁 + 모션 폭탄. (harness, fetch) 반환."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    _install_motion_bomb(monkeypatch)

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)

    return h, fetch


def _pack(fetch, provider, **kw):
    return _run(
        prepare_reference_pack(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=fetch,
            providers=[provider],
            cutout_fn=lambda raw: raw,
            log=lambda _msg: None,
            **kw,
        )
    )


def test_five_masters_prepared_without_any_motion_generation(storage, monkeypatch):
    _, fetch = _prepare(monkeypatch, storage)
    provider = FakeProvider("runway", [GOOD()] * 20)

    report = _pack(fetch, provider)

    # 정본 1 + 역할 4 = 마스터 5장, 전부 COMPLETE.
    assert report["canonical"]["status"] == canon.STATUS_COMPLETE
    assert tuple(report["keyframes"].keys()) == REFERENCE_PACK_ROLES
    for role in REFERENCE_PACK_ROLES:
        s = report["keyframes"][role]
        assert s["status"] == kf.STATUS_COMPLETE, role
        # 역할당 승인 이미지 1장 + QA 판정 + 스토리지 경로가 보고된다.
        assert s["selected_candidate_id"], role
        assert s["selected"]["decision"] == "PASS", role
        assert f"/keyframes/{role.lower()}/v1/" in s["selected"]["raw_object_path"], role
    assert report["canonical"]["selected_candidate_id"]
    assert report["canonical"]["selected"]["raw_object_path"]

    # 첫 QA PASS 중단 정책 그대로 — 마스터 5장 = 프로바이더 호출 정확히 5회.
    assert provider.calls == 5

    # 모션 흔적 0: 폭탄이 안 터졌고(위), 보고도 계약을 명시한다.
    assert report["motion_generation"] == "untouched"


def test_second_run_reuses_all_five_without_new_provider_calls(storage, monkeypatch):
    _, fetch = _prepare(monkeypatch, storage)
    first = _pack(fetch, FakeProvider("runway", [GOOD()] * 20))

    fresh = FakeProvider("runway", [GOOD()] * 20)
    second = _pack(fetch, fresh)

    assert fresh.calls == 0, "유효한 COMPLETE 가 있으면 재생성하지 않는다"
    assert second["canonical"]["reused"] is True
    assert second["canonical"]["id"] == first["canonical"]["id"]
    for role in REFERENCE_PACK_ROLES:
        assert second["keyframes"][role]["reused"] is True, role
        assert second["keyframes"][role]["id"] == first["keyframes"][role]["id"], role


def test_review_masters_are_not_reused_pack_builds_fresh_version(storage, monkeypatch):
    """REVIEW 는 재사용 대상이 아니다 — 팩의 목적은 승인(COMPLETE) 마스터 5장.

    라이브 실측 회귀: VLM 없이 만들어진 REVIEW 정본을 서비스 dedup 게이트가
    재사용해 팩이 영원히 REVIEW 에 갇혔다. 팩은 최신이 COMPLETE 가 아니면
    새 버전 빌드를 강제해야 한다.
    """
    h = _seed_three_ref_pet(monkeypatch)
    _install_motion_bomb(monkeypatch)

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)

    # 1차: VLM 전무 → 정본 REVIEW 로 남는다 (키프레임 단계에서 정지, 예외).
    install_vlm_qa(monkeypatch, None)
    install_kf_vlm(monkeypatch, None)
    with pytest.raises(kf.ActionKeyframeError):
        _pack(fetch, FakeProvider("runway", [GOOD()] * 20))
    stuck = _run(canon.get_canonical(user_id=USER, pet_id=PET))
    assert stuck.status == canon.STATUS_REVIEW

    # 2차: VLM 복구 → REVIEW v1 을 재사용하지 않고 v2 를 새로 빌드해 COMPLETE.
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    report = _pack(fetch, FakeProvider("runway", [GOOD()] * 20))
    assert report["canonical"]["status"] == canon.STATUS_COMPLETE
    assert report["canonical"]["version"] == 2
    assert report["canonical"]["reused"] is False
    for role in REFERENCE_PACK_ROLES:
        assert report["keyframes"][role]["status"] == kf.STATUS_COMPLETE, role


def test_pack_keyframes_are_reusable_by_motion_runs_by_role(storage, monkeypatch):
    """이후 모션 생성 런의 재사용 계약: 역할 → 정확히 이 COMPLETE 키프레임."""
    from backend.services import motion_spec as ms

    _, fetch = _prepare(monkeypatch, storage)
    report = _pack(fetch, FakeProvider("runway", [GOOD()] * 20))

    # (a) 역할 조회가 팩이 만든 바로 그 버전을 돌려준다 — 런 KEYFRAMES 스테이지의
    #     재사용 경로(get_keyframe → COMPLETE 재사용)와 같은 조회다.
    for role in REFERENCE_PACK_ROLES:
        latest = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role=role))
        assert latest and latest.id == report["keyframes"][role]["id"], role
        assert latest.status == kf.STATUS_COMPLETE, role

    # (b) Phase 6 리졸버가 추가 생성 없이 시작(+목표) 키프레임을 해석한다 —
    #     NEUTRAL_IDLE 시작(BREATHING), STAND_READY 시작(COME_CLOSER),
    #     STAND_READY→LIE 전이(LIE_DOWN)까지 팩만으로 충분하다.
    breath = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="BREATHING"))
    assert breath["start_keyframe"]["keyframe_id"] == report["keyframes"]["NEUTRAL_IDLE"]["id"]
    closer = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="COME_CLOSER"))
    assert closer["start_keyframe"]["keyframe_id"] == report["keyframes"]["STAND_READY"]["id"]
    lie_down = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="LIE_DOWN"))
    assert lie_down["start_keyframe"]["keyframe_id"] == report["keyframes"]["STAND_READY"]["id"]
    assert lie_down["target_keyframe"]["keyframe_id"] == report["keyframes"]["LIE"]["id"]


def test_reference_images_are_recorded_idempotently(storage, monkeypatch):
    _, fetch = _prepare(monkeypatch, storage)
    img = ("dog1.jpg", b"\xff\xd8\xff fake-jpeg-bytes", "image/jpeg")

    first = _pack(fetch, FakeProvider("runway", [GOOD()] * 20), images=[img])
    assert len(first["references"]) == 1
    # 원본과 짝지은 누끼가 함께 남는다 — 없으면 정본 신원 QA 에 시그니처가 없다.
    assert first["references"][0]["cutout_object_path"].endswith(".png")
    # 같은 바이트 재등록 → 같은 행 (record_original 의 바이트 멱등).
    second = _pack(fetch, FakeProvider("runway", [GOOD()] * 20), images=[img])
    assert second["references"][0]["id"] == first["references"][0]["id"]


def test_script_module_never_imports_motion_or_video_paths():
    """소스 스캔 — 임포트 수준에서 모션/영상/커머스 경로가 없어야 한다."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "dev_reference_pack.py"
    )
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # 문서 문자열/주석은 계약을 "언급"할 수 있다 — 금지 대상은 실제 임포트다.
    src = "\n".join(
        l for l in lines if l.strip().startswith(("import ", "from ")) or " import " in l
    )
    for banned in (
        "motion_video_service",
        "video_motion_providers",
        "motion_spec",
        "generation_queue",
        "premium_purchase",
        "premium_generation",
        "pet_generation_run_service",
    ):
        assert banned not in src, f"dev_reference_pack 이 {banned} 를 참조한다"
