"""
Eternal Beam — NFC 카드 기반 홀로그램 재생 (라즈베리 파이 / 노트북 시뮬레이션)

레이어 구조:
  Layer 1 (배경) : NFC 카드 삽입 시 슬롯별 배경 영상 등장. 카드 없으면 Pure Black.
  Layer 2 (피사체): 강아지 누끼 영상 (idle / action). 슬롯과 무관하게 항상 표시.

합성 방식:
  - 피사체 영상의 True Black(0~10) 픽셀 → 배경 픽셀로 교체 (빠른 마스킹)
  - 경계 페더링 + 대비 강화
  - 피사체 자동 스케일·위치: 배경 폭 50% 이내, 하단 15% 여백

[노트북 시뮬레이션 조작]
  마우스 클릭: 거리 센서 트리거 (Idle → Action)
  키보드 1~4 : NFC 슬롯 전환 (배경 교체)
  키보드 0   : 슬롯 해제 (배경 없음)
  ESC        : 종료

[하드웨어 사양]
  기기 폭 180mm → 출력 해상도 720×960 (3:4 세로)
  DLP 프로젝터: True Black = 빛 없음 = 투명
"""

import cv2
import time
import json
import numpy as np
from pathlib import Path

# ── 경로 설정 ─────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent
SLOT_MAP_PATH = BASE_DIR / "slot_map.json"
COMPOSITED_DIR = BASE_DIR / "composited"  # preprocess_subject.py 출력 폴더

# 피사체 영상 (슬롯 무관, 항상 동일) — 사전합성 없을 때 fallback
SUBJECT_IDLE_PATH   = str(BASE_DIR / "idle1.mp4")
SUBJECT_ACTION_PATH = str(BASE_DIR / "action1.mp4")

# ── 출력 해상도 (기기 폭 180mm 기준 3:4 세로) ────────────────────
OUTPUT_W = 720
OUTPUT_H = 960

TARGET_FPS           = 30
CROSSFADE_DURATION   = 0.8
PURE_BLACK_THRESHOLD = 10   # 0~10 → True Black (DLP 투명)

# 피사체 배치 비율 (배경 기준)
SUBJECT_MAX_W_RATIO  = 0.50  # 배경 폭의 50%
SUBJECT_MAX_H_RATIO  = 0.65  # 배경 높이의 65%
SUBJECT_BOTTOM_MARGIN = 0.12 # 하단 12% 여백 (깊이감)

# 거리 센서 임계값 (cm) — Pi VL53L0X 연결 후 조정
DISTANCE_THRESHOLD_CM = 15.0

WINDOW_NAME = "EternalBeam"


# ── 시뮬레이션 상태 ──────────────────────────────────────────────
_sim_nfc_slot: int | None = None
_sim_hand_near: bool = False


def _on_mouse_sim(event, x, y, flags, param):
    global _sim_hand_near
    if event == cv2.EVENT_LBUTTONDOWN:
        _sim_hand_near = True


# ── NFC / 거리 센서 ──────────────────────────────────────────────
def read_nfc_slot() -> int | None:
    """
    NFC 카드 슬롯 번호 반환. 없으면 None.
    [Pi 실제 구현 — PN532]:
        uid = pn532.read_passive_target(timeout=0.1)
        if uid: return int(pn532.ntag2xx_read_block(4)[0])
        return None
    """
    return _sim_nfc_slot


def is_hand_near() -> bool:
    """
    손 감지 여부 반환.
    [Pi 실제 구현 — VL53L0X]:
        import adafruit_vl53l0x
        return vl53.range < DISTANCE_THRESHOLD_CM * 10
    """
    global _sim_hand_near
    if _sim_hand_near:
        _sim_hand_near = False
        return True
    return False


# ── 슬롯 맵 ──────────────────────────────────────────────────────
def load_slot_map() -> dict:
    if SLOT_MAP_PATH.exists():
        return json.loads(SLOT_MAP_PATH.read_text(encoding="utf-8"))
    return {}


def get_video_paths(slot_number: int | None, slot_map: dict) -> tuple[str, str]:
    """
    슬롯 번호 → (idle경로, action경로) 반환.

    우선순위:
      1) composited/ 폴더에 사전합성 영상 있으면 사용 (최고품질)
      2) 없으면 실시간 합성 fallback (원본 영상 사용)
    """
    if slot_number is None:
        key = "no_slot"
    else:
        key = f"slot{slot_number}"

    idle_pre   = COMPOSITED_DIR / f"{key}_idle.mp4"
    action_pre = COMPOSITED_DIR / f"{key}_action.mp4"

    if idle_pre.exists() and action_pre.exists():
        print(f"[사전합성 영상 사용] {key}")
        return str(idle_pre), str(action_pre)

    # fallback: 실시간 합성 (원본 영상)
    print(f"[fallback: 실시간 합성] composited/{key}_*.mp4 없음")
    return SUBJECT_IDLE_PATH, SUBJECT_ACTION_PATH


def get_bg_path(slot_number: int | None, slot_map: dict) -> str | None:
    """실시간 합성 fallback용 배경 경로."""
    if slot_number is None:
        return None
    key = str(slot_number)
    if key in slot_map and "bg_video" in slot_map[key]:
        p = BASE_DIR / slot_map[key]["bg_video"]
        return str(p) if p.exists() else None
    return None


# ── 영상 유틸 ────────────────────────────────────────────────────
def open_cap(path: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {path}")
    return cap


def read_loop(cap: cv2.VideoCapture, w: int, h: int) -> np.ndarray | None:
    """루프 재생 + 리사이즈."""
    ret, frame = cap.read()
    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()
    if not ret:
        return None
    return cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)


def clamp_true_black(frame: np.ndarray) -> np.ndarray:
    """True Black 클램핑 (DLP 프로젝터 최적화)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    frame[gray <= PURE_BLACK_THRESHOLD] = [0, 0, 0]
    return frame


# ── 합성 핵심 함수 ───────────────────────────────────────────────
def make_subject_mask(subject_bgr: np.ndarray) -> np.ndarray:
    """
    피사체 프레임에서 True Black이 아닌 영역의 마스크 생성.
    반환: float32 (0.0~1.0), 피사체 있는 곳=1.0, 배경=0.0
    """
    gray = cv2.cvtColor(subject_bgr, cv2.COLOR_BGR2GRAY)
    mask = (gray > PURE_BLACK_THRESHOLD).astype(np.float32)
    # 경계 페더링 (자연스러운 가장자리)
    mask_blur = cv2.GaussianBlur(mask, (0, 0), 4)
    # 내부는 완전 불투명 유지
    mask_blur[mask_blur > 0.9] = 1.0
    return mask_blur


def scale_and_position_subject(subject_bgr: np.ndarray) -> tuple[np.ndarray, int, int]:
    """
    배경(OUTPUT_W × OUTPUT_H) 기준으로 피사체 자동 스케일 + 위치 결정.
    반환: (리사이즈된 피사체, x, y)
    깊이감: 피사체를 배경보다 작게 → 중앙 하단 배치
    """
    fh, fw = subject_bgr.shape[:2]

    max_w = int(OUTPUT_W * SUBJECT_MAX_W_RATIO)
    max_h = int(OUTPUT_H * SUBJECT_MAX_H_RATIO)
    scale = min(max_w / fw, max_h / fh)

    new_w = int(fw * scale)
    new_h = int(fh * scale)
    resized = cv2.resize(subject_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

    x = (OUTPUT_W - new_w) // 2
    bottom_y = int(OUTPUT_H * (1.0 - SUBJECT_BOTTOM_MARGIN))
    y = max(0, bottom_y - new_h)

    return resized, x, y


def composite(subject_bgr: np.ndarray, bg_bgr: np.ndarray | None) -> np.ndarray:
    """
    배경 위에 피사체를 합성해 최종 프레임 반환.
    bg_bgr=None → Pure Black 배경 (카드 없음 상태)
    """
    # 배경 결정
    if bg_bgr is None:
        canvas = np.zeros((OUTPUT_H, OUTPUT_W, 3), dtype=np.uint8)
    else:
        canvas = bg_bgr.copy()

    # 피사체 스케일·위치
    subj_resized, sx, sy = scale_and_position_subject(subject_bgr)
    sh, sw = subj_resized.shape[:2]

    # ROI 경계 클리핑
    ey = min(sy + sh, OUTPUT_H)
    ex = min(sx + sw, OUTPUT_W)
    subj_crop  = subj_resized[:ey - sy, :ex - sx]
    canvas_roi = canvas[sy:ey, sx:ex]

    # 마스크 생성
    mask = make_subject_mask(subj_crop)          # (h, w) float32

    # 대비 강화 (피사체를 더 선명하게)
    subj_f = subj_crop.astype(np.float32) / 255.0
    subj_f = np.clip((subj_f - 0.5) * 1.2 + 0.5 + 0.05, 0.0, 1.0)

    # 알파 블렌딩: canvas_roi = subj * mask + canvas_roi * (1-mask)
    mask3 = mask[:, :, np.newaxis]
    blended = (subj_f * mask3 + canvas_roi.astype(np.float32) / 255.0 * (1.0 - mask3))
    canvas[sy:ey, sx:ex] = (blended * 255).astype(np.uint8)

    return clamp_true_black(canvas)


# ── 크로스페이드 ─────────────────────────────────────────────────
def crossfade(from_f: np.ndarray, to_f: np.ndarray, steps: int):
    for i in range(steps):
        alpha = (i + 1) / steps
        yield clamp_true_black(
            cv2.addWeighted(from_f, 1 - alpha, to_f, alpha, 0.0)
        )


# ── 홀로그램 재생 ────────────────────────────────────────────────
def play_hologram(current_slot: int | None):
    """
    상태 머신:
      idle  ──(손 감지)──► action ──(영상 끝)──► idle
      NFC 슬롯 변경 → THEME_CHANGE 반환

    사전합성 영상(composited/) 있으면 그대로 재생 (최고품질).
    없으면 실시간 합성 fallback.
    """
    slot_map   = load_slot_map()
    idle_path, action_path = get_video_paths(current_slot, slot_map)
    bg_path    = get_bg_path(current_slot, slot_map)

    # 사전합성 영상이면 bg_cap 불필요 (이미 합성됨)
    use_precomposited = idle_path != SUBJECT_IDLE_PATH

    idle_cap   = open_cap(idle_path)
    action_cap = open_cap(action_path)
    bg_cap     = (open_cap(bg_path) if bg_path else None) if not use_precomposited else None

    frame_interval  = 1.0 / TARGET_FPS
    crossfade_steps = max(3, int(CROSSFADE_DURATION * TARGET_FPS))
    state           = "idle"
    prev_time       = time.time()

    print(f"[play_hologram] 슬롯={current_slot} │ 배경={'있음' if bg_path else 'Pure Black'}")

    try:
        while True:
            now = time.time()
            if now - prev_time < frame_interval:
                time.sleep(0.001)
                continue
            prev_time = now

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                return "EXIT", current_slot

            # 키보드 NFC 슬롯 시뮬레이션
            global _sim_nfc_slot
            if key in (ord('1'), ord('2'), ord('3'), ord('4')):
                _sim_nfc_slot = int(chr(key))
                print(f"[키보드] 슬롯 {_sim_nfc_slot} 선택")
            elif key == ord('0'):
                _sim_nfc_slot = None
                print("[키보드] 슬롯 해제 → Pure Black 배경")

            # NFC 폴링: 슬롯 변경 시 THEME_CHANGE
            new_slot = read_nfc_slot()
            if new_slot != current_slot:
                print(f"[THEME_CHANGE] 슬롯 {current_slot} → {new_slot}")
                return "THEME_CHANGE", new_slot

            # ── 프레임 읽기 및 합성 ──────────────────────────────
            def get_frame(cap: cv2.VideoCapture) -> np.ndarray | None:
                """사전합성이면 그대로, 아니면 실시간 합성."""
                raw = read_loop(cap, OUTPUT_W, OUTPUT_H)
                if raw is None:
                    return None
                if use_precomposited:
                    return clamp_true_black(raw)  # 이미 합성됨
                bg_frame = read_loop(bg_cap, OUTPUT_W, OUTPUT_H) if bg_cap else None
                return composite(raw, bg_frame)

            if state == "idle":
                frame = get_frame(idle_cap)
                if frame is None:
                    break
                cv2.imshow(WINDOW_NAME, frame)

                if is_hand_near():
                    action_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    to_frame = get_frame(action_cap) or frame
                    for blended in crossfade(frame, to_frame, crossfade_steps):
                        cv2.imshow(WINDOW_NAME, blended)
                        cv2.waitKey(int(1000 / TARGET_FPS))
                    state = "action"
                    print("[STATE] idle → action")

            elif state == "action":
                ret, raw = action_cap.read()
                if not ret:
                    # Action 끝 → Idle 복귀
                    action_cap.set(
                        cv2.CAP_PROP_POS_FRAMES,
                        max(action_cap.get(cv2.CAP_PROP_FRAME_COUNT) - 2, 0)
                    )
                    ret2, last_raw = action_cap.read()
                    if ret2:
                        last_raw = cv2.resize(last_raw, (OUTPUT_W, OUTPUT_H))
                        from_frame = clamp_true_black(last_raw) if use_precomposited else composite(
                            last_raw,
                            read_loop(bg_cap, OUTPUT_W, OUTPUT_H) if bg_cap else None
                        )
                    else:
                        from_frame = frame  # 이전 프레임 재사용

                    to_frame = get_frame(idle_cap) or from_frame
                    for blended in crossfade(from_frame, to_frame, crossfade_steps):
                        cv2.imshow(WINDOW_NAME, blended)
                        cv2.waitKey(int(1000 / TARGET_FPS))
                    state = "idle"
                    print("[STATE] action → idle")
                    continue

                raw = cv2.resize(raw, (OUTPUT_W, OUTPUT_H))
                if use_precomposited:
                    frame = clamp_true_black(raw)
                else:
                    bg_frame = read_loop(bg_cap, OUTPUT_W, OUTPUT_H) if bg_cap else None
                    frame = composite(raw, bg_frame)
                cv2.imshow(WINDOW_NAME, frame)

    finally:
        idle_cap.release()
        action_cap.release()
        if bg_cap:
            bg_cap.release()

    return "EXIT", current_slot


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, OUTPUT_W, OUTPUT_H)   # 창 모드 (노트북 테스트)
    # Pi 실제 배포 시 위 줄 주석, 아래 줄 주석 해제:
    # cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback(WINDOW_NAME, _on_mouse_sim)

    print("=" * 52)
    print("  Eternal Beam — 노트북 시뮬레이션 모드")
    print("=" * 52)
    print("  마우스 클릭 : 거리 센서 (Idle → Action)")
    print("  키보드 1~4  : NFC 슬롯 전환 (배경 교체)")
    print("  키보드 0    : 슬롯 해제 (Pure Black 배경)")
    print("  ESC         : 종료")
    print("=" * 52)

    current_slot = None

    while True:
        result, next_slot = play_hologram(current_slot)
        if result == "THEME_CHANGE":
            current_slot = next_slot
            continue
        break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
