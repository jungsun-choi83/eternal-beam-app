#!/usr/bin/env python3
"""
INMP441 (I2S MEMS) → 음성 감지 → Unity UDP {"event":"voice"}

Unity: HologramInteractionController.OnVoiceTriggered() (액션 영상)

── Pi 5 + INMP441 배선 (요약) ──
  INMP441        Raspberry Pi
  VDD    → 3.3V
  GND    → GND
  SCK    → GPIO 18 (BCLK)
  WS     → GPIO 19 (LRCLK / WS)
  SD     → GPIO 20 (DIN)
  L/R    → GND  (Left 채널; High면 Right)

/boot/firmware/config.txt (Pi 5) 또는 /boot/config.txt:
  dtparam=i2s=on
  dtoverlay=i2s-mmap

재부팅 후:
  arecord -l          # 카드 번호 확인
  arecord -D plughw:1,0 -f S32_LE -r 48000 -c 2 -d 2 test.wav

── Python ──
  sudo apt install -y portaudio19-dev alsa-utils
  pip install pyaudio numpy

  python voice_to_unity.py --list-devices
  python voice_to_unity.py --device 1 --rate 48000 --channels 2

환경변수:
  UDP_HOST, UDP_PORT (기본 127.0.0.1:5005)
  VOICE_DEVICE_INDEX   PyAudio 입력 장치 번호
  VOICE_RATE             48000 권장 (INMP441 I2S)
  VOICE_CHANNELS         2 (I2S 스테레오 프레임) → 좌채널만 사용
  VOICE_MIC_CHANNEL      0=Left, 1=Right
  VOICE_RMS_THRESHOLD    말할 때 RMS (기본 1200, 환경에 맞게 조절)
  VOICE_HOLD_MS          이 시간 이상 크면 트리거 (기본 350)
  VOICE_COOLDOWN_SEC     재트리거 간격 (기본 3)
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Callable

import numpy as np

VOICE_COOLDOWN_SEC = float(os.getenv("VOICE_COOLDOWN_SEC", "3"))
VOICE_RMS_THRESHOLD = float(os.getenv("VOICE_RMS_THRESHOLD", "1200"))
VOICE_HOLD_MS = int(os.getenv("VOICE_HOLD_MS", "350"))
VOICE_DEVICE_INDEX = int(os.getenv("VOICE_DEVICE_INDEX", "0"))
VOICE_CHUNK = int(os.getenv("VOICE_CHUNK", "1024"))
# INMP441 on Pi I2S: often 48000 Hz stereo S32_LE; PyAudio may expose 48000 int16
VOICE_RATE = int(os.getenv("VOICE_RATE", "48000"))
VOICE_CHANNELS = int(os.getenv("VOICE_CHANNELS", "2"))
VOICE_MIC_CHANNEL = int(os.getenv("VOICE_MIC_CHANNEL", "0"))


def list_input_devices() -> None:
    import pyaudio  # type: ignore

    pa = pyaudio.PyAudio()
    print("PyAudio input devices:")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) > 0:
            print(
                f"  [{i}] {info.get('name')}  "
                f"in={int(info['maxInputChannels'])}  "
                f"defaultRate={int(info.get('defaultSampleRate', 0))}"
            )
    pa.terminate()


def _chunk_rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def run_voice_loop(
    send: Callable[[dict], None],
    *,
    simulate: bool = False,
    device_index: int | None = None,
    rate: int | None = None,
    channels: int | None = None,
    mic_channel: int | None = None,
) -> None:
    if simulate:
        print("[INMP441] simulate — Enter 키 = voice 이벤트")
        last = 0.0
        while True:
            try:
                input()
                now = time.monotonic()
                if now - last >= VOICE_COOLDOWN_SEC:
                    send({"event": "voice", "source": "inmp441_sim"})
                    last = now
            except (EOFError, KeyboardInterrupt):
                break
        return

    import pyaudio  # type: ignore

    dev = VOICE_DEVICE_INDEX if device_index is None else device_index
    sr = VOICE_RATE if rate is None else rate
    ch = VOICE_CHANNELS if channels is None else channels
    mic_ch = VOICE_MIC_CHANNEL if mic_channel is None else mic_channel

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=ch,
            rate=sr,
            input=True,
            input_device_index=dev,
            frames_per_buffer=VOICE_CHUNK,
        )
    except Exception as e:
        pa.terminate()
        raise RuntimeError(
            f"INMP441 stream open failed (device={dev}, rate={sr}, ch={ch}): {e}\n"
            "Run: python voice_to_unity.py --list-devices"
        ) from e

    print(
        f"[INMP441] listening device={dev} rate={sr} ch={ch} mic_ch={mic_ch} "
        f"rms>={VOICE_RMS_THRESHOLD} hold={VOICE_HOLD_MS}ms"
    )

    last_sent = 0.0
    loud_since: float | None = None

    try:
        while True:
            data = stream.read(VOICE_CHUNK, exception_on_overflow=False)
            interleaved = np.frombuffer(data, dtype=np.int16)
            if ch > 1:
                frames = interleaved.reshape(-1, ch)
                samples = frames[:, mic_ch].astype(np.float32)
            else:
                samples = interleaved.astype(np.float32)

            rms = _chunk_rms(samples)
            now = time.monotonic()

            if rms >= VOICE_RMS_THRESHOLD:
                if loud_since is None:
                    loud_since = now
                elif (now - loud_since) * 1000 >= VOICE_HOLD_MS and (now - last_sent) >= VOICE_COOLDOWN_SEC:
                    send({"event": "voice", "source": "inmp441", "rms": int(rms)})
                    last_sent = now
                    loud_since = None
            else:
                loud_since = None
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def main() -> None:
    import json
    import socket

    ap = argparse.ArgumentParser(description="INMP441 → Unity voice event (UDP)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--simulate", action="store_true", help="PC 테스트: Enter=voice")
    ap.add_argument("--device", type=int, default=None, help="PyAudio input device index")
    ap.add_argument("--rate", type=int, default=None, help="Sample rate (INMP441: 48000)")
    ap.add_argument("--channels", type=int, default=None, help="Capture channels (often 2)")
    ap.add_argument("--mic-channel", type=int, default=None, help="0=left, 1=right")
    ap.add_argument("--threshold", type=float, default=None, help="RMS threshold")
    args = ap.parse_args()

    if args.list_devices:
        list_input_devices()
        return

    global VOICE_RMS_THRESHOLD
    if args.threshold is not None:
        VOICE_RMS_THRESHOLD = args.threshold

    host = os.getenv("UDP_HOST", "127.0.0.1")
    port = int(os.getenv("UDP_PORT", "5005"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(payload: dict) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        sock.sendto(msg.encode("utf-8"), (host, port))
        print(f"[UDP → {host}:{port}] {msg}")

    run_voice_loop(
        _send,
        simulate=args.simulate,
        device_index=args.device,
        rate=args.rate,
        channels=args.channels,
        mic_channel=args.mic_channel,
    )


if __name__ == "__main__":
    main()
