# Spine2D 자동 리깅 파이프라인 — 진행상황

이 문서는 "Action(달려오기/짖기/배깔기) 전용, SAM2+포즈추정+Spine2D 리깅 기반"
파이프라인의 실시간 작업 로그이자 상태판입니다. 새 단계를 완료할 때마다 이
문서를 갱신합니다.

> **Idle**(숨쉬기 등 은은한 앰비언트 모션)은 Luma AI 기반으로 이미 완료되어
> 있고(`backend/services/luma_idle_pipeline.py`, `luma_idle_templates.py`,
> `/api/generate-idle-variant`) 이번 작업에서 건드리지 않습니다.
>
> **LivePortrait 기반 Action 파이프라인**(`backend/services/live_portrait_*.py`,
> `backend/workers/live_portrait_worker.py`, `docs/LivePortrait_파이프라인_진행상황.md`)도
> 건드리지 않습니다 — 사용자의 최신 결정에 따라 이 파이프라인(SAM2+포즈추정+
> Spine2D 리깅)이 Action 카테고리의 **1차 경로**가 되고, LivePortrait는
> **보조/비교 대상**으로 격하되었습니다. 두 파이프라인은 완전히 독립적으로
> 병존합니다(하나가 다른 하나를 대체/삭제하지 않음).

## 왜 SAM2+DeepLabCut(포즈추정)+Spine2D인가 (사용자 결정 요약)

사용자가 명시한 이유: LivePortrait(드라이빙 비디오 기반 애니메이션)는 일반적인
"그럭저럭 자연스러운 움직임"에는 강하지만, **특정한 동작을 정확히 지정**(예:
"이 순간에 앞다리가 완전히 접혀야 한다")하기 어렵고, 결과물이 매번 드라이빙
영상에 종속된다. 반면 **뼈대 기반 리깅**은 일단 만들어지면:
- 동작(애니메이션)을 정밀하게 손으로 제어/수정할 수 있고,
- 같은 리깅에 여러 액션을 재사용할 수 있고,
- 이미 완성되어 있는 `device-renderer`(Spine-CPP 런타임)와 정확히 맞아떨어진다
  (LivePortrait는 mp4 영상을 만들고, device-renderer는 mp4가 아니라 Spine
  스켈레톤을 실시간 렌더링하는 구조이므로, 애초에 "본연의" 산출물 포맷이
  리깅 쪽이다).

## 전체 아키텍처 요약

```
[FastAPI] POST /api/auto-rigging/generate-rig
   → auto_rigging_jobs 테이블에 status='queued' 행 insert (Supabase)
   → 즉시 job_id 반환

[로컬 RTX 4090 머신] python -m backend.workers.auto_rigging_worker
   → status='queued'(또는 stale 'running') 행을 polling으로 claim
   → 사진(pet_image_url) 로드
   → (1) 세그멘테이션: 알파채널 있으면 그대로, 없으면 SAM2(실패 시 GrabCut 폴백)
        — vitmatte_service._sam2_mask / _grabcut_mask 재사용 (pose_estimation_service.py / auto_rigging_service.py)
      (2) 포즈 추정: 18개 키포인트 (pose_estimation_service.py)
          - 기본 백엔드 "heuristic_mask_geometry"(마스크 실루엣 기하, 의존성 없음)
          - "deeplabcut_superanimal" 백엔드도 구현되어 있음(DeepLabCut 3.0 pytorch
            엔진 필요 — 이 세션에서는 미검증, 아래 "DeepLabCut 조사 결과" 참고)
      (3) 리깅: 15개 본(계층 구조) 생성 + 본마다 사진에서 사각형 영역 크롭
          (mesh 스키닝 아님 — "본당 독립 사각형" 방식, auto_rigging_service.py)
      (4) 액션 애니메이션 재타겟: 손수 제작한 배깔기(lie_down) 모션 곡선을
          이 강아지의 탐지된 본 길이/회전에 맞춰 적용 (spine_action_curves.py)
      (5) skeleton.json + skeleton.atlas + skeleton.png 로 직렬화
          (spine_rig_builder.py) → Supabase Storage 업로드
   → 매 단계마다 auto_rigging_jobs.progress_json 갱신
   → 완료 시 result_json + status='done' (또는 예외 시 'failed' + error, 루프는 안 죽음)

[FastAPI] GET /api/auto-rigging/jobs/{job_id} → 진행률/결과 조회 (프론트 polling용)

[별도, 이미 완료된 부분] device-renderer(C++/Spine-CPP)가 skeleton.json+atlas+PNG를
읽어 실시간 렌더링 + playAction()으로 애니메이션 재생 — 이 문서의 범위 밖.
```

## 파일 목록

| 파일 | 역할 | 상태 |
|---|---|---|
| `backend/services/pose_estimation_service.py` | 사진+마스크 → 18개 키포인트 | ✅ 구현+테스트(heuristic) / ⚠️ 구현됨·미검증(deeplabcut) |
| `backend/services/spine_rig_builder.py` | 좌표 변환 + Spine JSON/아틀라스 저수준 직렬화 | ✅ 구현+테스트 |
| `backend/services/auto_rigging_service.py` | 키포인트 → 본 계층 + 이미지 크롭 + 아틀라스 패킹 + 파이프라인 오케스트레이션 | ✅ 구현+테스트 |
| `backend/services/spine_action_curves.py` | 배깔기(lie_down) 모션 곡선 재타겟 | ✅ 구현+테스트(구조) / ⚠️ 각도값 시각 미검증 |
| `backend/scripts/test_auto_rigging_goya.py` | 고야 사진 CLI 스모크 테스트 | ✅ 실행됨(아래 결과 참고) |
| `backend/models/auto_rigging.py` | API 요청/응답 Pydantic 모델 | ✅ |
| `backend/services/auto_rigging_jobs.py` | `auto_rigging_jobs` 테이블 CRUD | ✅ (Supabase 연결 자체는 이 세션에서 미검증) |
| `backend/routers/auto_rigging.py` | `/api/auto-rigging/*` | ✅ import/등록 검증됨 |
| `backend/workers/auto_rigging_worker.py` | 로컬 RTX 4090 polling 워커 | ✅ 코드 완성 / ⚠️ 실제 GPU 워커에서 미실행 |
| `supabase/migrations/20260721000300_auto_rigging_jobs.sql` | 테이블 스키마 | ✅ 작성됨 / ⚠️ 실제 Supabase에 미적용 |
| `backend/main.py` | `ENABLE_AUTO_RIGGING_API`(기본 1) 조건부 라우터 등록 | ✅ `import backend.main` 성공 확인 |

## 1단계 — 조사(Research): 사전학습 동물 포즈 모델, 2026년 기준

### DeepLabCut SuperAnimal-Quadruped
- **정지 이미지 1장 지원 여부: 예.** DeepLabCut 3.0(pytorch 엔진)의
  `deeplabcut.pose_estimation_pytorch.apis.superanimal_analyze_images(...)`가
  이미지 경로 리스트를 직접 받는다(비디오 전용 아님) — GitHub 소스
  (`deeplabcut/pose_estimation_pytorch/apis/analyze_images.py`)에서 시그니처를
  직접 확인함.
- **재학습 없이 바로 사용(zero-shot) 가능.** `superanimal_quadruped_hrnetw32`가
  Faster R-CNN 검출기 + HRNet-w32 top-down 포즈 모델 조합으로, Quadruped-80K
  (개/말/양/설치류/코끼리 등 다종 45+ 종 통합 데이터셋, Ye et al. 2023,
  *Nature Communications* 2024)로 학습되어 있다. 39개 키포인트.
- **이 세션에서 실제로 검증한 것**: 가벼운 헬퍼 패키지 `dlclibrary`(0.0.12)를
  이 샌드박스에 실제로 설치하고 실행해서
  `dlclibrary.get_available_models("superanimal_quadruped")` →
  `['hrnet_w32', 'resnet_50', 'rtmpose_s']` 를 받는 것까지 확인함. 또한
  HuggingFace 저장소(`mwmathis/DeepLabCutModelZoo-SuperAnimal-Quadruped`)의
  파일 목록도 직접 조회해서 각 모델의 `.pt` 체크포인트가 실제로 존재함을
  확인함. → **API/모델 존재 자체는 2026년 현재도 확실히 살아있다.**
- **이 세션에서 검증하지 못한 것**: 실제 추론 자체. `superanimal_analyze_images`를
  쓰려면 `deeplabcut[pytorch]` 풀 패키지(torch/torchvision 외 pandas,
  statsmodels, scikit-image, numba 등 무거운 의존성 체인)가 필요한데, 이
  샌드박스는 GPU가 없고(`torch 2.10.0+cpu`) 게다가 pip/네트워크 호출 1건당
  수십~100초 이상 걸릴 정도로 느려서, 실제 설치+추론까지는 시도하지 않았다
  (설치 자체는 실패할 이유가 딱히 없어 보이지만, 실제로 해보지 않고 "된다"고
  말하는 건 정직하지 않다고 판단).
- **키포인트 이름 정확한 스펠링을 확인 못함.** 39개 키포인트의 정식 이름
  목록을 담은 별도 소형 config 파일을 HuggingFace 저장소에서 찾지 못했다
  (거기엔 `.pt` 체크포인트만 있음 — 이름 목록은 `deeplabcut` 패키지 소스
  내부에 있는 것으로 보임). `pose_estimation_service.py`의
  `_DLC_KEYPOINT_NAME_GUESS`는 Quadruped-80K/AnimalPose류 데이터셋에서 흔히
  쓰이는 이름(`nose`, `tail_base`, `front_left_paw` 등)을 근거로 한 **추정치**이며,
  실제 설치 후 반환값으로 재검증이 필요하다. 이름이 틀려도 크래시하지 않고
  경고만 남기도록 방어적으로 구현했다.

### 대안: MMPose(AP-10K)
- `MMPoseInferencer('animal')`(RTMPose-m + RTMDet-m 조합, AP-10K 데이터셋
  학습)로 단일 이미지 추론이 표준 API로 제공됨을 확인(공식 문서/데모 스크립트
  기준). AP-10K는 10,000장, 60종 동물 데이터셋 — quadruped 다양성은
  SuperAnimal의 Quadruped-80K보다 작지만, RTMPose 계열이라 **추론 속도가
  훨씬 빠르고(실시간급)**, 설치도 `mmpose`+`mmengine`(+검출기용 `mmdet`)로
  DeepLabCut보다 의존성 체인이 상대적으로 가볍다.
- **결론**: 키포인트 커버리지/다종 일반화는 SuperAnimal-Quadruped가 근소하게
  우세해 보이지만(더 큰 데이터셋, 39개 vs AP-10K 17개), **설치 난이도/추론
  속도는 MMPose가 유리**하다. 실제 GPU 워커에 설치할 때 **둘 다 시도해보고
  Goya 사진 기준으로 실측 비교**하는 것을 권장(둘 다 pip 설치 가능, 상호
  배타적이지 않음).
- 둘 다 이 세션에서 실제 설치/추론은 하지 않았다(같은 이유: 샌드박스 제약).

### 현실적 결론(자동 리깅의 진짜 한계)
사진 1장에서 프로덕션급 **완전 자동 스킨드 메쉬**를 만드는 것은 여전히
미해결 문제다. 이번 구현은 사용자가 제시한 현실적 MVP 그대로: **(a)** SAM2
전신 마스크, **(b)** 사전학습 포즈 모델(또는 미확보 시 실루엣 휴리스틱)로
15~20개 키포인트, **(c)** 그 키포인트로 2D 본 계층 생성, **(d)** 본마다
독립적인 사각형 이미지 워프(메쉬 스키닝 아님), **(e)** 손수 제작한 모션 곡선을
탐지된 본 비율에 재타겟. **사람 개입이 필요한 지점**: 키포인트 정확도 검증/
보정(포즈 모델이 side-view가 아니거나 폐색이 심한 사진에서 틀릴 수 있음),
그리고 완성된 리그를 실제 렌더러로 보고 본 폭/모션 곡선 각도를 튜닝하는 것.

## 2단계 — 키포인트 추정 (`pose_estimation_service.py`)

- 상태: ✅ heuristic 백엔드 구현+테스트 완료 / ⚠️ deeplabcut_superanimal 백엔드
  구현됨(코드 리뷰 가능한 수준)·**실행 미검증**
- 18개 키포인트 스키마: `nose, head_top, neck, spine_mid, tail_base, tail_tip`
  (6) + 4다리 × `{shoulder/hip, elbow/knee, paw}` (12) = 18.
- `heuristic_mask_geometry` 백엔드(기본값, 의존성 없음): 마스크 실루엣의
  최상단/최하단 프로파일만으로 머리/목/등/꼬리/다리 위치를 근사. **실제
  Goya 사진으로 테스트한 결과(아래 5단계 참고), 코/머리 위치는 그럭저럭
  맞았지만 목/꼬리 위치가 눈에 띄게 부정확했고, 근/원위 다리를 구분하지
  못해 앞다리 좌우가 완전히 같은 픽셀 위치로 겹쳤다** — 이는 예상된
  한계이며(파일 상단 docstring에 사전 문서화됨), 학습 기반 포즈 모델 없이는
  근본적으로 개선되기 어렵다.
- `deeplabcut_superanimal` 백엔드: `dlclibrary`로 모델 목록 조회는 실제
  동작을 확인했지만, 추론 자체(`deeplabcut` 풀 패키지 필요)는 미검증.
- `POSE_ESTIMATION_BACKEND` 환경변수로 전환(`heuristic`(기본)/
  `deeplabcut_superanimal`/`auto`).

## 3단계 — 자동 리깅 (`auto_rigging_service.py`, `spine_rig_builder.py`)

- 상태: ✅ 완료(수학/포맷 레벨) — Goya 사진으로 end-to-end 실행 성공, 생성된
  `skeleton.json`이 구조 자체 검증(필수 키/슬롯-본 참조/어태치먼트-아틀라스
  참조 일치)을 통과함.
- 본 계층(15개, root 포함): `root → pelvis → {spine → {neck → head,
  front_left_upper→lower, front_right_upper→lower}, tail1→tail2,
  back_left_upper→lower, back_right_upper→lower}`.
- 좌표 변환: Spine의 부모-자식 변환 공식(`자식 월드원점 = 부모 월드원점 +
  Rotate(부모 월드회전)·(자식.x,자식.y)`)을 역산해서, "이미 아는 키포인트
  월드 좌표"로부터 각 본의 로컬 (x,y,rotation)을 계산 — `spine_rig_builder
  .world_to_local()`. 회전값은 (-180,180]로 정규화(가독성 목적).
- 이미지 워프: **메쉬 스키닝이 아니라 "본당 독립 사각형"** 방식(사용자가
  요청한 현실적 MVP 축소판). 원본 사진에서 픽셀 자체를 회전시키지 않고
  축 정렬 바운딩 박스만 크롭한 뒤, `RegionAttachment.rotation = -본의 셋업
  월드회전` 으로 상쇄시켜 셋업 포즈에서는 사진이 원본 그대로 보이게 하고,
  애니메이션이 본을 추가로 회전시키면 그 추가분만큼만 이미지가 따라 회전하는
  방식 채택(회전 보간으로 인한 픽셀 아티팩트 회피, 공식 유도는
  `spine_rig_builder.py` 상단 docstring 참고).
- 아틀라스: 단순 shelf(선반) 패킹으로 직접 구현(외부 패커 라이브러리 없음) —
  공간 효율은 최적화하지 않음(프로토타입 수준).
- **알려진 시각적 결함**: 관절 부위에서 인접한 사각형끼리 벌어지거나 겹칠 수
  있음(메쉬 스키닝이면 없는 문제) — 다음 단계 개선 과제.

## 4단계 — 액션 애니메이션: 배깔기(lie_down) (`spine_action_curves.py`)

- 상태: ✅ 구조 완료(재타겟 로직 검증됨) / ⚠️ **각도 값 자체는 시각적으로
  한 번도 확인 못함**(이 샌드박스에 OpenGL/C++ 빌드 환경이 없어 device-renderer로
  실제로 렌더링해볼 수 없었음).
- 3개 액션 중 **배깔기만 구현**(사용자 요청대로 "정적 자세 전환이라 가장
  쉬움"이라는 판단 그대로) — 달려오기(반복 보행 사이클)/짖기(머리+입+앞다리
  바운스 루프)는 **미구현**(다음 단계 과제).
- 재타겟 원리: Spine의 `rotation`은 애초에 본 길이와 무관한 각도값이라, 손수
  만든 "회전 델타(도)" 상수를 그대로 아무 강아지에나 적용할 수 있다. 유일하게
  길이에 의존하는 값(몸통이 가라앉는 이동 거리)만 탐지된 뒷다리 길이 비율로
  스케일링(`_translate_amount()`).
- 좌우 반전: `pose.head_side`가 "right"면 모든 회전 델타에 -1을 곱함(2D 평면
  거울 대칭에서 모든 회전각의 부호가 뒤집히는 것은 기하학적으로 정확함).

## 5단계 — Goya 사진 End-to-End 테스트 (`backend/scripts/test_auto_rigging_goya.py`)

- 상태: ✅ **이 세션에서 실제로 실행 완료**(CPU-only Windows 샌드박스,
  `python -m backend.scripts.test_auto_rigging_goya`).
- 테스트 이미지 관련 발견: `누끼딴고야.png`는 예상과 달리 **실제 알파채널이
  없는 순수 RGB 이미지**였다(`mode=RGB`, `alpha.min()==255`) — 미리보기에
  보이는 "체크무늬"는 투명도가 아니라 픽셀에 실제로 그려진 색상이었다. 따라서
  테스트 스크립트의 마스크 소스는 항상 세그멘테이션 경로(기본: GrabCut,
  `--use-sam2-mask`로 SAM2 재사용도 가능)를 타게 됐다.
- 실행 결과(요약, 전체 로그는 커밋 시점 `outputs/goya_auto_rigging_test/goya/`
  참고 — `.gitignore`로 커밋되지는 않았을 수 있음, 재현은 아래 "실행 방법"
  명령 그대로):
  - GrabCut 마스크 생성 성공(CPU, 약 2~4분 — 느린 샌드박스 환경 기준이라
    실제 GPU 워커에서는 훨씬 빠를 것).
  - `heuristic_mask_geometry` 백엔드로 18개 키포인트 전부 생성됨.
    - **잘 맞은 것**: `nose`(주둥이 근처), `head_top`(귀 근처), `spine_mid`
      (등 중앙 근방) — 대략 합리적.
    - **눈에 띄게 부정확했던 것**: `neck`이 `head_top`과 거의 같은 위치(귀
      근처)에 찍혀 실제 목 위치보다 훨씬 위쪽에 위치함. `tail_base`/`tail_tip`
      둘 다 실제 꼬리(사진에서 위로 말린 술 모양)가 아니라 엉덩이 뒤쪽 실루엣
      가장자리에 찍힘(꼬리가 가늘어서 "바깥쪽 30% 영역" 휴리스틱이 꼬리 자체를
      못 찾고 몸통 윤곽 끝을 대신 잡음). `front_left_*`/`front_right_*`가
      완전히 동일한 픽셀 좌표로 겹침(문서화된 근/원위 다리 구분 불가 한계
      그대로 발생) — 반면 뒷다리는 우연히 2개의 구분되는 열이 검출됐지만,
      그중 하나(`back_left_hip`, x≈689)가 실제로는 앞다리 군집과 거의 같은
      x좌표에 찍혀 실질적으로 뒷다리 위치가 아니었다(다리 후보를 앞/뒤로
      나누는 "목과의 거리 vs 꼬리와의 거리" 기준이 이 사진에서 오분류함).
  - 본 15개, 슬롯 14개 전부 생성, 아틀라스 페이지 2048×355 패킹 성공.
  - `lie_down` 애니메이션 14개 본에 키프레임 적용 성공.
  - **`skeleton.json` 구조 자체 검증 통과**(필수 키 존재, 모든 슬롯이 실제
    본을 참조, 모든 어태치먼트가 실제 아틀라스 리전을 참조, `json.dumps()`
    직렬화 성공).
  - `debug_keypoints.png`(원본 사진에 키포인트+본 오버레이)를 실제로 렌더링해
    육안으로 위 문제들을 확인함.
- **결론**: 파이프라인의 "배관"(세그멘테이션→키포인트→본 계층 수학→Spine
  JSON/아틀라스 직렬화→애니메이션 재타겟→구조 검증)은 **끝에서 끝까지
  실제로 동작**한다. 하지만 **`heuristic_mask_geometry`의 키포인트 품질은
  실사용하기엔 부족하다** — 이것이 정확히 조사 단계에서 예측한 바
  ("실루엣만으로는 학습된 포즈 모델을 대체할 수 없다")를 실제 사진으로
  재확인한 것.

## 실행 방법 (요약)

```bash
# 0) 최초 1회: Supabase SQL Editor에서 마이그레이션 실행
#    supabase/migrations/20260721000300_auto_rigging_jobs.sql

# 1) 빠른 스모크 테스트 (Goya 사진, 큐 없이 직접 실행, 추가 의존성 없음)
python -m backend.scripts.test_auto_rigging_goya
# 결과: outputs/goya_auto_rigging_test/goya/{skeleton.json,skeleton.atlas,skeleton.png,
#        debug_keypoints.png, manifest.json}

# 2) (선택, 미검증) DeepLabCut SuperAnimal-Quadruped로 포즈 추정 품질 비교
pip install "deeplabcut[pytorch]"   # 무거움 — RTX 4090 워커 머신에서 실행 권장
python -m backend.scripts.test_auto_rigging_goya --backend deeplabcut_superanimal

# 3) 정식 경로: 워커를 상시 실행
python -m backend.workers.auto_rigging_worker

# 4) FastAPI에서 잡 등록 (별도 서버/프론트에서 호출)
POST /api/auto-rigging/generate-rig
  {"user_id": "...", "pet_image_url": "https://.../고야.png", "pose_backend": "heuristic"}
GET  /api/auto-rigging/jobs/{job_id}
```

## 남은 작업 (사람이 이어서 해야 하는 것)

1. **실제 렌더링 검증(최우선)**: `device-renderer`를 빌드해서 이 파이프라인이
   만든 `skeleton.json`/`skeleton.atlas`/`skeleton.png`를 실제로 로드해보고
   (a) `RegionAttachment` 회전/좌표 상쇄 공식(`spine_rig_builder.py`의
   `region_attachment_transform`)이 실제로 "셋업 포즈에서 원본 사진처럼
   보이는지", (b) `lie_down` 애니메이션 각도가 시각적으로 그럭저럭 자연스러운
   지 확인. 이 두 가지는 이 세션(OpenGL/C++ 빌드 환경 없음)에서 전혀 확인하지
   못한 가장 큰 리스크다.
2. **포즈 추정 품질 개선(최우선)**: `deeplabcut[pytorch]` 또는 `mmpose`를
   실제 GPU 워커에 설치하고 Goya 사진으로 `heuristic_mask_geometry`와 실측
   비교. `_DLC_KEYPOINT_NAME_GUESS`(pose_estimation_service.py)의 이름 매핑을
   실제 반환값으로 재검증/수정.
3. 리깅 품질 개선: 본당 독립 사각형 대신 **간단한 메쉬(예: 각 사각형의 관절
   쪽 변을 인접 사각형과 공유하도록 정점을 살짝 겹치는 palette-map 스타일)**로
   업그레이드하면 관절 벌어짐/겹침 문제가 줄어듦 — 이번 프로토타입 범위 밖으로
   남김.
4. 달려오기(반복 보행 사이클)/짖기(머리+입+앞다리 바운스 루프) 애니메이션 곡선
   추가(`spine_action_curves.py`에 배깔기와 같은 패턴으로 추가하면 됨 —
   `build_run_animation()`, `build_bark_animation()` 등).
5. `dlclibrary`가 실제로 반환하는 키포인트 순서/이름을 실제 설치 후 캡처해서
   `_DLC_KEYPOINT_NAME_GUESS`를 정확한 값으로 교체.
6. Supabase에 `auto_rigging_jobs` 마이그레이션 실행 + `SUPABASE_URL`/
   `SUPABASE_SERVICE_ROLE_KEY`가 워커 실행 환경에도 설정돼 있는지 확인(이
   세션에서는 Supabase 연결 자체를 테스트하지 않음 — 로컬 파이프라인 함수
   호출까지만 검증됨).
7. 여러 강아지 사진(다른 종/자세/각도)으로 heuristic 백엔드의 실패 사례를
   더 모아서, 포즈 모델 도입 전에 휴리스틱만으로 어디까지 밀어붙일 수 있는지
   추가로 판단(예: "정면이 아니라 항상 측면 사진만 받는다"는 제약을 UX로
   강제하면 휴리스틱 정확도가 오를 가능성).

## 열린 결정사항 / 트레이드오프 로그

- **메쉬 스키닝 대신 본당 독립 사각형**: 사용자가 명시한 현실적 MVP 그대로 —
  완전 자동 메쉬 스키닝은 여전히 미해결 문제라고 판단, 사각형 워프로 축소.
- **픽셀 회전 대신 축 정렬 크롭 + attachment.rotation 상쇄**: 회전 보간으로
  인한 앨리어싱/여백 아티팩트를 피하고, 수학적으로 더 간단하고 검증하기 쉬운
  경로를 선택.
- **DeepLabCut/MMPose 실제 설치를 이 세션에서 시도하지 않음**: 샌드박스가
  GPU 없음 + 네트워크/셸 호출이 매우 느림(명령 1건당 수십~100초+) → 완전
  설치+추론 검증에 드는 시간 대비, "휴리스틱 폴백을 실제로 끝까지 검증하고
  DeepLabCut 경로는 근거 있는 조사+방어적 코드로 남기는" 쪽이 이번 세션의
  시간 예산에서 더 합리적이라고 판단.
- **액션 3종 중 배깔기만 구현**: 사용자가 제시한 우선순위(정적 자세 전환이
  가장 쉬움) 그대로 따름.
- **큐 인프라**: 기존 action_video_jobs/background_video_jobs와 동일하게
  Supabase(Postgres) 테이블 polling — 새 인프라 추가하지 않음.
