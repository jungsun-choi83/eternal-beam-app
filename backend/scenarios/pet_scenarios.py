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
  # 웹 전용 프리미엄 액션. ACTION_ORDER 밖이라 4코인 세트/device sync 에 영향 없음.
  "COME_CLOSER": (
    "주인을 알아보고 다가오는 느낌 — 걷는 동작 없이 크기가 커지며 살짝 통통 뛰는 반응"
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
  "COME_CLOSER": (
    "dog alone recognising the viewer and appearing to come closer, "
    "scale increases, no walking cycle, no person, no leash"
  ),
}

#: 웹 전용 아이들 이벤트 — **ACTION_ORDER 에 넣지 않는다.**
#: 프리미엄 액션과 저장/라우팅 규칙은 같지만(테마 독립) 성격이 다르다:
#: 사용자가 유발하는 사건이 아니라 스스로 일어나는 미세한 생명감이다.
#: Phase 1A = BLINKING, Phase 2 = EAR_TWITCHING, Phase 4 = HEAD_TILTING / TAIL_WAGGING.
IDLE_EVENTS: tuple[str, ...] = (
  "BLINKING",
  "EAR_TWITCHING",
  "HEAD_TILTING",
  "TAIL_WAGGING",
)

#: 웹 전용 프리미엄 액션 — **ACTION_ORDER 에 넣지 않는다.**
#: 4코인 = IDLE+TOUCH+VOICE+NFC 계약과 /device/sync 의 4종 게이트를 그대로 두기 위함.
PET_ACTIONS: tuple[str, ...] = ("COME_CLOSER",)

#: 테마 독립 · ACTION_ORDER 밖인 모든 것. 저장 키·파일명 규칙이 동일하다.
#: 이름은 하위호환으로 유지한다 — 여러 모듈이 이 심볼을 import 하고 있다.
PREMIUM_ACTIONS: tuple[str, ...] = PET_ACTIONS + IDLE_EVENTS


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


# ── 웹 프리미엄 전용 장소 (COME_CLOSER) ─────────────────────────────────────
# PLACES 는 **레거시 기기/NFC 팩**이다 — 각 항목의 `slot` 은 NFC 카드 슬롯 1~10 과
# 1:1 이고, /device/sync·Unity·4코인 계약이 그 번호에 묶여 있다. 그래서 앱의 기본
# 테마인 fresh_forest 를 거기 넣으면 존재하지 않는 11번째 슬롯이 생기거나 기존
# 슬롯 의미가 흔들린다.
#
# 반면 COME_CLOSER 는 ACTION_ORDER 밖의 웹 전용 프리미엄 액션이고, 장소는
# **저장 키((user,pet,place,action))와 파일명**에만 쓰인다 — 프롬프트에는 장소
# 설명이 들어가지 않는다(prompt_factory 참고: 배경은 기기에서 별도 레이어).
# 그래서 슬롯 없는 별도 레지스트리로 분리한다. 이쪽에 뭘 추가해도 기기/NFC 는
# 영향을 받지 않는다.
class WebPlaceDef(TypedDict):
  """웹 프리미엄 생성에만 쓰이는 장소. slot 이 **없다** = 기기/NFC 매핑에 안 들어간다."""

  theme_key: str
  name_ko: str
  name_en: str


# 테마 독립 액션이 쓰는 합성 장소.
# COME_CLOSER 는 검정 플레이트 위에서 펫만 생성하므로 결과물이 배경과 무관하다 —
# 같은 펫이면 어떤 테마에서도 **같은 클립 하나**를 재사용한다. 그래서 논리 키에서
# place 를 뺀다. 다만 저장 스키마의 place_id 는 NOT NULL 이고
# unique(user,pet,place,action) 이라, 고정 센티널을 써서 펫당 한 행으로 접히게 한다.
# (DB CHECK 제약이 없어 마이그레이션 없이 안전하다.)
THEME_INDEPENDENT_PLACE_KEY = "any"
THEME_INDEPENDENT_PLACE_ID = "any"

WEB_ONLY_PLACES: dict[str, WebPlaceDef] = {
  "web_fresh_forest": {
    "theme_key": "fresh_forest",
    "name_ko": "싱그러운 숲",
    "name_en": "Fresh Forest",
  },
  THEME_INDEPENDENT_PLACE_KEY: {
    "theme_key": THEME_INDEPENDENT_PLACE_ID,
    "name_ko": "테마 무관",
    "name_en": "Theme independent",
  },
}


def is_theme_independent_action(action_id: str) -> bool:
  """
  결과물이 배경과 무관한 액션인가.

  프리미엄 웹 액션(COME_CLOSER)만 해당한다. 레거시 IDLE/TOUCH/VOICE/NFC 는
  **절대 여기 들어오면 안 된다** — 그쪽은 장소별 자산이고 기기/NFC 가 그 전제로 돈다.
  """
  return (action_id or "").upper() in PREMIUM_ACTIONS


def generation_places() -> dict[str, dict]:
  """COME_CLOSER 생성이 허용되는 전체 장소 (레거시 10 + 웹 전용)."""
  return {**PLACES, **WEB_ONLY_PLACES}


def generation_place_ids() -> list[str]:
  """COME_CLOSER 가 지원하는 theme_key 목록 — 오류 응답·문서용."""
  return [p["theme_key"] for p in generation_places().values()]


def resolve_generation_place_id(selected_place_id: str) -> str:
  """
  COME_CLOSER 생성용 place 해석 — 레거시 10곳 + 웹 전용.

  resolve_place_id() 와 **일부러 분리**돼 있다. 그쪽은 NFC 슬롯 번호까지 받는
  레거시 경로라, 거기에 웹 전용 장소를 끼워 넣으면 기기 매핑이 오염된다.
  """
  raw = (selected_place_id or "").strip()
  if not raw:
    raise ValueError("selected_place_id is required")

  places = generation_places()
  if raw in places:
    return raw
  for key, place in places.items():
    if place["theme_key"] == raw:
      return key
  # 슬롯 번호는 레거시 의미이므로 레거시 해석기에만 맡긴다.
  return resolve_place_id(raw)


def place_public_id(place_key: str) -> str:
  """DB·API용 장소 ID (theme_key — Unity·NFC 카드와 동일)."""
  entry = PLACES.get(place_key) or WEB_ONLY_PLACES.get(place_key)
  if entry is None:
    raise KeyError(place_key)
  return entry["theme_key"]


def to_place_id(place_key_or_id: str) -> str:
  """place_key → theme_key. 이미 theme_key(또는 미지의 값)면 그대로 돌려준다."""
  entry = PLACES.get(place_key_or_id) or WEB_ONLY_PLACES.get(place_key_or_id)
  return entry["theme_key"] if entry else place_key_or_id


def storage_object_name(place_key: str, action_key: str) -> str:
  """
  Supabase path segment.

  레거시(장소별): SNOW_FOREST_IDLE.mp4
  테마 독립     : COME_CLOSER.mp4   ← 장소가 경로에 들어가지 않는다
  """
  if is_theme_independent_action(action_key):
    return f"{action_key.upper()}.mp4"
  safe_place = place_public_id(place_key).upper()
  return f"{safe_place}_{action_key}.mp4"
