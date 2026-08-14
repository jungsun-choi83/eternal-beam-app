"""
유료 제출 전 안전장치 (Phase 15C).

실제 사고를 재현 방지한다: COME_CLOSER 첫 시도에서 fal 제출은 성공해 과금됐는데
바로 다음 줄의 register_generation_job 이 `attempt` 컬럼 없음으로 실패했고,
external_id 를 아무 데도 남기지 않아 복구가 불가능했다.

  1) 필수 컬럼이 없으면 **프로바이더를 아예 호출하지 않는다**
  2) 제출 직후, DB 쓰기 **전에** external_id 를 로그로 남긴다

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.services import generation_safety as gsafe
from backend.services import generated_motions_service as gms
from backend.services.generation_safety import (
    REQUIRED_COLUMNS,
    SchemaNotReadyError,
    verify_reliability_schema,
)
from backend.services.video_generation import submit_generation


@pytest.fixture(autouse=True)
def _reset():
    gsafe.reset_schema_cache()
    yield
    gsafe.reset_schema_cache()


class FakeSupabase:
    """지정한 컬럼만 존재하는 가짜 Supabase."""

    def __init__(self, missing: set[str] | None = None):
        self.missing = missing or set()
        self.queried: list[str] = []

    def table(self, name: str):
        outer = self

        class T:
            def select(self, col: str):
                key = f"{name}.{col}"
                outer.queried.append(key)
                if key in outer.missing:
                    raise RuntimeError(f"PGRST204: could not find '{col}' of '{name}'")
                return self

            def limit(self, n):
                return self

            def execute(self):
                return type("R", (), {"data": []})()

        return T()


def _install(monkeypatch, missing=None):
    fake = FakeSupabase(missing)
    monkeypatch.setattr(gms, "_use_db", lambda: True)
    monkeypatch.setattr(gms, "_supabase", lambda: fake)
    return fake


# ── 1. 스키마 프리플라이트 ──────────────────────────────────────────────────


def test_required_columns_cover_the_reliability_path():
    assert REQUIRED_COLUMNS["motion_generation_jobs"] == (
        "candidate_url", "attempt", "validation", "promoted_at"
    )
    assert REQUIRED_COLUMNS["credit_generation_sessions"] == ("refunded_at", "finalized_at")


def test_all_columns_present_passes(monkeypatch):
    fake = _install(monkeypatch)
    ok, missing = verify_reliability_schema(use_cache=False)
    assert ok is True and missing == ()
    # 6개 컬럼을 실제로 조회했는지
    assert len(fake.queried) == 6


@pytest.mark.parametrize(
    "missing_col",
    [
        "motion_generation_jobs.candidate_url",
        "motion_generation_jobs.attempt",
        "motion_generation_jobs.validation",
        "motion_generation_jobs.promoted_at",
        "credit_generation_sessions.refunded_at",
        "credit_generation_sessions.finalized_at",
    ],
)
def test_any_missing_column_fails_preflight(monkeypatch, missing_col):
    _install(monkeypatch, missing={missing_col})
    ok, missing = verify_reliability_schema(use_cache=False)
    assert ok is False
    assert missing_col in missing


def test_ensure_raises_with_the_missing_list(monkeypatch):
    _install(monkeypatch, missing={"motion_generation_jobs.attempt"})
    with pytest.raises(SchemaNotReadyError) as ei:
        gsafe.ensure_reliability_schema()
    assert "motion_generation_jobs.attempt" in str(ei.value)
    assert ei.value.missing == ("motion_generation_jobs.attempt",)


def test_mock_mode_passes_without_db(monkeypatch):
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    ok, missing = verify_reliability_schema(use_cache=False)
    assert ok is True and missing == ()


def test_result_is_cached(monkeypatch):
    fake = _install(monkeypatch)
    verify_reliability_schema()
    n = len(fake.queried)
    verify_reliability_schema()
    assert len(fake.queried) == n, "캐시가 동작해야 매 제출마다 DB 를 때리지 않는다"
    gsafe.reset_schema_cache()
    verify_reliability_schema()
    assert len(fake.queried) > n


# ── 2. 컬럼이 없으면 프로바이더를 호출하지 않는다 (핵심) ────────────────────


def test_missing_column_blocks_provider_submission(monkeypatch):
    """이것이 사고의 핵심 — 돈이 나가기 전에 멈춰야 한다."""
    _install(monkeypatch, missing={"motion_generation_jobs.attempt"})
    called = {"luma": 0, "wan": 0}

    async def luma_spy(*a, **k):
        called["luma"] += 1
        return "should-not-happen"

    class _Sub:
        request_id = "x"
        status_url = "s"
        response_url = "r"

    async def wan_spy(*a, **k):
        called["wan"] += 1
        return _Sub()

    import backend.services.luma_service as ls
    import backend.services.wan_service as ws

    monkeypatch.setattr(ls, "create_generation", luma_spy)
    monkeypatch.setattr(ws, "create_generation", wan_spy)

    for provider in ("luma", "wan_turbo"):
        with pytest.raises(SchemaNotReadyError):
            asyncio.run(submit_generation("i", "p", provider=provider))

    assert called == {"luma": 0, "wan": 0}, "프로바이더가 단 한 번도 호출되면 안 된다"


def test_all_columns_present_allows_submission(monkeypatch):
    _install(monkeypatch)
    called = {"n": 0}

    async def luma_ok(*a, **k):
        called["n"] += 1
        return "gen-1"

    import backend.services.luma_service as ls

    monkeypatch.setattr(ls, "create_generation", luma_ok)
    job = asyncio.run(submit_generation("i", "p", provider="luma"))
    assert called["n"] == 1
    assert job.external_id == "gen-1"


# ── 3. 제출 성공 + DB 실패 → external_id 는 남는다 ──────────────────────────


def test_receipt_is_logged_before_db_write(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        gsafe.log_submission_receipt(
            provider="wan_turbo",
            provider_model="fal-ai/wan/v2.2-a14b/image-to-video/turbo",
            external_id="req-abc-123",
            session_id="sess-9",
            action_id="COME_CLOSER",
        )
    t = caplog.text
    assert "SUBMISSION RECEIPT" in t
    for needle in ("wan_turbo", "req-abc-123", "sess-9", "COME_CLOSER"):
        assert needle in t


def test_external_id_survives_db_registration_failure(monkeypatch, caplog):
    """
    사고 재현: 제출은 성공, register_generation_job 은 실패.
    그래도 external_id 가 로그에 남아 복구 조사가 가능해야 한다.
    """
    from backend.routers import dev_premium
    from backend.services.video_generation import SubmittedJob

    monkeypatch.setenv("ENABLE_DEV_PREMIUM_TRIGGER", "1")
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    gms._MOCK_SESSIONS.clear()

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        return SubmittedJob(provider=provider, external_id="rescue-me-42", model="MODEL-X")

    async def fake_keyframe(url, session_id):
        return "https://cdn/black.jpg"

    async def boom_register(*a, **k):
        raise RuntimeError("PGRST204: could not find the 'attempt' column")

    monkeypatch.setattr(dev_premium, "submit_generation", fake_submit)
    monkeypatch.setattr(dev_premium, "prepare_black_plate_keyframe", fake_keyframe)
    monkeypatch.setattr(dev_premium.motions_svc, "register_generation_job", boom_register)

    class _Req:
        base_url = "https://hook.test/"

    body = dev_premium.ComeCloserRequest(
        user_id="u1", pet_image_url="https://cdn/c.png",
        selected_place_id="snow_forest", pet_id="p1",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="attempt"):
            asyncio.run(dev_premium.trigger_come_closer(_Req(), body))

    assert "SUBMISSION RECEIPT" in caplog.text
    assert "rescue-me-42" in caplog.text, "DB 가 터져도 request_id 는 남아야 한다"
    assert "COME_CLOSER" in caplog.text


def test_receipt_precedes_registration_in_source():
    """소스 순서 확인 — 영수증이 DB 쓰기보다 먼저여야 한다."""
    from pathlib import Path

    for f, reg in (
        ("routers/dev_premium.py", "register_generation_job"),
        ("services/credit_luma_batch.py", "register_generation_job"),
    ):
        src = (Path(__file__).resolve().parents[1] / f).read_text(encoding="utf-8")
        assert "log_submission_receipt" in src, f
        assert src.index("log_submission_receipt(") < src.index(reg), (
            f"{f}: 영수증 로그가 DB 등록보다 뒤에 있으면 사고를 막지 못한다"
        )
