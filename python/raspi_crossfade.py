"""
Eternal Beam — 라즈베리 파이 / PC 재생 스크립트

[하드웨어 사양 강제]
1. 부팅 시퀀스 (Boot Sequence):
   - Step 1: 전원 On 후 1초간 Pure Black(#000000) 유지
   - Step 2: 'Eternal Beam' 로고 애니메이션 (중앙 정렬, 배경 Pure Black)
   - Step 3: 로고 페이드 아웃 → Idle 영상 진입

2. True Black (No Gray Blacks):
   - 모든 영상 자산(로고, Idle, Action) 배경에 회색기 없음
   - Pixel Clamping: 0~10 범위 픽셀을 Pure Black(#000000)으로 강제 클램핑
   - DLP 프로젝터 최적화: 검은색 = 빛 없음 = 투명 처리
"""

import cv2
import time
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WINDOW_NAME = "EternalBeam"
IDLE_PATH = str(BASE_DIR / "idle1.mp4")
ACTION_PATH = str(BASE_DIR / "action1.mp4")
CROSSFADE_DURATION = 0.8
TARGET_FPS = 30

# 부팅 시퀀스 설정
BOOT_BLACK_DURATION = 1.0  # Step 1: Pure Black 유지 시간 (초)
BOOT_LOGO_FADE_IN = 0.6   # Step 2: 로고 페이드 인
BOOT_LOGO_HOLD = 1.5      # Step 2: 로고 유지
BOOT_LOGO_FADE_OUT = 0.5  # Step 2→3: 로고 페이드 아웃
PURE_BLACK_THRESHOLD = 10  # 0~10 → 0으로 클램핑 (True Black)

mouse_triggered = False


def clamp_pure_black(frame: np.ndarray) -> np.ndarray:
    """
    True Black: 흑레벨 0~10 범위 픽셀을 Pure Black(#000000)으로 강제 클램핑.
    DLP 프로젝터 최적화 — 회색기 제거.
    """
    if frame is None or frame.size == 0:
        return frame
    out = frame.copy()
    # luminance 기반: 밝기 <= 10 인 픽셀 → (0,0,0)
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    mask = gray <= PURE_BLACK_THRESHOLD
    out[mask] = [0, 0, 0]
    return out


def on_mouse(event, x, y, flags, param):
    global mouse_triggered
    if event == cv2.EVENT_LBUTTONDOWN:
        mouse_triggered = True


def open_video(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"비디오를 열 수 없습니다: {path}")
    return cap


def read_frame_with_loop(cap, template_size=None):
    if cap is None or not cap.isOpened():
        return None

    ret, frame = cap.read()
    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()
        if not ret:
            return None

    if template_size is not None:
        h, w = template_size
        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
    return clamp_pure_black(frame)


def crossfade_frames(frame_from, frame_to, steps):
    for i in range(steps):
        alpha = (i + 1) / float(steps)
        beta = 1.0 - alpha
        blended = cv2.addWeighted(frame_from, beta, frame_to, alpha, 0.0)
        yield clamp_pure_black(blended)


def create_pure_black_frame(h, w):
    """Pure Black(#000000) 프레임 생성."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def create_logo_frame(h, w, alpha=1.0):
    """
    'Eternal Beam' 로고 프레임. 배경은 반드시 Pure Black(#000000).
    중앙 정렬, 흰색 텍스트.
    """
    frame = create_pure_black_frame(h, w)
    text = "Eternal Beam"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = min(w, h) / 400
    thickness = max(1, int(font_scale * 2))
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    x = (w - tw) // 2
    y = (h + th) // 2
    color = (255, 255, 255)
    cv2.putText(frame, text, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)
    if alpha < 1.0:
        frame = (frame * alpha).astype(np.uint8)
    return clamp_pure_black(frame)


def run_boot_sequence(h, w):
    """
    부팅 시퀀스 실행:
    Step 1: 1초 Pure Black
    Step 2: Eternal Beam 로고 애니메이션 (페이드 인 → 유지 → 페이드 아웃)
    Step 3: Idle로 전환 (main에서 처리)
    """
    frame_interval = 1.0 / TARGET_FPS

    # Step 1: Pure Black 1초
    black_frame = create_pure_black_frame(h, w)
    step1_end = time.time() + BOOT_BLACK_DURATION
    while time.time() < step1_end:
        cv2.imshow(WINDOW_NAME, black_frame)
        if cv2.waitKey(int(1000 * frame_interval)) & 0xFF == 27:
            return False

    # Step 2: 로고 애니메이션
    # 페이드 인
    fade_in_steps = max(1, int(BOOT_LOGO_FADE_IN * TARGET_FPS))
    for i in range(fade_in_steps):
        alpha = (i + 1) / fade_in_steps
        logo = create_logo_frame(h, w, alpha)
        cv2.imshow(WINDOW_NAME, logo)
        if cv2.waitKey(int(1000 * frame_interval)) & 0xFF == 27:
            return False

    # 유지
    logo_hold = create_logo_frame(h, w, 1.0)
    hold_end = time.time() + BOOT_LOGO_HOLD
    while time.time() < hold_end:
        cv2.imshow(WINDOW_NAME, logo_hold)
        if cv2.waitKey(int(1000 * frame_interval)) & 0xFF == 27:
            return False

    # 페이드 아웃
    fade_out_steps = max(1, int(BOOT_LOGO_FADE_OUT * TARGET_FPS))
    for i in range(fade_out_steps):
        alpha = 1.0 - (i + 1) / fade_out_steps
        logo = create_logo_frame(h, w, alpha)
        cv2.imshow(WINDOW_NAME, logo)
        if cv2.waitKey(int(1000 * frame_interval)) & 0xFF == 27:
            return False

    # Step 3: Pure Black 한 프레임 후 Idle로 전환
    cv2.imshow(WINDOW_NAME, black_frame)
    cv2.waitKey(int(1000 * frame_interval))
    return True


def main():
    global mouse_triggered

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(
        WINDOW_NAME,
        cv2.WND_PROP_FULLSCREEN,
        cv2.WINDOW_FULLSCREEN,
    )
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)

    idle_cap = open_video(IDLE_PATH)
    action_cap = open_video(ACTION_PATH)

    ret, idle_first = idle_cap.read()
    if not ret:
        raise RuntimeError("idle 영상에서 첫 프레임을 읽을 수 없습니다.")
    h, w = idle_first.shape[:2]
    idle_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    idle_first = clamp_pure_black(idle_first)

    # 부팅 시퀀스 (Step 1, 2, 3)
    if not run_boot_sequence(h, w):
        idle_cap.release()
        action_cap.release()
        cv2.destroyAllWindows()
        return

    frame_interval = 1.0 / TARGET_FPS
    crossfade_steps = max(3, int(CROSSFADE_DURATION * TARGET_FPS))

    state = "idle"
    prev_time = time.time()

    while True:
        now = time.time()
        if now - prev_time < frame_interval:
            time.sleep(0.001)
            continue
        prev_time = now

        if cv2.waitKey(1) & 0xFF == 27:
            break

        if state == "idle":
            frame_idle = read_frame_with_loop(idle_cap, template_size=(h, w))
            if frame_idle is None:
                break

            cv2.imshow(WINDOW_NAME, frame_idle)

            if mouse_triggered:
                mouse_triggered = False
                action_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame_action = action_cap.read()
                if not ret:
                    continue
                frame_action = clamp_pure_black(cv2.resize(frame_action, (w, h)))
                for blended in crossfade_frames(frame_idle, frame_action, crossfade_steps):
                    cv2.imshow(WINDOW_NAME, blended)
                    if cv2.waitKey(int(1000 / TARGET_FPS)) & 0xFF == 27:
                        idle_cap.release()
                        action_cap.release()
                        cv2.destroyAllWindows()
                        return
                state = "action"

        elif state == "action":
            ret, frame_action = action_cap.read()
            if not ret:
                frame_idle = read_frame_with_loop(idle_cap, template_size=(h, w))
                if frame_idle is None:
                    break
                action_cap.set(
                    cv2.CAP_PROP_POS_FRAMES,
                    max(action_cap.get(cv2.CAP_PROP_FRAME_COUNT) - 2, 0),
                )
                ret2, last_action = action_cap.read()
                if not ret2:
                    last_action = frame_idle
                else:
                    last_action = clamp_pure_black(cv2.resize(last_action, (w, h)))

                for blended in crossfade_frames(last_action, frame_idle, crossfade_steps):
                    cv2.imshow(WINDOW_NAME, blended)
                    if cv2.waitKey(int(1000 / TARGET_FPS)) & 0xFF == 27:
                        idle_cap.release()
                        action_cap.release()
                        cv2.destroyAllWindows()
                        return
                state = "idle"
                continue

            frame_action = clamp_pure_black(cv2.resize(frame_action, (w, h)))
            cv2.imshow(WINDOW_NAME, frame_action)

        else:
            state = "idle"

    idle_cap.release()
    action_cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
