# Supabase 빠른 설정 (2단계 진행)

`.env`에 Supabase 키가 이미 있으므로, 아래만 진행하면 됩니다.

---

## 1. 테이블 생성 (SQL 2개 실행)

### Supabase 대시보드 접속

1. https://supabase.com 로그인
2. 프로젝트 **kdlukiujgclczwqmwvmk** 선택 (또는 사용 중인 프로젝트)
3. 왼쪽 메뉴 **SQL Editor** 클릭

### 1) 기본 테이블 실행

1. **New query** 클릭
2. 아래 파일 내용 전체 복사 → 붙여넣기:
   - `supabase/migrations/001_eternal_beam_tables.sql`
3. **Run** (또는 Ctrl+Enter)
4. "Success" 확인

### 2) 할당량 테이블 실행

1. **New query** 클릭
2. 아래 파일 내용 전체 복사 → 붙여넣기:
   - `supabase/migrations/20250220000000_user_quotas.sql`
3. **Run**
4. "Success" 확인

---

## 2. Storage 버킷 생성 (user-media)

1. Supabase 왼쪽 메뉴 **Storage** 클릭
2. **New bucket** 클릭
3. 입력:
   - **Name**: `user-media`
   - **Public bucket**: OFF (비공개 유지)
4. **Create bucket** 클릭
5. 버킷 생성 후, **Policies** 탭에서 **New policy** → **For full access** (또는 테이블 기반 정책)로 업로드/다운로드 허용 설정

---

## 완료 확인

| 항목 | 확인 |
|------|------|
| Table Editor에 `contents`, `slot_content_mapping`, `device_slot_events`, `user_quotas`, `user_media` 보임 | ☐ |
| Storage에 `user-media` 버킷 보임 | ☐ |
| `.env`에 `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` 있음 | ☐ (이미 있음) |

위 3개 확인되면 Supabase 설정 완료입니다.
