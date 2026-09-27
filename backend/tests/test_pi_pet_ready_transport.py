"""
Pi /demo/pet-ready → UDP 전송 계약 — packed_url 통과 검증.

python/pi_sse_server.py 는 backend 패키지 밖이라 sys.path 로 직접 올린다(표준
라이브러리만 쓰므로 import 부작용이 없다). 네트워크·소켓은 건드리지 않는다.

핵심 계약:
  * packed_url 은 **추가** 필드 — idle_url/video_url 을 절대 덮어쓰지 않는다.
  * packed_url 이 없으면 본문은 예전과 바이트 단위로 동일해야 한다.
  * 모르는 키는 화이트리스트에서 걸러진다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PY_DIR = Path(__file__).resolve().parents[2] / "python"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from pi_sse_server import build_pet_ready_base  # noqa: E402


def test_without_packed_url_payload_is_unchanged():
    base = build_pet_ready_base(
        {"idle_url": "https://x/idle.mp4", "cutout_url": "https://x/c.png"}, "c1"
    )
    assert base == {
        "content_id": "c1",
        "source": "app_idle_ready",
        "idle_url": "https://x/idle.mp4",
        "video_url": "https://x/idle.mp4",
        "cutout_url": "https://x/c.png",
    }
    assert "packed_url" not in base


def test_packed_url_is_additive_and_does_not_replace_idle():
    base = build_pet_ready_base(
        {"idle_url": "https://x/idle.mp4", "packed_url": "https://x/idle_packed.mp4"}, "c1"
    )
    assert base["packed_url"] == "https://x/idle_packed.mp4"
    # 구형 S23 빌드가 읽는 두 키는 그대로 원본 URL 이어야 한다.
    assert base["idle_url"] == "https://x/idle.mp4"
    assert base["video_url"] == "https://x/idle.mp4"


def test_packed_url_alone_is_forwarded():
    base = build_pet_ready_base({"packed_url": "https://x/a_packed.mp4"}, "c1")
    assert base["packed_url"] == "https://x/a_packed.mp4"
    assert "idle_url" not in base


@pytest.mark.parametrize("value", ["", "   ", None, 0, False])
def test_empty_packed_url_creates_no_key(value):
    base = build_pet_ready_base({"idle_url": "https://x/i.mp4", "packed_url": value}, "c1")
    assert "packed_url" not in base


# ── Device D1 — Phase 7 신원/포맷 필드 ──────────────────────────────────────


def test_phase7_fields_pass_through_unchanged():
    """pet_id / motion_id / delivery_format 은 값 그대로 UDP 본문에 실린다."""
    base = build_pet_ready_base(
        {
            "packed_url": "https://x/breathing_packed.mp4?token=f",
            "pet_id": "pet_c1",
            "motion_id": "BREATHING",
            "delivery_format": "packed_alpha",
        },
        "c1",
    )
    assert base["pet_id"] == "pet_c1"
    assert base["motion_id"] == "BREATHING"
    assert base["delivery_format"] == "packed_alpha"
    # Pi 는 판정하지 않는다 — 값이 변형되지 않고 그대로다.
    assert base["packed_url"] == "https://x/breathing_packed.mp4?token=f"


def test_legacy_payload_stays_byte_identical_without_phase7_fields():
    """새 필드를 보내지 않는 구형 브라우저의 본문은 예전과 완전히 같다."""
    base = build_pet_ready_base({"idle_url": "https://x/idle.mp4"}, "c1")
    assert base == {
        "content_id": "c1",
        "source": "app_idle_ready",
        "idle_url": "https://x/idle.mp4",
        "video_url": "https://x/idle.mp4",
    }
    for key in ("pet_id", "motion_id", "delivery_format"):
        assert key not in base


@pytest.mark.parametrize("key", ["pet_id", "motion_id", "delivery_format"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_phase7_fields_create_no_keys(key, value):
    base = build_pet_ready_base({"idle_url": "https://x/i.mp4", key: value}, "c1")
    assert key not in base


def test_unknown_keys_are_still_dropped():
    """화이트리스트 밖 키(테마 포함)는 pet-ready 본문에 절대 실리지 않는다."""
    base = build_pet_ready_base(
        {
            "idle_url": "https://x/i.mp4",
            "theme_id": "7",           # 테마는 /demo/play 의 몫 — 분리 계약
            "qa_decision": "PASS",     # Pi 는 QA 를 추측/전달하지 않는다
            "evil": "x",
        },
        "c1",
    )
    for key in ("theme_id", "qa_decision", "evil"):
        assert key not in base


def test_whitespace_is_trimmed():
    base = build_pet_ready_base({"packed_url": "  https://x/p_packed.mp4  "}, "c1")
    assert base["packed_url"] == "https://x/p_packed.mp4"


def test_unknown_keys_are_dropped():
    base = build_pet_ready_base(
        {"idle_url": "https://x/i.mp4", "theme_id": "snow_forest", "junk": 1}, "c1"
    )
    assert "theme_id" not in base, "테마/배경 경로는 pet-ready 로 새어 들어오면 안 된다"
    assert "junk" not in base


def test_udp_commands_carry_packed_url():
    """_handle_pet_ready 는 base 를 nfc_match/idle 두 UDP 명령에 그대로 펼친다."""
    base = build_pet_ready_base(
        {"idle_url": "https://x/i.mp4", "packed_url": "https://x/i_packed.mp4"}, "c1"
    )
    cmds = [{**base, "event": e} for e in ("nfc_match", "idle")]
    for cmd in cmds:
        assert cmd["packed_url"] == "https://x/i_packed.mp4"
        assert cmd["video_url"] == "https://x/i.mp4"
    assert [c["event"] for c in cmds] == ["nfc_match", "idle"]
