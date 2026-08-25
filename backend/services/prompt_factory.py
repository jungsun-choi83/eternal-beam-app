"""
마법의 프롬프트 조립 공장 (Prompt Factory).

소비자 메인 사진 URL + 장소(PLACES) + 행동(ACTIONS) → Luma I2V 최종 문장.
"""

from __future__ import annotations

from ..scenarios.pet_scenarios import ACTIONS, ACTIONS_EN, generation_places
from .luma_prompts import build_scenario_luma_prompt, is_idle_event


def build_scenario_prompt(
    image_url: str,
    place_key: str,
    action_key: str,
    *,
    use_korean_motion: bool = False,
    background_baked: bool = False,
) -> str:
    """
    최종 Luma 프롬프트 한 줄 (강아지만, 사람·목줄 금지).

    background_baked=True 면 입력 키프레임이 **정본 장면**이다 — 보이드 요구를
    배경 보존 요구로 바꾼다(luma_prompts.bake_scene_background). 행동별 분기가
    아니라 최종 문장 한 곳에서 갈린다.
    """
    # 레거시 10곳 + 웹 전용(fresh_forest). 장소 설명은 모델에 넘기지 않으므로
    # 웹 전용 장소도 프롬프트 조립에 아무 문제가 없다.
    if place_key not in generation_places():
        raise KeyError(f"Unknown place_key: {place_key}")
    # 아이들 이벤트(BLINKING 등)는 **ACTIONS/ACTIONS_EN 에 넣지 않는다.**
    # 그 표는 all_scenario_keys() 가 장소×액션으로 전개하는 원본이라, 여기 넣으면
    # 레거시 배치가 테마 독립 이벤트를 10개 장소마다 하나씩 만들려 든다(불필요한 과금).
    # 아이들 이벤트는 luma_prompts 의 모듈 조립 경로를 따로 탄다.
    if action_key not in ACTIONS_EN and not is_idle_event(action_key):
        raise KeyError(f"Unknown action_key: {action_key}")

    # place_key 검증은 유지한다 — 과금·저장 경로·/device/sync 가 이 키를 쓴다.
    # 다만 장소 **설명문**은 더 이상 모델에 넘기지 않는다: 배경은 기기에서 별도
    # 레이어로 재생되므로, 펫 클립에 배경을 그려 넣으면 이중으로 겹친다.
    motion_ko = ACTIONS.get(action_key, "") if use_korean_motion else ""

    return build_scenario_luma_prompt(
        image_url,
        action_key,
        motion_ko_suffix=motion_ko,
        background_baked=background_baked,
    )
