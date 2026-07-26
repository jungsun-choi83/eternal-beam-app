# 보드 자동 실행 (systemd) — Raspberry Pi 5 / RK3566 공용

전원만 켜면 모니터 없이 `eternal_beam_pi.py`(ToF + NFC + 마이크 → Unity UDP)가
자동으로 시작되고, 비정상 종료 시 자동 복구됩니다.

보드별 GPIO/I2C 버스/오디오 카드 등은 코드에 하드코딩되어 있지 않고
[`python/hardware_config.yaml`](../hardware_config.yaml) 에서 읽습니다.
RPi5 ↔ RK3566 전환 시 이 파일(과 필요하면 `HARDWARE_BOARD` 환경변수)만 바꾸면 되고,
`python/*.py` 는 수정할 필요가 없습니다 — 자세한 내용은
[`docs/RK3566_이식_가이드.md`](../../docs/RK3566_이식_가이드.md) 참고.

## 설치

```bash
cd ~/eternal-beam-app
sudo apt install -y python3-pip portaudio19-dev alsa-utils i2c-tools gpiod libgpiod2
pip install -r python/requirements-pi.txt

# 보드 선택 (RPi5가 기본): RK3566이면 hardware_config.yaml 의 active_board: rk3566 로 바꾸거나
# python/systemd/eternal-beam-pi.env 에 HARDWARE_BOARD=rk3566 추가

# 환경설정: 폰(Unity) Wi-Fi IP 입력
cp python/systemd/eternal-beam-pi.env.example python/systemd/eternal-beam-pi.env
nano python/systemd/eternal-beam-pi.env      # UDP_HOST=192.168.0.xx

sudo bash python/systemd/install.sh
```

## 확인

```bash
systemctl status eternal-beam-pi.service
journalctl -u eternal-beam-pi.service -f      # 실시간 UDP 전송 로그
python3 python/sensor_check.py                # I2C/거리/마이크 점검 (보드 설정대로 bus 자동 선택)
```

## 배선/하드웨어 준비 사항 (조립 담당)

- I2C 활성화: RPi5는 `sudo raspi-config` → Interface Options → I2C → Enable.
  RK3566 등은 보드 dtb/커널 설정에서 이미 켜져 있는 경우가 많음 — `i2cdetect -l` 로 확인.
- ToF(VL53L0X), NFC(PN532) : I2C 버스 공유 (SDA/SCL). 버스 번호는
  `hardware_config.yaml` 의 `boards.<board>.i2c.bus` 에 설정 (RPi5=1, RK3566은 보드마다 다름).
- 마이크(INMP441 등) : I2S — `python/voice_to_unity.py` 상단 배선표.
  RPi5는 `/boot/firmware/config.txt` 에 `dtparam=i2s=on`, `dtoverlay=i2s-mmap`.
- `python3 python/sensor_check.py` 로 I2C 주소(0x29=VL53L0X, 0x24=PN532) 확인 —
  버스 번호는 hardware_config.yaml 설정을 자동으로 사용.
- GPIO(선택, 상태 LED 등): `gpiodetect` 로 칩 이름 확인 후 `hardware_config.yaml` 의
  `gpio.chip` / `gpio.lines.status_led` 에 설정.

## 일부 센서만 연결된 경우

`eternal_beam_pi.py` 는 init 실패한 센서를 자동으로 건너뛰고 나머지를 계속 구동합니다.
강제로 끄려면 `ExecStart` 에 `--no-voice` 등을 추가하세요.
PC에서 배선 없이 테스트: `python python/eternal_beam_pi.py --simulate`
