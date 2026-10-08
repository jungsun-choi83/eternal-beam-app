"""
펫 입력 잠금 (Stage 1c) 계약 테스트.

제품 규칙: 생성이 시작되기 전에는 사진·누끼를 자유롭게 더하고 빼고 바꿀 수 있다.
생성이 시작되면 바꿀 수 없다.

잠김(pet_reference_service.pet_inputs_locked) = 다음 중 하나:
  - FAILED/CANCELLED 가 아닌 생성 실행이 하나라도 있다
  - role='generated' 자산이 하나라도 있다

- 잠긴 펫: 업로드 / 누끼 교체 / 동기화 / 거절 → 409 PHASE1_LOCKED
- FAILED/CANCELLED 실행만 있고 생성 자산이 없으면 풀린다
- 잠금을 판정할 수 없으면 503 (fail closed)
- 잠금 확인과 실행 생성은 서로 끼어들 수 없다
- 읽기 엔드포인트는 영향받지 않는다
"""

from __future__ import annotations

import asyncio
import base64

import anyio
import httpx
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.routers import matting as matting_router
from backend.routers import pet_references_v1 as references_router
from backend.services import pet_generation_run_service as runs
from backend.services import pet_reference_service as refs
from backend.services import supabase_assets

from .conftest import make_jpeg_bytes, make_rgba_png_bytes
from .test_pet_reference_supersede import (  # noqa: F401 — fixtures
    CUTOUTS,
    PHOTOS,
    STABLE_PET,
    _auth,
    _ledger,
    _linked_cutouts,
    _mock_backend,
    _put,
    _run,
    _seed_run,
    _sha,
    _sync,
    _upload_three,
    client,
    uploads,
)

LOCKING = [
    runs.STATUS_QUEUED,
    runs.STATUS_RUNNING,
    runs.STATUS_WAITING_PROVIDER,
    runs.STATUS_RECOVERY_REQUIRED,
    runs.STATUS_PUBLISHED,
]
UNLOCKING = [runs.STATUS_FAILED, runs.STATUS_CANCELLED]


def _seed_generated_asset() -> None:
    _run(
        refs.record_generated(
            user_id="alice@test", content_id="stable",
            object_path="alice@test/stable/canonical/v1.png", generated_kind="canonical_pet",
        )
    )


def _assert_locked(res) -> None:
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "PHASE1_LOCKED"


def _reject(reference_id: str):
    return _run(
        refs.reject_original(user_id="alice@test", pet_id=STABLE_PET, reference_id=reference_id)
    )


# --------------------------------------------------------------------------
# 잠긴 뒤: 모든 변경은 409 PHASE1_LOCKED
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", LOCKING)
def test_upload_after_a_started_run_is_locked(client, status):
    a, _, _ = _upload_three(client)
    _sync(client, PHOTOS[:2])  # 한 자리가 비어 있어도
    _seed_run(status)
    before = _ledger()

    _assert_locked(_put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)))  # 새 사진
    _assert_locked(_put(client, PHOTOS[2], CUTOUTS[2]))  # 뺐던 사진 되살리기
    _assert_locked(_put(client, PHOTOS[0], make_rgba_png_bytes(0.9)))  # 누끼 교체
    _assert_locked(_put(client, PHOTOS[0], CUTOUTS[0]))  # 같은 바이트의 재업로드도
    _assert_locked(_put(client, PHOTOS[0]))

    assert _ledger() == before
    assert _linked_cutouts(a["reference_id"])[0].acceptance_state == refs.STATE_ACCEPTED


@pytest.mark.parametrize("status", LOCKING)
def test_sync_and_reject_after_a_started_run_are_locked(client, status):
    a, _, _ = _upload_three(client)
    _seed_run(status)
    before = _ledger()

    _assert_locked(_sync(client, PHOTOS[:2]))
    # 바꿀 것이 없는 동기화도 잠김으로 답한다 — 클라이언트가 이 응답으로 잠금을 안다.
    _assert_locked(_sync(client, PHOTOS))

    with pytest.raises(refs.PetReferenceError) as error:
        _reject(a["reference_id"])
    assert error.value.code == "PHASE1_LOCKED" and error.value.status == 409

    assert _ledger() == before


def test_existing_pet_with_generated_assets_and_no_run_row_is_locked(client):
    """실행 기록이 없는 기존 펫이라도 정본 등 생성 자산이 있으면 잠겨 있다."""
    a, _, _ = _upload_three(client)
    _seed_generated_asset()

    _assert_locked(_put(client, PHOTOS[0], make_rgba_png_bytes(0.9)))
    _assert_locked(_sync(client, PHOTOS[:2]))
    with pytest.raises(refs.PetReferenceError) as error:
        _reject(a["reference_id"])
    assert error.value.code == "PHASE1_LOCKED"
    assert len(refs.active_originals(_ledger())) == 3


# --------------------------------------------------------------------------
# 풀림: FAILED / CANCELLED (생성 자산이 없을 때만)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", UNLOCKING)
def test_a_failed_or_cancelled_run_unlocks_the_pet(client, status):
    a, b, c = _upload_three(client)
    _seed_run(status)

    assert _sync(client, PHOTOS[:2]).status_code == 200
    replacement = _put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4))
    assert replacement.status_code == 200, replacement.text
    recut = _put(client, PHOTOS[0], make_rgba_png_bytes(0.9))
    assert recut.status_code == 200, recut.text
    assert recut.json()["cutout_reference_id"] != a["cutout_reference_id"]
    _reject(b["reference_id"])
    assert len(refs.active_originals(_ledger())) == 2


@pytest.mark.parametrize("status", UNLOCKING)
def test_a_failed_run_does_not_unlock_when_generated_assets_exist(client, status):
    _upload_three(client)
    _seed_run(status)
    _seed_generated_asset()
    _assert_locked(_sync(client, PHOTOS[:2]))
    _assert_locked(_put(client, PHOTOS[0], make_rgba_png_bytes(0.9)))


def test_one_live_run_locks_even_next_to_failed_ones(client):
    _upload_three(client)
    _seed_run(runs.STATUS_FAILED)
    _seed_run(runs.STATUS_QUEUED)
    _assert_locked(_sync(client, PHOTOS[:2]))


def test_another_pets_run_does_not_lock_this_pet(client):
    _upload_three(client)
    _seed_run(runs.STATUS_RUNNING, pet_id="pet_someone_else")
    assert _sync(client, PHOTOS[:2]).status_code == 200


def test_editing_before_generation_still_works(client):
    """실행도 생성 자산도 없는 펫: 더하기·빼기·바꾸기·누끼 교체가 전부 통한다."""
    a, b, c = _upload_three(client)
    assert _sync(client, PHOTOS[:2]).status_code == 200
    assert _put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)).status_code == 200
    assert _put(client, PHOTOS[0], make_rgba_png_bytes(0.9)).status_code == 200
    _reject(b["reference_id"])
    assert _put(client, PHOTOS[1], CUTOUTS[1]).json()["reactivated"] is True


# --------------------------------------------------------------------------
# 판정 불가는 닫는다 (503)
# --------------------------------------------------------------------------


def test_unknown_lock_state_fails_closed(client, monkeypatch):
    a, _, _ = _upload_three(client)
    before = _ledger()

    async def boom(pet_id):
        raise runs.PetGenerationRunError("GENERATION_RUNS_UNAVAILABLE", "down", status=503)

    monkeypatch.setattr(runs, "pet_has_locking_run", boom)

    for res in (
        _put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)),
        _put(client, PHOTOS[0], CUTOUTS[0]),
        _sync(client, PHOTOS[:2]),
    ):
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "GENERATION_RUNS_UNAVAILABLE"
    with pytest.raises(refs.PetReferenceError) as error:
        _reject(a["reference_id"])
    assert error.value.status == 503
    assert _ledger() == before


# --------------------------------------------------------------------------
# 읽기 / 누끼 URL 계약은 그대로
# --------------------------------------------------------------------------


def test_read_endpoint_is_unaffected_and_reports_the_lock(client, monkeypatch):
    from backend.services import asset_url_refresh

    monkeypatch.setattr(asset_url_refresh, "sign_object", lambda obj, *, ttl=None: "https://signed.test/x")
    _upload_three(client)

    url = f"/api/v1/pet/references/{STABLE_PET}?content_id=stable"
    unlocked = client.get(url, headers=_auth())
    assert unlocked.status_code == 200
    assert unlocked.json()["inputs_locked"] is False

    _seed_run(runs.STATUS_RUNNING)
    locked = client.get(url, headers=_auth())
    assert locked.status_code == 200
    assert locked.json()["inputs_locked"] is True
    assert locked.json()["intake_ready"] is True
    assert len(locked.json()["references"]) == 6

    async def boom(pet_id):
        raise runs.PetGenerationRunError("GENERATION_RUNS_UNAVAILABLE", "down", status=503)

    monkeypatch.setattr(runs, "pet_has_locking_run", boom)
    unknown = client.get(url, headers=_auth())
    assert unknown.status_code == 200  # 읽기는 잠금 판정 실패로 깨지지 않는다
    assert unknown.json()["inputs_locked"] is None


def test_persist_cutout_keeps_its_url_contract_on_a_locked_pet_without_touching_the_ledger(
    client, monkeypatch
):
    """
    /api/assets/cutout 은 생성 **뒤에** COME_CLOSER 가 누끼의 원격 URL 을 얻는
    경로다. 잠긴 펫에서도 URL 은 돌려주되 대장에는 아무것도 남기지 않는다.
    """

    async def no_row(*args, **kwargs):
        return None

    monkeypatch.setattr(supabase_assets, "ensure_user_asset_row", no_row)
    # 단일 사진 + 부모 없는 누끼가 가능한 레거시 모양의 펫 (엄격 인테이크 아님).
    _run(refs.record_original(user_id="alice@test", content_id="stable", data=PHOTOS[0]))
    _seed_run(runs.STATUS_PUBLISHED)
    before = _ledger()

    res = client.post(
        "/api/assets/cutout",
        json={
            "user_id": "alice@test",
            "content_id": "stable",
            "data_url": "data:image/png;base64," + base64.b64encode(CUTOUTS[0]).decode(),
        },
    )
    assert res.status_code == 200
    assert res.json()["cutout_url"]
    assert _ledger() == before


def test_matting_save_does_not_record_a_cutout_on_a_locked_pet(monkeypatch, uploads):
    async def no_row(*args, **kwargs):
        return None

    monkeypatch.setattr(supabase_assets, "ensure_user_asset_row", no_row)
    monkeypatch.setattr(
        matting_router, "matte_foreground_with_meta", lambda raw, **kw: (CUTOUTS[0], {})
    )
    monkeypatch.setattr(matting_router, "analyze_alpha_fur_edge", lambda png: {})
    _run(refs.record_original(user_id="alice@test", content_id="stable", data=PHOTOS[0]))

    app = FastAPI()
    app.include_router(matting_router.router, prefix="/api")
    from .conftest import ASGITestClient

    def matte():
        return ASGITestClient(app).post(
            "/api/matting/cutout",
            files={"file": ("dog.jpg", PHOTOS[0], "image/jpeg")},
            data={"user_id": "alice@test", "content_id": "stable", "save_to_storage": "true"},
        )

    _seed_run(runs.STATUS_RUNNING)
    locked = matte()
    assert locked.status_code == 200
    assert [r.role for r in _ledger()] == [refs.ROLE_ORIGINAL]

    runs._MOCK_RUNS.clear()
    unlocked = matte()
    assert unlocked.status_code == 200
    assert [r.role for r in _ledger()] == [refs.ROLE_ORIGINAL, refs.ROLE_DERIVED]


# --------------------------------------------------------------------------
# 실행 생성: 잠금의 시작, 그리고 변경과 끼어들 수 없음
# --------------------------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    app.include_router(references_router.router, prefix="/api")
    return app


async def _aput(http, photo: bytes, cutout: bytes | None = None):
    files = {"file": ("dog.jpg", photo, "image/jpeg")}
    if cutout is not None:
        files["cutout_file"] = ("cutout.png", cutout, "image/png")
    return await http.post(
        "/api/assets/original",
        files=files,
        data={"user_id": "alice@test", "content_id": "stable", "phase1_intake": "true"},
        headers=_auth(),
    )


def _start():
    return runs.start_generation_run(
        user_id="alice@test", pet_id=STABLE_PET, idempotency_key="free-home:stable"
    )


def test_creating_the_run_is_what_locks_the_pet(client):
    _upload_three(client)
    assert _sync(client, PHOTOS).status_code == 200  # 생성 전 동기화는 통한다

    run = _run(_start())
    assert run.status == runs.STATUS_QUEUED

    _assert_locked(_sync(client, PHOTOS))
    _assert_locked(_put(client, PHOTOS[0], CUTOUTS[0]))

    # 다시 확인(같은 키)은 잠긴 펫에서도 같은 실행을 돌려준다 — 실행 생성 자체는 막지 않는다.
    again = _run(_start())
    assert again.id == run.id

    # 실행이 실패로 끝나면(생성 자산 없이) 다시 고칠 수 있다.
    next(r for r in runs._MOCK_RUNS if r["id"] == run.id)["status"] = runs.STATUS_FAILED
    assert _sync(client, PHOTOS[:2]).status_code == 200


def test_run_creation_waits_for_an_in_flight_upload(client, monkeypatch):
    """
    업로드가 잠금 확인을 통과하고 아직 쓰는 중이면, 실행 생성은 그 업로드가
    끝날 때까지 기다린다 — 업로드가 "확인"과 "쓰기" 사이에 추월당하지 않는다.
    """
    _upload_three(client)
    _sync(client, PHOTOS[:2])
    events: list[str] = []

    async def scenario():
        upload_reached_storage = asyncio.Event()
        release_storage = asyncio.Event()

        async def slow_upload(path, data, content_type):
            events.append("upload:writing")
            upload_reached_storage.set()
            await release_storage.wait()
            return f"https://storage.test/{path}"

        monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", slow_upload)

        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            upload = asyncio.create_task(_aput(http, make_jpeg_bytes(80, 80)))
            await upload_reached_storage.wait()

            async def start():
                run = await _start()
                events.append("run:created")
                return run

            starting = asyncio.create_task(start())
            await asyncio.sleep(0.05)
            # 업로드가 끝나지 않은 동안 실행은 만들어지지 않는다.
            assert runs._MOCK_RUNS == []
            assert not starting.done()

            events.append("upload:released")
            release_storage.set()
            response = await upload
            events.append("upload:done")
            run = await starting
            return response, run

    response, run = anyio.run(scenario)
    assert response.status_code == 200, response.text
    assert run.status == runs.STATUS_QUEUED
    assert events.index("upload:released") < events.index("run:created")
    # 그 업로드는 실행보다 먼저 대장에 들어갔다.
    assert len(refs.active_originals(_ledger())) == 3


def test_an_upload_arriving_during_run_creation_waits_and_is_then_locked(client, monkeypatch):
    """
    실행 생성이 인테이크를 검증하고 삽입하는 사이에 도착한 업로드는 그 사이로
    끼어들지 못한다 — 기다렸다가 잠긴 펫을 본다.
    """
    _upload_three(client)
    _sync(client, PHOTOS[:2])
    real_validate = runs._validate_intake_evidence

    async def scenario():
        validating = asyncio.Event()
        release_validate = asyncio.Event()

        async def slow_validate(user_id, pet_id):
            result = await real_validate(user_id, pet_id)
            validating.set()
            await release_validate.wait()
            return result

        monkeypatch.setattr(runs, "_validate_intake_evidence", slow_validate)

        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            starting = asyncio.create_task(_start())
            await validating.wait()

            upload = asyncio.create_task(_aput(http, make_jpeg_bytes(80, 80)))
            sync = asyncio.create_task(
                http.post(
                    f"/api/v1/pet/references/{STABLE_PET}/sync",
                    json={"content_hashes": [_sha(PHOTOS[0])]},
                    headers=_auth(),
                )
            )
            await asyncio.sleep(0.05)
            assert not upload.done() and not sync.done()

            release_validate.set()
            run = await starting
            return run, await upload, await sync

    run, upload, sync = anyio.run(scenario)
    assert run.status == runs.STATUS_QUEUED
    _assert_locked(upload)
    _assert_locked(sync)
    # 검증이 본 증거 그대로다 — 사이에 아무것도 바뀌지 않았다.
    assert len(refs.active_originals(_ledger())) == 2


def test_same_pet_uploads_still_overlap_each_other(client, monkeypatch):
    """문은 실행 생성만 단독으로 둔다 — 같은 펫의 사진들은 여전히 동시에 올라간다."""

    async def scenario():
        in_flight = 0
        peak = 0

        async def slow_upload(path, data, content_type):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return f"https://storage.test/{path}"

        monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", slow_upload)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            responses = await asyncio.gather(*(_aput(http, photo) for photo in PHOTOS))
        return peak, responses

    peak, responses = anyio.run(scenario)
    assert all(r.status_code == 200 for r in responses)
    assert peak >= 2


# --------------------------------------------------------------------------
# 빈 자리 복구: 잠긴 펫 뒤의 새 업로드는 새 펫이다
# --------------------------------------------------------------------------


def _put_as(client, content_id: str, photo: bytes, cutout: bytes | None = None):
    files = {"file": ("dog.jpg", photo, "image/jpeg")}
    if cutout is not None:
        files["cutout_file"] = ("cutout.png", cutout, "image/png")
    return client.post(
        "/api/assets/original",
        files=files,
        data={"user_id": "alice@test", "content_id": content_id, "phase1_intake": "true"},
        headers=_auth(),
    )


def test_first_upload_after_a_locked_pet_goes_to_a_new_pet_and_leaves_the_old_one_untouched(
    client,
):
    """
    새로고침 뒤: 자리는 비어 돌아오고, 사용자가 새 사진을 고른다. 클라이언트는 새
    content_id 를 발급한다(identityForAddedPhotos). 그 업로드는 새 pet_id 로 가고,
    생성이 시작된 예전 펫과 그 계보는 한 행도 바뀌지 않는다.
    """
    _upload_three(client)
    run = _run(_start())  # 예전 펫: 실행이 만들어져 잠겼다
    _seed_generated_asset()
    old_ledger = _ledger()
    old_runs = [dict(r) for r in runs._MOCK_RUNS]

    # 새 신원으로 올린다 — 예전 펫에 있던 것과 **같은 바이트**여도 새 펫의 행이다.
    fresh = _put_as(client, "fresh", PHOTOS[0], CUTOUTS[0])
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["pet_id"] == "pet_fresh"
    assert body["pet_id"] != STABLE_PET
    assert body["deduplicated"] is False and body["reactivated"] is False
    assert body["intake_ready"] is True
    assert body["reference_id"] not in {r.id for r in old_ledger}

    # 새 펫은 잠겨 있지 않다 — 더하고, 동기화하고, 누끼를 바꿀 수 있다.
    assert _put_as(client, "fresh", PHOTOS[1], CUTOUTS[1]).status_code == 200
    assert _sync(client, [PHOTOS[0]], pet="pet_fresh").status_code == 200
    assert _put_as(client, "fresh", PHOTOS[0], make_rgba_png_bytes(0.9)).status_code == 200
    new_ledger = _ledger("pet_fresh")
    assert all(r.pet_id == "pet_fresh" and r.content_id == "fresh" for r in new_ledger)
    assert len(refs.active_originals(new_ledger)) == 1

    # 예전 펫: 대장도 실행도 그대로다.
    assert _ledger() == old_ledger
    assert [dict(r) for r in runs._MOCK_RUNS] == old_runs
    assert _run(_start()).id == run.id

    # 그리고 예전 펫 앞으로 오는 변경은 여전히 전부 막힌다.
    _assert_locked(_put(client, make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)))
    _assert_locked(_put(client, PHOTOS[0], CUTOUTS[0]))
    _assert_locked(_sync(client, PHOTOS[:1]))
    assert _ledger() == old_ledger


def test_a_new_pets_uploads_cannot_be_redirected_into_the_locked_pet(client):
    """새 펫의 동기화가 예전 펫의 원본을 물리지 못한다 — 대장은 pet_id 로 갈린다."""
    _upload_three(client)
    _seed_run(runs.STATUS_PUBLISHED)
    old_ledger = _ledger()

    assert _put_as(client, "fresh", make_jpeg_bytes(80, 80), make_rgba_png_bytes(0.4)).status_code == 200
    synced = _sync(client, [make_jpeg_bytes(80, 80)], pet="pet_fresh")
    assert synced.status_code == 200
    assert synced.json()["rejected_reference_ids"] == []
    assert _ledger() == old_ledger
