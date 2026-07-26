# S23 + 라즈베리파이 2디스플레이 구조

Eternal Beam 기계는 **화면이 두 개**입니다. 반드시 **두 프로세스를 동시에** 실행해야 합니다.

| 디스플레이 | 역할 | 프로세스 | UDP |
|---|---|---|---|
| **Pi 터치스크린** | 배경 영상 (숲 등) | `pi_display_bg.py` | **수신 :9999** |
| **S23 (PetVFX APK)** | 강아지 idle / 액션 | `eternal_beam_pi.py` 또는 `s23_bridge_simple.py` | **송신 → S23 :5005** |

## 왜 S23만 되거나 배경만 되나?

| 증상 | 원인 |
|---|---|
| S23만 반응, Pi 배경 없음 | `pi_display_bg.py`(또는 `film_display_simple.py`) **미실행** |
| Pi 배경만, S23 idle 없음 | 브리지 미실행 또는 `UDP_HOST`에 S23 Wi‑Fi IP 미설정 |
| NFC 시 배경만 바뀜 | (구버전) NFC가 :9999만 향함 → **최신 브리지는 S23에도 `nfc_match` 전송** |

## 이벤트 라우팅 (최신 `eternal_beam_pi.py`)

```
NFC 카드
  ├─► udp://127.0.0.1:9999  {"event":"nfc_tagged","theme_id":"forest"}
  └─► udp://S23_IP:5005      {"event":"nfc_match","theme_id":"forest","source":"pi_nfc"}

손 가까이 (ToF touch/approach)
  └─► udp://S23_IP:5005      {"event":"approach","action_id":"RUN","mock":true}  ← 달려오기 목업

마이크 (voice)
  └─► udp://S23_IP:5005      {"event":"voice",...}

액션 후 10초
  └─► S23 idle 복귀         {"event":"nfc_match"} + {"event":"idle"}
```

환경변수 `ACTION_MOCK=run`(기본) — `off`면 예전처럼 plain approach만.

## Pi에서 실행 (촬영/현장)

**터미널 1 — 배경**
```bash
cd ~/eternal-beam-app/python
export DISPLAY=:0 XAUTHORITY=/home/pi/.Xauthority
python3 pi_display_bg.py --videos-dir ./backgrounds --wait-nfc
```

**터미널 2 — 센서 브리지**
```bash
export UDP_HOST=<S23_WiFi_IP>
export BG_DISPLAY_HOST=127.0.0.1
export BG_DISPLAY_PORT=9999
export ACTION_MOCK=run
python3 eternal_beam_pi.py --sse-port 0
```

또는 한 번에:
```bash
bash start_machine_sensors.sh bg      # 터미널1
bash start_machine_sensors.sh bridge  # 터미널2 (UDP_HOST 설정)
```

## systemd (부팅 자동)

```bash
sudo systemctl enable --now pi-display-bg.service
sudo systemctl enable --now s23-bridge.service   # s23-bridge.env 에 S23_IP
```

## 수동 테스트

```bash
# S23 달려오기 목업
bash send_s23_action.sh <S23_IP> approach

# Pi 배경만
echo '{"event":"nfc_tagged","theme_id":"forest"}' | nc -u -w1 127.0.0.1 9999
```

## 필수 파일

- `python/backgrounds/fresh_forest.mp4` — Pi 배경
- S23 **Pet.apk** 실행, UDP **5005** 수신 대기
- `python/nfc_theme_map.json` — NFC UID → theme_id
