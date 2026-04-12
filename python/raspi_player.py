"""
Eternal Beam — 비동기 홀로그램 플레이어 (Pi 5 / 노트북)

[핵심 설계]
  - 부팅 시 모든 슬롯 영상을 백그라운드 스레드로 미리 디코드
  - 카드 삽입 → 버퍼 포인터 교체(1ms) → 즉시 재생
  - 딜레이 목표: 카드 인식~첫 프레임 표시 < 100ms (NFC 읽기 50ms 포함)

[조작 — 노트북 시뮬레이션]
  마우스 클릭 : Idle → Action 전환
  키보드 1~4  : NFC 슬롯 전환
  키보드 0    : 슬롯 해제 (Pure Black 배경)
  ESC         : 종료
"""

import cv2
import time
import json
import threading
import queue
import numpy as np
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).resolve().parent
COMPOSITED_DIR = BASE_DIR / "composited"
SLOT_MAP_PATH  = BASE_DIR / "slot_map.json"

SUBJECT_IDLE_PATH   = str(BASE_DIR / "idle1.mp4")
SUBJECT_ACTION_PATH = str(BASE_DIR / "action1.mp4")

OUTPUT_W = 720
OUTPUT_H = 960
TARGET_FPS = 30
FRAME_INTERVAL = 1.0 / TARGET_FPS

CROSSFADE_FRAMES  = 18    # 0.6초 크로스페이드 (18프레임)
PURE_BLACK_THRESH = 10
BUFFER_SIZE       = 20    # 스레드당 프레임 버퍼 크기 (약 0.67초치)

WINDOW_NAME = "EternalBeam"

# ── 시뮬레이션 상태 ──────────────────────────────────────────────
_sim_slot: int | None = None
_sim_hand: bool = False

def _on_mouse(event, x, y, flags, param):
    global _sim_hand
    if event == cv2.EVENT_LBUTTONDOWN:
        _sim_hand = True


# ── FrameBuffer: 백그라운드 디코드 스레드 ──────────────────────
class FrameBuffer:
    """
    VideoCapture를 백그라운드 스레드에서 미리 디코드해
    Queue에 적재. 메인 루프는 get()으로 즉시 프레임을 가져감.
    """

    def __init__(self, path: str, buf_size: int = BUFFER_SIZE):
        self.path    = path
        self._q      = queue.Queue(maxsize=buf_size)
        self._stop   = threading.Event()
        self._reset  = threading.Event()
        self._lock   = threading.Lock()
        self._last   = np.zeros((OUTPUT_H, OUTPUT_W, 3), dtype=np.uint8)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            return

        while not self._stop.is_set():
            # 리셋 요청 처리 (Action → Idle 복귀 시 되감기)
            if self._reset.is_set():
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                # 큐 비우기
                while not self._q.empty():
                    try: self._q.get_nowait()
                    except queue.Empty: break
                self._reset.clear()

            if self._q.full():
                time.sleep(0.002)
                continue

            ret, frame = cap.read()
            if not ret:  # 루프 재생
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            frame = cv2.resize(frame, (OUTPUT_W, OUTPUT_H))
            # True Black 클램핑
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame[gray <= PURE_BLACK_THRESH] = [0, 0, 0]

            with self._lock:
                self._last = frame

            try:
                self._q.put(frame, timeout=0.01)
            except queue.Full:
                pass

        cap.release()

    def get(self) -> np.ndarray:
        """큐에서 프레임 즉시 반환. 비어있으면 마지막 프레임 재사용."""
        try:
            frame = self._q.get_nowait()
            with self._lock:
                self._last = frame
            return frame
        except queue.Empty:
            with self._lock:
                return self._last.copy()

    def is_depleted(self) -> bool:
        """큐가 비어있고 실제로 영상이 끝났는지 감지 (action 종료 판단)."""
        return self._q.empty()

    def reset(self):
        """영상 처음으로 되감기 (비동기)."""
        self._reset.set()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1.0)

    @property
    def buffered_count(self) -> int:
        return self._q.qsize()


# ── SlotManager: 부팅 시 모든 슬롯 사전 로드 ──────────────────
class SlotManager:
    """
    부팅 시 모든 슬롯의 idle/action 버퍼를 동시에 시작.
    카드 삽입 시 active_slot 변경만으로 즉시 전환.
    """

    def __init__(self, slot_map: dict):
        self.buffers: dict[str, dict[str, FrameBuffer]] = {}
        self._init_buffers(slot_map)

    def _resolve_paths(self, slot_key: str) -> tuple[str, str]:
        """composited/ 우선, 없으면 원본 fallback."""
        idle_p   = COMPOSITED_DIR / f"{slot_key}_idle.mp4"
        action_p = COMPOSITED_DIR / f"{slot_key}_action.mp4"
        if idle_p.exists() and action_p.exists():
            return str(idle_p), str(action_p)
        return SUBJECT_IDLE_PATH, SUBJECT_ACTION_PATH

    def _init_buffers(self, slot_map: dict):
        # 슬롯 없음 (Pure Black)
        idle_p, action_p = self._resolve_paths("no_slot")
        self.buffers["no_slot"] = {
            "idle":   FrameBuffer(idle_p),
            "action": FrameBuffer(action_p),
        }
        print(f"  [no_slot] 버퍼 시작: {Path(idle_p).name}")

        # 각 슬롯
        for slot_id in slot_map:
            key = f"slot{slot_id}"
            idle_p, action_p = self._resolve_paths(key)
            self.buffers[key] = {
                "idle":   FrameBuffer(idle_p),
                "action": FrameBuffer(action_p),
            }
            print(f"  [{key}] 버퍼 시작: {Path(idle_p).name}")

    def get_buffer(self, slot: int | None, mode: str) -> FrameBuffer:
        key = "no_slot" if slot is None else f"slot{slot}"
        if key not in self.buffers:
            key = "no_slot"
        return self.buffers[key][mode]

    def stop_all(self):
        for slot_buffers in self.buffers.values():
            for buf in slot_buffers.values():
                buf.stop()


# ── 크로스페이드 ─────────────────────────────────────────────────
def crossfade_display(from_f: np.ndarray, to_f: np.ndarray, steps: int):
    for i in range(steps):
        alpha = (i + 1) / steps
        blended = cv2.addWeighted(from_f, 1 - alpha, to_f, alpha, 0.0)
        gray = cv2.cvtColor(blended, cv2.COLOR_BGR2GRAY)
        blended[gray <= PURE_BLACK_THRESH] = [0, 0, 0]
        cv2.imshow(WINDOW_NAME, blended)
        cv2.waitKey(int(1000 / TARGET_FPS))


# ── NFC / 거리 센서 ──────────────────────────────────────────────
def read_nfc() -> int | None:
    """
    [Pi 실제 구현 — PN532]:
        uid = pn532.read_passive_target(timeout=0.05)
        if uid: return int(pn532.ntag2xx_read_block(4)[0])
        return None
    """
    return _sim_slot

def hand_near() -> bool:
    """
    [Pi 실제 구현 — VL53L0X]:
        return vl53.range < DISTANCE_THRESHOLD_CM * 10
    """
    global _sim_hand
    if _sim_hand:
        _sim_hand = False
        return True
    return False


# ── 메인 루프 ────────────────────────────────────────────────────
def main():
    # 슬롯 맵 로드
    slot_map = {}
    if SLOT_MAP_PATH.exists():
        slot_map = json.loads(SLOT_MAP_PATH.read_text(encoding="utf-8"))

    print("=" * 52)
    print("  Eternal Beam — 비동기 플레이어")
    print("  부팅 시 모든 슬롯 버퍼 사전 로드 중...")
    print("=" * 52)

    # 모든 슬롯 버퍼 동시 시작 (부팅 시 백그라운드로)
    mgr = SlotManager(slot_map)

    # 버퍼가 최소 5프레임 채워질 때까지 대기 (부팅 시 1회)
    print("버퍼 워밍업 중...", end="")
    warmup_start = time.time()
    while True:
        counts = [mgr.get_buffer(None, "idle").buffered_count]
        if all(c >= 5 for c in counts):
            break
        if time.time() - warmup_start > 3.0:
            break
        time.sleep(0.05)
        print(".", end="", flush=True)
    print(f" 완료 ({time.time()-warmup_start:.2f}초)\n")

    print("  마우스 클릭 : Idle → Action")
    print("  키보드 1~4  : NFC 슬롯 전환")
    print("  키보드 0    : 슬롯 해제")
    print("  ESC         : 종료")
    print("=" * 52)

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, OUTPUT_W, OUTPUT_H)
    # Pi 배포 시: 위 두 줄 대신 아래 주석 해제
    # cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback(WINDOW_NAME, _on_mouse)

    current_slot: int | None = None
    state = "idle"
    prev_time = time.time()
    last_frame = np.zeros((OUTPUT_H, OUTPUT_W, 3), dtype=np.uint8)
    action_frame_count = 0
    ACTION_MAX_FRAMES = int(TARGET_FPS * 8)  # action 최대 8초

    global _sim_slot

    try:
        while True:
            now = time.time()
            elapsed = now - prev_time
            if elapsed < FRAME_INTERVAL:
                time.sleep(0.001)
                continue
            prev_time = now

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break

            # 키보드 슬롯 시뮬레이션
            if key in (ord('1'), ord('2'), ord('3'), ord('4')):
                _sim_slot = int(chr(key))
                print(f"[키보드] 슬롯 {_sim_slot}")
            elif key == ord('0'):
                _sim_slot = None
                print("[키보드] 슬롯 해제")

            # ── 슬롯 변경 감지 → 즉시 전환 (<1ms) ──────────────
            new_slot = read_nfc()
            if new_slot != current_slot:
                t_switch = time.time()
                current_slot = new_slot
                state = "idle"
                action_frame_count = 0

                # 새 슬롯의 idle 버퍼 첫 프레임 즉시 표시
                buf = mgr.get_buffer(current_slot, "idle")
                frame = buf.get()
                cv2.imshow(WINDOW_NAME, frame)

                print(f"[슬롯 전환] {new_slot} │ 전환시간: {(time.time()-t_switch)*1000:.1f}ms")
                last_frame = frame
                continue

            # ── 상태 머신 ────────────────────────────────────────
            if state == "idle":
                buf = mgr.get_buffer(current_slot, "idle")
                frame = buf.get()
                cv2.imshow(WINDOW_NAME, frame)
                last_frame = frame

                if hand_near():
                    # Idle → Action 크로스페이드
                    action_buf = mgr.get_buffer(current_slot, "action")
                    action_buf.reset()  # action 처음부터

                    # 첫 action 프레임 대기 (최대 50ms)
                    wait_start = time.time()
                    while action_buf.buffered_count < 1:
                        if time.time() - wait_start > 0.05:
                            break
                        time.sleep(0.002)

                    to_frame = action_buf.get()
                    crossfade_display(last_frame, to_frame, CROSSFADE_FRAMES)
                    state = "action"
                    action_frame_count = 0
                    print("[STATE] idle → action")

            elif state == "action":
                buf = mgr.get_buffer(current_slot, "action")
                frame = buf.get()
                cv2.imshow(WINDOW_NAME, frame)
                last_frame = frame
                action_frame_count += 1

                # Action 최대 시간 초과 → Idle 복귀
                if action_frame_count >= ACTION_MAX_FRAMES:
                    idle_buf = mgr.get_buffer(current_slot, "idle")
                    to_frame = idle_buf.get()
                    crossfade_display(last_frame, to_frame, CROSSFADE_FRAMES)
                    state = "idle"
                    action_frame_count = 0
                    print("[STATE] action → idle (시간 초과)")

    finally:
        mgr.stop_all()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
