"""
테마 카탈로그 회귀 방지 — 어떤 테마가 존재하고, 그중 무엇이 유료인가는 PM 소관이다.

우발적으로 새 테마 키가 추가되거나(예: "spring"), DEFAULT_PAID_KEYS 가 조용히
바뀌면 그 자체가 상업적 결정이 된다. 이 테스트는 그 목록을 고정한다 — 목록을
바꾸려면 이 테스트도 함께, 의도적으로 바꿔야 한다.
"""

from backend.services import theme_catalog


def test_all_theme_keys_matches_known_catalog():
    assert theme_catalog.ALL_THEME_KEYS == (
        "fresh_forest",
        "beach",
        "snow_forest",
        "celestial",
        "golden_meadow",
        "starlight",
        "aurora",
        "sunset",
        "ocean_deep",
        "custom_photo_bg",
    )


def test_default_paid_keys_unchanged():
    assert theme_catalog.DEFAULT_PAID_KEYS == frozenset(
        {"aurora", "sunset", "ocean_deep", "custom_photo_bg"}
    )


def test_no_price_is_invented_for_a_paid_theme_without_env_config(monkeypatch):
    monkeypatch.delenv("THEME_PRICE_AURORA_KRW", raising=False)
    monkeypatch.delenv("THEME_PAID_KEYS", raising=False)
    assert theme_catalog.price_krw("aurora") is None
    offer = theme_catalog.offer("aurora")
    assert offer.free is False
    assert offer.price_krw is None
    assert offer.purchasable is False
