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
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from hardware import load_hardware_config  # noqa: E402

_HW = load_hardware_config()


def _voice_event_payload(**extra: object) -> dict:
    body: dict = {"event": "voice", **extra}
    try:
        from pet_wake_store import voice_payload_extras

        body.update(voice_payload_extras())
    except Exception:
        pass
    return body


VOICE_COOLDOWN_SEC = float(os.getenv("VOICE_COOLDOWN_SEC", _HW.get("voice", "cooldown_sec", default=3)))
VOICE_RMS_THRESHOLD = float(os.getenv("VOICE_RMS_THRESHOLD", _HW.get("voice", "rms_threshold", default=1200)))
VOICE_HOLD_MS = int(os.getenv("VOICE_HOLD_MS", _HW.get("voice", "hold_ms", default=350)))
VOICE_DEVICE_INDEX = int(os.getenv("VOICE_DEVICE_INDEX", "-1"))
VOICE_CHUNK = int(os.getenv("VOICE_CHUNK", _HW.get("voice", "chunk", default=1024)))
# I2S 마이크(INMP441 등): 보드별로 흔히 48000Hz 스테레오 S32_LE; PyAudio는 48000 int16 로 노출.
VOICE_RATE = int(os.getenv("VOICE_RATE", _HW.get("voice", "rate", default=48000)))
VOICE_CHANNELS = int(os.getenv("VOICE_CHANNELS", _HW.get("voice", "channels", default=2)))
VOICE_MIC_CHANNEL = int(os.getenv("VOICE_MIC_CHANNEL", _HW.get("voice", "mic_channel", default=0)))
# ALSA 카드 번호는 보드마다 다름(arecord -l 로 확인) — hardware_config.yaml 의 audio.alsa_card 사용.
VOICE_ALSA_CARD = _HW.alsa_card.strip()
VOICE_DEVICE_KEYWORDS = tuple(
    str(k).lower() for k in _HW.get("voice", "device_keywords", default=["i2s", "inmp", "snd_rpi", "googlevoice", "voicehat", "seeed"])
)
VOICE_USE_ARECORD = os.getenv("VOICE_USE_ARECORD", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
VOICE_DEBUG_RMS = os.getenv("VOICE_DEBUG_RMS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def list_input_devices() -> None:
    import pyaudio  # type: ignore

    pa = pyaudio.PyAudio()
    print("PyAudio devices (capture 가능 in>0):")
    found = False
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        ins = int(info.get("maxInputChannels", 0))
        outs = int(info.get("maxOutputChannels", 0))
        if ins > 0:
            found = True
            print(
                f"  [{i}] {info.get('name')}  "
                f"in={ins} out={outs}  "
                f"defaultRate={int(info.get('defaultSampleRate', 0))}"
            )
    if not found:
        print("  (없음) → arecord -l 확인, ~/.asoundrc, bash fix_voicehat_alsa.sh")
    pa.terminate()


def autodetect_input_device() -> int | None:
    """INMP441 / googlevoicehat PyAudio 인덱스."""
    import pyaudio  # type: ignore

    keywords = VOICE_DEVICE_KEYWORDS
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
            name = str(info.get("name", "")).lower()
            if any(k in name for k in keywords):
                return i
        try:
            default = pa.get_default_input_device_info()
            if int(default.get("maxInputChannels", 0)) > 0:
                return int(default["index"])
        except OSError:
            pass
        return None
    finally:
        pa.terminate()


def _open_input_stream(
    pa: object,
    *,
    device_index: int | None = None,
    rate: int | None = None,
    channels: int | None = None,
) -> tuple[object, int, int, int]:
    """여러 device/rate/ch 조합 시도 → (stream, dev, rate, ch)."""
    import pyaudio  # type: ignore

    base_rate = VOICE_RATE if rate is None else rate
    base_ch = VOICE_CHANNELS if channels is None else channels
    rates = list(dict.fromkeys([base_rate, 48000, 44100]))
    chans = list(dict.fromkeys([base_ch, 2, 1]))
    dev_candidates: list[int] = []

    forced = VOICE_DEVICE_INDEX if device_index is None else device_index
    if forced is not None and forced >= 0:
        dev_candidates.append(forced)

    detected = autodetect_input_device()
    if detected is not None and detected not in dev_candidates:
        dev_candidates.insert(0, detected)

    keywords = VOICE_DEVICE_KEYWORDS
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) <= 0:
            continue
        name = str(info.get("name", "")).lower()
        if any(k in name for k in keywords) and i not in dev_candidates:
            dev_candidates.append(i)

    for i in range(pa.get_device_count()):
        if int(pa.get_device_info_by_index(i).get("maxInputChannels", 0)) > 0:
            if i not in dev_candidates:
                dev_candidates.append(i)

    tried: set[tuple[int, int, int]] = set()
    last_err: Exception | None = None
    for dev in dev_candidates:
        for sr in rates:
            for ch in chans:
                key = (dev, sr, ch)
                if key in tried:
                    continue
                tried.add(key)
                try:
                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=ch,
                        rate=sr,
                        input=True,
                        input_device_index=dev,
                        frames_per_buffer=VOICE_CHUNK,
                    )
                    print(
                        f"[INMP441] opened device={dev} rate={sr} ch={ch}",
                        flush=True,
                    )
                    return stream, dev, sr, ch
                except Exception as e:  # noqa: BLE001
                    last_err = e
    raise RuntimeError(
        f"INMP441 stream open failed (tried {len(tried)} combos): {last_err}\n"
        "  arecord -l / ~/.asoundrc / bash fix_voicehat_alsa.sh\n"
        "  python3 voice_to_unity.py --list-devices"
    )


def _chunk_rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def _start_arecord_capture(
    card: str,
    *,
    rate: int,
    channels: int,
) -> tuple[object, int, int]:
    """arecord 프로세스 시작. (proc, rate, channels)"""
    import subprocess

    device = f"plughw:{card},0"
    chunk_bytes = VOICE_CHUNK * channels * 2
    cmd = [
        "arecord",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(rate),
        "-c",
        str(channels),
        "-t",
        "raw",
        "-q",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdout is None:
        raise RuntimeError("arecord stdout 없음")
    return proc, rate, channels, chunk_bytes, device


def _run_voice_loop_arecord(
    send: Callable[[dict], None],
    *,
    mic_channel: int,
) -> None:
    """INMP441 — arecord(card 2) 로 직접 캡처 → voice UDP."""
    card = VOICE_ALSA_CARD or "2"
    sr = VOICE_RATE
    ch_try = [VOICE_CHANNELS, 2, 1]
    ch_try = list(dict.fromkeys(c for c in ch_try if c in (1, 2)))

    proc = None
    ch = 2
    chunk_bytes = 0
    device = ""
    last_err = ""
    pending = b""
    for ch in ch_try:
        for attempt in range(3):
            try:
                proc, sr, ch, chunk_bytes, device = _start_arecord_capture(
                    card, rate=sr, channels=ch
                )
                probe = proc.stdout.read(chunk_bytes)
                if not probe:
                    err = proc.stderr.read().decode() if proc.stderr else ""
                    raise RuntimeError(err or "arecord empty read")
                print(
                    f"[INMP441] arecord OK {device} rate={sr} ch={ch} "
                    f"rms>={VOICE_RMS_THRESHOLD} hold={VOICE_HOLD_MS}ms",
                    flush=True,
                )
                pending = probe
                break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                if proc is not None:
                    proc.terminate()
                    proc = None
                if "busy" in last_err.lower() and attempt < 2:
                    import subprocess

                    print("[INMP441] 마이크 busy — 정리 후 재시도…", flush=True)
                    subprocess.run(
                        ["pkill", "-9", "arecord"],
                        check=False,
                        capture_output=True,
                    )
                    time.sleep(1.5)
                    continue
                break
        if proc is not None:
            break
    else:
        raise RuntimeError(
            f"arecord 실패 card={card}: {last_err}\n"
            "  bash free_mic.sh\n"
            "  pkill -f eternal_beam_pi; pkill -f s23_bridge\n"
            "  bash fix_voicehat_alsa.sh"
        )

    last_sent = 0.0
    loud_since: float | None = None
    debug_peak = 0.0
    debug_at = time.monotonic()
    pending_local = pending

    try:
        while True:
            if pending_local:
                data = pending_local
                pending_local = b""
            else:
                data = proc.stdout.read(chunk_bytes)
            if not data:
                err = proc.stderr.read().decode() if proc.stderr else ""
                raise RuntimeError(f"arecord 종료: {err}")
            interleaved = np.frombuffer(data, dtype=np.int16)
            use_ch = min(mic_channel, ch - 1) if ch > 1 else 0
            if ch > 1:
                frames = interleaved.reshape(-1, ch)
                samples = frames[:, use_ch].astype(np.float32)
            else:
                samples = interleaved.astype(np.float32)
            rms = _chunk_rms(samples)
            now = time.monotonic()

            if VOICE_DEBUG_RMS and now - debug_at >= 1.0:
                print(f"[INMP441] rms peak={debug_peak:.0f} (threshold={VOICE_RMS_THRESHOLD})", flush=True)
                debug_peak = 0.0
                debug_at = now
            if rms > debug_peak:
                debug_peak = rms

            if rms >= VOICE_RMS_THRESHOLD:
                if loud_since is None:
                    loud_since = now
                elif (now - loud_since) * 1000 >= VOICE_HOLD_MS and (now - last_sent) >= VOICE_COOLDOWN_SEC:
                    send(_voice_event_payload(source="inmp441", rms=int(rms)))
                    print(f"[INMP441] → voice (rms={int(rms)})", flush=True)
                    last_sent = now
                    loud_since = None
            else:
                loud_since = None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


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
                    send(_voice_event_payload(source="inmp441_sim"))
                    last = now
            except (EOFError, KeyboardInterrupt):
                break
        return

    mic_ch = VOICE_MIC_CHANNEL if mic_channel is None else mic_channel

    if VOICE_USE_ARECORD:
        _run_voice_loop_arecord(send, mic_channel=mic_ch)
        return

    import pyaudio  # type: ignore

    pa = pyaudio.PyAudio()
    try:
        stream, dev, sr, ch = _open_input_stream(
            pa,
            device_index=device_index,
            rate=rate,
            channels=channels,
        )
    except Exception as pyaudio_err:
        pa.terminate()
        print(f"[INMP441] PyAudio 실패 → arecord 사용: {pyaudio_err}", flush=True)
        _run_voice_loop_arecord(send, mic_channel=mic_ch)
        return

    print(
        f"[INMP441] listening device={dev} rate={sr} ch={ch} mic_ch={mic_ch} "
        f"rms>={VOICE_RMS_THRESHOLD} hold={VOICE_HOLD_MS}ms",
        flush=True,
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
                    send(_voice_event_payload(source="inmp441", rms=int(rms)))
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
