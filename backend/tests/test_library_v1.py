"""
Phase 11 — GET /api/v1/library 계약 테스트.

    BREATHING(무료 기본)  ← pet_motion_publications
    구매/멤버십 생성 모션   ← owned_generated_assets

두 표를 합친 하나의 목록이 나오는지, 그리고 소유/접근 규칙(만료된 멤버십도
이미 만든 것은 계속 보임, 아직 안 만든 것은 아예 안 보임, 폐기된 것은 안 보임)
이 지켜지는지를 검증한다.

⚠️ ASGITestClient(conftest.py) 는 요청마다 자체 이벤트 루프(anyio.run)를 돈다 —
그래서 테스트 함수 자체는 (다른 라우터 테스트들처럼) 동기 def 로 두고, 비동기
셋업만 _run() 으로 개별 실행한다. @pytest.mark.anyio 테스트 안에서 클라이언트를
부르면 "이미 실행 중인 루프" 오류가 난다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import anyio
import pytest
from fastapi import FastAPI

from .conftest import ASGITestClient
from ..routers import library_v1
from ..services import (
    asset_url_refresh,
    generated_motions_service,
    motion_publication_service,
    owned_assets,
    pet_registry,
    premium_purchase,
    product_catalog,
    subscription_store_service,
)
from ..models.subscription import UserSubscriptionRow
from ..scenarios.pet_scenarios import THEME_INDEPENDENT_PLACE_ID

USER = "user_lib"
OTHER_USER = "user_other"
PET = "pet_lib"

STORAGE = "https://proj.supabase.co/storage/v1/object/sign/user-assets"
STALE_PATH = f"{USER}/{PET}/library/TAIL_WAGGING_old.mp4"
PACKED_PATH = f"{USER}/{PET}/motions/tail_wagging/v2/seedance_a1_packed.mp4"
RAW_PATH = f"{USER}/{PET}/motions/tail_wagging/v2/seedance_a1_raw.mp4"


def _run(coro):
    return anyio.run(lambda: coro)


def _reset_all() -> None:
    owned_assets.__reset_for_tests()
    motion_publication_service.__reset_for_tests()
    product_catalog.__reset_for_tests()
    pet_registry.__reset_for_tests()
    premium_purchase.__reset_for_tests()
    subscription_store_service._MOCK_SUBS.clear()
    subscription_store_service._MOCK_EVENTS.clear()
    generated_motions_service._MOCK_MOTIONS.clear()


@pytest.fixture
def lib_client(monkeypatch: pytest.MonkeyPatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    _reset_all()

    app = FastAPI()
    app.include_router(library_v1.router, prefix="/api")
    yield ASGITestClient(app)

    _reset_all()


def _sign_only(*valid_paths: str):
    """valid_paths 만 서명되고 나머지 객체는 스토리지에 없다(None)."""
    def sign(obj, **_kwargs):
        return f"https://signed.test/{obj.path}" if obj.path in valid_paths else None
    return sign


def _record_pointer(action_id: str, path: str, *, user: str = USER) -> None:
    _run(
        generated_motions_service.record_pointer(
            user_id=user, pet_id=PET, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id=action_id, video_url=f"{STORAGE}/{path}?token=stale",
        )
    )


def _publish_breathing(*, version: int = 1, published_at: str = "2026-01-01T00:00:00+00:00", **overrides) -> None:
    row = {
        "publication_id": f"pub-{version}",
        "motion_version_id": f"v-{version}",
        "selected_candidate_id": f"c-{version}",
        "user_id": USER,
        "pet_id": PET,
        "motion_version": version,
        "bucket": "user-assets",
        "object_path": f"breathing_v{version}.mp4",
        "published_at": published_at,
        "deduplicated": False,
    }
    row.update(overrides)
    motion_publication_service._MOCK_PUBLICATIONS.append(row)


def _record_asset(product_key: str, job: str, **overrides) -> None:
    asset = owned_assets.OwnedAsset(
        user_id=overrides.pop("user_id", USER),
        pet_id=overrides.pop("pet_id", PET),
        product_key=product_key,
        video_url=overrides.pop("video_url", f"https://cdn.test/{job}.mp4"),
        source_job_id=job,
        **overrides,
    )
    _run(owned_assets.record(asset))


def _get(client: ASGITestClient, *, user: str = USER):
    return client.get(f"/api/v1/library?pet_id={PET}", headers={"Authorization": f"Bearer test:{user}"})


def _by_motion(motions: list[dict], motion_id: str) -> dict:
    matches = [m for m in motions if m["motion_id"] == motion_id]
    assert matches, f"{motion_id} not in {[m['motion_id'] for m in motions]}"
    return matches[0]


# ── 무료 기본 (BREATHING) ────────────────────────────────────────────────────


def test_included_breathing_is_playable_and_permanent(lib_client: ASGITestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", lambda obj: f"https://signed.test/{obj.path}")
    _publish_breathing()

    resp = _get(lib_client)
    assert resp.status_code == 200
    entry = _by_motion(resp.json()["motions"], "BREATHING")

    assert entry["ownership"]["type"] == "included"
    assert entry["ownership"]["permanent"] is True
    assert entry["access"]["state"] == "playable"
    assert entry["url"] == "https://signed.test/breathing_v1.mp4"
    assert entry["version"] == 1
    assert entry["publication_id"] == "pub-1"


# ── 구매/멤버십 분류 ─────────────────────────────────────────────────────────


def test_credit_purchased_motion_is_classified_correctly(lib_client: ASGITestClient):
    _record_asset("action:PAW_WAVE", "job1", credits_spent=4, ledger_id="ledger-1", source=owned_assets.SOURCE_PURCHASE)

    resp = _get(lib_client)
    entry = _by_motion(resp.json()["motions"], "PAW_WAVE")

    assert entry["ownership"]["type"] == "credit_purchase"
    assert entry["ownership"]["permanent"] is True
    assert entry["access"]["state"] == "playable"
    assert entry["url"] == "https://cdn.test/job1.mp4"


def test_membership_generated_motion_is_classified_correctly(lib_client: ASGITestClient):
    _record_asset("idle:BLINKING", "job2", source=owned_assets.SOURCE_FREE, credits_spent=0)

    resp = _get(lib_client)
    entry = _by_motion(resp.json()["motions"], "BLINKING")

    assert entry["ownership"]["type"] == "membership"
    assert entry["ownership"]["permanent"] is True


def test_legacy_asset_is_classified_as_generic_owned(lib_client: ASGITestClient):
    _record_asset("action:PET_HEAD", "legacy:1", source=owned_assets.SOURCE_LEGACY, credits_spent=0)

    resp = _get(lib_client)
    entry = _by_motion(resp.json()["motions"], "PET_HEAD")

    assert entry["ownership"]["type"] == "owned"


# ── 만료된 멤버십의 접근 규칙 ─────────────────────────────────────────────────


def test_expired_membership_keeps_already_owned_motion_playable(lib_client: ASGITestClient):
    subscription_store_service._MOCK_SUBS[USER] = UserSubscriptionRow(
        user_id=USER, plan_id="standard_subscription", status="expired"
    )
    _record_asset("idle:TAIL_WAGGING", "job3", source=owned_assets.SOURCE_FREE, credits_spent=0)

    resp = _get(lib_client)
    body = resp.json()
    assert body["entitled"] is False
    assert body["subscription_status"] == "expired"

    entry = _by_motion(body["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "playable"
    assert entry["ownership"]["permanent"] is True


def test_expired_membership_does_not_invent_ungenerated_motion(lib_client: ASGITestClient):
    """아직 한 번도 만들지 않은 멤버십 모션은 오퍼일 뿐 라이브러리에 나타나지 않는다."""
    subscription_store_service._MOCK_SUBS[USER] = UserSubscriptionRow(
        user_id=USER, plan_id="standard_subscription", status="expired"
    )

    resp = _get(lib_client)
    assert resp.json()["motions"] == []


# ── 여러 발행/버전 ───────────────────────────────────────────────────────────


def test_multiple_breathing_publications_all_returned(lib_client: ASGITestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", lambda obj: f"https://signed.test/{obj.path}")
    _publish_breathing(version=1, published_at="2026-01-01T00:00:00+00:00")
    _publish_breathing(version=2, published_at="2026-02-01T00:00:00+00:00")

    resp = _get(lib_client)
    breathing = [m for m in resp.json()["motions"] if m["motion_id"] == "BREATHING"]
    assert len(breathing) == 2
    assert {m["version"] for m in breathing} == {1, 2}
    # 최신 발행이 먼저 나온다.
    assert breathing[0]["version"] == 2


def test_multiple_purchases_of_the_same_product_collapse_to_one_card(lib_client: ASGITestClient):
    """그리드에는 pet·motion 당 카드 하나. 이력은 지우지 않고 provenance 에 남는다."""
    _record_asset("action:PAW_WAVE", "j1", credits_spent=4, ledger_id="l1", source=owned_assets.SOURCE_PURCHASE,
                  created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _record_asset("action:PAW_WAVE", "j2", credits_spent=4, ledger_id="l2", source=owned_assets.SOURCE_PURCHASE,
                  created_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    newest = next(a for a in owned_assets._MOCK if a.source_job_id == "j2")
    older = next(a for a in owned_assets._MOCK if a.source_job_id == "j1")

    resp = _get(lib_client)
    paw_waves = [m for m in resp.json()["motions"] if m["motion_id"] == "PAW_WAVE"]
    assert len(paw_waves) == 1
    card = paw_waves[0]
    assert card["id"] == f"asset:{newest.asset_id}"
    assert card["provenance"]["superseded_asset_ids"] == [older.asset_id]
    # 소유 원장은 그대로 두 줄이다 — 지우지도 폐기하지도 않았다.
    assert len(_run(owned_assets.list_for_pet(USER, PET))) == 2


# ── 재생성 뒤 옛 소유 행 — 현재 포인터 폴백 ─────────────────────────────────


def _stale_tail_wagging(**overrides) -> None:
    _record_asset(
        "idle:TAIL_WAGGING", "phase7:run-old", source=owned_assets.SOURCE_FREE, credits_spent=0,
        video_url=f"{STORAGE}/{STALE_PATH}?token=stale", bucket="user-assets", object_path=STALE_PATH,
        lineage={"publication_id": "pub-old", "delivery_format": "packed_alpha"},
        **overrides,
    )


def test_valid_owned_storage_asset_is_playable_from_owned_row(lib_client, monkeypatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(STALE_PATH, PACKED_PATH))
    _stale_tail_wagging()
    _record_pointer("TAIL_WAGGING", PACKED_PATH)

    entry = _by_motion(_get(lib_client).json()["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "playable"
    assert entry["url"] == f"https://signed.test/{STALE_PATH}"
    assert entry["provenance"]["playback_source"] == "owned_generated_assets"


def test_stale_owned_asset_falls_back_to_current_packed_pointer(lib_client, monkeypatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(PACKED_PATH))
    _stale_tail_wagging()
    _record_pointer("TAIL_WAGGING", PACKED_PATH)
    owned = _run(owned_assets.list_for_pet(USER, PET))[0]

    entry = _by_motion(_get(lib_client).json()["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "playable"
    assert entry["url"] == f"https://signed.test/{PACKED_PATH}"
    assert entry["delivery_format"] == "packed_alpha"
    # 소유/출처는 여전히 소유 행에서 온다 — 포인터는 재생 URL 만 빌려 준다.
    assert entry["id"] == f"asset:{owned.asset_id}"
    assert entry["ownership"]["type"] == "membership"
    assert entry["publication_id"] == "pub-old"
    assert entry["provenance"]["source"] == "owned_generated_assets"
    assert entry["provenance"]["playback_source"] == "generated_motions"
    # 소유 행은 손대지 않았다.
    after = _run(owned_assets.list_for_pet(USER, PET))[0]
    assert (after.bucket, after.object_path, after.video_url) == (owned.bucket, owned.object_path, owned.video_url)


def test_stale_owned_asset_without_valid_pointer_is_unavailable(lib_client, monkeypatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only())
    _stale_tail_wagging()
    _record_pointer("TAIL_WAGGING", PACKED_PATH)  # 포인터는 있지만 객체가 없다

    entry = _by_motion(_get(lib_client).json()["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "unavailable"
    assert entry["url"] is None
    assert entry["ownership"]["type"] == "membership"


def test_stale_owned_asset_never_falls_back_to_raw_provider_video(lib_client, monkeypatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(RAW_PATH))
    _stale_tail_wagging()
    _record_pointer("TAIL_WAGGING", RAW_PATH)

    entry = _by_motion(_get(lib_client).json()["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "unavailable"
    assert entry["url"] is None


def test_newest_playable_owned_row_wins_over_stale_newer_row(lib_client, monkeypatch):
    old_ok = f"{USER}/{PET}/motions/tail_wagging/v1/seedance_a1_packed.mp4"
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(old_ok))
    _record_asset(
        "idle:TAIL_WAGGING", "phase7:run-1", source=owned_assets.SOURCE_FREE, credits_spent=0,
        video_url=f"{STORAGE}/{old_ok}?token=x", bucket="user-assets", object_path=old_ok,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _stale_tail_wagging(created_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
    playable_row = next(a for a in owned_assets._MOCK if a.source_job_id == "phase7:run-1")

    motions = _get(lib_client).json()["motions"]
    assert [m["motion_id"] for m in motions] == ["TAIL_WAGGING"]
    entry = motions[0]
    assert entry["access"]["state"] == "playable"
    assert entry["id"] == f"asset:{playable_row.asset_id}"
    assert entry["url"] == f"https://signed.test/{old_ok}"


def test_newest_of_two_playable_rows_is_selected(lib_client, monkeypatch):
    v1 = f"{USER}/{PET}/motions/tail_wagging/v1/seedance_a1_packed.mp4"
    v2 = f"{USER}/{PET}/motions/tail_wagging/v2/seedance_a1_packed.mp4"
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(v1, v2))
    for path, job, when in ((v1, "phase7:r1", datetime(2026, 1, 1, tzinfo=timezone.utc)),
                            (v2, "phase7:r2", datetime(2026, 2, 1, tzinfo=timezone.utc))):
        _record_asset("idle:TAIL_WAGGING", job, source=owned_assets.SOURCE_FREE, credits_spent=0,
                      video_url=f"{STORAGE}/{path}?token=x", bucket="user-assets", object_path=path, created_at=when)

    motions = _get(lib_client).json()["motions"]
    assert len(motions) == 1
    assert motions[0]["url"] == f"https://signed.test/{v2}"


def test_pointer_fallback_never_uses_another_users_pointer(lib_client, monkeypatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(PACKED_PATH))
    _stale_tail_wagging()
    _record_pointer("TAIL_WAGGING", PACKED_PATH, user=OTHER_USER)  # 같은 pet_id, 다른 사용자

    entry = _by_motion(_get(lib_client).json()["motions"], "TAIL_WAGGING")
    assert entry["access"]["state"] == "unavailable"
    assert entry["url"] is None


def test_pointer_alone_never_creates_a_library_card(lib_client, monkeypatch):
    """소유 행이 없으면 포인터가 있어도 카드는 없다 — 소유 근거는 소유 원장뿐이다."""
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(PACKED_PATH))
    _run(premium_purchase.assert_pet_owned(USER, PET))
    _record_pointer("TAIL_WAGGING", PACKED_PATH)

    assert _get(lib_client).json()["motions"] == []


def test_breathing_does_not_use_pointer_fallback(lib_client, monkeypatch):
    """BREATHING 은 발행 원장 그대로 — 포인터 폴백은 소유 행에만 적용된다."""
    monkeypatch.setattr(asset_url_refresh, "sign_object", _sign_only(PACKED_PATH))
    _publish_breathing()
    _record_pointer("BREATHING", PACKED_PATH)

    entry = _by_motion(_get(lib_client).json()["motions"], "BREATHING")
    assert entry["access"]["state"] == "unavailable"
    assert entry["url"] is None
    assert entry["provenance"]["source"] == "pet_motion_publications"


# ── 폐기된 소유권 ────────────────────────────────────────────────────────────


def test_revoked_ownership_is_excluded(lib_client: ASGITestClient):
    _record_asset("action:COME_CLOSER", "job4")
    asset = _run(owned_assets.list_for_pet(USER, PET))[0]
    asset.revoked_at = datetime.now(timezone.utc)

    resp = _get(lib_client)
    assert resp.json()["motions"] == []


# ── 스토리지 객체 누락 ───────────────────────────────────────────────────────


def test_missing_storage_object_is_marked_unavailable(lib_client: ASGITestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(asset_url_refresh, "sign_object", lambda obj: None)
    _publish_breathing()

    resp = _get(lib_client)
    entry = _by_motion(resp.json()["motions"], "BREATHING")

    assert entry["access"]["state"] == "unavailable"
    assert entry["url"] is None
    # 그래도 소유/발행 기록 자체는 계속 보인다 — 사라지지 않는다.
    assert entry["ownership"]["type"] == "included"


# ── 타인 접근 거부 ───────────────────────────────────────────────────────────


def test_cross_user_access_is_denied(lib_client: ASGITestClient):
    pet_registry._MOCK_PETS[PET] = {
        "pet_id": PET,
        "user_id": USER,
        "content_id": None,
        "breathing_bucket": None,
        "breathing_object_path": None,
        "source": "app",
        "background_baked": False,
        "created_at": "2026-01-01T00:00:00+00:00",
    }

    resp = _get(lib_client, user=OTHER_USER)
    assert resp.status_code == 403


def test_cross_user_access_is_denied_for_premium_only_pet(lib_client: ASGITestClient):
    """
    BREATHING 등록이 없는(프리미엄만 있는) 펫도 타인 접근이 막힌다 (TOFU).

    TOFU 는 "최초 사용자"를 소유자로 귀속한다 — 그래서 진짜 소유자가 먼저 한 번
    조회해 선점한 뒤에야 타인의 시도를 의미 있게 거절할 수 있다.
    """
    _record_asset("action:COME_CLOSER", "job5")

    owner_first = _get(lib_client, user=USER)
    assert owner_first.status_code == 200

    resp = _get(lib_client, user=OTHER_USER)
    assert resp.status_code == 403
