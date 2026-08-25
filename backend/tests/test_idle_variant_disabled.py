"""
`/generate-idle-variant` — 보호되지 않은 유료 경로는 **닫아 둔다** (Phase 20.1).

── 왜 닫는가 ────────────────────────────────────────────────────────────────
이 엔드포인트는 아직 scene_generation_jobs 예약을 쓰지 않는다. provider_job_id 를
남기지 않으므로 이미 제출한 유료 작업을 되찾을 방법이 없고, 동기식이라
타임아웃·새로고침·502 재시도가 각각 새 유료 작업이 된다 —
`/generate-pet-video` 가 고치기 전에 갖고 있던 바로 그 노출이다.

프론트 패널은 이미 VITE_ENABLE_LUMA 로 꺼져 있지만 **엔드포인트 자체는**
ENABLE_GENERATE_API=1 인 배포에서 그대로 열려 있었다(BREATHING 이 같은 라우터에
있어 그 플래그를 끌 수 없다). 경로를 아는 사람은 누구나 보호 없는 유료 생성을
반복시킬 수 있었다.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from backend.services import generation_endpoints as flags


SRC = pathlib.Path("backend/routers/generate.py").read_text()


def _body() -> str:
    i = SRC.index('@router.post("/generate-idle-variant")')
    return SRC[i:]


# ── 기본값: 닫힘 ─────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    """
    **설정하지 않으면 닫힌다.** 보호되지 않은 유료 경로의 기본값은 닫힘이어야 한다 —
    켜는 것은 의식적인 행동이고, 열려 있는 것은 사고다.
    """
    monkeypatch.delenv("ENABLE_IDLE_VARIANT_API", raising=False)
    assert flags.idle_variant_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off", "  "])
def test_stays_closed_for_non_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ENABLE_IDLE_VARIANT_API", value)
    assert flags.idle_variant_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_opens_only_on_explicit_optin(monkeypatch, value):
    monkeypatch.setenv("ENABLE_IDLE_VARIANT_API", value)
    assert flags.idle_variant_enabled() is True


def test_rejection_carries_an_explicit_code():
    assert flags.DISABLED_STATUS == 503
    assert flags.DISABLED_DETAIL["code"] == "GENERATION_ENDPOINT_DISABLED"
    assert "/api/generate-idle-variant" in flags.DISABLED_DETAIL["endpoint"]
    assert flags.DISABLED_DETAIL["message"]


# ── 가드가 유료 작업보다 **먼저** 온다 ──────────────────────────────────────


def test_guard_runs_before_any_paid_or_costly_work():
    """
    **핵심 검사.** 가드가 한 줄이라도 뒤로 밀리면 비활성 상태에서도 파일 읽기·
    누끼·스토리지 업로드가 돌고, 최악의 경우 유료 제출에 닿는다.
    """
    body = _body()
    guard = body.index("_idle_variant_enabled()")

    paid = re.search(r"await generate_idle_variant\(", body)
    assert paid is not None, "유료 파이프라인 호출을 찾지 못했다"
    assert guard < paid.start(), "가드보다 유료 제출이 먼저다"

    for label, needle in (
        ("파일 읽기", "await file.read()"),
        ("누끼", "_cutout_to_dog_bytes"),
        ("스토리지 업로드", "upload_asset_to_storage"),
    ):
        pos = body.find(needle)
        assert pos > guard, f"가드보다 {label} 가 먼저다"


def test_guard_precedes_even_template_validation():
    """
    가드는 template_key 검증보다도 먼저다. 그 검증이 먼저 오면 비활성 상태에서도
    400/503 이 상황에 따라 갈리고, "닫혀 있다"는 사실이 흐려진다.
    """
    body = _body()
    assert body.index("_idle_variant_enabled()") < body.index("is_known_template(")


# ── 보호된 경로는 계속 열려 있어야 한다 ─────────────────────────────────────


def test_breathing_endpoint_is_not_gated_by_this_flag():
    """
    같은 라우터의 `/generate-pet-video` 는 예약 보호가 끝났다. 이 플래그로 함께
    닫히면 제품이 멈춘다 — 그래서 ENABLE_GENERATE_API 로 대신할 수 없었다.
    """
    i = SRC.index('@router.post("/generate-pet-video")')
    j = SRC.index('@router.post("/generate-idle-variant")')
    pet_video = SRC[i:j]
    assert "_idle_variant_enabled()" not in pet_video


def test_protected_path_still_reserves_before_submitting():
    """회귀 방지 — BREATHING 의 fail-closed 보호가 그대로 남아 있는가."""
    assert "except scene_generation_jobs.IdempotencyUnavailableError" in SRC
    assert SRC.index("scene_generation_jobs.reserve(") < SRC.index(
        "create_generation_and_get_video_url("
    )


def test_idle_variant_has_no_reservation_yet_which_is_why_it_is_closed():
    """
    이 검사는 **문서이자 알람**이다. 언젠가 이 경로에 예약이 붙으면 여기서 실패하고,
    그때가 플래그와 가드를 지울 시점이다.
    """
    body = _body()
    assert "scene_generation_jobs.reserve(" not in body, (
        "예약이 붙었다 — 이제 ENABLE_IDLE_VARIANT_API 가드를 제거하고 "
        "/generate-pet-video 와 같은 fail-closed 처리를 적용하라"
    )
