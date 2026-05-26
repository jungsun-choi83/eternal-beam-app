# Render 백엔드 배포 (5분)

프론트는 이미 **Vercel** (`device.eternalbeam.com`) 입니다.  
백엔드만 **Render**에 올리면 `/api/cutout`, 크레딧 API, Luma 웹훅이 동작합니다.

---

## 1. Blueprint 연결 (최초 1회)

1. [dashboard.render.com](https://dashboard.render.com) 로그인
2. **New +** → **Blueprint**
3. GitHub **`jungsun-choi83/eternal-beam-app`** 연결
4. `render.yaml` 인식 → **Apply**
5. 서비스 이름: **`eternal-beam-video-api`**  
   URL: `https://eternal-beam-video-api.onrender.com`

배포가 끝날 때까지 5~15분 걸릴 수 있습니다 (Docker + rembg 설치).

---

## 2. Environment Variables (필수)

Render → **eternal-beam-video-api** → **Environment**

| 변수 | 값 |
|------|-----|
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role JWT (프론트에 넣지 말 것) |
| `SUPABASE_STORAGE_BUCKET` | `user-assets` (버킷 이름) |
| `LUMA_API_KEY` | Luma API 키 (`luma-...`) — 없으면 MOCK만 |
| `PUBLIC_API_BASE_URL` | `https://eternal-beam-video-api.onrender.com` |

**Save Changes** 후 자동 재배포됩니다.

---

## 3. Supabase SQL

Supabase → **SQL Editor** → 붙여넣기 실행:

- `docs/supabase_hybrid_business.sql` (지갑·모션·크레딧)
- (선택) `docs/supabase_pet_scenarios.sql` (40건 배치)

Storage에 **`user-assets`** 버킷 생성 (없으면).

---

## 4. 동작 확인

브라우저 또는 터미널:

```text
https://eternal-beam-video-api.onrender.com/health
→ {"status":"ok"}

https://device.eternalbeam.com/api/health
→ 같은 응답 (Vercel 프록시)
```

Swagger: `https://eternal-beam-video-api.onrender.com/docs`

---

## 5. Vercel (이미 설정됨)

- `vercel.json`이 `/api/*` → Render로 프록시
- `VITE_VIDEO_API_URL`은 **비워 두거나** Render URL — 둘 다 OK

---

## 문제 해결

| 증상 | 조치 |
|------|------|
| `no-server` / 404 | Blueprint를 아직 안 만든 상태 → 1단계 다시 |
| `/health` 느림 (1분+) | 무료 플랜 슬립 → 첫 요청 후 정상, 또는 유료 플랜 |
| 누끼 OOM | Render 로그 확인 → 앱은 **브라우저 누끼** 폴백 사용 |
| Luma 웹훅 실패 | `PUBLIC_API_BASE_URL`이 Render URL과 일치하는지 확인 |

---

## 로컬과 병행

```bash
npm run video-api   # localhost:8000
npm run dev         # Vite → 프록시 /api
```

프로덕션만 Render + Vercel 조합을 쓰면 됩니다.
