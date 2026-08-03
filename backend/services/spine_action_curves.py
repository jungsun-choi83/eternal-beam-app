"""
Action(달려오기/짖기/배깔기) 리깅 파이프라인 3단계 — 손수 제작한(hand-authored)
모션 곡선을 특정 강아지의 탐지된 본 길이/셋업 회전값에 "재타겟(retarget)"한다.

★ 왜 "학습"이 아니라 "손수 제작 + 재타겟"인가 (사용자 지시 그대로)
데이터로 액션을 학습하는 게 아니라, 모션 곡선(각 본의 회전/이동 값이 시간에
따라 어떻게 변하는지)을 **한 번만 사람이 직접 만들고**, 그 곡선을 이 강아지의
실제 탐지된 본 길이·비율에 맞춰 재사용한다. 재타겟이 쉬운 이유는 Spine 본의
"rotation" 값이 애초에 본 길이와 무관한(각도이므로 스케일 불변) 값이기 때문 —
길이에 의존하는 유일한 값(예: 몸통이 주저앉는 이동 거리)은 별도로 본 길이
비율로 스케일링한다(`_translate_amount()` 참고).

★ 이번 요청에서 구현하는 액션: 배깔기(lying down) — 그럭저럭 정적인 자세
전환이라 3개 액션 중 가장 구현이 쉽다고 판단(요청서 그대로의 이유). 짖기/
달려오기(반복 보행 사이클)는 다음 단계 과제로 남긴다(진행상황 문서 참고).

★ 좌우 반전(mirroring) 처리
pose.head_side가 "right"(머리가 이미지 오른쪽)이면, 아래 상수들은 "머리가
왼쪽"을 가정하고 손으로 만든 값이므로 전부 -1을 곱해 좌우 반전한다 — 2D
평면상의 좌우 반전(거울 대칭)에서는 모든 회전각의 부호가 뒤집히는 것이
기하학적으로 정확하다(이동량 y축은 반전 영향 없음, 아래로 내려앉는 동작은
좌우와 무관).

★ 값의 근거 = 없음(1차 추정)
아래 각도들은 "앞다리를 가슴 밑으로 접고, 뒷다리를 옆으로 모으고, 몸통이
가라앉는" 일반적인 개의 배깔기 자세를 참고한 **1차 추정치**다. 실제
spine-cpp 렌더러로 시각 확인을 한 번도 못 했다(이 샌드박스에는 OpenGL/빌드
환경이 없음) — 반드시 실제 렌더링 후 각도를 눈으로 보고 다시 조정해야 한다.
"""

from __future__ import annotations

from typing import Optional

# 본 이름 → 배깔기 완료 시점의 "추가" 회전(도, degree) — 셋업 포즈 회전에 더해진다.
# (부호 기준: head_side == "left" 가정. head_side == "right"면 전부 -1을 곱함.)
_LIE_DOWN_ROTATION_DELTA_DEG: dict[str, float] = {
    "pelvis": -8.0,
    "spine": -10.0,
    "neck": 18.0,
    "head": 5.0,
    "tail1": -20.0,
    "tail2": -15.0,
    "front_left_upper": -60.0,
    "front_left_lower": -90.0,
    "front_right_upper": -55.0,
    "front_right_lower": -85.0,
    "back_left_upper": 45.0,
    "back_left_lower": 70.0,
    "back_right_upper": 40.0,
    "back_right_lower": 65.0,
}

# 전환 타이밍(0~1 사이 진행률, duration_sec에 곱해 실제 시간이 됨) — 3키프레임:
# 대기(0.0) → 주저앉는 중간 동작(0.55) → 완전히 엎드림(1.0, 이후 유지).
_KEYFRAME_FRACTIONS: tuple[float, ...] = (0.0, 0.55, 1.0)
_EASE_WEIGHTS: tuple[float, ...] = (0.0, 0.65, 1.0)  # 각 키프레임에서 delta에 곱할 가중치


def _translate_amount(bone_lengths: dict[str, float]) -> float:
    """몸통이 가라앉는 거리(월드 Y, 아래 방향=음수) — 뒷다리 길이 비율로 스케일링."""
    back_lengths = [
        bone_lengths.get("back_left_upper", 0.0),
        bone_lengths.get("back_right_upper", 0.0),
    ]
    back_lengths = [v for v in back_lengths if v > 0]
    avg_back_upper = sum(back_lengths) / len(back_lengths) if back_lengths else 40.0
    return -0.55 * avg_back_upper


def build_lie_down_animation(
    *,
    bone_local_rotations: dict[str, float],
    bone_local_positions: dict[str, tuple[float, float]],
    bone_lengths: dict[str, float],
    head_side: str = "left",
    duration_sec: float = 1.2,
    animation_name: str = "lie_down",
) -> dict:
    """Spine JSON의 `animations` 블록에 들어갈 {animation_name: {...}} 딕셔너리 반환.

    호출자(auto_rigging_service.build_rig_from_pose가 반환한 RigResult)의
    bone_local_rotations / bone_local_positions / bone_lengths를 그대로 넘기면 된다.
    """
    mirror = -1.0 if head_side == "right" else 1.0

    bones_block: dict[str, dict] = {}

    for bone_name, delta_deg in _LIE_DOWN_ROTATION_DELTA_DEG.items():
        setup_rotation = bone_local_rotations.get(bone_name)
        if setup_rotation is None:
            continue  # 이 본이 이번 리그에 없음(예: 크롭 실패로 스킵됨) — 조용히 무시.
        signed_delta = delta_deg * mirror
        rotate_keys = [
            {"time": round(frac * duration_sec, 4), "value": round(setup_rotation + signed_delta * w, 3)}
            for frac, w in zip(_KEYFRAME_FRACTIONS, _EASE_WEIGHTS)
        ]
        bones_block.setdefault(bone_name, {})["rotate"] = rotate_keys

    # 몸통(pelvis)이 가라앉는 이동 애니메이션 — pelvis의 부모가 root(회전 0 고정)라
    # local 이동량 == 월드 이동량이라 계산이 단순해진다(auto_rigging_service.py 참고).
    pelvis_pos = bone_local_positions.get("pelvis", (0.0, 0.0))
    drop_y = _translate_amount(bone_lengths)
    translate_keys = [
        {
            "time": round(frac * duration_sec, 4),
            "x": round(pelvis_pos[0], 3),
            "y": round(pelvis_pos[1] + drop_y * w, 3),
        }
        for frac, w in zip(_KEYFRAME_FRACTIONS, _EASE_WEIGHTS)
    ]
    bones_block.setdefault("pelvis", {})["translate"] = translate_keys

    return {animation_name: {"bones": bones_block}}


def available_actions() -> list[str]:
    """지금까지 구현된 액션 이름 목록(짖기/달려오기는 아직 미구현 — 문서의 다음 단계 참고)."""
    return ["lie_down"]
