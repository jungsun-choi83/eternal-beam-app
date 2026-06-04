# Raspberry Pi 자동 실행 (systemd)

전원만 켜면 모니터 없이 `eternal_beam_pi.py`(ToF + NFC + 마이크 → Unity UDP)가
자동으로 시작되고, 비정상 종료 시 자동 복구됩니다.

## 설치 (Pi 5, Raspberry Pi OS)

```bash
cd ~/eternal-beam-app
sudo apt install -y python3-pip portaudio19-dev alsa-utils i2c-tools
pip install -r python/requirements-pi.txt

# 환경설정: 폰(Unity) Wi-Fi IP 입력
cp python/systemd/eternal-beam-pi.env.example python/systemd/eternal-beam-pi.env
nano python/systemd/eternal-beam-pi.env      # UDP_HOST=192.168.0.xx

sudo bash python/systemd/install.sh
```

## 확인

```bash
systemctl status eternal-beam-pi.service
journalctl -u eternal-beam-pi.service -f      # 실시간 UDP 전송 로그
```

## 배선/하드웨어 준비 사항 (조립 담당)

- I2C 활성화: `sudo raspi-config` → Interface Options → I2C → Enable
- ToF(VL53L0X), NFC(PN532) : I2C 버스 공유 (SDA/SCL)
- 마이크(INMP441) : I2S — `python/voice_to_unity.py` 상단 배선표 + `/boot/firmware/config.txt`
  ```
  dtparam=i2s=on
  dtoverlay=i2s-mmap
  ```
- `i2cdetect -y 1` 로 0x29(VL53L0X), 0x24(PN532) 주소 확인

## 일부 센서만 연결된 경우

`eternal_beam_pi.py` 는 init 실패한 센서를 자동으로 건너뛰고 나머지를 계속 구동합니다.
강제로 끄려면 `ExecStart` 에 `--no-voice` 등을 추가하세요.
PC에서 배선 없이 테스트: `python python/eternal_beam_pi.py --simulate`
