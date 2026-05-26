"""
마법의 프롬프트 조립 공장 (Prompt Factory).

소비자 메인 사진 URL + 장소(PLACES) + 행동(ACTIONS) → Luma I2V 최종 문장.
"""

from __future__ import annotations

from ..scenarios.pet_scenarios import ACTIONS, ACTIONS_EN, PLACES
from .luma_prompts import build_scenario_luma_prompt


def build_scenario_prompt(
    image_url: str,
    place_key: str,
    action_key: str,
    *,
    use_korean_motion: bool = False,
) -> str:
    """
    최종 Luma 프롬프트 한 줄 (강아지만, 사람·목줄 금지).
    """
    if place_key not in PLACES:
        raise KeyError(f"Unknown place_key: {place_key}")
    if action_key not in ACTIONS_EN:
        raise KeyError(f"Unknown action_key: {action_key}")

    place = PLACES[place_key]
    motion_ko = ACTIONS[action_key] if use_korean_motion else ""

    return build_scenario_luma_prompt(
        image_url,
        place["prompt"],
        action_key,
        motion_ko_suffix=motion_ko,
    )
