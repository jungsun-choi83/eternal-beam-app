-- 공유 링크의 **출처(provenance)** — 누가 왜 만들었는가.
--
-- 소유 모델이 확정되면서 QR 생성의 주체가 판매자/운영으로 옮겨졌다. 그러면
-- "이 링크를 누가 만들었는가"가 실제 운영 질문이 된다:
--   * 인쇄물에 잘못된 QR 이 붙었을 때 누가 만든 것인지 추적해야 한다
--   * 고객이 직접 만든 링크와 편지에 인쇄된 링크는 폐기 정책이 다르다
--     (고객 링크는 고객이 지울 수 있어야 하고, 인쇄된 링크는 함부로 지우면
--      이미 배송된 제품이 죽는다)
--
-- order_ref 는 **Phase 12–13 을 위한 연결 지점**이다. 지금은 항상 null 이고
-- 아무도 읽지 않는다. 여기 미리 두는 이유는 주문 → petId → 공유 → QR →
-- 인쇄물 사슬이 나중에 붙을 때 공유 테이블을 다시 마이그레이션하지 않기
-- 위해서다. 이행 파이프라인 자체는 이 단계에서 만들지 않는다.

alter table public.shaker_shares
  add column if not exists created_by text,
  add column if not exists purpose text,
  add column if not exists order_ref text;

-- 주문 하나에 걸린 공유들을 찾는 것이 Phase 13 의 조회 패턴이 된다.
create index if not exists shaker_shares_order_idx
  on public.shaker_shares (order_ref)
  where order_ref is not null;

comment on column public.shaker_shares.created_by is
  '이 링크를 만든 주체의 user_id. 운영자가 만들었으면 운영자 id, 고객이면 고객 id';
comment on column public.shaker_shares.purpose is
  'CUSTOMER | OPS | LETTER | MEMORY_BOX. 인쇄물에 붙은 링크와 고객 링크를 구분한다';
comment on column public.shaker_shares.order_ref is
  'Phase 12–13 주문 참조. 지금은 항상 null — 이행 파이프라인이 붙을 자리만 예약한다';
