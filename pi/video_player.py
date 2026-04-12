import cv2
import numpy as np


def play_simple_video(source: str, window_name: str = "Eternal Beam") -> None:
  """
  단일 영상 재생.
  실패하면 조용히 리턴한다.
  """
  cap = cv2.VideoCapture(source)
  if not cap.isOpened():
    print("영상 열기 실패:", source)
    return

  while True:
    ret, frame = cap.read()
    if not ret:
      # 끝나면 처음부터 다시 (무한 루프)
      cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
      continue

    cv2.imshow(window_name, frame)
    if cv2.waitKey(30) & 0xFF == ord("q"):
      break

  cap.release()
  cv2.destroyAllWindows()


def _composite_overlay_on_background(bg_frame: np.ndarray, ov_frame: np.ndarray) -> np.ndarray:
  """
  검은 배경을 투명하게 만들고, 가장자리를 부드럽게 페더링해서 합성한다.
  """
  # 크기 맞추기
  ov_resized = cv2.resize(ov_frame, (bg_frame.shape[1], bg_frame.shape[0]))

  # 그레이로 변환해서 "얼마나 어두운지" 기준으로 마스크 생성 (검은 배경 제거)
  gray = cv2.cvtColor(ov_resized, cv2.COLOR_BGR2GRAY)

  # 어두운 부분(거의 검정)을 0, 그 외를 255 쪽으로
  _, mask = cv2.threshold(gray, 10, 255, cv2.THRESH_BINARY)

  # 마스크를 살짝 블러해서 가장자리 부드럽게 (페더링)
  mask = cv2.GaussianBlur(mask, (31, 31), 0)

  # 0~1 범위 알파로 변환
  alpha = mask.astype(np.float32) / 255.0
  alpha = cv2.merge([alpha, alpha, alpha])

  # 합성: 결과 = bg * (1 - alpha) + ov * alpha
  bg_f = bg_frame.astype(np.float32)
  ov_f = ov_resized.astype(np.float32)
  out = bg_f * (1.0 - alpha) + ov_f * alpha

  return out.astype(np.uint8)


def play_video_with_overlay(
  background_source: str,
  overlay_path: str,
  fallback_path: str,
  window_name: str = "Eternal Beam",
) -> None:
  """
  배경 영상 위에 3D 강아지 영상을 오버레이해서 재생.
  - background_source: 로컬 경로나 URL
  - overlay_path: 고야동영상.mp4 (검은 배경 포함)
  - fallback_path: 배경화며.mp4 (대기 화면)
  """
  cap_bg = cv2.VideoCapture(background_source)
  cap_ov = cv2.VideoCapture(overlay_path)

  if not cap_bg.isOpened():
    print("배경 영상 열기 실패, fallback 재생")
    play_simple_video(fallback_path, window_name)
    return

  if not cap_ov.isOpened():
    print("오버레이 영상 열기 실패, 배경만 재생")
    play_simple_video(background_source, window_name)
    return

  while True:
    ret_bg, frame_bg = cap_bg.read()
    if not ret_bg:
      # 배경도 끝나면 정지
      break

    ret_ov, frame_ov = cap_ov.read()
    if not ret_ov:
      # 오버레이가 끝나면 마지막 프레임 유지 (또는 break 로 완전 종료해도 됨)
      cap_ov.set(cv2.CAP_PROP_POS_FRAMES, 0)
      ret_ov, frame_ov = cap_ov.read()
      if not ret_ov:
        blended = frame_bg
      else:
        blended = _composite_overlay_on_background(frame_bg, frame_ov)
    else:
      blended = _composite_overlay_on_background(frame_bg, frame_ov)

    cv2.imshow(window_name, blended)
    if cv2.waitKey(30) & 0xFF == ord("q"):
      break

  cap_bg.release()
  cap_ov.release()
  cv2.destroyAllWindows()


