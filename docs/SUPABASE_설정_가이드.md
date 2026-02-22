# Supabase 설정 가이드 (비전공자용, 단계별)

Eternal Beam 앱의 백엔드(콘텐츠 저장, 슬롯 매핑, 기기 API)를 Supabase로 만드는 방법입니다.  
**순서대로만 따라 하시면 됩니다.**

---

## 1단계: Supabase 가입 및 프로젝트 만들기

1. 브라우저에서 **https://supabase.com** 접속
2. **Start your project** 클릭 → Google/GitHub로 로그인 또는 이메일 가입
3. **New Project** 클릭
4. 다음처럼 입력:
   - **Name**: `eternal-beam` (원하는 이름)
   - **Database Password**: 비밀번호 하나 정해서 입력 (꼭 메모해 두세요!)
   - **Region**: `Northeast Asia (Seoul)` 선택 (한국 사용자용)
5. **Create new project** 클릭 → 1~2분 기다리기
6. 왼쪽 메뉴에서 **Project Settings**(톱니바퀴) → **API** 들어가기
7. 여기 적힌 두 개를 메모:
   - **Project URL** (예: `https://xxxxx.supabase.co`)
   - **anon public** 키 (긴 문자열)  
   → 나중에 앱의 `.env`에 넣습니다.

---

## 2단계: 테이블 만들기 (SQL 한 번 실행)

1. Supabase 왼쪽 메뉴에서 **SQL Editor** 클릭
2. **New query** 클릭
3. 아래 **전체 SQL**을 복사해서 붙여넣기
4. **Run** (또는 Ctrl+Enter) 클릭
5. "Success" 나오면 성공입니다. 에러가 나오면 메시지 복사해서 개발자에게 보내면 됩니다.

```sql
-- Eternal Beam: 콘텐츠 및 슬롯 매핑 테이블

-- 1) 콘텐츠 테이블 (한 번 만든 콘텐츠 한 줄)
CREATE TABLE IF NOT EXISTS public.contents (
  content_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  main_photo_url TEXT,
  hologram_video_id TEXT,
  video_url TEXT,
  background_image_url TEXT,
  background_theme_id TEXT,
  background_theme_name TEXT,
  composed_preview_url TEXT,
  mixed_audio_url TEXT,
  payment_id TEXT,
  payment_status TEXT DEFAULT 'completed',
  amount INTEGER DEFAULT 0,
  nfc_slot_number INTEGER,
  nfc_written BOOLEAN DEFAULT FALSE,
  nfc_written_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2) 슬롯별 콘텐츠 매핑 (기기가 "2번 슬롯" 읽으면 → 어떤 content 재생할지)
CREATE TABLE IF NOT EXISTS public.slot_content_mapping (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slot_number INTEGER NOT NULL,
  content_id TEXT NOT NULL REFERENCES public.contents(content_id) ON DELETE CASCADE,
  device_id TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(slot_number)
);

-- 3) 기기가 슬롯 인식했을 때 로그 (선택, 나중에 재생 이력 확인용)
CREATE TABLE IF NOT EXISTS public.device_slot_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slot_number INTEGER NOT NULL,
  content_id TEXT,
  device_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4) 앱/기기에서 읽기·쓰기 허용 (보안 정책)
ALTER TABLE public.contents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.slot_content_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_slot_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for contents" ON public.contents
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for slot_mapping" ON public.slot_content_mapping
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for device_events" ON public.device_slot_events
  FOR ALL USING (true) WITH CHECK (true);

-- 5) 슬롯 번호로 재생할 콘텐츠 정보 보기 (나중에 기기/앱에서 사용)
CREATE OR REPLACE FUNCTION public.get_playback_by_slot(p_slot_number INTEGER)
RETURNS TABLE (
  content_id TEXT,
  video_url TEXT,
  mixed_audio_url TEXT,
  composed_preview_url TEXT,
  background_theme_name TEXT
) AS $$
  SELECT c.content_id, c.video_url, c.mixed_audio_url, c.composed_preview_url, c.background_theme_name
  FROM public.slot_content_mapping m
  JOIN public.contents c ON c.content_id = m.content_id
  WHERE m.slot_number = p_slot_number
  LIMIT 1;
$$ LANGUAGE sql SECURITY DEFINER;
```

위 SQL까지 완료하셨으면 **2단계 끝**입니다.

### 2-2) 할당량 테이블 (플랜/저장용량/생성횟수)

플랜 제한 기능을 쓰려면 아래 SQL도 실행하세요.

```sql
-- supabase/migrations/20250220000000_user_quotas.sql 내용 참고
-- 또는 프로젝트 내 해당 파일 전체 복사 후 SQL Editor에서 Run
```

---

## 3단계: 앱에 Supabase 키 넣기

1. 프로젝트 폴더(eternal-beam-app) 루트에 **`.env`** 파일이 있는지 확인  
   없으면 **새 파일** 만들고 이름을 정확히 `.env` 로 저장
2. 아래 두 줄을 넣고, `여기_프로젝트_URL` / `여기_anon_키` 를 1단계에서 메모한 값으로 바꿉니다.

```env
VITE_SUPABASE_URL=여기_프로젝트_URL
VITE_SUPABASE_ANON_KEY=여기_anon_키
```

예시:
```env
VITE_SUPABASE_URL=https://abcdefgh.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

3. 저장 후 **앱을 한 번 껐다가 다시 실행** (npm run dev) 해주세요.  
   이제 앱이 Supabase에 콘텐츠를 저장하고, 슬롯 번호로 재생 정보를 가져올 수 있습니다.

---

## 4단계: (선택) 기기용 API – 슬롯 번호로 영상 URL 받기

라즈베리 파이 같은 **기기**에서 "2번 슬롯 꽂음 → 어떤 영상 재생할지" 알고 싶을 때 쓰는 방법입니다.

- **방법 A – DB 함수 직접 호출**  
  기기에서 Supabase 클라이언트로 `get_playback_by_slot(2)` RPC 호출  
  → 반환: `video_url`, `mixed_audio_url` 등

- **방법 B – Edge Function (HTTP)**  
  프로젝트에 `supabase/functions/get_playback_by_slot` 함수가 포함되어 있습니다.  
  배포 후 기기에서 **GET** 요청만 보내면 됩니다:
  ```
  GET https://프로젝트ID.supabase.co/functions/v1/get_playback_by_slot?slot=2
  헤더: Authorization: Bearer (anon 키)
  ```
  **Edge Function 배포 방법 (선택):**
  1. Supabase CLI 설치: https://supabase.com/docs/guides/cli
  2. 터미널에서 `supabase login` 후 `supabase link --project-ref 프로젝트ID`
  3. `supabase functions deploy get_playback_by_slot`
  4. 배포 후 위 URL로 `?slot=2` 호출해서 테스트

지금은 **방법 A만** 써도, 슬롯 번호로 재생할 콘텐츠를 가져오는 데는 충분합니다.

---

## 5단계: 배포할 때 체크

- **Vercel / Netlify 등**에 배포할 때:
  - **Environment Variables**에 위와 똑같이 `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` 추가
  - 값은 Supabase **Project Settings → API** 에서 복사한 그대로 사용

- **.env** 파일은 절대 GitHub 등에 올리지 마세요. (이미 `.gitignore`에 있을 수 있음)

---

## 요약

| 단계 | 할 일 |
|------|--------|
| 1 | Supabase 가입 → 프로젝트 생성 → URL / anon 키 메모 |
| 2 | SQL Editor에서 위 SQL 전체 실행 |
| 3 | 프로젝트 `.env`에 `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` 넣기 |
| 4 | (선택) 기기는 `get_playback_by_slot(슬롯번호)` 또는 Edge Function으로 재생 정보 조회 |

---

## 6단계: 배포용 체크리스트 (Vercel / Netlify)

앱을 실제 서비스로 배포할 때 확인할 것:

1. **Supabase**  
   - 1~3단계 완료했는지 확인  
   - `contents`, `slot_content_mapping` 테이블 생성됐는지 확인

2. **환경 변수**  
   - Vercel/Netlify **Settings → Environment Variables**에 추가:
     - `VITE_SUPABASE_URL`
     - `VITE_SUPABASE_ANON_KEY`
     - (Firebase 사용 중이면) `VITE_FIREBASE_*` 변수들
     - (결제 사용 중이면) `VITE_TOSS_CLIENT_KEY` 등

3. **빌드 & 배포**  
   - `npm run build` 로컬에서 한 번 실행해 보고 에러 없는지 확인  
   - Vercel: GitHub 연결 후 자동 빌드  
   - Netlify: 빌드 명령 `npm run build`, 출력 폴더 `dist`

4. **도메인**  
   - 배포 후 나오는 URL(예: `your-app.vercel.app`)이 정상 작동하는지 확인
| 5 | 배포 시 같은 환경 변수 설정 |

여기까지 하시면 Supabase 백엔드 설정은 끝입니다.  
앱 쪽 코드는 이 가이드와 함께 제공된 서비스 파일들이 Supabase를 사용하도록 되어 있습니다.
