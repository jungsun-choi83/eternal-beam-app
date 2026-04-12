import cv2
import numpy as np
from rembg import remove
from pathlib import Path
import time


def feather_edges(alpha: np.ndarray, feather_radius: int = 15):
    """알파 채널에 페더링 적용 (경계 부드럽게)"""
    alpha_float = alpha.astype(np.float32) / 255.0
    blurred = cv2.GaussianBlur(alpha_float, (0, 0), feather_radius)
    blurred = np.clip(blurred, 0.0, 1.0)
    return (blurred * 255).astype(np.uint8)


def auto_scale_and_position(fg_rgba: np.ndarray, bg_bgr: np.ndarray):
    """배경 기준으로 피사체 자동 리사이즈 + 위치 잡기

    - 비율 유지
    - 배경 가로 중앙
    - 세로 하단에서 15% 띄운 위치
    """
    bg_h, bg_w = bg_bgr.shape[:2]
    fg_h, fg_w = fg_rgba.shape[:2]

    target_w = int(bg_w * 0.4)
    target_h = int(bg_h * 0.6)
    scale = min(target_w / fg_w, target_h / fg_h)

    new_w = int(fg_w * scale)
    new_h = int(fg_h * scale)
    fg_resized = cv2.resize(fg_rgba, (new_w, new_h), interpolation=cv2.INTER_AREA)

    x = (bg_w - new_w) // 2
    bottom_y = int(bg_h * (1.0 - 0.15))
    y = bottom_y - new_h
    y = max(0, min(y, bg_h - new_h))

    return fg_resized, x, y


def composite_frame(fg_rgba: np.ndarray, bg_bgr: np.ndarray):
    """단일 프레임 합성 (배경 위에 고야 피사체만 알파 블렌딩)"""
    fg_resized, x, y = auto_scale_and_position(fg_rgba, bg_bgr)

    h, w = fg_resized.shape[:2]
    bg_bgra = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2BGRA)

    # 알파 채널 준비 + 페더링
    alpha = fg_resized[:, :, 3]
    alpha_feather = feather_edges(alpha, feather_radius=5)

    # 내부는 완전 불투명, 가장자리는 부드럽게
    inner_mask = alpha_feather > 230
    alpha_feather[inner_mask] = 255
    alpha_f = alpha_feather.astype(np.float32) / 255.0

    # 강아지 RGB에만 살짝 콘트라스트 강화
    fg_rgb = fg_resized[:, :, :3].astype(np.float32) / 255.0
    # 강아지 본체를 더 또렷하게: 대비/밝기 강화
    contrast = 1.2   # 20% 대비 증가
    brightness = 0.1  # 10% 정도 밝기 상승
    fg_rgb = np.clip((fg_rgb - 0.5) * contrast + 0.5 + brightness, 0.0, 1.0)
    fg_rgb_uint8 = (fg_rgb * 255).astype(np.uint8)

    roi = bg_bgra[y : y + h, x : x + w]

    # 얇은 검은 외곽선(쉐도우) 마스크 생성 (너무 두껍지 않게 좁은 구간만)
    edge_low, edge_high = 80, 140
    edge_mask = (alpha_feather >= edge_low) & (alpha_feather <= edge_high)
    # 외곽선이 들어갈 위치를 배경 기준으로 어둡게
    roi_edges = roi[:, :, :3]
    roi_edges[edge_mask] = (roi_edges[edge_mask].astype(np.float32) * 0.4).astype(
        np.uint8
    )

    # 알파 블렌딩 (불투명 강화된 알파 사용)
    # 몸통(알파가 충분히 높은 곳)은 배경을 전혀 섞지 않고 완전 덮어쓰기
    solid_mask = alpha_f > 0.95
    for c in range(3):
        blended = (
            fg_rgb_uint8[:, :, c].astype(np.float32) * alpha_f
            + roi[:, :, c].astype(np.float32) * (1.0 - alpha_f)
        ).astype(np.uint8)
        channel = roi[:, :, c]
        channel[~solid_mask] = blended[~solid_mask]
        channel[solid_mask] = fg_rgb_uint8[:, :, c][solid_mask]
        roi[:, :, c] = channel

    bg_bgra[y : y + h, x : x + w] = roi

    # 경계선 주변(아주 얇은 띠)에서만 작은 노이즈를 제거하기 위해 True Black 클램핑
    # 알파가 거의 0에 가까운 테두리만 대상으로 함
    edge_clamp_mask = (alpha_feather > 0) & (alpha_feather < 25)
    roi_edge_clamp = bg_bgra[y : y + h, x : x + w]
    roi_edge_clamp[edge_clamp_mask, 0:3] = 0

    result_bgr = cv2.cvtColor(bg_bgra, cv2.COLOR_BGRA2BGR)
    return result_bgr


def clamp_black_levels(img_bgr: np.ndarray, threshold: int = 5):
    """(옵션) 화면 전체에 적용할 수 있는 흑레벨 클램핑 - 현재는 미세 조정용"""
    max_channel = img_bgr.max(axis=2)
    mask = max_channel <= threshold
    clamped = img_bgr.copy()
    clamped[mask] = 0
    return clamped


def compute_static_alpha_mask(video_path: Path) -> np.ndarray:
    """피사체 영상에서 중간 프레임 하나만 rembg로 누끼 → 알파 마스크만 추출"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"피사체 영상을 열 수 없습니다: {video_path}")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target_idx = max(frame_count // 2, 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise RuntimeError("피사체 영상에서 프레임을 읽지 못했습니다.")

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = frame_rgb.shape[:2]

    max_side = max(h, w)
    scale = 1.0
    if max_side > 720:
        scale = 720.0 / max_side

    if scale < 1.0:
        small_w = int(w * scale)
        small_h = int(h * scale)
        small_rgb = cv2.resize(frame_rgb, (small_w, small_h), interpolation=cv2.INTER_AREA)
        small_rgba = remove(small_rgb)
        small_rgba = np.asarray(small_rgba)
        if small_rgba.shape[2] == 3:
            alpha_small = np.full(small_rgba.shape[:2], 255, dtype=np.uint8)
        else:
            alpha_small = small_rgba[:, :, 3]
        alpha_full = cv2.resize(alpha_small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        full_rgba = remove(frame_rgb)
        full_rgba = np.asarray(full_rgba)
        if full_rgba.shape[2] == 3:
            alpha_full = np.full(full_rgba.shape[:2], 255, dtype=np.uint8)
        else:
            alpha_full = full_rgba[:, :, 3]

    cap.release()
    return alpha_full


def next_foreground_frame(cap: cv2.VideoCapture, alpha_full: np.ndarray):
    """피사체 영상의 RGB만 매 프레임 읽고, 고정된 알파 마스크를 입혀서 반환 (고속 + 깔끔한 외곽)"""
    if not cap.isOpened():
        raise RuntimeError("피사체 영상이 열려있지 않습니다.")
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError("피사체 영상에서 프레임을 읽지 못했습니다.")

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = frame_rgb.shape[:2]

    # 알파 마스크 해상도와 다르면 맞춰서 리사이즈
    if alpha_full.shape[0] != h or alpha_full.shape[1] != w:
        alpha = cv2.resize(alpha_full, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        alpha = alpha_full

    fg_rgba = np.dstack([frame_rgb, alpha])
    return fg_rgba


def open_background_video(base_dir: Path):
    """heaven_bg.mp4 / background.mp4 / background1.mp4 중 하나를 루프로 재생"""
    candidates = [
        base_dir / "heaven_bg.mp4",  # 우선순위 1
        base_dir / "background.mp4",
        base_dir / "background1.mp4",
    ]
    for c in candidates:
        if c.exists():
            cap = cv2.VideoCapture(str(c))
            if cap.isOpened():
                return cap, c
    raise FileNotFoundError("배경 영상(background.mp4 또는 background1.mp4)을 찾을 수 없습니다.")


def main():
    base_dir = Path(__file__).parent

    # 피사체 영상: main.mp4 우선, 없으면 goya1.mp4 사용
    fg_candidates = [
        base_dir / "main.mp4",
        base_dir / "goya1.mp4",
    ]
    fg_video = None
    for c in fg_candidates:
        if c.exists():
            fg_video = c
            break
    if fg_video is None:
        raise FileNotFoundError("피사체 영상(main.mp4 또는 goya1.mp4)을 찾을 수 없습니다.")

    print(f"[피사체] {fg_video} 에서 중간 프레임으로 누끼 마스크를 한 번만 계산합니다.")
    static_alpha = compute_static_alpha_mask(fg_video)
    fg_cap = cv2.VideoCapture(str(fg_video))
    if not fg_cap.isOpened():
        raise RuntimeError(f"피사체 영상을 열 수 없습니다: {fg_video}")

    bg_cap, bg_path = open_background_video(base_dir)
    print(f"[배경] {bg_path} 를 루프로 재생하면서 합성합니다.")

    # 타겟 해상도: 배경 첫 프레임 기준으로 통일
    ret_bg_init, frame_bg_init = bg_cap.read()
    if not ret_bg_init or frame_bg_init is None:
        raise RuntimeError("배경 영상에서 첫 프레임을 읽지 못했습니다.")
    target_h, target_w = frame_bg_init.shape[:2]
    bg_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    win_name = "EternalBeam Video Preview"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    target_fps = 24.0
    frame_interval = 1.0 / target_fps

    while True:
        loop_start = time.time()

        ret_bg, frame_bg = bg_cap.read()
        if not ret_bg or frame_bg is None:
            # 배경 끝 → 처음부터 다시
            bg_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        # 해상도 통일 (배경 기준)
        if frame_bg.shape[0] != target_h or frame_bg.shape[1] != target_w:
            frame_bg = cv2.resize(frame_bg, (target_w, target_h), interpolation=cv2.INTER_AREA)

        # 피사체 한 프레임 가져와 고정 알파 마스크 적용
        fg_rgba = next_foreground_frame(fg_cap, static_alpha)

        composited = composite_frame(fg_rgba, frame_bg)
        # 필요시만 전체 흑레벨 클램핑 사용 (지금은 그대로 사용)
        final_frame = clamp_black_levels(composited, threshold=5)

        cv2.imshow(win_name, final_frame)

        # 30fps 근사 유지
        elapsed = time.time() - loop_start
        remaining = frame_interval - elapsed
        delay_ms = 1 if remaining <= 0 else int(remaining * 1000)

        key = cv2.waitKey(delay_ms) & 0xFF
        if key == 27:  # ESC
            break

    bg_cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

