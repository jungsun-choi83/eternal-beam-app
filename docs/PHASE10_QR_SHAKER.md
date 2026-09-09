# Phase 10 — QR Shaker

기기를 갖고 있지 않은 사람이 **자기 펫을 폰에서 처음 보는** 공개 경험.

    QR  →  /shaker?petId=<PET_ID>&share=<SAFE_TOKEN>  →  BREATHING 전체 화면

QR 은 BREATHING 을 **잠금 해제하지 않는다.** BREATHING 은 언제나 무료이고, 이
링크는 그저 그것을 가리킨다. Shaker 는 **생성하지 않는다** — 이미 READY 인 자산만
읽는다.

---

## 0. 소유 모델 (확정)

```
ETERNAL BEAM / 판매자가 소유          사용자가 소유
├─ Shaker 웹앱                        ├─ 자기 펫 프로필
├─ /shaker 라우트                     ├─ 자기 펫 콘텐츠
├─ 백엔드 / API                       ├─ 생성된 펫 경험
├─ 펫 조회                            ├─ 물리 편지 / 메모리 카드
├─ 영상 호스팅·접근                   └─ 그 펫으로 가는 개인 QR 링크
├─ 보안 / 공유 토큰
├─ QR 생성 서비스
└─ 판매자·운영 도구
```

**생산용 QR 은 판매자가 만든다.** 고객이 만드는 경로는 선택이며 기본으로 꺼져 있다
(`VITE_SHAKER_CUSTOMER_SHARE`). 둘 다 열려 있으면 "이 QR 은 누가 만든 것인가"가
흐려지고, 고객이 만든 링크가 인쇄물에 섞여 들어갈 수 있다.

### canonical petId 는 하나뿐이다

```
고객이 사진 업로드
  → canonical petId 생성 (pet_{content_id})
  → 운영이 /ops/shaker 에서 공유 발급
  → 그 공유 URL 로 QR 생성
  → 편지 / 메모리 카드에 인쇄
  → 배송
  → 고객이 스캔 → **같은 펫**이 열린다
```

QR·편지·메모리 박스·웹앱·미래의 기기가 전부 같은 petId 를 가리킨다. 제품별로 펫이
갈라질 자리가 **구조적으로 없다** — 운영 라우터에 펫 생성 경로가 존재하지 않고,
소유자조차 운영자가 입력하지 않는다(서버가 canonical 바인딩에서 읽는다).

---

## 1. 보안 모델

### 임의 petId 접근이 구조적으로 불가능하다

공개 엔드포인트에 **pet_id 조회 경로가 없다.** 토큰이 펫을 데려온다.

```
GET /api/v1/shaker/pet?share=<TOKEN>[&pet_id=<PET_ID>]
```

`pet_id` 는 선택이고 **조회에 쓰이지 않는다** — 토큰이 데려온 펫과 같은지
대조만 한다. 다르면 404(= 없는 링크와 같은 답)다. 토큰 하나로 pet_id 를 탐색할 수
없다. `share` 를 빼면 라우트 자체가 성립하지 않는다(422).

### 토큰

| 항목 | 값 |
|---|---|
| 엔트로피 | 256비트 (`secrets.token_urlsafe(32)`, 43자) |
| 저장 | **sha256 해시만.** 원문은 발급 응답 1회에만 존재한다 |
| 조회 | 해시 PK lookup — 비교가 아니라 인덱스 조회라 타이밍 여지가 없다 |
| 폐기 | `revoked_at` 표시(삭제 아님) → 410 `SHARE_REVOKED` |
| 만료 | 선택. 기본 무기한(인쇄된 QR 은 회수할 수 없다) → 410 `SHARE_EXPIRED` |

원문을 저장하지 않는 결과: **소유자 목록 화면도 링크를 다시 보여 줄 수 없다.**
잃어버리면 폐기하고 재발급하는 것이 유일한 경로다. 이것은 제약이 아니라 의도다 —
DB 덤프가 유출돼도 열쇠가 함께 새지 않는다.

### 응답 허용 목록

`ShakerPetResponse` 에 선언된 필드가 **전부**다.

```
pet_id, pet_name, breathing_url, poster_url, actions[{id,url}], double_tap_action_id
```

없는 것: `user_id`, 이메일, 구독 상태, 크레딧/지갑, 주문, 결제, 프로바이더
(luma/fal), 그리고 **생성 진행 상태(generating/missing)**. 마지막 항목은 단순
누락이 아니다 — 소유자가 지금 무엇을 만들고 있는지는 링크를 받은 사람이 알 이유가
없다.

응답에는 `Cache-Control: no-store` 가 붙는다. 중간 캐시가 응답을 들고 있으면
폐기해도 계속 열리는 것처럼 보이기 때문이다.

### 남용 방어

프로세스 로컬 고정 창(60초, 기본 60회/IP). 무효 토큰도 카운트한다.

**이것은 토큰 추측 방어가 아니다.** 추측 방어는 256비트 엔트로피가 한다. 리밋이
막는 것은 스크래핑과 폭주 폴링이며, 워커가 여러 개면 워커 수만큼 곱해진다. 진짜
분산 방어가 필요해지면 엣지(Vercel/Cloudflare)에서 해야 한다.

---

## 2. 생성 금지 보장

두 층위로 고정돼 있고, 둘 다 테스트가 있다.

**구조** — Shaker 모듈들은 생성 모듈을 import 하지 않는다. AST 로 검사하므로
함수 안에서 하는 지연 import 도 잡힌다. 라우터가 `premium_purchase` 에서 쓰는
것은 `asset_state`(읽기 전용) / `assert_pet_owned` / `PurchaseError` **셋뿐**이며,
테스트가 이 집합을 정확히 고정한다.

**런타임** — 생성·과금 진입점 7개를 폭탄으로 갈아 끼우고 엔드포인트를 두드린다.
자산이 없을 때(가장 위험한 구간), 폐기된 링크, petId 바꿔치기, 반복 폴링 모두
호출 0회다.

프론트도 같은 규칙을 구조로 지킨다: `App.tsx` 가 `EternalBeamApp` **바깥에서**
분기하므로, 공개 방문자에게는 메인 앱 트리가 아예 마운트되지 않는다. 인증 부팅·
파이프라인 복원·프리미엄 폴링·기기 동기화 effect 가 실행 자체를 하지 않는다.

---

## 3. 더블탭 정책 — PM 확정: **membership**

    구독 ACTIVE  ∩  자산 READY  ∩  선호 ON

Phase 6 의 런타임 적격성(`behavior-library.ts` `isBehaviorEligible`)과 **같은 규칙**
이다. 규칙이 갈리면 "메인 앱에서 꺼 둔 행동이 QR 로는 재생된다"는 구멍이 생기고,
사용자가 자기 설정을 신뢰할 수 없게 된다. 소유자가 끈 행동은 어디서도 재생되지 않는다.

세 조건 중 하나라도 **판정할 수 없으면 거절한다**(fail closed). 구독 조회 장애나
선호 조회 장애가 곧 무료 배포가 되면 안 된다. 거절돼도 **BREATHING 은 그대로 나간다.**

| `SHAKER_DOUBLE_TAP_POLICY` | 의미 |
|---|---|
| `membership` **(기본, PM 확정)** | 구독 ∩ READY ∩ 선호 ON |
| `disabled` | 더블탭을 완전히 끈다 (되돌리기용) |
| `free` | (A) READY 인 액션이면 허용 |
| `ready-only` | (B) COME_CLOSER 가 READY 일 때만 허용 |

알 수 없는 값은 기본값(`membership`)으로 떨어진다 — 세 조건을 모두 요구하므로
오타로 여기 떨어져도 자격 없는 방문자에게 열리는 것이 없다.

**자격이 없으면 생성하지 않는다.** "없으면 만들어 준다"는 예전 경로
(`come-closer-autogen`)의 발상이 여기 새어 들어오면 로그인도 하지 않은 방문자가
프로바이더 비용을 태우게 된다. 자격 없음의 결과는 생성 유도가 아니라 그냥 BREATHING 이다.

`membership` 일 때만 구독·선호 테이블을 조회한다. 나머지 정책은 **아예 건드리지
않는다** — 건드리지 않으면 샐 수도 없다(테스트로 고정). 선호는 구독이 유효할 때만
읽는다(자격이 없으면 결과가 어차피 빈 목록이라 조회가 낭비다).

---

## 3-1. 서명 URL 재발급 — 인쇄된 QR 이 서명보다 오래 산다

업로드 시점 서명은 **7일**짜리다(`services/supabase_assets.py`). QR 은 편지·메모리
박스에 **인쇄되어** 나간다. 8일째에 QR 을 찍은 사람은 유효한 토큰을 들고 있는데도
영상이 재생되지 않는다 — 링크도 자산도 살아 있고 그 사이의 서명만 죽은 상태다.

그래서 Shaker 는 **해석할 때마다** 새로 서명한다. 저장된 URL 을 그대로 내보내지 않는다.

    저장된 객체 경로 (만료 없음)  ──서명──▶  짧은 수명의 새 URL

우선순위:

1. 저장된 **객체 경로**(`breathing_object_path`) — 만료되지 않는 정본
2. 저장된 URL 을 파싱해 경로를 얻는다 — 경로 컬럼 이전에 만들어진 행
3. 저장된 URL 그대로 — 외부 CDN·공개 버킷이거나 Supabase 미설정(로컬)

3단계가 있어야 재서명이 불가능한 환경에서도 재생이 멈추지 않는다. **재서명은
개선이지 전제가 아니다.**

`generated_motions` 의 액션 URL 도 같은 문제를 갖고 있어 같은 처리를 한다.

재서명은 **읽기 서명 생성일 뿐** 업로드도 생성도 아니다. 그리고 **토큰 검증 뒤에**
온다 — 폐기·만료·불일치 링크는 서명 호출조차 일어나지 않는다(테스트로 고정).

TTL 은 `SHAKER_SIGNED_URL_TTL_SECONDS`(기본 3600). 매번 새로 만들므로 길 이유가 없다.

---

## 4. 배포

### 마이그레이션

```
supabase/migrations/20260820000000_shaker_shares.sql              # 공유 테이블
supabase/migrations/20260820000100_shaker_share_object_paths.sql  # 재서명용 경로 컬럼
supabase/migrations/20260820000200_shaker_share_ops_provenance.sql # created_by/purpose/order_ref
```

**첫 번째를 적용하지 않으면 공유 발급이 503 이다.** (공개 조회는 링크가 없으므로
애초에 호출되지 않는다.)

두 번째가 없으면 발급은 되지만 경로가 저장되지 않아, 재서명이 2순위(저장된 URL
파싱)로만 동작한다. 지금은 그것으로도 충분하지만 URL 형식이 바뀌면 깨진다.

### 환경변수

```bash
SHAKER_DOUBLE_TAP_POLICY=membership              # PM 확정 (기본값이라 생략 가능)
SHAKER_RATE_LIMIT_ENABLED=1
SHAKER_PUBLIC_RATE_LIMIT=60
SHAKER_SIGNED_URL_TTL_SECONDS=3600
SHAKER_PROXY_ASSET_URLS=1                        # 고객 이메일 비노출 (기본 켬)

# 판매자/운영 QR 워크플로
SHAKER_OPS_USER_IDS=ops@eternalbeam.com          # 미설정이면 QR 을 만들 수 없다
PUBLIC_WEB_BASE_URL=https://eternalbeam.com      # ⚠️ API 도메인이 아니다
```

`pip install segno` (순수 파이썬, 의존성 없음). 세 requirements 파일에 모두 추가돼 있다.

`GET /readiness` 가 현재 정책과 리밋 상태를 보고한다. `membership` 이 아니면
**경고**로 표시된다 — 승인 없이 바뀐 것을 배포 후에 알아차릴 수 있게.

### SPA 라우팅

`vercel.json` 의 기존 리라이트가 `/shaker` 를 이미 `index.html` 로 보낸다.
**인프라 변경이 필요 없다.**

---

## 5. 판매자/운영 QR 워크플로

### 콘솔

`/ops/shaker` — 고객 앱 **바깥**에서 분기된다(`App.tsx`). 공개 Shaker 번들에 운영
도구가 딸려 가지 않고, 소유 경계가 코드 구조로 드러난다.

화면은 네 단계다:

1. **고객 펫 찾기** — pet_id 또는 고객 계정 일부로 검색.
   출처는 `generated_motions` — "실제로 경험이 만들어진 펫"의 권위 있는 목록이다.
   아직 아무것도 만들지 않은 펫에는 붙일 QR 이 없다.
2. **공유 만들기** — 용도(`OPS` / `LETTER` / `MEMORY_BOX`)와 주문 번호(선택)를 고른다.
3. **QR** — 화면 표시 + SVG(인쇄용) / PNG 내려받기 + Shaker 미리보기 + 링크 복사.
4. **기존 공유 보기 / 해제**.

### 인가

`require_ops` = 검증된 JWT **위에** `SHAKER_OPS_USER_IDS` allowlist.
미설정이면 전원 403 (fail closed).

공유 시크릿 하나로 열지 않은 이유는 **감사 추적**이다. 인쇄되어 나가는 링크라
"누가 만들었는가"가 남아야 하고, 그 값이 `shaker_shares.created_by` 에 기록된다.

### 운영자가 입력하지 않는 것

| 값 | 어떻게 정해지는가 |
|---|---|
| 펫 소유자 | `generated_motions` 의 canonical 바인딩에서 **서버가** 읽는다 |
| BREATHING 위치 | `{user_id}/{content_id}/idle_loop.mp4` 규약에서 유도, **서명 성공이 존재 증명** |
| 공유 토큰 | 서버가 발급(256비트), 응답 1회에만 존재 |

소유자를 손으로 넣게 하면 오타 하나로 남의 펫에 QR 이 붙고, 그때는 이미 인쇄된
뒤다. BREATHING 을 못 찾으면 **거절한다**(409) — 없는 것을 만들지 않는다.

운영자는 고객의 브라우저 세션(`pipeline.idle_video_url`)을 볼 수 없다. 규약 경로가
서버가 아는 유일한 단서이고, 별도의 존재 확인 API 를 두지 않는 이유는 **서명
시도가 곧 존재 확인**이기 때문이다(없는 객체에는 서명이 만들어지지 않는다).

### QR 은 Shaker URL 만 인코딩한다

`qr_service.assert_shaker_url()` 를 통과하지 못하면 아무것도 만들어지지 않는다.
조건 전부를 만족해야 한다: http(s) 절대 URL · 경로가 정확히 `/shaker` ·
`share` 파라미터 존재 · 금지 조각(`supabase.co`, `/storage/v1/`, `.mp4`, `token=` …) 없음.

스토리지 URL 이 인쇄되면 (1) 7일 뒤 죽고 (2) 토큰 검증·폐기·레이트 리밋을 **전부
우회**하며 (3) 폐기할 방법이 없다 — 이미 종이다. 그래서 규칙을 관례가 아니라
**코드**로 둔다.

QR 인코딩은 `segno`(순수 파이썬, 의존성 없음)를 쓴다. 직접 구현하지 않는 이유:
Reed-Solomon/마스킹이 미묘하게 틀린 QR 은 테스트에서 읽히고 현장에서 실패하며,
그 실패는 인쇄된 재고 전량이다.

### API

```bash
GET  /api/v1/shaker/ops/pets?query=…          # 고객 펫 찾기
POST /api/v1/shaker/ops/share                 # {pet_id, purpose, order_ref?}
GET  /api/v1/shaker/ops/shares?pet_id=…       # 이 펫의 공유 목록 (토큰 없음)
POST /api/v1/shaker/ops/share/{id}/revoke     # {pet_id}
GET  /api/v1/shaker/ops/qr?share_url=…&kind=svg|png
```

QR 은 `share_id` 가 아니라 **`share_url`** 을 받는다. 서버가 원문 토큰을 저장하지
않으므로 share_id 만으로는 URL 을 복원할 수 없다 — 제약이 아니라 의도다.

### 고객 경로 (선택, 기본 꺼짐)

`ShakerShareCard` 는 재생 화면 카드 스택에 남아 있지만
`VITE_SHAKER_CUSTOMER_SHARE=1` 일 때만 렌더링된다. API 는 그대로 살아 있다
(`src/lib/shaker-share.ts`).

---

## 5-1. 재생 URL 프록시 — 고객 이메일 비노출

**운영 발급 테스트가 잡은 실제 결함이다.**

스토리지 객체 경로가 `{user_id}/{content_id}/idle_loop.mp4` 이고 이 저장소의
`user_id` 는 **이메일**이다. 서명 URL 을 공개 응답에 그대로 실으면 로그인하지 않은
방문자가 받는 JSON 에 고객 이메일이 들어간다.

기존 누출 테스트가 놓친 이유: 픽스처 URL 이 `https://cdn.test/goya/idle_loop.mp4`
라 이메일이 애초에 없었다. **실제 경로 형태로 테스트하지 않으면 잡히지 않는 종류**다.

해결: 공개 응답은 `/api/v1/shaker/asset?share=…&k=…` 만 싣고, 그 엔드포인트가 302 로
갓 서명한 URL 을 가리킨다. **바이트를 흘려보내지 않으므로 대역폭 비용이 없다.**
소유 모델상 "영상 호스팅·접근"이 판매자 영역이라는 점과도 맞는다.

⚠️ 프록시는 `/pet` 과 **같은 해석기**(`_resolve_public_shaker`)를 쓴다. 나눠 두면
`/asset` 이 멤버십 게이트를 통째로 우회하는 구멍이 된다 — 가장 쉽게 생기는 실수라
판정을 물리적으로 한 함수에 묶었다.

되돌리기: `SHAKER_PROXY_ASSET_URLS=0` (⚠️ 끄면 이메일 노출이 돌아온다).

---

## 6. 자이로 / 패럴랙스

깊이는 레이어를 **다른 양만큼** 움직여 만든다. 펫 1.0× / 배경 0.35×, 같은 방향.
반대로 움직이면 깊이가 아니라 찢어짐으로 보인다.

| 항목 | 값 |
|---|---|
| 펫 최대 이동 | 10px (상한 16px — 설정으로도 못 넘는다) |
| 배경 최대 이동 | 3.5px (상한 8px) |
| 최대 도달 각도 | 26° |
| 데드존 | 1.2° (손 떨림으로 떨지 않게) |
| 감쇠 | 지수 0.12 |

**첫 샘플이 기준 자세가 된다.** 사람은 폰을 45° 쯤 기울여 들고 보는데, 절대 각도를
쓰면 시작하자마자 최대치에 붙어 움직일 여지가 없다. 방향 전환 시 기준을 다시 잡는다.

폴백 순서:

1. **iOS 13+** — `DeviceOrientationEvent.requestPermission()`. 반드시 사용자
   제스처 안에서 부른다(버튼). 거부는 실패가 아니라 다음 단계로 간다.
2. **Android/데스크톱 크롬** — 지원하면 곧바로 구독.
3. **비-자이로 폴백** — 포인터 이동을 같은 계산 경로에 넣는다(`pointerToGyroSample`).
   트래커를 두 벌 만들지 않으므로 상한·데드존·감쇠가 자동으로 동일하다.
4. **아무것도 없으면** 정지. BREATHING 은 계속 돈다.

`prefers-reduced-motion: reduce` 면 **완전히 끈다**(줄이지 않는다). 전정기관 장애가
있는 사용자에게 "약한 움직임"은 여전히 증상을 유발한다 — 절반은 배려가 아니다.
이 경우 센서 구독 자체를 하지 않는다(배터리).

---

## 7. 재사용한 것 / 새로 만든 것

**재사용 (수정 없음)**

- `IdleLoopVideo` — BREATHING 루프, 1회 재생, **자동 BREATHING 복귀**, 실패 복구
  (`error`/`abort`→즉시, `stalled`→2.5초 유예). Phase 10.4 의 상태 기계가 이미 여기 있다.
- `recognizeTap` — 드래그와 공존하는 더블탭 인식
- `pet-runtime-events` — 이벤트 등록·트리거 판정
- `premium_purchase.asset_state` — 읽기 전용 자산 조회 (과금·제출 없음)
- `premium_purchase.assert_pet_owned` — 발급 시 소유권 검사
- `backend/auth.require_user` — 소유자 경로 인증
- `vercel.json` SPA 리라이트

**새로 만든 것** — 백엔드 5, 프론트 6, 마이그레이션 1, 테스트 7.
(상세는 아래 "변경 파일" 참고)

**공유 파일 수정 1건** — `IdleLoopVideo` 에 선택 prop `onFirstFrame` 추가.
포스터를 언제 걷을지 정하는 신호다. `onFeetMarginChange` 로는 대신할 수 없다 —
그쪽은 packed 소스나 측정 실패 시 발화하지 않아 포스터가 영영 안 걷히는 화면이 된다.
기본 `undefined` 라 기존 호출부 동작은 그대로다.

---

## 8. 테스트

| 파일 | 내용 |
|---|---|
| `backend/tests/test_shaker_share_token.py` | 추측 불가, 원문 미저장, 폐기·만료, petId 바꿔치기, 남의 링크 폐기 |
| `backend/tests/test_shaker_public_api.py` | 응답 허용 목록, 4개 정책 전부에서 누출 없음, 인가, 레이트 리밋 |
| `backend/tests/test_shaker_no_generation.py` | AST 구조 검사 + 진입점 7개 폭탄 런타임 검사 |
| `src/lib/shaker-entry.test.ts` | 경로·파라미터·토큰 형식, "petId 만으로는 절대 ready 아님" |
| `src/lib/shaker-api.test.ts` | 파싱, 오류 분류, 재시도 가능 여부 |
| `src/lib/shaker-gyro.test.ts` | 은은함 상한, 기준 자세, 데드존, reduced-motion, 폴백 |
| `src/lib/shaker-playback.test.ts` | 정책∩등록 이중 필터, 버전 스큐, 소스 1개만 마운트 |
| `src/lib/shaker-share.test.ts` | 목록 요약 파싱(토큰 없음), URL 조립 |
| `backend/tests/test_shaker_membership_policy.py` | 구독 ∩ READY ∩ 선호 ON, fail closed, 자격 없음이 생성을 부르지 않음 |
| `backend/tests/test_shaker_signed_url_refresh.py` | 만료 URL 교체, 매 해석 재서명, 검증 뒤 순서, 폴백 |
| `backend/tests/test_shaker_owner_share.py` | 발급 인증·소유권, 펫 복제 없음, 목록에 토큰 없음, 폐기 |
| `src/lib/shaker-share-panel.test.ts` | 카드 상태 게이트, "다시 볼 수 없는 링크" 설명 |
| `backend/tests/test_shaker_ops.py` | 운영 인가, canonical petId 하나, 소유자=고객, QR 안전성, 생성 없음 |
| `backend/tests/test_shaker_asset_proxy.py` | **이메일 비노출 회귀**, 프록시가 정책을 우회하지 않음, 토큰 검증 유지 |
| `src/lib/shaker-ops-entry.test.ts` | 운영 경로 감지(공개 경로와 비충돌), 권한 상태 구분 |
