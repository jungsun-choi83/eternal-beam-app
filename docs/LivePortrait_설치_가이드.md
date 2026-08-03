# LivePortrait 설치 가이드 (액션 20종 파이프라인용)

이 문서는 `backend/services/live_portrait_service.py` / `live_portrait_batch.py` /
`backend/workers/live_portrait_worker.py`가 실제로 동작하려면 **사용자의 로컬 GPU
머신(RTX 4090 등)에** 무엇을 설치해야 하는지 정리한 가이드입니다.

> Luma idle 루프(이미 구현/운영 중)와는 무관합니다. 이 가이드는 "액션(20종)" 전용
> LivePortrait Animals 모드 설치 방법입니다.

## 1. 왜 Render(웹 서비스)가 아니라 로컬 GPU(또는 Modal GPU)에서 도는가

이 프로젝트의 Render 배포는 `backend/requirements-render.txt`만 설치하는
가벼운 무료 플랜(512MB RAM / 0.1 CPU)이고, `backend/Dockerfile`도
`requirements-render.txt`만 COPY합니다 — torch/ultralytics/transformers조차 없습니다.
실측 테스트에서 이미 SAM2+ViTMatte 같은 비교적 가벼운 CPU 추론도 이 인스턴스에서
OOM(SIGKILL)로 죽는 걸 확인했습니다. LivePortrait는:

- PyTorch + 커스텀 CUDA 연산(X-Pose의 `MultiScaleDeformableAttention`, Animals 모드용)
- 수 GB의 체크포인트
- 실질적으로 GPU 없이는 쓸 수 없는 추론 속도

가 필요해서, Render 웹 서비스에는 **절대 설치하지 않습니다**(requirements-render.txt에
추가 금지). 대신:

- **1차 경로(이 프로젝트가 채택)**: 사용자의 로컬 RTX 4090 머신에서 상시 도는 워커
  프로세스(`backend/workers/live_portrait_worker.py`)가 Supabase 잡 큐를 polling해서
  처리 — 4단계 참고.
- **보조/선택 경로**: Modal(서버리스 GPU) — `backend/modal_apps/live_portrait_app.py`
  (검증 안 됨, 필요 시 나중에 `modal deploy`로 확인 필요).

## 2. 설치 (로컬 RTX 4090 머신 기준, Windows/Linux 공통 절차)

```bash
# 1) 리포 클론 (백엔드 리포와는 별개 위치에)
git clone https://github.com/KwaiVGI/LivePortrait.git
cd LivePortrait

# 2) 가상환경 (conda 예시 — 공식 README 기준 python 3.10 권장)
conda create -n LivePortrait python=3.10
conda activate LivePortrait

# 3) PyTorch (CUDA 버전에 맞게 — pytorch.org 참고, RTX 4090이면 CUDA 12.x 계열)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 4) 나머지 의존성
pip install -r requirements_base.txt

# 5) Animals 모드 전용: X-Pose 커스텀 op 빌드 (Linux/Windows + NVIDIA GPU 전용)
cd src/utils/dependencies/XPose/models/UniPose/ops
python setup.py build install
cd -   # (원래 LivePortrait 루트로)

# 6) 가중치 다운로드 — 공식 README의 "Download pretrained weights" 섹션대로
#    (HuggingFace 또는 Google Drive) pretrained_weights/ 에 human + animals 모델 모두 받기
#    (Animals 모드는 pretrained_weights/liveportrait_animals/ 하위에 별도 체크포인트)

# 7) 설치 확인 (공식 예제로 스모크 테스트)
python inference_animals.py -s assets/examples/source/s39.jpg \
    -d assets/examples/driving/wink.pkl --no_flag_stitching --driving_multiplier 1.75
```

FFmpeg도 PATH에 있어야 합니다(이 백엔드의 다른 서비스들과 동일 요구사항 — 이미
설치돼 있다면 재설치 불필요).

## 3. 이 백엔드와 연결하기

`backend/env.local` 또는 루트 `.env`에 다음을 추가:

```bash
LIVE_PORTRAIT_REPO_DIR=/path/to/LivePortrait
LIVE_PORTRAIT_PYTHON=/path/to/conda/envs/LivePortrait/bin/python   # Windows는 ...\python.exe
LIVE_PORTRAIT_DRIVING_MULTIPLIER=1.75
LIVE_PORTRAIT_DRIVING_VIDEOS_DIR=backend/assets/driving_videos   # 기본값, 굳이 안 적어도 됨
```

`backend/services/live_portrait_service.py`가 이 두 환경변수로 `inference_animals.py`를
서브프로세스로 호출합니다. 리포 코드 안으로 import해서 쓰는 대신 서브프로세스로 호출하는
이유: (1) LivePortrait가 자체 argparse/tyro CLI 스크립트 구조라 내부 함수를 안전하게
import하는 공개 API가 없고, (2) 별도 가상환경(다른 CUDA/torch 버전)에서 도는 걸
그대로 지원할 수 있어 이 백엔드 자체의 의존성(SAM2용 torch 등)과 충돌할 걱정이 없습니다.

## 4. 정체성(identity) 보존 파라미터 — 요약

자세한 근거는 `backend/services/live_portrait_service.py`의 `LivePortraitIdentityParams`
docstring에 출처(공식 changelog/README)와 함께 정리했습니다. 요약:

| 파라미터 | 값 | 이유 |
|---|---|---|
| `flag_relative_motion` | `True`(기본 유지) | absolute 모드는 공식 문서가 "identity leakage 위험" 명시 |
| `flag_stitching` | `False` | Animals 모드는 stitching 모듈을 학습 안 함(공식 권장) |
| `flag_pasteback` | `False` | Animals 모드 공식 비권장 + 우리가 SAM2로 직접 배경 합성하므로 불필요 |
| `flag_do_crop` | `True`(기본 유지) | 소스를 표준 공간으로 정합해야 워핑 품질이 안정적 |
| `driving_multiplier` | `1.75` | 공식 Animals 예시 커맨드 값 (stitching이 꺼져 있어 배율 보강 필요) |
| `driving_option` | `"pose-friendly"`(기본 유지) | 몸/머리 동작 위주라 표정 특화 모드보다 안전 |

**실제 고야(Goya) 사진으로 1~2개 액션을 먼저 생성해보고**, 생김새가 흔들리면
`driving_multiplier`를 낮추거나(예: 1.2~1.5) `flag_do_crop` 조합을 바꿔보는 걸
권장합니다 — 이 값들은 문서 조사 기반 권장값이고, 실제 소스 이미지 없이 최종
검증하지는 못했습니다.

## 5. 다음 단계

- `backend/assets/driving_videos/`에 실제 20개 mp4를 채우기(README.md 참고)
- `python -m backend.workers.live_portrait_worker`로 워커 실행
- `backend/scripts/test_live_portrait_goya.py`로 고야 사진 스모크 테스트
  (`docs/LivePortrait_파이프라인_진행상황.md`의 "테스트" 섹션 참고)
