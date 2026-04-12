import os
import time

import board
import busio
from adafruit_pn532.i2c import PN532_I2C

from db_layer import get_video_url_for_slot
from video_player import play_simple_video, play_video_with_overlay


# TODO: 실제 경로에 맞게 수정
OVERLAY_VIDEO_PATH = "/home/pi/videos/zeodeep_dog.mp4"
FALLBACK_VIDEO_PATH = "/home/pi/videos/waiting_screen.mp4"


def uid_to_slot(uid_bytes) -> int:
  """
  NFC 태그 UID → 슬롯 번호(1~5) 변환.
  지금은 간단히 UID 정수값 mod 5 로 매핑한다.
  """
  uid_int = int.from_bytes(uid_bytes, "big")
  slot = (uid_int % 5) + 1  # 1~5
  return slot


def read_slot_from_nfc(pn532: PN532_I2C, timeout: float = 0.5) -> int | None:
  uid = pn532.read_passive_target(timeout=timeout)
  if uid is None:
    return None
  slot = uid_to_slot(uid)
  print(f"NFC 태그 감지 → UID={uid.hex()} → 슬롯 {slot}번")
  return slot


def main_loop():
  # NFC 리더 초기화 (I2C 기준)
  i2c = busio.I2C(board.SCL, board.SDA)
  pn532 = PN532_I2C(i2c, debug=False)
  pn532.SAM_configuration()

  print("NFC 태그를 리더에 가져다 대세요. (종료: Ctrl+C)")

  # 부팅 직후에는 기본 대기 영상 재생
  if os.path.exists(FALLBACK_VIDEO_PATH):
    play_simple_video(FALLBACK_VIDEO_PATH)

  while True:
    try:
      slot_number = read_slot_from_nfc(pn532)
      if slot_number is None:
        time.sleep(0.1)
        continue

      # Supabase 에서 슬롯에 매핑된 배경 영상 URL 조회
      video_url = get_video_url_for_slot(slot_number)

      if video_url:
        print("매핑된 배경 영상 URL:", video_url)
        play_video_with_overlay(
          background_source=video_url,
          overlay_path=OVERLAY_VIDEO_PATH,
          fallback_path=FALLBACK_VIDEO_PATH,
        )
      else:
        print("매핑된 영상 없음 → 기본 대기 영상 재생")
        play_simple_video(FALLBACK_VIDEO_PATH)

    except KeyboardInterrupt:
      print("사용자 중단으로 종료합니다.")
      break
    except Exception as e:
      print("오류 발생:", e)
      # 심각한 오류 시에도 기기가 멈추지 않고 대기 영상으로 복귀
      play_simple_video(FALLBACK_VIDEO_PATH)


if __name__ == "__main__":
  main_loop()

