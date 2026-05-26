# Eternal Beam 배포 가이드 (Vercel + Render)

이 프로젝트는 아래 2개를 **따로 배포**합니다.

- **프론트엔드**: Vite (Vercel)
- **누끼/합성 백엔드**: FastAPI + FFmpeg + rembg (Render, Docker)

---

## 1) 백엔드(Render) 배포

**상세 한국어 가이드:** [`docs/RENDER_설정_가이드.md`](docs/RENDER_설정_가이드.md)

> `curl https://eternal-beam-video-api.onrender.com/health` 가 **404** 이고 `x-render-routing: no-server` 이면  
> Blueprint를 아직 연결하지 않은 상태입니다.

### A. Render Blueprint (권장)

1. [Render Dashboard](https://dashboard.render.com) → **New → Blueprint**
2. GitHub `jungsun-choi83/eternal-beam-app` → `render.yaml` **Apply**
3. Environment에 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `LUMA_API_KEY` 입력
4. 배포 완료 후 `GET /health` → `{"status":"ok"}`

### B. 환경변수 (Render → Environment)

| 변수 | 용도 |
|------|------|
| `SUPABASE_URL` | Storage·DB |
| `SUPABASE_SERVICE_ROLE_KEY` | 서버 전용 |
| `SUPABASE_STORAGE_BUCKET` | 예: `user-assets` |
| `LUMA_API_KEY` | 크레딧/Luma 생성 (없으면 MOCK만) |
| `PUBLIC_API_BASE_URL` | `https://eternal-beam-video-api.onrender.com` (웹훅) |

Supabase SQL (대시보드 SQL Editor):

1. `docs/supabase_hybrid_business.sql` — 지갑·모션·크레딧
2. (선택) `docs/supabase_pet_scenarios.sql` — 40건 배치

누끼만 쓸 때는 Supabase 없이도 `/api/cutout` base64 반환은 동작합니다.

### C. 배포 확인

배포가 끝나면 Render 서비스 URL이 생깁니다. 예:

- `https://eternal-beam-video-api.onrender.com`

브라우저에서 아래를 확인하세요.

- `GET /health` → `{ "status": "ok" }`
- `GET /docs` → FastAPI Swagger UI

---

## 2) 프론트(Vercel) 배포

### A. Vercel 프로젝트 생성

- Vercel 대시보드에서 **New Project**
- Framework: Vite (자동 인식)
- Build Command: `npm run build`
- Output: `dist`

### B. 환경변수 설정(필수)

Vercel 프로젝트 → **Settings → Environment Variables**

- Key: `VITE_VIDEO_API_URL`
- Value: Render에서 배포된 백엔드 URL (끝 슬래시 없이)
  - 예: `https://eternal-beam-video-api.onrender.com`
- **주의**: `trycloudflare.com` 임시 터널 URL은 몇 시간 뒤 만료되어 `ERR_NAME_NOT_RESOLVED` 가 납니다. 넣었다면 **삭제** 후 재배포하세요.

설정하지 않아도 루트 `vercel.json`이 `/api`를 Render로 프록시합니다(같은 도메인 `/api/cutout`).

설정 후 **Redeploy** 하세요.

---

## 3) 동작 확인(누끼)

프론트에서 업로드 후 Processing 단계에서 아래 API가 호출됩니다.

- `POST {VITE_VIDEO_API_URL}/api/cutout`

만약 `VITE_VIDEO_API_URL`을 설정하지 않았거나 `/api` 프록시가 없다면,
프로덕션에서 404가 발생하며 화면에 “API가 설정되지 않았습니다” 안내가 표시됩니다.

