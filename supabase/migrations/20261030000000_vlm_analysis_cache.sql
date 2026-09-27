-- VLM 분석 결과 durable 캐시 (Phase 2/3) — 같은 이미지 재분석/재과금 방지.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- vlm_identity.analyze_semantic_traits / classify_reference 의 결과를 내용
-- 주소(cache_key = model + analyzer 버전 + 이미지 바이트 해시)로 저장한다.
-- 프로세스 내 in-memory LRU(64건, 재시작 시 소실)만으로는 워커 재시작/여러
-- 프로세스 사이에서 같은 이미지를 다시 과금할 수 있었다 — 이 테이블이 그
-- 캐시를 프로세스 수명 밖으로 넘긴다.
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- * 순수 캐시다 — 정본이 아니다. 비어 있거나 조회에 실패해도 호출자는 그냥
--   다시 계산한다(기존 폴백 그대로).
-- * cache_key 자체에 모델/분석기 버전/이미지 바이트 해시가 전부 들어있으므로
--   원본이나 모델·버전이 바뀌면 키가 바뀌어 자동으로 무효화된다 — 행을
--   덮어쓸 필요가 없다(upsert 는 재계산 경합 시 같은 값으로 수렴할 뿐).
-- * 실패(None) 결과는 절대 저장하지 않는다 — 호출자와 동일한 원칙.

create table if not exists public.pet_vlm_analysis_cache (
  cache_key text primary key,
  -- 'semantic_traits' | 'reference_classification' — 관측/디버깅용, 키 구성에는 안 쓴다.
  kind text not null,
  analyzer_version text not null,
  model text not null,
  result jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists pet_vlm_analysis_cache_kind_idx
  on public.pet_vlm_analysis_cache (kind, created_at desc);

comment on table public.pet_vlm_analysis_cache is
  'VLM 분석 결과의 durable 내용-주소 캐시 (순수 캐시, 정본 아님) — 같은 이미지/모델/분석기 버전의 재과금을 프로세스 재시작 이후에도 막는다';
comment on column public.pet_vlm_analysis_cache.cache_key is
  'sha256(model + analyzer_version + [mime + image_bytes]...) — 내용 주소, 원본/모델/버전이 바뀌면 자동 무효화';
