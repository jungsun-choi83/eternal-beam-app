"""
10개 슬롯 배경(장소) × 4개 행동 = 40개 Luma 시나리오 정의.

- Supabase 테이블 없이도 동작 (딕셔너리만으로 프롬프트 조립 가능).
- `theme_key` 는 Unity / 앱 memorialThemes 와 맞춤.
- 08~10 은 커스텀 장소 플레이스홀더 (프롬프트만 교체하면 확장).
"""

from __future__ import annotations

from typing import TypedDict


class PlaceDef(TypedDict):
    """슬롯 배경 하나의 설명."""

    slot: int
    theme_key: str
    name_ko: str
    name_en: str
    prompt: str


# ── 10 places (slot backgrounds) ───────────────────────────────────────────
PLACES: dict[str, PlaceDef] = {
  "01_snow_forest": {
    "slot": 1,
    "theme_key": "snow_forest",
    "name_ko": "눈 숲",
    "name_en": "Snow Forest",
    "prompt": (
      "a quiet snow-covered pine forest with soft falling snow, "
      "gentle winter light through trees, peaceful depth"
    ),
  },
  "02_celestial": {
    "slot": 2,
    "theme_key": "celestial",
    "name_ko": "천상",
    "name_en": "Celestial",
    "prompt": (
      "a serene celestial meadow under starlight and soft nebula glow, "
      "ethereal mist, infinite calm horizon"
    ),
  },
  "03_golden_meadow": {
    "slot": 3,
    "theme_key": "golden_meadow",
    "name_ko": "황금 들판",
    "name_en": "Golden Meadow",
    "prompt": (
      "a sunlit golden meadow at late afternoon, warm breeze in tall grass, "
      "rolling hills, cinematic warmth"
    ),
  },
  "04_starlight": {
    "slot": 4,
    "theme_key": "starlight",
    "name_ko": "별빛",
    "name_en": "Starlight",
    "prompt": (
      "a moonlit clearing with countless stars reflected on still water, "
      "silver light, tranquil night atmosphere"
    ),
  },
  "05_aurora": {
    "slot": 5,
    "theme_key": "aurora",
    "name_ko": "오로라",
    "name_en": "Aurora",
    "prompt": (
      "northern lights over a frozen lake, green and violet aurora ribbons, "
      "crisp arctic air, wide cinematic sky"
    ),
  },
  "06_sunset": {
    "slot": 6,
    "theme_key": "sunset",
    "name_ko": "일몰",
    "name_en": "Sunset",
    "prompt": (
      "ocean sunset beach with orange and rose sky, gentle waves, "
      "long shadows, romantic golden hour"
    ),
  },
  "07_ocean_deep": {
    "slot": 7,
    "theme_key": "ocean_deep",
    "name_ko": "깊은 바다",
    "name_en": "Ocean Deep",
    "prompt": (
      "underwater-inspired deep blue cavern opening to calm sea, "
      "caustic light rays, mysterious yet safe depth"
    ),
  },
  "08_cherry_blossom": {
    "slot": 8,
    "theme_key": "cherry_blossom",
    "name_ko": "벚꽃 터널",
    "name_en": "Cherry Blossom",
    "prompt": (
      "a cherry blossom tunnel path with petals drifting in the wind, "
      "soft pink light, spring serenity (custom slot 8)"
    ),
  },
  "09_autumn_maple": {
    "slot": 9,
    "theme_key": "autumn_maple",
    "name_ko": "단풍길",
    "name_en": "Autumn Maple",
    "prompt": (
      "a winding autumn maple lane with red and gold leaves, "
      "misty morning light, nostalgic warmth (custom slot 9)"
    ),
  },
  "10_christmas": {
    "slot": 10,
    "theme_key": "christmas",
    "name_ko": "크리스마스",
    "name_en": "Christmas",
    "prompt": (
      "a cozy festive living room with tree lights and fireplace glow, "
      "warm holiday atmosphere (custom slot 10)"
    ),
  },
}

# ── 4 fixed action recipes ───────────────────────────────────────────────────
ACTIONS: dict[str, str] = {
  "IDLE": (
    "가만히 앉아서 눈만 깜빡이는 평온한 모습, 자연스러운 숨쉬기 동작"
  ),
  "TOUCH": (
    "누군가 쓰다듬는 느낌에 기분 좋아하며 고개를 부비는 동작, 꼬리를 살랑거림"
  ),
  "VOICE": (
    "주인의 목소리를 듣고 귀를 쫑긋하며 소리 나는 쪽을 쳐다보는 동작"
  ),
  "NFC": (
    "익숙한 장소에 온 것을 알아채고 주위를 두리번거리며 냄새를 맡는 동작"
  ),
}

# English motion text for Luma (API works best in English)
ACTIONS_EN: dict[str, str] = {
  "IDLE": (
    "dog alone, sitting calmly, blinking, natural breathing, no person, no leash"
  ),
  "TOUCH": (
    "dog alone reacting happily as if petted, nuzzle and tail wag, "
    "no visible human hand or arm, no leash"
  ),
  "VOICE": (
    "dog alone perking ears toward off-camera voice, turning head, no person in frame"
  ),
  "NFC": (
    "dog alone in familiar place, looking around, gentle sniffing, no owner, no leash"
  ),
}


def all_scenario_keys() -> list[tuple[str, str]]:
  """(place_key, action_key) 40개 목록."""
  out: list[tuple[str, str]] = []
  for place_key in PLACES:
    for action_key in ACTIONS:
      out.append((place_key, action_key))
  return out


def scenario_count() -> int:
  return len(PLACES) * len(ACTIONS)


CREDIT_COST_PER_PLACE_SET = 4
ACTION_ORDER: tuple[str, ...] = ("IDLE", "TOUCH", "VOICE", "NFC")


def resolve_place_id(selected_place_id: str) -> str:
  """
  NFC 슬롯·앱 테마 ID → PLACES 딕셔너리 키.

  허용 형식:
  - place_key: "01_snow_forest"
  - theme_key: "snow_forest"
  - slot 번호: "1" … "10"
  """
  raw = (selected_place_id or "").strip()
  if not raw:
    raise ValueError("selected_place_id is required")
  if raw in PLACES:
    return raw
  for key, place in PLACES.items():
    if place["theme_key"] == raw:
      return key
  if raw.isdigit():
    slot = int(raw)
    for key, place in PLACES.items():
      if place["slot"] == slot:
        return key
  raise ValueError(f"Unknown place_id: {selected_place_id}")


def place_public_id(place_key: str) -> str:
  """DB·API용 장소 ID (theme_key — Unity·NFC 카드와 동일)."""
  return PLACES[place_key]["theme_key"]


def storage_object_name(place_key: str, action_key: str) -> str:
  """Supabase path segment: e.g. snow_forest_IDLE.mp4"""
  place = PLACES[place_key]
  safe_place = place["theme_key"].upper()
  return f"{safe_place}_{action_key}.mp4"
