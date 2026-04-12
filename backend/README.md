# Eternal Beam 영상 처리 백엔드 (SSR)

서버 사이드에서 **누끼(rembg + Alpha Matting)** 와 **FFmpeg 합성**을 수행합니다.

## 요구사항

- Python 3.10+
- FFmpeg (PATH에 설치)
- (선택) GPU용: `pip install rembg[gpu]`

## 설치 및 실행

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

`.env` (프로젝트 루트 또는 backend):

```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
# 테마 영상 폴더 (선택)
THEMES_VIDEO_DIR=./themes
# 또는 테마별 URL
# THEME_VIDEO_URL_GALAXY_DREAM=https://...
```

테마 MP4를 `backend/themes/` 에 두거나, 환경 변수로 URL 지정:

- `sweet_memory.mp4`, `galaxy_dream.mp4`, `nature_bloom.mp4` 등 (BGM_PRESETS id와 동일한 파일명)

실행:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

프로젝트 루트에서:

```bash
uvicorn backend.main:app --reload --port 8000
```

## API

- `POST /api/cutout` — 이미지 업로드 → rembg + Alpha Matting → PNG (또는 Storage URL)
- `POST /api/compose-video` — 누끼 PNG + theme_id → 결제 Gate 후 FFmpeg 합성 → unique_url, nfc_payload
- `GET /api/purchased-slots?user_id=` — 구매한 테마 ID 목록
- `GET /health` — 헬스 체크

## 결제 Gate

유료 테마 ID(`galaxy_dream`, `neon_city`)로 합성 요청 시, `payment_status=true` 인 경우에만 FFmpeg 합성을 진행합니다.

## NFC payload

`/api/compose-video` 응답의 `nfc_payload`를 NFC 태그에 기록하면, 기기에서 `unique_url`로 영상을 재생할 수 있습니다.

## DB (Supabase)

- `user_assets`: 원본/누끼/합성영상 URL
- `purchased_slots`: 유저별 구매 테마 및 권한

마이그레이션: `supabase/migrations/20250302000000_user_assets_purchased_slots.sql`  
Storage 버킷 `user-assets` 생성 후 Public 또는 서비스 역할로 업로드 가능하게 설정하세요.
