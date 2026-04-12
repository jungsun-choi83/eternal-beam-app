import cv2
import numpy as np
from rembg import remove
from pathlib import Path


def load_images(input_path: Path, background_path: Path):
    if not input_path.exists():
        raise FileNotFoundError(f"입력 이미지가 없습니다: {input_path}")
    if not background_path.exists():
        raise FileNotFoundError(f"배경 이미지가 없습니다: {background_path}")

    # input: BGR → RGBA로 변경 후 rembg에 전달
    input_bgr = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if input_bgr is None:
        raise RuntimeError(f"입력 이미지를 읽을 수 없습니다: {input_path}")

    input_rgb = cv2.cvtColor(input_bgr, cv2.COLOR_BGR2RGB)
    # rembg는 RGB 또는 RGBA 이미지를 받아 RGBA를 반환
    fg_rgba = remove(input_rgb)

    # numpy array일 수 있으므로 보장
    fg_rgba = np.asarray(fg_rgba)
    if fg_rgba.shape[2] == 3:
        # 알파 채널이 없으면 전체 불투명으로 추가
        alpha = np.full(fg_rgba.shape[:2], 255, dtype=np.uint8)
        fg_rgba = np.dstack([fg_rgba, alpha])

    bg_bgr = cv2.imread(str(background_path), cv2.IMREAD_COLOR)
    if bg_bgr is None:
        raise RuntimeError(f"배경 이미지를 읽을 수 없습니다: {background_path}")

    return fg_rgba, bg_bgr


def auto_scale_and_position(fg_rgba: np.ndarray, bg_bgr: np.ndarray):
    """배경 기준으로 피사체 자동 리사이즈 + 위치 잡기

    - 비율 유지
    - 배경 가로 중앙
    - 세로 하단에서 15% 띄운 위치
    """
    bg_h, bg_w = bg_bgr.shape[:2]
    fg_h, fg_w = fg_rgba.shape[:2]

    # 피사체를 배경의 폭 40%, 높이 60% 중 더 작은 쪽에 맞춰 스케일
    target_w = int(bg_w * 0.4)
    target_h = int(bg_h * 0.6)
    scale = min(target_w / fg_w, target_h / fg_h)

    new_w = int(fg_w * scale)
    new_h = int(fg_h * scale)
    fg_resized = cv2.resize(fg_rgba, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # 위치 계산: 가로 중앙, 세로 하단에서 15% 위
    x = (bg_w - new_w) // 2
    bottom_y = int(bg_h * (1.0 - 0.15))
    y = bottom_y - new_h
    y = max(0, min(y, bg_h - new_h))

    return fg_resized, x, y


def feather_edges(alpha: np.ndarray, feather_radius: int = 15):
    """알파 채널에 페더링 적용 (경계 부드럽게)"""
    # 0~255 범위 유지
    alpha_float = alpha.astype(np.float32) / 255.0
    blurred = cv2.GaussianBlur(alpha_float, (0, 0), feather_radius)
    blurred = np.clip(blurred, 0.0, 1.0)
    return (blurred * 255).astype(np.uint8)


def composite(fg_rgba: np.ndarray, bg_bgr: np.ndarray):
    # 자동 스케일 + 위치
    fg_resized, x, y = auto_scale_and_position(fg_rgba, bg_bgr)

    bg_h, bg_w = bg_bgr.shape[:2]
    h, w = fg_resized.shape[:2]

    # 배경을 BGR → BGRA로 변환
    bg_bgra = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2BGRA)

    # 페더링된 알파
    alpha = fg_resized[:, :, 3]
    alpha_feather = feather_edges(alpha, feather_radius=10)
    alpha_f = alpha_feather.astype(np.float32) / 255.0

    # 합성 영역
    roi = bg_bgra[y : y + h, x : x + w]

    for c in range(3):
        roi[:, :, c] = (
            fg_resized[:, :, c].astype(np.float32) * alpha_f
            + roi[:, :, c].astype(np.float32) * (1.0 - alpha_f)
        ).astype(np.uint8)

    # 합성 결과를 다시 넣어줌
    bg_bgra[y : y + h, x : x + w] = roi

    # 외곽(완전 투명 또는 거의 투명) 영역은 True Black 처리
    full_alpha = bg_bgra[:, :, 3]
    mask_outer = full_alpha < 5
    bg_bgra[mask_outer, 0:3] = 0  # BGR 모두 0

    # 알파는 필요 없으므로 BGR로 반환
    result_bgr = cv2.cvtColor(bg_bgra, cv2.COLOR_BGRA2BGR)
    return result_bgr


def clamp_black_levels(img_bgr: np.ndarray, threshold: int = 10):
    """흑레벨 0~threshold 사이를 강제로 완전 블랙(#000000)으로 클램핑"""
    # 각 픽셀의 최대 채널 값이 threshold 이하이면 완전 블랙
    max_channel = img_bgr.max(axis=2)
    mask = max_channel <= threshold
    clamped = img_bgr.copy()
    clamped[mask] = 0
    return clamped


def show_fullscreen(img_bgr: np.ndarray, window_name: str = "EternalBeam Preview"):
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(
        window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
    )
    cv2.imshow(window_name, img_bgr)
    print("아무 키나 누르면 창이 닫힙니다. (ESC 권장)")
    key = cv2.waitKey(0)
    if key == 27:  # ESC
        cv2.destroyAllWindows()
    else:
        cv2.destroyAllWindows()


def main():
    base_dir = Path(__file__).parent

    # 입력: input.jpg (이미 있음)
    input_path = base_dir / "input.jpg"

    # 배경: 우선순위대로 찾기
    # 1) background.png
    # 2) background.jpg
    # 3) background.jpeg
    # 4) background (확장자 숨김)
    # 5) background1.mp4에서 프레임 추출
    background_path = None
    candidates = [
        base_dir / "background.png",
        base_dir / "background.jpg",
        base_dir / "background.jpeg",
        base_dir / "background",
    ]
    for c in candidates:
        if c.exists():
            background_path = c
            break

    if background_path is None:
        video_path = base_dir / "background1.mp4"
        if not video_path.exists():
            raise FileNotFoundError(
                "배경 이미지를 찾을 수 없습니다. background(.png/.jpg) 또는 background1.mp4 중 하나가 필요합니다."
            )

        # background1.mp4 가운데 프레임 한 장을 배경으로 추출
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"배경 동영상을 열 수 없습니다: {video_path}")

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target_idx = max(frame_count // 2, 0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            raise RuntimeError("배경 동영상에서 프레임을 읽지 못했습니다.")

        background_path = base_dir / "background_from_video.png"
        cv2.imwrite(str(background_path), frame)
        print(f"background1.mp4에서 프레임 추출 → {background_path}")

    output_path = base_dir / "composited_output.png"

    print(f"입력: {input_path}")
    print(f"배경: {background_path}")

    fg_rgba, bg_bgr = load_images(input_path, background_path)
    print("1) rembg로 배경 제거(누끼) 완료")

    composited = composite(fg_rgba, bg_bgr)
    print("2) 배경 위 자동 배치 + 페더링 합성 완료")

    clamped = clamp_black_levels(composited, threshold=10)
    print("3) 흑레벨 0~10 영역 #000000 클램핑 완료")

    # 결과 저장 (디버깅용)
    cv2.imwrite(str(output_path), clamped)
    print(f"합성 결과 저장: {output_path}")

    # 전체 화면 출력
    show_fullscreen(clamped)


if __name__ == "__main__":
    main()

