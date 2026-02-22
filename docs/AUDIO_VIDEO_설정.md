# 음성/음악/영상 합성 시스템 설정

## 요구사항

- **FFmpeg**: 시스템에 설치되어 있어야 함
  - Windows: `winget install FFmpeg` 또는 [ffmpeg.org](https://ffmpeg.org)에서 다운로드
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg` (Ubuntu/Debian)

## 실행 방법

1. **백엔드 서버** (FFmpeg 합성 API):
   ```bash
   npm run server
   # 또는 개발 시 자동 재시작:
   npm run server:dev
   ```
   - 기본 포트: 3001
   - `SERVER_PORT=3002 npm run server` 로 포트 변경 가능

2. **프론트엔드** (Vite):
   ```bash
   npm run dev
   ```
   - `/api`, `/output` 요청은 자동으로 localhost:3001으로 프록시됨

## 기본 음악 라이브러리

`public/assets/music/` 폴더에 MP3/M4A/AAC/WAV 파일을 넣으면 앱에서 "기본 라이브러리"로 선택할 수 있습니다.

예시:
```
public/assets/music/
  sweet_memory.mp3
  galaxy_dream.mp3
  nature_bloom.mp3
```

## Pi Zero 2W 전송

현재 `/api/device/:deviceId/upload` 와 `/api/device/:deviceId/play` 엔드포인트는 준비되어 있습니다.  
실제 Wi-Fi 전송 및 재생 트리거는 Pi 쪽 API와 연동하도록 구현해야 합니다.
