"""
Memorial 테마 가격표 — src/components/memorial/themes.ts 와 반드시 동일하게 유지.

프론트는 theme_key(문자열, 예: "aurora")로 결제를 요청하므로, 서버도 theme_key
기준으로 가격을 검증한다. 클라이언트가 보낸 금액을 그대로 믿지 않고 여기서
재확인한 뒤 PayPal 주문을 생성한다.
"""

from __future__ import annotations

# themes.ts의 premium 테마 3종 + "내 사진으로 나만의 배경 만들기"(custom_photo_bg).
# 나머지(snow_forest/celestial/golden_meadow/starlight/fresh_forest)는 무료라서
# 여기 없으면 "0.00"으로 취급한다.
#
# custom_photo_bg: 배경_인페인팅_파이프라인(SAM2 인페인팅 → Luma 배경 애니메이션,
# backend/services/background_video_pipeline.py)의 결과물 가격. 기존 프리미엄
# 테마(aurora/sunset/ocean_deep)와 동일한 $2.99로 맞춤 — 이 기능도 "미리 만들어진
# 배경 영상 1개를 잠금 해제"한다는 점에서 프리미엄 테마와 동일한 가치 단위이고,
# 사용자가 별도로 더 비싼 가격을 요구하지 않았으므로 기존 티어를 그대로 따름.
PREMIUM_THEME_PRICES_USD: dict[str, str] = {
    "aurora": "2.99",
    "sunset": "2.99",
    "ocean_deep": "2.99",
    "custom_photo_bg": "2.99",
}


def get_theme_price_usd(theme_key: str) -> str:
    """theme_key → USD 가격 문자열("0.00" | "2.99" 등). 모르는 키도 무료로 취급."""
    return PREMIUM_THEME_PRICES_USD.get((theme_key or "").strip().lower(), "0.00")


def is_free_theme(theme_key: str) -> bool:
    return get_theme_price_usd(theme_key) == "0.00"
