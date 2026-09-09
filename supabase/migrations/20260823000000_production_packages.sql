-- 인쇄 생산 패키지 (Phase 13).
--
-- ── 파일을 저장하지 않고 **입력 스냅샷**을 저장한다 ─────────────────────────
-- 이 테이블에 PDF/PNG 바이트가 없다. 대신 그것을 만들 때 쓴 입력을 적어 둔다:
-- 편지 스냅샷 id, QR 로 인코딩한 Shaker URL, 사진 원본 URL.
--
-- 왜: 렌더링이 결정적이라 같은 입력이면 같은 출력이 나온다. 파일을 저장하면
-- 스토리지 수명(서명 URL 만료)을 또 관리해야 하는데, 그 문제로 이미 두 번
-- 데였다(Phase 10 재서명, Phase 11 주문). 입력만 남기면 언제든 동일한 인쇄물을
-- 다시 뽑을 수 있고 새 만료 표면이 생기지 않는다.
--
-- 감사에도 이쪽이 낫다: "무엇이 인쇄됐는가"는 결국 **어떤 편지와 어떤 QR 이
-- 쓰였는가**이고, 그게 여기 그대로 남는다.
--
-- ── 멱등성 ──────────────────────────────────────────────────────────────────
-- order_id 가 PK 다. 생산 준비를 두 번 눌러도 패키지가 두 벌 생기지 않고,
-- **QR 도 다시 발급되지 않는다** — 이미 붙은 share 를 그대로 쓴다.

create table if not exists public.production_packages (
  -- physical_orders.order_id 와 1:1.
  order_id text primary key,
  user_id text not null,
  -- canonical petId. 새로 만들지 않는다.
  pet_id text not null,
  product_type text not null,

  -- 편지: Soul Trace 가 만든 것을 가리킨다. **여기서 생성하지 않는다.**
  soul_trace_letter_id text not null,

  -- QR 에 인코딩된 Shaker URL. 기존 공유를 재사용한다.
  qr_share_url text not null,
  shaker_share_id text,

  -- 사진 카드 원본 (메모리 박스). 없으면 카드 없이 나간다.
  photo_image_url text,

  -- 생산 시점의 수령인 스냅샷 — 주소가 나중에 바뀌어도 인쇄물은 흔들리지 않는다.
  recipient_name text,
  recipient_phone text,
  postal_code text,
  address_line1 text,
  address_line2 text,

  built_at timestamptz not null default now()
);

create index if not exists production_packages_pet_idx
  on public.production_packages (pet_id);

comment on table public.production_packages is
  '인쇄 생산 패키지의 **입력 스냅샷**. 파일 바이트는 저장하지 않는다 (렌더링이 결정적)';
comment on column public.production_packages.qr_share_url is
  'QR 에 인코딩된 Shaker URL. 기존 공유를 재사용하며 생산이 새 공유를 만들지 않는다';
comment on column public.production_packages.soul_trace_letter_id is
  'Soul Trace 편지 참조. Eternal Beam 은 편지를 생성하지 않는다';
