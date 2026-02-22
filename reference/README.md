# NFC 참조 코드 (Reference)

이터널빔 프로젝트용 NFC Writer / Reader 참조 구현입니다.

## 1. [앱용] NFC Writer (Flutter)
- **경로**: `nfc_writer_flutter/`
- **역할**: 슬롯 배송 전, 앱으로 NFC 태그에 영상 ID 기록
- **기술**: Flutter + nfc_manager
- **사용법**: 별도 Flutter 프로젝트로 열어서 실행

## 2. [프로젝터용] NFC Reader (Python)
- **경로**: `nfc_reader_projector/`
- **역할**: 슬롯 하단 NFC를 읽어 해당 영상 MP4 전체화면 재생
- **기술**: Python + PN532 + omxplayer/mpv/vlc
- **사용법**:
  ```bash
  cd nfc_reader_projector
  pip install -r requirements.txt
  # 영상 파일을 videos/ 폴더에 배치
  python nfc_video_player.py
  ```

## 데이터 형식
- NFC 태그에 기록되는 텍스트: `EB_VIDEO:{영상ID}`
- 예: `EB_VIDEO:christmas_wonder` → christmas_wonder.mp4 재생
