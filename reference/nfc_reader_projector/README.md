# [프로젝터용] NFC 슬롯 감지 + 영상 재생기 (NFC Reader)

본체에 슬롯이 들어왔을 때, 하단 NFC를 읽어 어떤 영상인지 판단하고 해당 MP4를 전체화면 재생합니다.

## 사용 기술
- Python 3
- PN532 (I2C/SPI/UART) NFC 리더
- OMXPlayer 또는 VLC (라즈베리파이) / mpv (일반 PC) - 전체화면 비디오 재생

## 하드웨어
- PN532 NFC 리더 모듈
- 라즈베리파이 또는 호환 SBC (I2C 연결)

## 설정
```bash
pip install -r requirements.txt
# 영상 파일을 videos/ 폴더에 배치
# 예: videos/christmas_wonder.mp4
```
