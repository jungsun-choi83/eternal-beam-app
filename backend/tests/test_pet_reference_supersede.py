"""
사용자가 뺀/바꾼 사진의 퇴장 (stale-reference hotfix, Stage 1a) 계약 테스트.

- 동기화: 현재 사진 해시 목록에 없는 accepted 원본은 누끼와 함께 거절된다
- 거절된 원본은 신원/형태 프로필과 레퍼런스 세트에 기여하지 않는다
- 거절된 원본은 MAX_ORIGINALS_PER_PET 자리를 차지하지 않는다 (교체가 409 가 아니다)
- 같은 바이트를 다시 올리면 원본과 누끼가 되살아나고 엄격한 짝짓기가 통한다
- 목록에 있는 해시의 원본은 절대 거절되지 않는다 (이번 패스의 업로드 실패 포함)
- 핀으로 잡힌 과거 id 는 계속 조회된다 (행을 지우지 않는다)
"""

from __future__ import annotations

import hashlib

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.routers import pet_references_v1 as references_router
from backend.services import pet_identity_service as ids
from backend.services import pet_generation_run_service as runs
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry

from .conftest import ASGITestClient, make_jpeg_bytes, make_rgba_png_bytes
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import CID, PET, USER, Harness

STABLE_PET = "pet_stable"
PHOTOS = [make_jpeg_bytes(128, 96), make_jpeg_bytes(64, 64), make_jpeg_bytes(96, 72)]
CUTOUTS = [make_rgba_png_bytes(0.3), make_rgba_png_bytes(0.5), make_rgba_png_bytes(0.7)]


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    for svc in (refs, pet_registry, ids, morph, sets, runs):
        svc.__reset_for_tests()
    yield
    for svc in (refs, pet_registry, ids, morph, sets, runs):
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


@pytest.fixture
def client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    app.include_router(references_router.router, prefix="/api")
    return ASGITestClient(app)


def _run(coro):
    return anyio.run(lambda: coro)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _auth(user="alice@test"):
    return {"Authorization": f"Bearer test:{user}"}


def _put(client, photo: bytes, cutout: bytes | None = None):
    files = {"file": ("dog.jpg", photo, "image/jpeg")}
    if cutout is not None:
        files["cutout_file"] = ("cutout.png", cutout, "image/png")
    return client.post(
        "/api/assets/original",
        files=files,
        data={"user_id": "alice@test", "content_id": "stable", "phase1_intake": "true"},
        headers=_auth(),
    )


def _sync(client, photos: list[bytes], *, pet=STABLE_PET, user="alice@test"):
    return client.post(
        f"/api/v1/pet/references/{pet}/sync",
        json={"content_hashes": [_sha(p) for p in photos]},
        headers=_auth(user),
    )


def _ledger(pet=STABLE_PET, user="alice@test"):
    return _run(refs.list_references(user_id=user, pet_id=pet))


def _upload_three(client) -> list[dict]:
    out = []
    for photo, cut in zip(PHOTOS, CUTOUTS):
        res = _put(client, photo, cut)
        assert res.status_code == 200, res.text
        out.append(res.json())
    return out


# --------------------------------------------------------------------------
# 동기화 엔드포인트
# --------------------------------------------------------------------------


def test_sync_rejects_the_removed_original_and_its_cutout(client):
    a, b, c = _upload_three(client)

    res = _sync(client, PHOTOS[:2])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rejected_reference_ids"] == [c["reference_id"]]
    assert {x["reference_id"] for x in body["active"]} == {a["reference_id"], b["reference_id"]}
    assert {x["content_hash"] for x in body["active"]} == {_sha(p) for p in PHOTOS[:2]}
    assert body["missing_hashes"] == []

    by_id = {r.id: r for r in _ledger()}
    for rid in (c["reference_id"], c["cutout_reference_id"]):
        assert by_id[rid].acceptance_state == refs.STATE_REJECTED
        assert by_id[rid].rejection_code == refs.REJECTION_SUPERSEDED_BY_USER
    for kept in (a, b):
        assert by_id[kept["reference_id"]].acceptance_state == refs.STATE_ACCEPTED
        assert by_id[kept["cutout_reference_id"]].acceptance_state == refs.STATE_ACCEPTED

    ledger = _ledger()
    # 행은 지워지지 않는다 — 핀으로 잡힌 과거 id 는 계속 조회된다.
    assert len(ledger) == 6
    assert {r.id for r in refs.active_originals(ledger)} == {a["reference_id"], b["reference_id"]}
    assert refs.strict_cutout_for_original(ledger, c["reference_id"]) is None
    assert refs.pair_cutouts(ledger)[c["reference_id"]] is None
    assert c["reference_id"] not in refs.strict_lineage_map(ledger)


def test_sync_is_a_noop_when_the_hash_list_matches_the_active_set(client):
    _upload_three(client)
    before = _ledger()

    res = _sync(client, PHOTOS)
    assert res.status_code == 200
    assert res.json()["rejected_reference_ids"] == []
    assert len(res.json()["active"]) == 3
    assert _ledger() == before

    # 반복해도 같다 (멱등).
    _sync(client, PHOTOS[:2])
    once = _ledger()
    again = _sync(client, PHOTOS[:2])
    assert again.json()["rejected_reference_ids"] == []
    assert _ledger() == once


def test_sync_never_rejects_a_listed_hash_even_if_this_pass_failed_to_upload_it(client):
    """
    이전 패스에서 accepted 된 사진이 이번 패스에서는 업로드에 실패했다. UI 에는
    여전히 있으므로 해시는 목록에 들어 있고, 그 행은 살아남아야 한다.
    """
    a, b, c = _upload_three(client)
    never_uploaded = make_jpeg_bytes(80, 80)

    # C 는 이번 패스에 다시 올라오지 않았지만 목록에는 있다. B 는 UI 에서 빠졌다.
    res = _sync(client, [PHOTOS[0], PHOTOS[2], never_uploaded])
    assert res.status_code == 200
    body = res.json()
    assert body["rejected_reference_ids"] == [b["reference_id"]]
    assert {x["reference_id"] for x in body["active"]} == {a["reference_id"], c["reference_id"]}
    # 대장에 아직 없는 사진은 정직하게 missing 으로 보고된다.
    assert body["missing_hashes"] == [_sha(never_uploaded)]


def test_sync_requires_auth_ownership_and_valid_hashes(client):
    _upload_three(client)

    unauthenticated = client.post(
        f"/api/v1/pet/references/{STABLE_PET}/sync",
        json={"content_hashes": [_sha(PHOTOS[0])]},
    )
    assert unauthenticated.status_code == 401

    stranger = _sync(client, PHOTOS[:1], user="mallory@test")
    assert stranger.status_code == 403
    assert stranger.json()["detail"]["code"] == "PET_NOT_OWNED"

    # 빈 목록은 "전부 퇴장"이 아니라 거절이다.
    empty = client.post(
        f"/api/v1/pet/references/{STABLE_PET}/sync",
        json={"content_hashes": []},
        headers=_auth(),
    )
    assert empty.status_code == 422

    bad = client.post(
        f"/api/v1/pet/references/{STABLE_PET}/sync",
        json={"content_hashes": ["not-a-hash"]},
        headers=_auth(),
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "PET_REFERENCE_SYNC_INVALID_HASH"

    assert len(refs.active_originals(_ledger())) == 3


def test_sync_hash_comparison_is_case_insensitive(client):
    _upload_three(client)
    res = client.post(
        f"/api/v1/pet/references/{STABLE_PET}/sync",
        json={"content_hashes": [_sha(p).upper() for p in PHOTOS]},
        headers=_auth(),
    )
    assert res.status_code == 200
    assert res.json()["rejected_reference_ids"] == []


# --------------------------------------------------------------------------
# 자리 상한 / 교체 / 재등록
# --------------------------------------------------------------------------


def test_replacing_a_photo_after_sync_does_not_hit_the_original_limit(client):
    _upload_three(client)
    replacement = make_jpeg_bytes(80, 80)

    # 동기화 전에는 물러난 사진이 없으므로 4번째는 여전히 409 다.
    blocked = _put(client, replacement, make_rgba_png_bytes(0.4))
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PHASE1_ORIGINAL_LIMIT"

    assert _sync(client, PHOTOS[:2]).status_code == 200

    added = _put(client, replacement, make_rgba_png_bytes(0.4))
    assert added.status_code == 200, added.text
    assert added.json()["intake_ready"] is True
    assert added.json()["deduplicated"] is False

    ledger = _ledger()
    assert len(refs.active_originals(ledger)) == 3
    assert len([r for r in ledger if r.role == refs.ROLE_ORIGINAL]) == 4
    # 버전은 거절된 행까지 포함해 유일하게 증가한다 (version 유니크 인덱스).
    assert sorted(r.version for r in ledger if r.role == refs.ROLE_ORIGINAL) == [1, 2, 3, 4]


def test_readding_the_same_bytes_reactivates_original_and_cutout(client, uploads):
    a, b, c = _upload_three(client)
    assert _sync(client, PHOTOS[:2]).status_code == 200
    uploaded_before = len(uploads)

    res = _put(client, PHOTOS[2], CUTOUTS[2])
    assert res.status_code == 200, res.text
    body = res.json()
    # 새 행이 아니라 같은 행이 되살아난다.
    assert body["reference_id"] == c["reference_id"]
    assert body["cutout_reference_id"] == c["cutout_reference_id"]
    assert body["reactivated"] is True
    assert body["intake_ready"] is True
    # 바이트는 이미 저장돼 있다 — 다시 올리지 않는다.
    assert len(uploads) == uploaded_before

    ledger = _ledger()
    assert len(ledger) == 6
    by_id = {r.id: r for r in ledger}
    for rid in (c["reference_id"], c["cutout_reference_id"]):
        assert by_id[rid].acceptance_state == refs.STATE_ACCEPTED
        assert by_id[rid].rejection_code is None
    assert (
        refs.strict_cutout_for_original(ledger, c["reference_id"]).id == c["cutout_reference_id"]
    )

    # 이미 살아 있는 사진의 재시도는 reactivated 가 아니다.
    retry = _put(client, PHOTOS[2], CUTOUTS[2]).json()
    assert retry["deduplicated"] is True and retry["reactivated"] is False


def test_readding_a_rejected_photo_respects_the_active_limit(client):
    """뺐던 사진은 자리가 남아 있을 때만 되살아난다 — 살아 있는 3장을 넘지 못한다."""
    _upload_three(client)
    _sync(client, PHOTOS[:2])
    assert _put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)).status_code == 200

    res = _put(client, PHOTOS[2], CUTOUTS[2])
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PHASE1_ORIGINAL_LIMIT"
    rejected = next(r for r in _ledger() if r.content_hash == _sha(PHOTOS[2]))
    assert rejected.acceptance_state == refs.STATE_REJECTED


def test_cutout_attached_after_reactivation_is_revived_with_its_original(client):
    """원본만 먼저 되살아난 뒤(누끼 없이 재업로드) 누끼가 뒤따라와도 짝이 맞는다."""
    _, _, c = _upload_three(client)
    _sync(client, PHOTOS[:2])

    first = _put(client, PHOTOS[2]).json()
    assert first["reactivated"] is True
    # 연결된 누끼는 원본과 함께 되살아난다.
    assert first["intake_ready"] is True
    assert first["cutout_reference_id"] == c["cutout_reference_id"]


def test_record_derived_revives_a_superseded_cutout_whose_original_is_active(uploads):
    """부분 실패(원본만 accepted, 누끼는 물러난 채)를 다음 누끼 기록이 푼다."""
    original = _run(
        refs.record_original(user_id=USER, content_id=CID, data=PHOTOS[0], mime_type="image/jpeg")
    )
    path = f"{USER}/{CID}/references/cutout_{original.content_hash[:16]}.png"
    cut = _run(
        refs.record_derived(
            user_id=USER, content_id=CID, object_path=path,
            derived_kind="cutout_reference", parent_reference_id=original.id,
        )
    )
    row = next(r for r in refs._MOCK_REFS if r["id"] == cut.id)
    row.update(acceptance_state=refs.STATE_REJECTED, rejection_code=refs.REJECTION_SUPERSEDED_BY_USER)

    again = _run(
        refs.record_derived(
            user_id=USER, content_id=CID, object_path=path,
            derived_kind="cutout_reference", parent_reference_id=original.id,
        )
    )
    assert again.id == cut.id
    assert again.reactivated is True and again.acceptance_state == refs.STATE_ACCEPTED
    assert refs.strict_cutout_for_original(_ledger(PET, USER), original.id).id == cut.id


# --------------------------------------------------------------------------
# reject_original (서비스)
# --------------------------------------------------------------------------


def test_reject_original_cascades_and_is_idempotent(uploads):
    h = Harness()
    keep = h.seed(cutout=make_pet_cutout_png())
    drop = h.seed(cutout=make_pet_cutout_png())

    first = _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=drop.id))
    second = _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=drop.id))
    assert first == second

    by_id = {r.id: r for r in second}
    assert by_id[drop.id].acceptance_state == refs.STATE_REJECTED
    assert by_id[drop.id].rejection_code == refs.REJECTION_SUPERSEDED_BY_USER
    dropped_cutouts = [r for r in second if r.parent_reference_id == drop.id]
    assert len(dropped_cutouts) == 1
    assert dropped_cutouts[0].acceptance_state == refs.STATE_REJECTED
    assert by_id[keep.id].acceptance_state == refs.STATE_ACCEPTED
    assert refs.strict_cutout_for_original(second, keep.id) is not None


def test_reject_original_is_ownership_isolated_and_only_targets_originals(uploads):
    h = Harness()
    original = h.seed(cutout=make_pet_cutout_png())
    cutout = next(r for r in _ledger(PET, USER) if r.role == refs.ROLE_DERIVED)

    with pytest.raises(refs.PetReferenceError) as denied:
        _run(refs.reject_original(user_id="mallory@test", pet_id=PET, reference_id=original.id))
    assert denied.value.code == "PET_NOT_OWNED"

    with pytest.raises(refs.PetReferenceError) as not_original:
        _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=cutout.id))
    assert not_original.value.code == "PET_REFERENCE_NOT_FOUND"
    assert all(r.acceptance_state == refs.STATE_ACCEPTED for r in _ledger(PET, USER))


def test_a_non_user_rejection_is_not_reactivated_by_reupload(uploads):
    """SUPERSEDED_BY_USER 가 아닌 거절은 같은 바이트의 재업로드로 풀리지 않는다."""
    first = _run(
        refs.record_original(
            user_id=USER, content_id=CID, data=PHOTOS[0], mime_type="image/jpeg",
            acceptance_state=refs.STATE_REJECTED, rejection_code="NOT_A_PET",
        )
    )
    again = _run(
        refs.record_original(user_id=USER, content_id=CID, data=PHOTOS[0], mime_type="image/jpeg")
    )
    assert again.id == first.id
    assert again.reactivated is False
    assert again.acceptance_state == refs.STATE_REJECTED
    assert again.rejection_code == "NOT_A_PET"


# --------------------------------------------------------------------------
# 소비자: 신원 / 형태 / 레퍼런스 세트
# --------------------------------------------------------------------------


def test_rejected_original_is_excluded_from_identity_morphology_and_reference_set(uploads):
    h = Harness()
    kept = [h.seed(cutout=make_pet_cutout_png()) for _ in range(2)]
    removed = h.seed(cutout=make_pet_cutout_png())

    before = h.build()
    assert removed.id in before.source_reference_ids

    result = _run(
        refs.sync_active_originals(
            user_id=USER, pet_id=PET, content_hashes=[r.content_hash for r in kept]
        )
    )
    assert result.rejected_original_ids == [removed.id]

    kept_ids = sorted(r.id for r in kept)
    identity = _run(ids.build_identity_profile(user_id=USER, pet_id=PET, fetch_bytes=h.fetch))
    morphology = _run(morph.build_morphology_profile(user_id=USER, pet_id=PET, fetch_bytes=h.fetch))
    after = h.build()

    # 사진 집합이 바뀌었으므로 재사용이 아니라 새 버전이다.
    assert identity.deduplicated is False and identity.version == 2
    assert morphology.deduplicated is False and morphology.version == 2
    assert after.deduplicated is False and after.version == before.version + 1

    assert sorted(identity.source_reference_ids) == kept_ids
    assert sorted(morphology.source_reference_ids) == kept_ids
    assert sorted(after.source_reference_ids) == kept_ids
    assert removed.id not in after.reference_analysis
    assert all(item["reference_id"] != removed.id for item in after.items)
    assert removed.id not in morphology.reference_observations

    # 과거 세트는 그대로다 — 핀으로 잡힌 계보는 다시 쓰이지 않는다.
    old = _run(sets.get_set(user_id=USER, pet_id=PET, version=before.version))
    assert removed.id in old.source_reference_ids


def test_rejecting_every_original_leaves_builders_with_no_evidence(uploads):
    h = Harness()
    only = h.seed(cutout=make_pet_cutout_png())
    _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=only.id))

    ledger = _ledger(PET, USER)
    assert refs.intake_readiness(ledger) == (False, None, None)
    with pytest.raises(sets.PetReferenceSetError) as error:
        h.build()
    assert error.value.code == "NO_ORIGINAL_REFERENCES"


# --------------------------------------------------------------------------
# pair_cutouts / 유니크 인덱스 흉내
# --------------------------------------------------------------------------


def _ref(rid, role, **kw):
    return refs.PetReference(
        id=rid, pet_id="pet_c", content_id="c", user_id="u", role=role,
        source=refs.SOURCE_APP, bucket="b", object_path=f"{rid}.png", version=1, **kw,
    )


def test_pair_cutouts_ignores_rejected_cutouts():
    original = _ref("o1", refs.ROLE_ORIGINAL)
    rejected = _ref(
        "d1", refs.ROLE_DERIVED, derived_kind="cutout_reference", parent_reference_id="o1",
        acceptance_state=refs.STATE_REJECTED, rejection_code=refs.REJECTION_SUPERSEDED_BY_USER,
    )
    assert refs.pair_cutouts([original, rejected]) == {"o1": None}
    assert refs.active_cutouts([original, rejected]) == []

    # 거절된 누끼가 앞에 있어도 accepted 누끼가 짝이 된다.
    accepted = _ref(
        "d2", refs.ROLE_DERIVED, derived_kind="cutout_reference", parent_reference_id="o1"
    )
    assert refs.pair_cutouts([original, rejected, accepted])["o1"].id == "d2"

    # 거절된 부모 없는(레거시) 누끼도 폴백 후보가 아니다.
    legacy_rejected = _ref(
        "d3", refs.ROLE_DERIVED, derived_kind="cutout_client",
        acceptance_state=refs.STATE_REJECTED,
    )
    assert refs.pair_cutouts([original, legacy_rejected]) == {"o1": None}


def test_legacy_parentless_cutout_never_flows_to_a_replacement_original():
    """
    단일 사진 펫의 부모 없는 누끼는 그 원본이 물러나도 새 원본에 붙지 않는다 —
    "원본이 하나인가"는 거절된 원본까지 센다.
    """
    old = _ref(
        "o1", refs.ROLE_ORIGINAL,
        acceptance_state=refs.STATE_REJECTED, rejection_code=refs.REJECTION_SUPERSEDED_BY_USER,
    )
    new = _ref("o2", refs.ROLE_ORIGINAL)
    legacy = _ref("d1", refs.ROLE_DERIVED, derived_kind="cutout_client")
    assert refs.pair_cutouts([old, new, legacy])["o2"] is None
    assert refs.intake_readiness([old, new, legacy]) == (False, new, None)


def test_mock_insert_keeps_the_real_unique_index_rules_for_rejected_rows(uploads):
    """
    유니크 인덱스(pet_id, content_hash / pet_id, object_path / pet_id, role, version)는
    acceptance_state 를 보지 않는다. 인메모리 대장도 거절된 행에 대해 같은 규칙을
    지켜야 한다 — 그래서 재등록은 새 행이 아니라 재활성화다.
    """
    original = _run(
        refs.record_original(user_id=USER, content_id=CID, data=PHOTOS[0], mime_type="image/jpeg")
    )
    path = f"{USER}/{CID}/references/cutout_{original.content_hash[:16]}.png"
    cut = _run(
        refs.record_derived(
            user_id=USER, content_id=CID, object_path=path,
            derived_kind="cutout_reference", parent_reference_id=original.id,
        )
    )
    _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=original.id))

    base = next(r for r in refs._MOCK_REFS if r["id"] == original.id)
    dup_hash = {**base, "id": "new-original", "version": 99, "acceptance_state": refs.STATE_ACCEPTED}
    ok, _ = _run(refs._insert_row(dup_hash))
    assert ok is False

    cut_row = next(r for r in refs._MOCK_REFS if r["id"] == cut.id)
    dup_path = {**cut_row, "id": "new-cutout", "version": 99, "acceptance_state": refs.STATE_ACCEPTED}
    ok, _ = _run(refs._insert_row(dup_path))
    assert ok is False

    dup_version = {**base, "id": "other", "content_hash": "f" * 64, "object_path": "x.jpg"}
    ok, _ = _run(refs._insert_row(dup_version))
    assert ok is False

    assert len(refs._MOCK_REFS) == 2


# --------------------------------------------------------------------------
# 재등록 시 누끼 바이트가 달라진 경우 (Step 0)
# --------------------------------------------------------------------------


def _linked_cutouts(original_id: str):
    return [r for r in _ledger() if r.parent_reference_id == original_id]


def test_readding_a_removed_photo_with_different_cutout_bytes_replaces_the_cutout(
    client, uploads
):
    _, _, c = _upload_three(client)
    _sync(client, PHOTOS[:2])
    new_cutout = make_rgba_png_bytes(0.9)
    uploaded_before = list(uploads)

    res = _put(client, PHOTOS[2], new_cutout)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reference_id"] == c["reference_id"]
    assert body["reactivated"] is True
    assert body["intake_ready"] is True
    # 새 누끼는 새 행·새 객체다 — 예전 객체를 덮어쓰지 않는다 (과거 계보 보존).
    assert body["cutout_reference_id"] != c["cutout_reference_id"]
    assert body["cutout_object_path"] != c["cutout_object_path"]
    assert uploads[len(uploaded_before):] == [body["cutout_object_path"]]

    linked = {r.id: r for r in _linked_cutouts(c["reference_id"])}
    assert len(linked) == 2
    old, new = linked[c["cutout_reference_id"]], linked[body["cutout_reference_id"]]
    assert old.acceptance_state == refs.STATE_REJECTED
    assert old.rejection_code == refs.REJECTION_CUTOUT_REPLACED
    assert new.acceptance_state == refs.STATE_ACCEPTED
    assert new.diagnostics["content_hash"] == _sha(new_cutout)

    ledger = _ledger()
    assert refs.strict_cutout_for_original(ledger, c["reference_id"]).id == new.id
    assert refs.pair_cutouts(ledger)[c["reference_id"]].id == new.id

    # 같은 요청의 재시도는 멱등이다.
    retry = _put(client, PHOTOS[2], new_cutout)
    assert retry.status_code == 200
    assert retry.json()["cutout_reference_id"] == new.id
    assert len(_linked_cutouts(c["reference_id"])) == 2


def test_cutout_replacement_survives_a_failed_upload_and_retry(client, monkeypatch):
    """예전 누끼를 먼저 물리므로, 새 누끼 업로드가 실패해도 재시도가 409 로 막히지 않는다."""
    from backend.services import supabase_assets

    _, _, c = _upload_three(client)
    _sync(client, PHOTOS[:2])
    new_cutout = make_rgba_png_bytes(0.9)

    good_upload = supabase_assets.upload_asset_to_storage

    async def boom(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", boom)
    failed = _put(client, PHOTOS[2], new_cutout)
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "PHASE1_CUTOUT_PERSIST_FAILED"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", good_upload)
    retry = _put(client, PHOTOS[2], new_cutout)
    assert retry.status_code == 200, retry.text
    assert retry.json()["intake_ready"] is True
    assert retry.json()["cutout_reference_id"] != c["cutout_reference_id"]
    active = [
        r for r in _linked_cutouts(c["reference_id"]) if r.acceptance_state == refs.STATE_ACCEPTED
    ]
    assert len(active) == 1


def test_replaced_cutout_bytes_coming_back_revive_their_own_row(client, uploads):
    """X → (뺌) → Y → (뺌) → X: 항상 누끼 하나만 살아 있고, X 는 자기 행으로 돌아온다."""
    _, _, c = _upload_three(client)
    cut_x, cut_y = CUTOUTS[2], make_rgba_png_bytes(0.9)

    _sync(client, PHOTOS[:2])
    y = _put(client, PHOTOS[2], cut_y).json()
    _sync(client, PHOTOS[:2])
    uploaded_before = len(uploads)
    x = _put(client, PHOTOS[2], cut_x)
    assert x.status_code == 200, x.text
    assert x.json()["cutout_reference_id"] == c["cutout_reference_id"]
    assert x.json()["intake_ready"] is True
    assert len(uploads) == uploaded_before  # 두 객체 모두 이미 저장돼 있다

    linked = {r.id: r for r in _linked_cutouts(c["reference_id"])}
    assert len(linked) == 2
    assert linked[c["cutout_reference_id"]].acceptance_state == refs.STATE_ACCEPTED
    assert linked[y["cutout_reference_id"]].rejection_code == refs.REJECTION_CUTOUT_REPLACED

    # 교체로 물러난 누끼는 원본이 되살아날 때 따라 살아나지 않는다.
    _sync(client, PHOTOS[:2])
    again = _put(client, PHOTOS[2]).json()
    assert again["cutout_reference_id"] == c["cutout_reference_id"]
    active = [
        r for r in _linked_cutouts(c["reference_id"]) if r.acceptance_state == refs.STATE_ACCEPTED
    ]
    assert [r.id for r in active] == [c["cutout_reference_id"]]


def test_before_generation_a_new_cutout_supersedes_the_live_one(client, uploads):
    """생성 전에는 빠진 적 없는 원본의 누끼도 자유롭게 바꿀 수 있다."""
    a, _, _ = _upload_three(client)
    new_cutout = make_rgba_png_bytes(0.9)
    uploaded_before = list(uploads)

    res = _put(client, PHOTOS[0], new_cutout)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reference_id"] == a["reference_id"]
    assert body["reactivated"] is False
    assert body["intake_ready"] is True
    assert body["cutout_reference_id"] != a["cutout_reference_id"]
    # 예전 객체는 덮어쓰이지 않는다 — 새 누끼는 새 경로다.
    assert body["cutout_object_path"] != a["cutout_object_path"]
    assert uploads[len(uploaded_before):] == [body["cutout_object_path"]]

    linked = {r.id: r for r in _linked_cutouts(a["reference_id"])}
    assert len(linked) == 2
    assert linked[a["cutout_reference_id"]].acceptance_state == refs.STATE_REJECTED
    assert linked[a["cutout_reference_id"]].rejection_code == refs.REJECTION_CUTOUT_REPLACED
    assert linked[body["cutout_reference_id"]].acceptance_state == refs.STATE_ACCEPTED
    assert refs.pair_cutouts(_ledger())[a["reference_id"]].id == body["cutout_reference_id"]

    # 다시 예전 바이트로 돌아가면 예전 행이 되살아나고, 살아 있는 누끼는 여전히 하나다.
    back = _put(client, PHOTOS[0], CUTOUTS[0])
    assert back.status_code == 200, back.text
    assert back.json()["cutout_reference_id"] == a["cutout_reference_id"]
    active = [
        r for r in _linked_cutouts(a["reference_id"]) if r.acceptance_state == refs.STATE_ACCEPTED
    ]
    assert [r.id for r in active] == [a["cutout_reference_id"]]
    assert len(_linked_cutouts(a["reference_id"])) == 2


def _seed_run(status: str, pet_id: str = STABLE_PET) -> None:
    runs._MOCK_RUNS.append(
        {"id": f"run-{len(runs._MOCK_RUNS)}", "pet_id": pet_id, "user_id": "alice@test", "status": status}
    )


@pytest.mark.parametrize("status", [runs.STATUS_FAILED, runs.STATUS_CANCELLED])
def test_a_failed_or_cancelled_run_does_not_lock_cutout_replacement(client, status):
    a, _, _ = _upload_three(client)
    _seed_run(status)
    res = _put(client, PHOTOS[0], make_rgba_png_bytes(0.9))
    assert res.status_code == 200, res.text
    assert res.json()["cutout_reference_id"] != a["cutout_reference_id"]


def test_a_run_for_another_pet_does_not_lock_this_one(client):
    _upload_three(client)
    _seed_run(runs.STATUS_RUNNING, pet_id="pet_someone_else")
    assert _put(client, PHOTOS[0], make_rgba_png_bytes(0.9)).status_code == 200


def test_unknown_lock_state_does_not_allow_replacement(client, monkeypatch):
    a, _, _ = _upload_three(client)

    async def boom(pet_id):
        raise runs.PetGenerationRunError("GENERATION_RUNS_UNAVAILABLE", "down", status=503)

    monkeypatch.setattr(runs, "pet_has_locking_run", boom)
    res = _put(client, PHOTOS[0], make_rgba_png_bytes(0.9))
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "GENERATION_RUNS_UNAVAILABLE"
    assert _linked_cutouts(a["reference_id"])[0].acceptance_state == refs.STATE_ACCEPTED


def test_supersede_cutouts_refuses_originals(uploads):
    h = Harness()
    original = h.seed(cutout=make_pet_cutout_png())
    with pytest.raises(refs.PetReferenceError) as error:
        _run(refs.supersede_cutouts(user_id=USER, pet_id=PET, reference_ids=[original.id]))
    assert error.value.code == "PET_REFERENCE_NOT_FOUND"
    assert all(r.acceptance_state == refs.STATE_ACCEPTED for r in _ledger(PET, USER))


# --------------------------------------------------------------------------
# 적용되지 않은 UPDATE 는 성공이 아니다 (Step 0)
# --------------------------------------------------------------------------


def test_a_noop_update_is_reported_as_an_error_not_success(client, monkeypatch, caplog):
    """UPDATE 가 오류 없이 0행을 바꾼 경우 (예: anon 키 + RLS)."""
    a, b, c = _upload_three(client)

    async def silently_does_nothing(pet_id, ids, patch):
        return None

    monkeypatch.setattr(refs, "_update_acceptance_rows", silently_does_nothing)

    with caplog.at_level("ERROR"):
        res = _sync(client, PHOTOS[:2])
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "PET_REFERENCE_UPDATE_NOT_APPLIED"
    assert any("적용되지 않았다" in rec.getMessage() for rec in caplog.records)

    with pytest.raises(refs.PetReferenceError) as error:
        _run(
            refs.reject_original(
                user_id="alice@test", pet_id=STABLE_PET, reference_id=c["reference_id"]
            )
        )
    assert error.value.code == "PET_REFERENCE_UPDATE_NOT_APPLIED"
    assert error.value.status == 503
    assert len(refs.active_originals(_ledger())) == 3

    # 바꿀 것이 없는 호출은 쓰기 자체를 하지 않으므로 여전히 성공이다.
    assert _sync(client, PHOTOS).status_code == 200


def test_a_partially_applied_update_is_also_an_error(client, monkeypatch):
    _, _, c = _upload_three(client)

    async def only_the_original(pet_id, ids, patch):
        for r in refs._MOCK_REFS:
            if r["id"] == c["reference_id"]:
                r.update(patch)

    monkeypatch.setattr(refs, "_update_acceptance_rows", only_the_original)
    res = _sync(client, PHOTOS[:2])
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "PET_REFERENCE_UPDATE_NOT_APPLIED"


def test_a_noop_reactivation_is_an_error(client, monkeypatch):
    _upload_three(client)
    _sync(client, PHOTOS[:2])

    async def silently_does_nothing(pet_id, ids, patch):
        return None

    monkeypatch.setattr(refs, "_update_acceptance_rows", silently_does_nothing)
    res = _put(client, PHOTOS[2], CUTOUTS[2])
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "PET_REFERENCE_UPDATE_NOT_APPLIED"


def test_a_failing_update_is_503(client, monkeypatch):
    _upload_three(client)

    async def boom(pet_id, ids, patch):
        raise RuntimeError("db down")

    monkeypatch.setattr(refs, "_update_acceptance_rows", boom)
    res = _sync(client, PHOTOS[:2])
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "PET_REFERENCES_UNAVAILABLE"
    assert len(refs.active_originals(_ledger())) == 3
