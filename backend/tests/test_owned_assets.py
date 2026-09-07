"""
생성 자산 **영구 소유 원장**의 계약 (Phase 6).

지키려는 것 하나:

    Sleeping #1   owned
    Sleeping #2   owned      ← 셋 다 공존한다
    Sleeping #3   owned

감사에서 나온 결함: generated_motions 는 unique (user, pet, place, action) 이고
승격이 upsert 라, 같은 행동을 두 번 만들면 **두 번째가 첫 번째를 덮어썼다.**
고객이 각각 값을 낸 자산인데 하나만 남는다.

여기서 고정하는 것:
  * (user, pet, product_key) 에 유일성이 **없다**
  * 유일성은 "무엇이 만들었는가"(source_job_id)에 걸린다 — 재전송은 막고 새 구매는 통과
  * generated_motions 는 포인터로 남는다 (계속 덮어쓴다 — 그게 포인터의 일이다)
  * 레거시 자산은 credits_spent=0 / legacy_migration — **소급 과금하지 않는다**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services import owned_assets

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261005000000_owned_generated_assets.sql"

USER = "user_lib"
PET = "pet_lib"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    owned_assets.__reset_for_tests()
    yield
    owned_assets.__reset_for_tests()


def _asset(product: str, job: str, **kw) -> owned_assets.OwnedAsset:
    return owned_assets.OwnedAsset(
        user_id=kw.pop("user_id", USER),
        pet_id=kw.pop("pet_id", PET),
        product_key=product,
        video_url=kw.pop("video_url", f"https://cdn.test/{job}.mp4"),
        source_job_id=job,
        **kw,
    )


# ── 버전 공존 ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_multiple_versions_of_the_same_idle_coexist():
    """**이 파일의 이유.** 같은 상품을 세 번 만들면 세 자산이 남는다."""
    for n in (1, 2, 3):
        await owned_assets.record(_asset("idle:SLEEPING", f"job_sleep_{n}"))

    assert await owned_assets.count_for_product(USER, PET, "idle:SLEEPING") == 3
    urls = {a.video_url for a in await owned_assets.list_for_pet(USER, PET)}
    assert len(urls) == 3, "버전들이 같은 파일을 가리킨다"


@pytest.mark.anyio
async def test_different_products_coexist_too():
    await owned_assets.record(_asset("idle:SLEEPING", "j1"))
    await owned_assets.record(_asset("idle:SLEEPING", "j2"))
    await owned_assets.record(_asset("action:PAW_WAVE", "j3"))
    await owned_assets.record(_asset("action:PAW_WAVE", "j4"))

    assert await owned_assets.count_for_product(USER, PET, "idle:SLEEPING") == 2
    assert await owned_assets.count_for_product(USER, PET, "action:PAW_WAVE") == 2
    assert len(await owned_assets.list_for_pet(USER, PET)) == 4


@pytest.mark.anyio
async def test_owning_one_does_not_block_buying_another():
    """
    소유 개수는 **게이트가 아니다.** 몇 개를 갖고 있든 또 살 수 있다 — 그것이
    "고객이 또 만들면 또 소유한다"는 모델의 핵심이다.
    """
    await owned_assets.record(_asset("idle:SLEEPING", "j1"))
    assert await owned_assets.count_for_product(USER, PET, "idle:SLEEPING") == 1

    assert await owned_assets.record(_asset("idle:SLEEPING", "j2")) is not None
    assert await owned_assets.count_for_product(USER, PET, "idle:SLEEPING") == 2


# ── 유일성은 "무엇이 만들었는가"에 ──────────────────────────────────────────


@pytest.mark.anyio
async def test_the_same_job_records_once():
    """웹훅 재전송이 소유 자산을 두 배로 만들지 않는다."""
    first = await owned_assets.record(_asset("idle:SLEEPING", "same_job"))
    second = await owned_assets.record(_asset("idle:SLEEPING", "same_job"))

    assert first is not None
    assert second is None, "같은 작업이 두 번 기록됐다"
    assert await owned_assets.count_for_product(USER, PET, "idle:SLEEPING") == 1


def test_the_schema_has_no_uniqueness_on_the_product():
    """
    (user, pet, product_key) 에 unique 가 생기면 이 표의 존재 이유가 사라진다.
    스키마를 직접 읽어 못박는다 — 나중에 "정리" 하다가 붙이기 쉬운 종류의 제약이다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    uniques = re.findall(r"create unique index[^;]+;", sql, re.S | re.I)

    assert uniques, "유일 인덱스가 하나도 없다 — 재전송 방어가 사라졌다"
    for u in uniques:
        body = u.lower()
        assert "product_key" not in body, f"상품에 유일성이 걸렸다:\n{u}"
    # 재전송 방어는 남아 있어야 한다.
    assert any("source_job_id" in u.lower() for u in uniques)


# ── 레거시: 소급 과금하지 않는다 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_legacy_assets_are_never_recorded_as_paid():
    await owned_assets.record(
        _asset("idle:BLINKING", "legacy:1", source=owned_assets.SOURCE_LEGACY)
    )
    a = (await owned_assets.list_for_pet(USER, PET))[0]
    assert a.credits_spent == 0
    assert a.ledger_id is None
    assert a.source == owned_assets.SOURCE_LEGACY


@pytest.mark.anyio
async def test_charging_a_legacy_asset_is_rejected():
    """
    "옛 고객에게 소급 청구하지 않는다"를 코드에서도 막는다. 스키마 제약이
    최종 방어선이지만, 여기서 걸리면 스택 트레이스가 호출부를 가리킨다.
    """
    with pytest.raises(ValueError, match="과금"):
        await owned_assets.record(
            _asset("idle:BLINKING", "legacy:2",
                   source=owned_assets.SOURCE_LEGACY, credits_spent=3)
        )


def test_the_schema_forbids_charged_legacy_rows():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "owned_assets_free_is_free" in sql
    assert "source = 'purchase' or credits_spent = 0" in sql
    # 지불했으면 원장이 설명해야 한다.
    assert "credits_spent = 0 or ledger_id is not null" in sql


def test_the_backfill_never_charges():
    """백필 SQL 이 credits_spent=0 / legacy_migration 으로만 넣는지 직접 읽는다."""
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("create or replace function public.backfill_owned_assets", 1)[1]
    insert = block.split("values", 1)[1].split(")", 1)[0]
    assert "0, null, 'legacy_migration'" in insert, insert


# ── 상품 키 규약 ────────────────────────────────────────────────────────────


def test_product_key_convention_matches_the_purchase_path():
    """
    두 곳이 갈라지면 카탈로그·원장·소유가 서로 다른 문자열로 같은 것을 가리킨다.
    """
    from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
    from backend.services import premium_purchase

    for event in IDLE_EVENTS:
        assert owned_assets.product_key_for_action(event) == f"idle:{event}"
        assert premium_purchase._product_key(premium_purchase.action_kind(event)) == f"idle:{event}"

    for action in PET_ACTIONS:
        assert owned_assets.product_key_for_action(action) == f"action:{action}"
        assert (
            premium_purchase._product_key(premium_purchase.action_kind(action))
            == f"action:{action}"
        )

    # BREATHING 은 IDLE_EVENTS 밖이지만(무료 기본 모션) 성격은 아이들이다.
    assert owned_assets.product_key_for_action("BREATHING") == "idle:BREATHING"


# ── 포인터와 원장의 역할 분리 ───────────────────────────────────────────────


def test_generated_motions_stays_the_pointer():
    """
    generated_motions 는 계속 upsert 한다 — 기기는 한 번에 하나를 재생하므로
    그것이 포인터로서 올바른 동작이다. 틀렸던 것은 그 표를 **소유의 근거**로
    쓴 것이지, 덮어쓰는 것 자체가 아니다.
    """
    src = (REPO / "backend" / "services" / "generated_motions_service.py").read_text(
        encoding="utf-8"
    )
    assert 'on_conflict="user_id,pet_id,place_id,action_id"' in src, (
        "포인터 upsert 가 사라졌다 — 기기 재생 대상이 모호해진다"
    )


def test_promotion_records_ownership_before_moving_the_pointer():
    """
    순서가 계약이다: 포인터를 먼저 옮기면, 소유 기록이 실패했을 때 옛 자산이
    소유 목록에도 포인터에도 없게 된다.
    """
    src = (REPO / "backend" / "services" / "generated_motions_service.py").read_text(
        encoding="utf-8"
    )
    body = src.split("async def promote_candidate", 1)[1].split("\nasync def ", 1)[0]
    owned_at = body.index("_record_owned_asset")
    pointer_at = body.index("_record_promoted_motion")
    assert owned_at < pointer_at, "포인터를 소유 기록보다 먼저 옮긴다"


def test_promotion_uses_a_versioned_storage_path():
    """
    고정 경로에 덮어쓰면 소유 원장에 여러 줄을 적어도 전부 같은 파일을 가리킨다 —
    "버전이 공존한다"가 기록상으로만 참이 된다.
    """
    src = (REPO / "backend" / "services" / "generated_motions_service.py").read_text(
        encoding="utf-8"
    )
    body = src.split("async def promote_candidate", 1)[1].split("\nasync def ", 1)[0]
    assert "library_object_name(" in body
    assert "job_id" in body


def test_library_object_name_differs_per_job():
    from backend.services.generated_motions_service import library_object_name

    a = library_object_name("01_snow_forest", "TOUCH", "job_a")
    b = library_object_name("01_snow_forest", "TOUCH", "job_b")
    assert a != b, "두 생성이 같은 객체에 쓴다 — 앞 버전이 지워진다"
    assert a.startswith("library/") and a.endswith(".mp4")
