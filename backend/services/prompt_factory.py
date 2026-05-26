"""
마법의 프롬프트 조립 공장 (Prompt Factory).

소비자 메인 사진 URL + 장소(PLACES) + 행동(ACTIONS) → Luma I2V 최종 문장.
"""

from __future__ import annotations

from ..scenarios.pet_scenarios import ACTIONS_EN, PLACES


def build_scenario_prompt(
    image_url: str,
    place_key: str,
    action_key: str,
    *,
    use_korean_motion: bool = False,
) -> str:
  """
  최종 Luma 프롬프트 한 줄을 만든다.

  Args:
    image_url: 공개 접근 가능한 메인 피사체(누끼/원본) 이미지 URL
    place_key: PLACES 딕셔너리 키 (예: "01_snow_forest")
    action_key: ACTIONS 키 (IDLE | TOUCH | VOICE | NFC)
    use_korean_motion: True면 한국어 행동 설명도 프롬프트에 포함
  """
  if place_key not in PLACES:
    raise KeyError(f"Unknown place_key: {place_key}")
  if action_key not in ACTIONS_EN:
    raise KeyError(f"Unknown action_key: {action_key}")

  place = PLACES[place_key]
  motion_en = ACTIONS_EN[action_key]

  motion_extra = ""
  if use_korean_motion:
    from ..scenarios.pet_scenarios import ACTIONS

    motion_extra = f" ({ACTIONS[action_key]})"

  return (
    f"3D Luma generation of the pet from {image_url}. "
    f"Location: {place['prompt']}. "
    f"Motion: {motion_en}{motion_extra}. "
    "High-end minimalist cinematic lighting, hyper-realistic 3D environment depth, "
    "photorealistic fur detail, stable camera, no text, no watermark."
  )
