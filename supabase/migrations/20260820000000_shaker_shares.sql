-- QR Shaker 공개 공유 링크 (Phase 10).
--
-- 이 테이블 하나가 "임의의 petId 조회 금지"를 **구조로** 보장한다. 공개
-- 엔드포인트는 pet_id 로 조회하지 않는다 — 토큰으로만 조회하고, 토큰이 pet 을
-- 데려온다. pet_id 를 URL 에 넣는 것은 QR 가독성과 표시용일 뿐이며 서버는 그것을
-- 조회키로 쓰지 않는다(일치 검사에만 쓴다).
--
-- ── 왜 토큰 원문이 아니라 해시를 저장하는가 ─────────────────────────────────
-- 이 행들은 로그인 없이 접근 가능한 자산을 가리킨다. DB 덤프가 유출되면 원문
-- 토큰은 그 자체로 모든 공유 펫의 열쇠 꾸러미가 된다. sha256 해시만 저장하면
-- 유출된 덤프로는 아무것도 열 수 없다 — 원문은 발급 응답 **한 번**만 존재하고
-- 그 뒤로 서버 어디에도 남지 않는다.
--
-- 해시를 PK 로 두는 것이 조회 경로이기도 하다: resolve 는 sha256(token) 으로
-- PK lookup 한 번이다. 타이밍 공격 여지가 없고(비교가 아니라 인덱스 조회),
-- 별도 인덱스도 필요 없다.
--
-- ── 이 테이블이 **담지 않는** 것 ────────────────────────────────────────────
-- 구독·지갑·주문·결제·프로바이더 정보가 여기 없다. 공개 응답 조립에 필요한
-- 최소 스냅샷(이름/BREATHING/포스터)만 둔다. 그래서 공개 라우터는 그런 테이블을
-- 조회할 이유 자체가 없고, 실수로 새어 나갈 경로가 생기지 않는다.
--
-- user_id 는 저장하되 **절대 응답에 싣지 않는다.** 소유권 검사(누가 이 링크를
-- 폐기할 수 있는가)와 READY 자산 조회(generated_motions 는 user_id 로 키를 잡는다)에
-- 필요하다.

create table if not exists public.shaker_shares (
  -- sha256(token) hex. 원문 토큰은 어디에도 저장하지 않는다.
  token_hash text primary key,
  -- 소유자에게 노출되는 **비밀이 아닌** 식별자. 폐기·목록 조회에 쓴다.
  -- 이것으로는 Shaker 를 열 수 없다 — 여는 것은 오직 원문 토큰이다.
  share_id text not null unique,
  -- 링크 소유자. 응답에 나가지 않는다.
  user_id text not null,
  pet_id text not null,
  -- 공개 표시용 스냅샷. 발급 시점의 값이며, 프로필 변경을 자동 추적하지 않는다.
  pet_name text,
  -- 무료 BREATHING 루프. QR 이 이것을 "잠금 해제"하는 것이 아니다 —
  -- BREATHING 은 언제나 무료이고, 이 링크는 그저 그것을 가리킬 뿐이다.
  breathing_url text not null,
  poster_url text,
  created_at timestamptz not null default now(),
  -- 폐기(revoke)는 삭제가 아니라 표시다. 삭제하면 "폐기된 링크"와 "존재한 적
  -- 없는 링크"를 구분할 수 없어, 사용자에게 무엇이 일어났는지 설명할 수 없다.
  revoked_at timestamptz,
  -- null 이면 무기한. 인쇄된 QR 은 회수할 수 없으므로 기본은 무기한이다.
  expires_at timestamptz
);

-- 소유자의 "내 공유 링크" 목록 — 유일한 비-PK 조회 패턴이다.
create index if not exists shaker_shares_owner_idx
  on public.shaker_shares (user_id, pet_id);

comment on table public.shaker_shares is
  'QR Shaker 공개 공유 링크. 토큰 해시로만 조회된다 — pet_id 직접 조회 경로는 없다';
comment on column public.shaker_shares.token_hash is
  'sha256(원문 토큰) hex. 원문은 발급 응답 1회에만 존재하고 저장되지 않는다';
comment on column public.shaker_shares.share_id is
  '폐기·목록용 공개 식별자. 비밀이 아니며 이것으로 Shaker 를 열 수 없다';
comment on column public.shaker_shares.user_id is
  '소유자. 소유권 검사와 READY 자산 조회에만 쓰고 공개 응답에는 절대 싣지 않는다';
comment on column public.shaker_shares.revoked_at is
  '폐기 시각. 행을 지우지 않는 이유는 "폐기됨"과 "없음"을 구분해 설명하기 위해서다';
