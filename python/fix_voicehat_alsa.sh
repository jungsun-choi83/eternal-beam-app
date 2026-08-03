#!/usr/bin/env bash
# INMP441 (googlevoicehat card 2) → PyAudio가 인식하도록 ALSA 기본 장치 설정
#   bash fix_voicehat_alsa.sh
set -euo pipefail

CARD="${VOICE_ALSA_CARD:-2}"

echo "[*] capture card = $CARD (arecord -l 로 확인)"

cat >"$HOME/.asoundrc" <<EOF
# Eternal Beam — INMP441 googlevoicehat
pcm.capture_mic {
    type plug
    slave.pcm "hw:${CARD},0"
}

pcm.!default {
    type asym
    playback.pcm {
        type plug
        slave.pcm "hw:0,0"
    }
    capture.pcm "capture_mic"
}

ctl.!default {
    type hw
    card ${CARD}
}
EOF

echo "[+] ~/.asoundrc 작성됨"
cat "$HOME/.asoundrc"

echo ""
echo "[*] arecord 테스트 (2초)…"
if arecord -D plughw:${CARD},0 -f S16_LE -r 48000 -c 2 -d 2 /tmp/voice_test.wav 2>/tmp/arecord.err; then
  ls -lh /tmp/voice_test.wav
  echo "[OK] arecord 성공"
else
  echo "[!] arecord 실패 (마이크 사용 중일 수 있음):"
  cat /tmp/arecord.err 2>/dev/null || true
  echo "    → pkill -f eternal_beam_pi; pkill -f s23_bridge 후 다시 실행"
fi

echo ""
echo "[*] PyAudio 장치 목록:"
python3 <<'PY'
import pyaudio
pa = pyaudio.PyAudio()
print("PyAudio devices:")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    ins = int(info.get("maxInputChannels", 0))
    outs = int(info.get("maxOutputChannels", 0))
    if ins > 0 or outs > 0:
        print(f"  [{i}] {info.get('name')}  in={ins} out={outs}")
pa.terminate()
PY

echo ""
echo "다음:"
echo "  export VOICE_DEVICE_INDEX=-1"
echo "  export UDP_HOST=172.30.1.54"
echo "  python3 voice_to_unity.py"
