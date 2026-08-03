# LivePortrait 액션 20종 파이프라인 — 진행상황

이 문서는 "강아지 액션 20종 영상 생성"(LivePortrait 기반, Luma idle 루프와는 별개) 작업의
실시간 작업 로그이자 상태판입니다. 새 단계를 완료할 때마다 이 문서를 갱신합니다.

> Luma idle 루프(`backend/services/luma_service.py`, `luma_idle_pipeline.py`)는 이미
> 완료되어 있고 이번 작업에서 건드리지 않습니다. 여기서 다루는 건 "액션(20종)" 전용
> LivePortrait 파이프라인입니다.

## 전체 아키텍처 요약

```
[FastAPI] POST /api/live-portrait/generate-action-set
   → action_video_jobs 테이블에 status='queued' 행 insert (Supabase)
   → 즉시 job_id 반환

[로컬 RTX 4090 머신] python -m backend.workers.live_portrait_worker
   → status='queued'(또는 stale 'running') 행을 polling으로 claim
   → 사진(dog_image_url: Supabase URL 또는 로컬 경로) 로드
   → backend/assets/driving_videos/*.mp4 순회
      → (1) LivePortrait Animals 모드 추론 (live_portrait_service.run_live_portrait_inference)
      → (2) SAM2 배경 강제 블랙 후처리 (live_portrait_postprocess.force_black_background)
      → (3) ffmpeg로 800x480 letterbox 리사이즈/인코딩
      → (4) Supabase Storage 업로드
      → 매 영상마다 action_video_jobs.progress_json 갱신
   → 전체 완료 시 results_json + status='done' (또는 예외 시 'failed' + error, 루프는 안 죽음)

[FastAPI] GET /api/live-portrait/jobs/{job_id} → 진행률/결과 조회 (프론트 polling용)
```

Modal GPU 경로(최초 1차안)는 사용자가 로컬 RTX 4090을 쓰기로 하면서 **보조/선택
경로**로 전환됨 — `backend/modal_apps/live_portrait_app.py`(검증 안 됨, 필요 시 나중에
활성화).

## 단계별 상태

### 1단계 — 설치/추론 래퍼
- 상태: ✅ 완료
- 파일: `backend/services/live_portrait_service.py`, `docs/LivePortrait_설치_가이드.md`
- 한 일:
  - LivePortrait 공식 저장소/changelog 조사 → **Animals 모드**(`inference_animals.py`,
    사람용 `inference.py`가 아님)가 우리 케이스(강아지)에 맞는 모델임을 확인.
  - 정체성 보존 플래그 확정: `flag_relative_motion=True`, `flag_stitching=False`,
    `flag_pasteback=False`, `flag_do_crop=True`, `driving_multiplier=1.75`,
    `driving_option="pose-friendly"` — 근거는 서비스 파일 docstring 및 설치 가이드에
    표로 정리.
  - `run_live_portrait_inference(source_image, driving_video_path, output_path, **kwargs)`
    구현: source_image가 **bytes / URL / 로컬 파일 경로** 중 무엇이든 받도록
    `resolve_source_image_to_local_path()`로 통일 — Supabase 업로드 없이 로컬 파일로
    바로 테스트 가능(고야 테스트 스크립트가 이 경로를 씀).
  - 서브프로세스 호출 방식 채택(내부 함수 직접 import 대신) — 이유는 설치 가이드 3절 참고.

### 2단계 — 배치 파이프라인
- 상태: ✅ 완료
- 파일: `backend/services/live_portrait_batch.py`
- 한 일:
  - `run_live_portrait_batch(dog_image, driving_videos_dir=..., ...)` 구현 — 폴더 안의
    드라이빙 영상을 순회하며 영상당 (LivePortrait → SAM2 배경 강제 → ffmpeg 800x480
    리사이즈 → Supabase 업로드) 4단계 실행.
  - `list_driving_videos()`: 정확히 20개가 아니어도 에러 내지 않고 "있는 만큼" 처리 +
    경고 로그. 0개면 별도로 명확히 경고.
  - 부분 실패 정책: 항목 하나가 예외를 던져도 `try/except`로 잡아 `ActionVideoResult
    .success=False, .error=...`로 기록하고 다음 항목으로 계속 진행 — 전체 배치를 죽이지
    않음.
  - `stage_cb`(세부 단계 콜백) / `progress_cb`(항목 완료 콜백) 두 개를 분리 노출 —
    워커는 progress_cb만 써서 DB 진행률만 갱신하고, 고야 테스트 스크립트는 stage_cb까지
    써서 콘솔에 실시간 4단계 로그를 찍음.
  - **처리 순서 결정**: LivePortrait 추론 → SAM2 배경 정리(추론 전이 아니라 후) —
    이유는 파일 상단 docstring에 기록(LivePortrait Animals 모드는 `flag_pasteback=False`
    라 드라이빙 영상 배경이 애초에 안 섞이고, SAM2가 지워야 할 노이즈는 LivePortrait가
    만들고 난 뒤에만 존재하기 때문).
  - 출력 해상도 800x480은 크롭이 아니라 **letterbox 패딩**(검정) 방식 채택 — 근거는
    파일 docstring.
  - `backend/assets/driving_videos/`(README.md + `.gitkeep`) 폴더 생성 — 실제 20개
    영상 파일은 **아직 없음**(저장소에 기존 에셋 없음을 확인함, 사용자가 채워야 함).

### 3단계 — SAM2 후처리(배경 강제 블랙)
- 상태: ✅ 완료
- 파일: `backend/services/live_portrait_postprocess.py`
- 한 일:
  - `vitmatte_service._load_sam2`를 그대로 import해서 재사용(새 SAM2 로딩 코드를
    따로 안 만듦) — box 프롬프트만 우리 쪽에서 새로 계산.
  - **키프레임(기본 10프레임) + optical flow(Farneback) 마스크 전파** 방식으로 절충 —
    매 프레임 SAM2를 도는 건 20개 영상 배치에 너무 느리다고 판단(CPU/GPU 모두). 근거와
    대안 비교는 파일 상단 docstring에 기록.
  - 마스크 경계는 약하게 dilate(`LIVE_PORTRAIT_SAM2_MASK_DILATE_PX`, 기본 3px)해서
    강아지 몸통이 깎이는 쪽보다 배경이 살짝 남는 쪽으로 안전하게 처리.
  - SAM2가 예외를 던지면(의존성 문제/OOM 등) 이전 프레임 마스크 재사용 → 완전 실패는
    아님(vitmatte_service의 GrabCut 폴백만큼 정교하진 않지만 배치 전체를 안 죽임).
  - 오디오 트랙은 `cv2.VideoWriter`가 못 다루므로 ffmpeg로 원본 오디오를 다시 mux —
    ffmpeg가 없거나 원본에 오디오가 없으면 비디오만 있는 결과로 폴백.
  - **실행 위치 결정**: 별도 Modal 함수로 안 쪼개고, LivePortrait 추론과 같은 워커
    프로세스 안에서 순차 실행하기로 함 — 애초에 로컬 GPU가 1차 실행 장소가 됐으므로
    굳이 벤뉴를 나눌 이유가 없어짐(근거는 파일 docstring).

### 4단계 — 로컬 GPU 큐 시스템 (RTX 4090 워커) — 이번 요청으로 1차 경로가 됨
- 상태: ✅ 완료
- 파일:
  - `supabase/migrations/20260721000000_action_video_jobs.sql` — `action_video_jobs`
    테이블(status/progress_json/results_json/error/claimed_by/claimed_at 등).
  - `backend/services/action_video_jobs.py` — job CRUD(`create_job`, `get_job`,
    `claim_next_job`, `update_progress`, `mark_done`, `mark_failed`).
  - `backend/models/live_portrait.py` — API 요청/응답 Pydantic 모델.
  - `backend/routers/live_portrait.py` — `POST /api/live-portrait/generate-action-set`
    (잡 등록, 즉시 반환), `GET /api/live-portrait/jobs/{job_id}`(상태 조회). `main.py`에
    `ENABLE_LIVE_PORTRAIT_API`(기본 1)로 조건부 등록.
  - `backend/workers/live_portrait_worker.py` — polling 루프. `python -m
    backend.workers.live_portrait_worker`로 실행.
  - `backend/services/supabase_assets.py`에 `get_client()` public 래퍼 1개 추가(기존
    `_client()` 그대로 두고 재사용만 노출 — 기존 동작 변경 없음).
- 한 일 / 결정사항:
  - **큐 인프라**: Redis/Celery 등 새 인프라를 추가하지 않고 기존 Supabase(Postgres)
    테이블 polling으로 구현 — 이 프로젝트 규모(사용자 1명당 잡 1건, 워커 1~소수대)에
    맞고 기존 스택과 일치한다는 사용자 지시를 그대로 따름.
  - **클레임(선점) 방식**: `claim_next_job()`은 "조회 → 조건부 UPDATE(`WHERE id=... AND
    status=이전상태`) → 결과 비어있으면 스킵" 패턴으로 낙관적 잠금 — 워커가 1대뿐인
    기본 운영 형태에서는 경쟁이 실질적으로 발생하지 않음(다만 진짜 원자적 트랜잭션은
    아니므로, 워커를 여러 대 동시에 돌릴 계획이 생기면 Postgres 함수(`SELECT ... FOR
    UPDATE SKIP LOCKED`)로 강화하는 걸 다음 단계로 고려할 것).
  - **내결함성**: 워커 메인 루프는 잡 처리 예외를 절대 밖으로 흘리지 않고
    `mark_failed(job_id, error)`로 기록 후 계속 polling. SIGINT/SIGTERM 핸들러로
    "현재 잡은 마치고 종료" 지원. 워커 자체는 상태를 안 들고 있어(stateless) 재시작해도
    DB만 보고 이어감 — 죽은 워커가 물고 있던 `running` 잡은 `LIVE_PORTRAIT_STALE_MINUTES`
    (기본 30분) 지나면 다른(또는 재시작된 같은) 워커가 재클레임.
  - 진행률: `progress_json = {"total": 20, "completed": N, "current_action": "..."}` —
    영상 1건 끝날 때마다 갱신(요청사항: "20개 추론의 중간 진행률도 폴링 가능해야 함").

### 5단계 — 고야(Goya) 테스트 진입점 — 추가 요청으로 신설
- 상태: ✅ 완료
- 파일: `backend/scripts/test_live_portrait_goya.py`
- 대상 파일: 레포 루트의 `누끼딴고야.png`(이미 누끼딴 상태, 이번 요청에서 사용자가
  실제로 준비해 둔 파일)
- 한 일 / 결정사항:
  - **큐를 거치지 않고 파이프라인을 직접 호출**하는 방식으로 구현(`run_live_portrait_batch()`
    를 동기 직접 호출) — 워커가 내부적으로 부르는 함수와 완전히 동일해서, 여기서
    성공하면 큐 경로도 그대로 동작함이 보장됨. 처음 설치 확인 단계에서는 Supabase
    큐 테이블/워커까지 다 띄울 필요 없이 스크립트 하나로 빠르게 눈으로 확인하는 게
    낫다고 판단(정식 운영 경로는 여전히 라우터+워커).
  - 소스 이미지 기본값 = 레포 루트 `누끼딴고야.png`(하드코딩된 기본 경로, `--image`로
    변경 가능) — 로컬 파일 경로를 그대로 넘기므로 Supabase 업로드가 전혀 필요 없음
    (1단계에서 만든 로컬 경로 지원을 그대로 사용).
  - 드라이빙 영상이 0개면(현재 예상되는 상태 — 아직 실제 파일 없음) 에러로 죽지 않고
    어디에 어떤 이름/형식으로 파일을 넣어야 하는지 안내 문구를 출력하고 종료 코드 0으로
    끝남(정상적인 "아직 준비 안 됨" 상태로 취급).
  - `stage_cb`를 연결해 영상 1건당 "1/4 LivePortrait 추론 → 2/4 SAM2 배경 강제 →
    3/4 ffmpeg 리사이즈 → 4/4 업로드(선택)"를 실시간으로 콘솔에 출력.
  - 기본은 `--upload` 없이 로컬 저장만(네트워크/Supabase 설정 불필요) — 결과와 매니페스트를
    `outputs/goya_live_portrait_test/`에 저장.

## 실행 방법 (요약)

```bash
# 0) 최초 1회: Supabase SQL Editor에서 마이그레이션 실행
#    supabase/migrations/20260721000000_action_video_jobs.sql

# 1) 로컬 RTX 4090 머신에 LivePortrait 설치 (docs/LivePortrait_설치_가이드.md)

# 2) backend/assets/driving_videos/ 에 실제 강아지 액션 20개 mp4 채우기
#    (backend/assets/driving_videos/README.md 참고)

# 3) 빠른 스모크 테스트 (고야 사진, 큐 없이 직접 실행)
python -m backend.scripts.test_live_portrait_goya

# 4) 정식 경로: 워커를 상시 실행
python -m backend.workers.live_portrait_worker

# 5) FastAPI에서 잡 등록 (별도 서버/프론트에서 호출)
POST /api/live-portrait/generate-action-set  {"user_id": "...", "dog_image_url": "https://.../고야.png"}
GET  /api/live-portrait/jobs/{job_id}
```

## 남은 작업 (사용자가 해야 하는 것)

1. `docs/LivePortrait_설치_가이드.md`대로 로컬 RTX 4090 머신에 LivePortrait 설치
   (X-Pose 커스텀 op 빌드 포함) + `LIVE_PORTRAIT_REPO_DIR`/`LIVE_PORTRAIT_PYTHON` 설정.
2. `backend/assets/driving_videos/`에 실제 20개 강아지 액션 레퍼런스 mp4 확보/배치.
3. Supabase에 `action_video_jobs` 마이그레이션 실행 + `SUPABASE_URL`/
   `SUPABASE_SERVICE_ROLE_KEY`가 워커 실행 환경에도 설정돼 있는지 확인.
4. `python -m backend.scripts.test_live_portrait_goya`로 고야 사진 1차 스모크 테스트 →
   결과 보고 `driving_multiplier` 등 파라미터 미세조정.
5. (선택) Modal 보조 경로를 쓰고 싶다면 `modal deploy backend/modal_apps/
   live_portrait_app.py` — 단, 이 경로는 이번 작업에서 검증되지 않았음.

## 열린 결정사항 / 트레이드오프 로그

- **실행 장소 변경**: 최초 설계는 Modal 서버리스 GPU가 1차였으나, 사용자가 로컬
  RTX 4090을 보유하고 있어 로컬 워커 폴링 방식을 1차 경로로 전환. Modal은 선택적
  보조 경로로 문서화만 유지(검증 안 됨).
- **큐 구현**: Redis/Celery 등 새 인프라 대신 기존 Supabase(Postgres) 테이블 polling
  사용 — 이 프로젝트 규모(견 1명당 20개 영상, 워커 1대)에 충분하고 기존 스택과 일치.
- **SAM2 후처리 실행 위치**: Modal이 1차 경로가 아니게 되면서, 굳이 SAM2 배경 강제
  단계만 별도 GPU 함수로 분리할 이유가 없어져 워커 프로세스 안에 그대로 통합.
- **정체성 보존 파라미터는 문서 조사 기반 권장값** — 실제 GPU/LivePortrait 설치 없이
  이 세션에서 최종 검증은 못 함. 고야 사진으로 1차 테스트 후 조정 필요할 수 있음.
- **드라이빙 영상 20개는 아직 미확보** — 저장소 전체를 검색했으나 기존 에셋 없음을
  확인함. 폴더/네이밍 규칙만 정의해 두었고, 실제 촬영/수집은 사용자 몫.
