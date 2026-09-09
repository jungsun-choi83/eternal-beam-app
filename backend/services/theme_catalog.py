"""
테마 카탈로그 — 무엇이 무료이고, 무엇이 유료이며, 얼마인가.

**여기서 가격을 발명하지 않는다.** 이것이 이 모듈의 존재 이유다.

기존 `theme_prices.py` 에 $2.99 가 있지만 그건 **레거시 PayPal 경로**의 USD
가격이고, 그 경로는 이번 단계에서 건드리지 않는다. 새 스토어는 KRW 로 Toss 를
쓰는데, "$2.99 니까 4,000원쯤" 같은 환산은 **가격 결정**이지 구현이 아니다.
PM 이 정하지 않은 값을 코드가 정하면 그 숫자가 그대로 매출이 된다.

그래서 가격은 **설정에서만** 온다. 설정이 없으면 그 테마는 팔리지 않는다
(구매 시도가 THEME_PRICE_NOT_SET 으로 거절된다). 화면에는 "준비 중"으로 나온다.
가격표가 비어 있는 것이 잘못된 가격표보다 낫다.

── 어떤 테마가 유료인가도 PM 소관이다 ────────────────────────────────────────
기본값은 프론트 themes.ts 의 `premium` 플래그를 그대로 따른다(aurora / sunset /
ocean_deep / custom_photo_bg). 지금 무료인 테마를 유료로 바꾸는 것은 상업적
결정이므로 코드가 아니라 THEME_PAID_KEYS 로 한다 — 요구사항의 예시(Beach/Snow
Forest 유료)는 그 설정으로 표현할 수 있고, 코드 변경이 필요 없다.

**무료 테마는 언제나 결제 없이 쓸 수 있다.** 유료 목록에 없으면 무료다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

#: 프론트 themes.ts 의 themeKey 전체. 순서는 표시 순서와 무관하다.
#: ⚠️ 숫자 id 를 쓰지 않는다 — themes.ts 에서 beach 와 custom_photo_bg 가 둘 다
#: id 9 라 getMemorialTheme(9) 이 beach 를 돌려준다(기존 결함). key 는 충돌이 없다.
ALL_THEME_KEYS: tuple[str, ...] = (
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

#: themes.ts 의 `premium: true` 와 같은 집합. 기본 유료 목록이다.
DEFAULT_PAID_KEYS: frozenset[str] = frozenset(
    {"aurora", "sunset", "ocean_deep", "custom_photo_bg"}
)

#: 지원 통화. Toss 는 KRW 다.
CURRENCY = "KRW"


class ThemeCatalogError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def normalize_theme_key(raw: str | None) -> str:
    """theme_key 검증 → canonical. 모르는 키는 거절한다."""
    k = (raw or "").strip().lower()
    if not k:
        raise ThemeCatalogError("THEME_KEY_REQUIRED", "theme_key 가 필요합니다.")
    if k not in ALL_THEME_KEYS:
        raise ThemeCatalogError(
            "THEME_UNKNOWN", f"{k} 는 알 수 없는 테마입니다.", status=404
        )
    return k


def paid_theme_keys() -> frozenset[str]:
    """
    유료 테마 집합. THEME_PAID_KEYS 로 덮어쓸 수 있다 (쉼표 구분).

    빈 문자열을 명시하면 "전부 무료"가 된다 — 되돌리기 스위치다.
    설정이 **없으면** themes.ts 의 premium 플래그를 따른다.
    """
    raw = os.getenv("THEME_PAID_KEYS")
    if raw is None:
        return DEFAULT_PAID_KEYS
    keys = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return frozenset(k for k in keys if k in ALL_THEME_KEYS)


def is_free(theme_key: str) -> bool:
    """무료인가. **유료 목록에 없으면 무료다** — 기본이 무료 쪽이다."""
    return theme_key not in paid_theme_keys()


def price_krw(theme_key: str) -> Optional[int]:
    """
    이 테마의 KRW 가격. **설정되지 않았으면 None** (= 팔 수 없다).

    None 을 0 으로 떨어뜨리지 않는 것이 핵심이다. 0 으로 두면 "무료로 팔린다" —
    가격 미설정이 곧 전량 무료 배포가 된다. 팔리지 않는 편이 안전하다.

    환경변수: THEME_PRICE_<THEME_KEY 대문자>_KRW
        예) THEME_PRICE_AURORA_KRW=4900
    """
    if is_free(theme_key):
        return 0
    raw = (os.getenv(f"THEME_PRICE_{theme_key.upper()}_KRW") or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    # 0 이하는 "무료"가 아니라 설정 실수로 본다. 무료로 만들려면 THEME_PAID_KEYS
    # 에서 빼야 하고, 가격 0 이 우연히 그 뜻이 되면 안 된다.
    return v if v > 0 else None


def entitlement_ttl_days() -> Optional[int]:
    """
    구매한 테마가 몇 일간 유효한가. **기본 None = 영구.**

    ⚠️ PM 미결. 목표 UX 가 "OWNED" 이므로 영구가 기본이고, 기간제가 새로운
    발명이다. PM 이 기간제를 정하면 이 값만 채우면 된다 — 저장 스키마(expires_at)
    는 이미 준비돼 있다.
    """
    raw = (os.getenv("THEME_ENTITLEMENT_TTL_DAYS") or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if v > 0 else None


@dataclass(frozen=True)
class ThemeOffer:
    """카탈로그 한 줄. 화면이 [Use] / [Buy] / 준비 중을 정하는 데 필요한 전부."""

    theme_key: str
    free: bool
    #: 유료인데 가격이 설정되지 않았으면 None → 살 수 없다.
    price_krw: Optional[int]
    currency: str = CURRENCY

    @property
    def purchasable(self) -> bool:
        """지금 결제를 시작해도 되는가. 무료는 살 필요가 없으므로 False 다."""
        return (not self.free) and self.price_krw is not None and self.price_krw > 0


def offer(theme_key: str) -> ThemeOffer:
    k = normalize_theme_key(theme_key)
    free = is_free(k)
    return ThemeOffer(theme_key=k, free=free, price_krw=0 if free else price_krw(k))


def catalog() -> list[ThemeOffer]:
    """전체 카탈로그. 표시 순서는 프론트(themes.ts)가 정한다."""
    return [offer(k) for k in ALL_THEME_KEYS]
