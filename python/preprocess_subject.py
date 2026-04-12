"""
Eternal Beam — 피사체 영상 사전합성 파이프라인

[실행 방법]
  python python/preprocess_subject.py

[역할]
  1. Runway 출력 영상(배경 있음)에서 rembg로 프레임별 누끼 추출
  2. 알파채널 페더링 + True Black 클램핑
  3. 슬롯별 배경과 합성 → 최종 MP4 저장
     (Pi 5에서는 이 파일만 재생 → CPU 부담 없음)

[출력 파일]
  python/composited/slot1_idle.mp4
  python/composited/slot1_action.mp4
  python/composited/slot2_idle.mp4  ...등

[품질 설정]
  REMBG_MODEL = "birefnet-general"  ← 최고품질 (느림, 첫 실행 시 다운로드)
               = "u2net"            ← 빠름, 품질 보통
"""

import cv2
import numpy as np
import json
import time
from pathlib import Path
from rembg import remove, new_session

# ── 설정 ──────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
OUT_DIR    = BASE_DIR / "composited"
OUT_DIR.mkdir(exist_ok=True)

SLOT_MAP_PATH = BASE_DIR / "slot_map.json"

SUBJECT_IDLE_PATH   = BASE_DIR / "idle1.mp4.mp4"
SUBJECT_ACTION_PATH = BASE_DIR / "action1.mp4.mp4"

OUTPUT_W = 720
OUTPUT_H = 960

# rembg 모델: "birefnet-general"(최고품질) 또는 "u2net"(빠름)
REMBG_MODEL = "u2net"

# 피사체 배치 비율
SUBJECT_MAX_W_RATIO   = 0.65
SUBJECT_MAX_H_RATIO   = 0.75
SUBJECT_BOTTOM_MARGIN = 0.30  # 0.12→0.30 : 강아지를 더 위로 (중앙쪽)

PURE_BLACK_THRESHOLD = 18   # 높을수록 엣지 더 깔끔 (10→18)
EDGE_ERODE_PIXELS    = 2    # 알파 엣지 침식 픽셀 수 (반투명 잔여물 제거)
ANCHOR_JSON_PATH = OUT_DIR / "anchor_data.json"


# ── 머리/입 좌표 자동 감지 ──────────────────────────────────────────
def detect_anchors(subject_path: Path, session, sample_frames: int = 5) -> dict:
    """
    피사체 영상에서 rembg 마스크 분석 → 정규화 앵커 좌표 반환.
    - head: 바운딩 박스 상단 20% 지점, 가로 중앙
    - mouth: 바운딩 박스 상단 38% 지점, 가로 중앙 + 약간 오른쪽
    좌표는 합성 캔버스(OUTPUT_W×OUTPUT_H) 기준 0~1 정규화 값.
    """
    cap = cv2.VideoCapture(str(subject_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total == 0:
        cap.release()
        return {"head_nx": 0.50, "head_ny": 0.18, "mouth_nx": 0.52, "mouth_ny": 0.35}

    step = max(1, total // sample_frames)
    xs_min, ys_min, xs_max, ys_max = [], [], [], []

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            rgba = extract_rgba(frame, session)
            # 합성 후 실제 캔버스 좌표 계산
            _, sx, sy = scale_and_position(rgba)
            sh, sw = rgba.shape[:2]
            max_w_ratio = SUBJECT_MAX_W_RATIO
            max_h_ratio = SUBJECT_MAX_H_RATIO
            scale = min(int(OUTPUT_W * max_w_ratio) / rgba.shape[1],
                        int(OUTPUT_H * max_h_ratio) / rgba.shape[0])
            rw = int(rgba.shape[1] * scale)
            rh = int(rgba.shape[0] * scale)
            resized_rgba = cv2.resize(rgba, (rw, rh))

            # 알파 채널에서 non-zero 영역의 bounding box 추출
            alpha = resized_rgba[:, :, 3]
            ys, xs = np.where(alpha > 30)
            if len(ys) > 0:
                # 캔버스 절대 좌표
                xs_min.append(sx + xs.min())
                xs_max.append(sx + xs.max())
                ys_min.append(sy + ys.min())
                ys_max.append(sy + ys.max())

        frame_idx += 1
    cap.release()

    if not xs_min:
        return {"head_nx": 0.50, "head_ny": 0.18, "mouth_nx": 0.52, "mouth_ny": 0.35}

    # 샘플 평균 bounding box
    x0 = np.mean(xs_min)
    x1 = np.mean(xs_max)
    y0 = np.mean(ys_min)
    y1 = np.mean(ys_max)
    cx = (x0 + x1) / 2          # 가로 중앙
    height = y1 - y0

    head_nx  = cx / OUTPUT_W
    head_ny  = (y0 + height * 0.18) / OUTPUT_H   # 상단에서 18%
    mouth_nx = (cx + (x1 - x0) * 0.04) / OUTPUT_W  # 약간 오른쪽
    mouth_ny = (y0 + height * 0.38) / OUTPUT_H   # 상단에서 38%

    return {
        "head_nx":  round(float(np.clip(head_nx,  0, 1)), 4),
        "head_ny":  round(float(np.clip(head_ny,  0, 1)), 4),
        "mouth_nx": round(float(np.clip(mouth_nx, 0, 1)), 4),
        "mouth_ny": round(float(np.clip(mouth_ny, 0, 1)), 4),
    }


# ── 누끼 추출 ────────────────────────────────────────────────────
def extract_rgba(frame_bgr: np.ndarray, session) -> np.ndarray:
    """BGR 프레임 → RGBA (rembg 누끼)"""
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    rgba = remove(frame_rgb, session=session)
    return np.asarray(rgba)  # H×W×4


def feather_alpha(alpha: np.ndarray, radius: int = 6) -> np.ndarray:
    """알파채널 페더링 + 엣지 침식 — 반투명 잔여물 완전 제거"""
    # 1. 엣지 침식: 경계 픽셀 EDGE_ERODE_PIXELS만큼 안으로 깎음 → 잔여물 제거
    kernel = np.ones((EDGE_ERODE_PIXELS * 2 + 1,) * 2, np.uint8)
    alpha_eroded = cv2.erode(alpha, kernel, iterations=1)

    alpha_f = alpha_eroded.astype(np.float32) / 255.0
    blurred = cv2.GaussianBlur(alpha_f, (0, 0), radius)

    # 2. 내부는 완전 불투명, 외부는 완전 투명하게 이진화에 가깝게
    blurred[alpha_f > 0.85] = 1.0
    blurred[alpha_f < 0.08] = 0.0
    return (np.clip(blurred, 0.0, 1.0) * 255).astype(np.uint8)


# ── 피사체 스케일·위치 ───────────────────────────────────────────
def scale_and_position(rgba: np.ndarray):
    """배경(OUTPUT_W×OUTPUT_H) 기준 피사체 스케일+위치 계산"""
    fh, fw = rgba.shape[:2]
    max_w = int(OUTPUT_W * SUBJECT_MAX_W_RATIO)
    max_h = int(OUTPUT_H * SUBJECT_MAX_H_RATIO)
    scale = min(max_w / fw, max_h / fh)
    new_w, new_h = int(fw * scale), int(fh * scale)
    resized = cv2.resize(rgba, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x = (OUTPUT_W - new_w) // 2
    bottom_y = int(OUTPUT_H * (1.0 - SUBJECT_BOTTOM_MARGIN))
    y = max(0, bottom_y - new_h)
    return resized, x, y


# ── 단일 프레임 합성 ─────────────────────────────────────────────
def composite_frame(
    subject_rgba: np.ndarray,
    bg_bgr: np.ndarray | None,
    contrast: float = 1.8,    # 페퍼스고스트 빛손실 보정: 기기에서 선명하게
    brightness: float = 0.28,  # 모니터에서 약간 밝다 싶어야 기기에서 딱 맞음
) -> np.ndarray:
    """
    배경 위에 피사체(RGBA) 합성 → True Black 클램핑 → BGR 반환
    bg_bgr=None → Pure Black 배경
    """
    canvas = np.zeros((OUTPUT_H, OUTPUT_W, 3), dtype=np.uint8)
    if bg_bgr is not None:
        canvas = cv2.resize(bg_bgr, (OUTPUT_W, OUTPUT_H)).copy()

    subj_resized, sx, sy = scale_and_position(subject_rgba)
    sh, sw = subj_resized.shape[:2]

    ey = min(sy + sh, OUTPUT_H)
    ex = min(sx + sw, OUTPUT_W)
    subj_crop = subj_resized[:ey - sy, :ex - sx]

    # 알파 페더링
    alpha_raw = subj_crop[:, :, 3]
    alpha_f   = feather_alpha(alpha_raw).astype(np.float32) / 255.0

    # 피사체 RGB 처리
    fg_rgb = subj_crop[:, :, :3].astype(np.float32) / 255.0

    # Shadow Lift: 어두운 털 픽셀 선택적 boosting
    # 페퍼스고스트 빛손실 보정: 어두운 영역을 더 적극적으로 올림
    lum = 0.299*fg_rgb[...,0] + 0.587*fg_rgb[...,1] + 0.114*fg_rgb[...,2]
    shadow_lift = (1.0 - lum[..., np.newaxis]) * 0.35   # 0.25→0.35 보정값 강화
    fg_rgb = fg_rgb + shadow_lift

    # 대비·밝기 강화
    fg_rgb = np.clip((fg_rgb - 0.5) * contrast + 0.5 + brightness, 0.0, 1.0)

    # 알파 블렌딩
    roi = canvas[sy:ey, sx:ex].astype(np.float32) / 255.0
    a3  = alpha_f[:, :, np.newaxis]
    blended = fg_rgb * a3 + roi * (1.0 - a3)
    canvas[sy:ey, sx:ex] = (blended * 255).astype(np.uint8)

    # True Black 클램핑 (DLP 프로젝터 투명처리)
    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    canvas[gray <= PURE_BLACK_THRESHOLD] = [0, 0, 0]

    return canvas


# ── 영상 파이프라인 ──────────────────────────────────────────────
def process_video(
    subject_path: Path,
    bg_path: Path | None,
    output_path: Path,
    session,
    label: str = "",
) -> None:
    cap_s = cv2.VideoCapture(str(subject_path))
    cap_b = cv2.VideoCapture(str(bg_path)) if bg_path else None

    total = int(cap_s.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps   = cap_s.get(cv2.CAP_PROP_FPS) or 30.0

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (OUTPUT_W, OUTPUT_H))

    # ── 알파 마스크 영상 (rembg 정확한 알파를 별도 저장) ───────────
    # Unity PetShader 에서 이 영상의 R채널을 Alpha 로 사용
    # → 검은 털도 완벽하게 보존됨 (셰이더 luminance 추정 불필요)
    alpha_path  = output_path.parent / (output_path.stem + "_alpha.mp4")
    alpha_writer = cv2.VideoWriter(str(alpha_path), fourcc, fps, (OUTPUT_W, OUTPUT_H))

    print(f"\n[{label}] 합성 시작 → {output_path.name}  ({total}프레임)")
    t0 = time.time()

    frame_idx = 0
    while True:
        ret_s, frame_s = cap_s.read()
        if not ret_s:
            break

        # 배경 프레임 루프 읽기
        bg_frame = None
        if cap_b:
            ret_b, bg_frame = cap_b.read()
            if not ret_b:
                cap_b.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret_b, bg_frame = cap_b.read()
                if not ret_b:
                    bg_frame = None

        # rembg 누끼 추출 (핵심 — 프레임별 정확한 알파)
        rgba = extract_rgba(frame_s, session)

        # ── RGB 합성 영상 ────────────────────────────────────────
        composited = composite_frame(rgba, bg_frame)
        writer.write(composited)

        # ── 알파 마스크 영상: rembg 알파 → 캔버스 정위치에 배치 ──
        alpha_canvas = np.zeros((OUTPUT_H, OUTPUT_W), dtype=np.uint8)
        subj_resized, sx, sy = scale_and_position(rgba)
        sh, sw = subj_resized.shape[:2]
        ey = min(sy + sh, OUTPUT_H)
        ex = min(sx + sw, OUTPUT_W)
        raw_alpha = subj_resized[:ey - sy, :ex - sx, 3]
        feathered  = feather_alpha(raw_alpha)
        alpha_canvas[sy:ey, sx:ex] = feathered
        # 그레이스케일 → BGR (VideoWriter는 BGR 필요)
        alpha_bgr = cv2.cvtColor(alpha_canvas, cv2.COLOR_GRAY2BGR)
        alpha_writer.write(alpha_bgr)

        frame_idx += 1
        if frame_idx % 10 == 0:
            elapsed = time.time() - t0
            fps_actual = frame_idx / elapsed
            remaining = (total - frame_idx) / fps_actual if fps_actual > 0 else 0
            print(f"  {frame_idx}/{total}  ({fps_actual:.1f}fps)  남은시간 {remaining:.0f}초", end="\r")

    cap_s.release()
    if cap_b:
        cap_b.release()
    writer.release()
    alpha_writer.release()
    print(f"\n  완료: {output_path}  +  {alpha_path.name}  ({time.time()-t0:.1f}초)")


# ── 메인 ────────────────────────────────────────────────────────
def main():
    slot_map = {}
    if SLOT_MAP_PATH.exists():
        slot_map = json.loads(SLOT_MAP_PATH.read_text(encoding="utf-8"))

    print(f"rembg 세션 로드 중... (모델: {REMBG_MODEL})")
    session = new_session(REMBG_MODEL)
    print("모델 로드 완료.")

    # 슬롯 없음 (Pure Black 배경) — 항상 생성
    print("\n── Pure Black 배경 (카드 없음 상태) ──")
    process_video(
        SUBJECT_IDLE_PATH,
        None,
        OUT_DIR / "no_slot_idle.mp4",
        session, "기본 idle"
    )
    process_video(
        SUBJECT_ACTION_PATH,
        None,
        OUT_DIR / "no_slot_action.mp4",
        session, "기본 action"
    )

    # 슬롯별 배경 합성
    for slot_id, info in slot_map.items():
        bg_rel = info.get("bg_video")
        if not bg_rel:
            continue
        bg_path = (BASE_DIR / bg_rel).resolve()
        if not bg_path.exists():
            print(f"\n[슬롯 {slot_id}] 배경 파일 없음: {bg_path} — 스킵")
            continue

        theme = info.get("theme", slot_id)
        print(f"\n── 슬롯 {slot_id} ({theme}) ──")

        process_video(
            SUBJECT_IDLE_PATH,
            bg_path,
            OUT_DIR / f"slot{slot_id}_idle.mp4",
            session, f"슬롯{slot_id} idle"
        )
        process_video(
            SUBJECT_ACTION_PATH,
            bg_path,
            OUT_DIR / f"slot{slot_id}_action.mp4",
            session, f"슬롯{slot_id} action"
        )

    # ── 앵커 좌표 자동 감지 → JSON 저장 ─────────────────────────────
    print("\n── 앵커 좌표 자동 감지 중... ──")
    anchor_data = {}

    for clip_name, vid_path in [
        ("no_slot_idle",   SUBJECT_IDLE_PATH),
        ("no_slot_action", SUBJECT_ACTION_PATH),
    ]:
        if vid_path.exists():
            print(f"  {clip_name} 분석 중...", end=" ", flush=True)
            anchor_data[clip_name] = detect_anchors(vid_path, session)
            a = anchor_data[clip_name]
            print(f"head=({a['head_nx']:.3f},{a['head_ny']:.3f})  mouth=({a['mouth_nx']:.3f},{a['mouth_ny']:.3f})")

    for slot_id in slot_map:
        for suffix in ("idle", "action"):
            clip_name = f"slot{slot_id}_{suffix}"
            vid_path  = OUT_DIR / f"{clip_name}.mp4"
            if vid_path.exists():
                print(f"  {clip_name} 분석 중...", end=" ", flush=True)
                anchor_data[clip_name] = detect_anchors(vid_path, session)
                a = anchor_data[clip_name]
                print(f"head=({a['head_nx']:.3f},{a['head_ny']:.3f})  mouth=({a['mouth_nx']:.3f},{a['mouth_ny']:.3f})")

    ANCHOR_JSON_PATH.write_text(
        json.dumps(anchor_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n앵커 JSON 저장: {ANCHOR_JSON_PATH}")

    print("\n\n모든 합성 완료.")
    print(f"출력 폴더: {OUT_DIR}")
    print("다음 단계: python python/raspi_nfc_playback.py 실행")


if __name__ == "__main__":
    main()
