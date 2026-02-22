#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
[프로젝터용] NFC 슬롯 감지 + 영상 재생기 (NFC Reader)
═══════════════════════════════════════════════════════════════════════════════
본체에 슬롯이 들어왔을 때, 하단의 NFC 태그를 읽어서
태그 안의 영상 ID를 추출하고, 해당 ID에 맞는 MP4 파일을 자동으로 전체화면 재생합니다.

예: NFC 태그에 "EB_VIDEO:christmas_wonder" 가 있으면
    → videos/christmas_wonder.mp4 를 재생

필요 하드웨어: PN532 NFC 리더 (I2C/SPI/UART)
═══════════════════════════════════════════════════════════════════════════════
"""

import time
import subprocess
import os

# PN532 NFC 리더용 라이브러리 (pip install pn532pi)
# 라즈베리파이 I2C: from pn532pi import Pn532I2c
# 라즈베리파이 SPI: from pn532pi import Pn532Spi
# 여기서는 시뮬레이션 모드도 지원 (NFC 없이 테스트 가능)

try:
    from pn532pi import Pn532I2c
    NFC_AVAILABLE = True
except ImportError:
    NFC_AVAILABLE = False
    print("[주의] pn532pi가 설치되지 않았습니다. 시뮬레이션 모드로 동작합니다.")


# ═══════════════════════════════════════════════════════════════════════════════
# 설정 (필요에 따라 수정)
# ═══════════════════════════════════════════════════════════════════════════════

# 영상 파일이 들어 있는 폴더 경로
VIDEO_DIR = os.path.join(os.path.dirname(__file__), "videos")

# 영상 ID → 파일명 매핑
# NFC 태그에 "EB_VIDEO:christmas_wonder" 가 있으면 christmas_wonder.mp4 재생
VIDEO_MAP = {
    "christmas_wonder": "christmas_wonder.mp4",
    "sunset_beach": "sunset_beach.mp4",
    "forest_dream": "forest_dream.mp4",
    "memorial_light": "memorial_light.mp4",
    "galaxy_trip": "galaxy_trip.mp4",
    "sakura_garden": "sakura_garden.mp4",
    "ocean_dive": "ocean_dive.mp4",
    "aurora_light": "aurora_light.mp4",
}

# NFC 태그 접두사 (앱에서 기록할 때 사용한 형식과 동일해야 함)
TAG_PREFIX = "EB_VIDEO:"

# 태그 재감지 방지 대기 시간 (초) - 같은 슬롯을 다시 읽지 않도록
COOLDOWN_SECONDS = 3


# ═══════════════════════════════════════════════════════════════════════════════
# 비디오 재생 함수
# ═══════════════════════════════════════════════════════════════════════════════

def play_video_fullscreen(video_path: str) -> subprocess.Popen | None:
    """
    MP4 파일을 전체화면으로 재생합니다.

    - 라즈베리파이: omxplayer 또는 vlc 사용 (하드웨어 가속)
    - 일반 PC: mpv 또는 vlc 사용
    """
    if not os.path.isfile(video_path):
        print(f"[오류] 파일 없음: {video_path}")
        return None

    # 재생기 선택 (시스템에 설치된 것 사용)
    # 라즈베리파이: omxplayer가 보통 기본
    # PC: mpv 또는 vlc
    for player, args in [
        ("omxplayer", ["-o", "hdmi", "--loop", "--no-osd", video_path]),
        ("mpv", ["--fs", "--loop", "--no-osc", video_path]),
        ("vlc", ["--fullscreen", "--loop", "--no-video-title-show", video_path]),
    ]:
        try:
            proc = subprocess.Popen(
                [player] + args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[재생] {video_path} ({player})")
            return proc
        except FileNotFoundError:
            continue

    print("[오류] 사용 가능한 비디오 플레이어가 없습니다. (omxplayer, mpv, vlc 중 설치 필요)")
    return None


def stop_current_video(proc: subprocess.Popen | None) -> None:
    """현재 재생 중인 비디오를 중지합니다."""
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=2)


# ═══════════════════════════════════════════════════════════════════════════════
# NFC 읽기 함수
# ═══════════════════════════════════════════════════════════════════════════════

def read_nfc_tag_pn532() -> str | None:
    """
    PN532 리더로 NFC 태그의 NDEF 텍스트를 읽습니다.
    "EB_VIDEO:christmas_wonder" 형식이면 "christmas_wonder" 반환.
    """
    if not NFC_AVAILABLE:
        return None

    try:
        # I2C 연결 (라즈베리파이 기본: /dev/i2c-1, 주소 0x24)
        pn532 = Pn532I2c(1, 0x24)
        pn532.SAM_configuration()

        # 태그 감지 (타임아웃 1초)
        uid = pn532.read_passive_target(timeout=1)
        if uid is None:
            return None

        # NDEF 메시지 읽기
        if not pn532.ntag2xx_read_tag():
            return None

        # NDEF 레코드에서 텍스트 추출 (pn532pi API에 따라 구현 조정 필요)
        # 여기서는 get_ndef_message 등 해당 라이브러리의 실제 API를 사용
        # 예시: text = pn532.get_ndef_text()
        text = _extract_ndef_text_from_pn532(pn532)

        if text and text.startswith(TAG_PREFIX):
            video_id = text[len(TAG_PREFIX) :].strip()
            return video_id if video_id in VIDEO_MAP else None

    except Exception as e:
        print(f"[NFC 오류] {e}")

    return None


def _extract_ndef_text_from_pn532(pn532) -> str | None:
    """
    pn532pi 라이브러리의 실제 API에 맞게 NDEF 텍스트 추출.
    라이브러리 버전에 따라 메서드명이 다를 수 있음.
    """
    try:
        # pn532pi 예시: ndef = pn532.read_ndef()
        # 여기서는 공통 인터페이스 가정
        if hasattr(pn532, "read_ndef_message"):
            msg = pn532.read_ndef_message()
            if msg and msg.records:
                rec = msg.records[0]
                if hasattr(rec, "text"):
                    return rec.text
        return None
    except Exception:
        return None


def read_nfc_tag_simulated() -> str | None:
    """
    NFC 없이 테스트할 때 사용하는 시뮬레이션.
    next_video.txt 파일에 영상 ID를 적어두면 그 영상을 재생합니다.
    예: echo christmas_wonder > next_video.txt
    """
    sim_file = os.path.join(VIDEO_DIR, "next_video.txt")
    if os.path.isfile(sim_file):
        try:
            with open(sim_file, "r") as f:
                video_id = f.read().strip()
            os.remove(sim_file)
            return video_id if video_id in VIDEO_MAP else None
        except (OSError, IOError):
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 메인 루프
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("이터널빔 NFC 프로젝터 - 슬롯 감지 대기 중...")
    print("(슬롯 하단 NFC 태그를 리더에 갖다 대세요)")
    print("-" * 50)

    os.makedirs(VIDEO_DIR, exist_ok=True)

    last_video_id: str | None = None
    cooldown_until = 0.0
    current_player: subprocess.Popen | None = None

    # NFC 하드웨어 있으면 PN532 사용, 없으면 파일 기반 시뮬레이션
    read_tag_fn = read_nfc_tag_pn532 if NFC_AVAILABLE else read_nfc_tag_simulated

    if not NFC_AVAILABLE:
        print("[시뮬레이션] videos/next_video.txt 에 영상 ID를 적으면 재생됩니다.")
        print("  예: echo christmas_wonder > videos/next_video.txt")
    print("-" * 50)

    try:
        while True:
            now = time.time()

            # 쿨다운 중이면 NFC 읽기 스킵 (같은 태그 반복 인식 방지)
            if now < cooldown_until:
                time.sleep(0.5)
                continue

            video_id = read_tag_fn()

            if video_id and video_id != last_video_id:
                # 새 슬롯 감지
                filename = VIDEO_MAP.get(video_id)
                video_path = os.path.join(VIDEO_DIR, filename) if filename else None

                if video_path:
                    stop_current_video(current_player)
                    current_player = play_video_fullscreen(video_path)
                    last_video_id = video_id
                else:
                    print(f"[경고] 알 수 없는 영상 ID: {video_id}")

                cooldown_until = now + COOLDOWN_SECONDS

            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n종료합니다.")
        stop_current_video(current_player)


if __name__ == "__main__":
    main()
