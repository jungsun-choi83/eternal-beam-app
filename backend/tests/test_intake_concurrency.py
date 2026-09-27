"""
무료(no-credit) 인테이크/레퍼런스 분석 동시성 계약 테스트.

이 병렬화가 반드시 지켜야 하는 것:
  1. 서로 다른 사진은 실제로 동시에 처리된다(이벤트 루프가 막히지 않는다).
  2. 사진 1장 안에서 원본 저장과 누끼 생성이 겹친다.
  3. 같은 이미지에 대한 유료 VLM 호출은 동시에 돌려도 중복 과금되지 않는다
     (identity/morphology 프로필을 동시에 빌드해도 마찬가지).
  4. 레퍼런스 lineage/dedup 판정은 병렬화 전과 동일하다.
  5. 한 사진의 실패가 다른 사진의 등록을 막지 않는다.
  6. 사진 1장짜리 플로우는 기존 계약 그대로다.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.scripts.dev_reference_pack import register_one_reference
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, supabase_assets, vlm_identity

from .conftest import make_jpeg_bytes
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import CID, DIAG, PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    for m in (refs, pet_registry, ids, morph, sets):
        m.__reset_for_tests()
    vlm_identity.clear_semantic_cache()
    yield
    for m in (refs, pet_registry, ids, morph, sets):
        m.__reset_for_tests()
    vlm_identity.clear_semantic_cache()


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture
def fast_uploads(monkeypatch):
    """record_original/record_derived 를 직접 부르는 테스트용 — 지연 없는 목업 업로드."""

    async def fake_upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return app


# ══════════════════════════════════════════════════════════════════════════
# 1) 서로 다른 사진이 실제로 동시에 처리된다 (이벤트 루프가 막히지 않는다)
# ══════════════════════════════════════════════════════════════════════════


def test_multiple_photos_upload_concurrently_not_serially(monkeypatch):
    delay = 0.12
    lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}

    async def fake_upload(path, data, content_type):
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        await asyncio.sleep(delay)
        with lock:
            state["inflight"] -= 1
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)

    app = _app()

    async def _go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            async def _one(i: int):
                return await client.post(
                    "/api/assets/original",
                    data={"user_id": f"user{i}@test", "content_id": f"cid{i}"},
                    files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
                )

            started = time.monotonic()
            responses = await asyncio.gather(*(_one(i) for i in range(3)))
            elapsed = time.monotonic() - started
            return responses, elapsed

    responses, elapsed = _run(_go())

    for r in responses:
        assert r.status_code == 200, r.text
        assert r.json()["reference_recorded"] is True

    # 직렬이면 3 * delay(~0.36s). 동시면 delay 하나 정도(~0.12~0.2s)다.
    assert elapsed < delay * 2, f"업로드가 겹치지 않은 것 같다 (elapsed={elapsed:.3f}s)"
    assert state["max_inflight"] >= 2, "업로드가 실제로 동시에 진행되지 않았다"


# ══════════════════════════════════════════════════════════════════════════
# 2) 사진 1장 안에서 원본 저장과 누끼 생성이 겹친다
# ══════════════════════════════════════════════════════════════════════════


def test_original_persistence_overlaps_cutout_generation(monkeypatch):
    delay = 0.15

    async def slow_upload(path, data, content_type):
        # 원본 저장에만 지연을 준다 — 누끼를 스토리지에 올리는 다음 단계(같은
        # 함수를 쓴다)는 이 테스트의 관심사가 아니라 빠르게 통과시킨다. 그래야
        # "원본 저장 ⧺ 누끼 생성" 겹침만 순수하게 잰다.
        if "/references/original_" in path:
            await asyncio.sleep(delay)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", slow_upload)

    def slow_cutout(data: bytes) -> bytes:
        time.sleep(delay)  # 로컬 세그멘테이션(SAM2/ViTMatte) 흉내 — CPU-bound, 무료
        return b"cutout-bytes"

    started = time.monotonic()
    entry = _run(
        register_one_reference(
            "dog.jpg",
            make_jpeg_bytes(),
            "image/jpeg",
            user_id=USER,
            cid=CID,
            reference_cutout=slow_cutout,
        )
    )
    elapsed = time.monotonic() - started

    assert entry["id"] and entry["cutout_object_path"]
    # 순차였다면 2*delay(~0.30s: 원본 업로드 + 누끼 생성). 겹치면 delay 하나
    # 정도(~0.15~0.22s)다.
    assert elapsed < delay * 1.7, f"원본 저장과 누끼 생성이 겹치지 않은 것 같다 (elapsed={elapsed:.3f}s)"


def test_original_persistence_overlap_survives_a_failing_upload(monkeypatch):
    """겹쳐 놓은 누끼 생성이 원본 저장 실패 시에도 매달린 태스크를 남기지 않는다."""

    async def boom_upload(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", boom_upload)

    cutout_calls = {"n": 0}

    def counting_cutout(data: bytes) -> bytes:
        cutout_calls["n"] += 1
        return b"cutout-bytes"

    with pytest.raises(RuntimeError):
        _run(
            register_one_reference(
                "dog.jpg",
                make_jpeg_bytes(),
                "image/jpeg",
                user_id=USER,
                cid=CID,
                reference_cutout=counting_cutout,
            )
        )
    # 실패 격리는 "사진 간" 이야기지만, 겹쳐 놓은 누끼 작업 자체가 예외를
    # 삼키거나 프로세스를 오염시키지 않는지는 여기서 확인한다.
    assert cutout_calls["n"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 3) 같은 이미지의 유료 VLM 호출은 동시에 돌려도 중복 과금되지 않는다
# ══════════════════════════════════════════════════════════════════════════


def _install_fake_anthropic(monkeypatch, *, delay: float = 0.05) -> dict[str, int]:
    import anthropic

    counters = {"n": 0}
    lock = threading.Lock()

    class _FakeMessages:
        def create(self, **kwargs):
            with lock:
                counters["n"] += 1
            time.sleep(delay)
            text = json.dumps({})  # 소비자는 .get() 방어적 접근 — 빈 dict 로 충분
            return SimpleNamespace(
                stop_reason=None,
                model="claude-test",
                content=[SimpleNamespace(type="text", text=text)],
            )

    class _FakeClient:
        def __init__(self, *a, **k):
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)
    return counters


def test_concurrent_calls_for_same_image_hit_the_api_once(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    counters = _install_fake_anthropic(monkeypatch)

    data = make_jpeg_bytes()

    async def _go():
        return await asyncio.gather(
            asyncio.to_thread(vlm_identity.analyze_semantic_traits, [(data, "image/jpeg")]),
            asyncio.to_thread(vlm_identity.analyze_semantic_traits, [(data, "image/jpeg")]),
        )

    r1, r2 = _run(_go())
    assert r1 is not None and r2 is not None
    assert r1 == r2
    assert counters["n"] == 1, "같은 이미지에 대해 유료 호출이 두 번 나갔다"


def test_concurrent_calls_for_different_images_both_hit_the_api(monkeypatch):
    """single-flight 가 다른 사진끼리도 잘못 합치지 않는다."""
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    counters = _install_fake_anthropic(monkeypatch)

    async def _go():
        return await asyncio.gather(
            asyncio.to_thread(
                vlm_identity.analyze_semantic_traits, [(make_jpeg_bytes(64, 48), "image/jpeg")]
            ),
            asyncio.to_thread(
                vlm_identity.analyze_semantic_traits, [(make_jpeg_bytes(65, 49), "image/jpeg")]
            ),
        )

    r1, r2 = _run(_go())
    assert r1 is not None and r2 is not None
    assert counters["n"] == 2


def test_identity_and_morphology_builds_share_vlm_calls_when_run_concurrently(monkeypatch, fast_uploads):
    """
    build_reference_set 이 identity/morphology 프로필을 동시에 빌드해도, 같은
    원본 레퍼런스에 대한 시맨틱 분석은 한 번만 유료 호출된다.
    """
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    counters = _install_fake_anthropic(monkeypatch)
    # classify_reference(뷰/포즈)는 이 테스트의 관심사가 아니다 — 결정론
    # UNKNOWN 경로로 고정해 시맨틱 호출 수만 순수하게 센다.
    monkeypatch.setattr(vlm_identity, "classify_reference", lambda data, mime="image/jpeg": None)

    bytes_by_path: dict[str, bytes] = {}

    def _seed(n: int):
        orig = make_jpeg_bytes(120 + n, 90 + n)
        ref = _run(
            refs.record_original(
                user_id=USER, content_id=CID, data=orig, mime_type="image/jpeg", diagnostics=DIAG
            )
        )
        bytes_by_path[ref.object_path] = orig
        cutout = make_pet_cutout_png()
        cut_path = f"{USER}/{CID}/references/cutout_{ref.content_hash[:16]}.png"
        derived = _run(
            refs.record_derived(
                user_id=USER, content_id=CID, object_path=cut_path,
                derived_kind="cutout_reference", parent_reference_id=ref.id,
                mime_type="image/png",
            )
        )
        bytes_by_path[derived.object_path] = cutout

    _seed(1)
    _seed(2)

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    result = _run(sets.build_reference_set(user_id=USER, pet_id=PET, fetch_bytes=fetch))

    assert result.identity_profile_id and result.morphology_profile_id
    # 2장의 서로 다른 원본 → 시맨틱 분석 2번, identity/morphology 가 동시에
    # 돌아도 4번이 아니다.
    assert counters["n"] == 2, f"같은 이미지의 VLM 호출이 identity/morphology 사이에서 중복됐다 (n={counters['n']})"


# ══════════════════════════════════════════════════════════════════════════
# 4) lineage/dedup 판정은 병렬화 전과 동일하다
# ══════════════════════════════════════════════════════════════════════════


def test_lineage_and_dedup_are_unchanged_after_parallelization(fast_uploads):
    bytes_by_path: dict[str, bytes] = {}

    def _seed(n: int):
        orig = make_jpeg_bytes(120 + n, 90 + n)
        ref = _run(
            refs.record_original(
                user_id=USER, content_id=CID, data=orig, mime_type="image/jpeg", diagnostics=DIAG
            )
        )
        bytes_by_path[ref.object_path] = orig
        cutout = make_pet_cutout_png()
        cut_path = f"{USER}/{CID}/references/cutout_{ref.content_hash[:16]}.png"
        derived = _run(
            refs.record_derived(
                user_id=USER, content_id=CID, object_path=cut_path,
                derived_kind="cutout_reference", parent_reference_id=ref.id,
                mime_type="image/png",
            )
        )
        bytes_by_path[derived.object_path] = cutout
        return ref, derived

    ref1, cut1 = _seed(1)
    ref2, cut2 = _seed(2)

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    result = _run(sets.build_reference_set(user_id=USER, pet_id=PET, fetch_bytes=fetch))
    assert result.deduplicated is False

    all_refs = _run(refs.list_references(user_id=USER, pet_id=PET))
    lineage = refs.strict_lineage_map(all_refs)
    assert lineage[str(ref1.id)] == {"cutout_reference_id": str(cut1.id), "strict": True}
    assert lineage[str(ref2.id)] == {"cutout_reference_id": str(cut2.id), "strict": True}

    profile = _run(ids.get_profile(user_id=USER, pet_id=PET))
    assert set(profile.source_reference_ids) == {str(ref1.id), str(ref2.id)}
    for rid in (str(ref1.id), str(ref2.id)):
        assert profile.reference_eligibility[rid]["usable_for_identity"] is True

    morphology = _run(morph.get_profile(user_id=USER, pet_id=PET))
    assert set(morphology.source_reference_ids) == {str(ref1.id), str(ref2.id)}

    # 재빌드는 멱등해야 한다 — 입력이 그대로면 셋 다 dedup 된다.
    rebuilt = _run(sets.build_reference_set(user_id=USER, pet_id=PET, fetch_bytes=fetch))
    assert rebuilt.deduplicated is True
    assert rebuilt.identity_profile_version == result.identity_profile_version
    assert rebuilt.morphology_profile_version == result.morphology_profile_version


# ══════════════════════════════════════════════════════════════════════════
# 5) 한 사진의 실패가 다른 사진의 등록을 막지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_failures_stay_isolated_per_photo(monkeypatch):
    from backend.services.concurrency import gather_bounded

    calls = {"n": 0}

    async def flaky_upload(path, data, content_type):
        calls["n"] += 1
        if "cid_bad" in path:
            raise RuntimeError("storage rejected this one photo")
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky_upload)

    photos = [
        ("good1.jpg", "cid_good1", make_jpeg_bytes(120, 90)),
        ("bad.jpg", "cid_bad", make_jpeg_bytes(121, 91)),
        ("good2.jpg", "cid_good2", make_jpeg_bytes(122, 92)),
    ]

    async def _go():
        return await gather_bounded(
            [
                (
                    lambda path=path, cid=cid, data=data: register_one_reference(
                        path, data, "image/jpeg",
                        user_id=USER, cid=cid,
                        reference_cutout=lambda d: None,
                    )
                )
                for path, cid, data in photos
            ],
            return_exceptions=True,
        )

    results = _run(_go())

    assert isinstance(results[1], RuntimeError)
    assert results[0]["id"] and results[2]["id"]

    # 실패한 사진의 펫에는 아무것도 기록되지 않았고, 성공한 둘은 정상 기록됐다.
    good1_refs = _run(refs.list_references(user_id=USER, pet_id=refs.pet_id_for_content("cid_good1")))
    good2_refs = _run(refs.list_references(user_id=USER, pet_id=refs.pet_id_for_content("cid_good2")))
    bad_refs = _run(refs.list_references(user_id=USER, pet_id=refs.pet_id_for_content("cid_bad")))
    assert len(good1_refs) == 1
    assert len(good2_refs) == 1
    assert len(bad_refs) == 0


# ══════════════════════════════════════════════════════════════════════════
# 6) 사진 1장짜리 플로우는 기존 계약 그대로다
# ══════════════════════════════════════════════════════════════════════════


def test_single_photo_flow_is_unchanged(monkeypatch):
    uploads: list[str] = []

    async def fake_upload(path, data, content_type):
        uploads.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)

    app = _app()

    async def _go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post(
                "/api/assets/original",
                data={"user_id": USER, "content_id": "cid_single"},
                files={
                    "file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg"),
                    "cutout_file": ("cutout.png", make_pet_cutout_png(), "image/png"),
                },
            )

    r = _run(_go())
    body = r.json()
    assert r.status_code == 200
    assert body["reference_recorded"] is True
    assert body["deduplicated"] is False
    assert body["cutout_recorded"] is True
    assert body["cutout_reference_id"]
    assert body["intake_ready"] is True
    assert len(uploads) == 2  # 원본 1 + 누끼 1

    all_refs = _run(
        refs.list_references(user_id=USER, pet_id=refs.pet_id_for_content("cid_single"))
    )
    ready, original, cutout = refs.intake_readiness(all_refs)
    assert ready is True
    assert original is not None and cutout is not None
    assert cutout.parent_reference_id == original.id
