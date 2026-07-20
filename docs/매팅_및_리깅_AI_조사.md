# 매팅(Matting) 모델 & 오토 리깅 AI 서비스 조사

## 1. 오픈소스 매팅 모델: ViTMatte vs SAM2Matting

| | [hustvl/ViTMatte](https://github.com/hustvl/ViTMatte) | [FudanCVL/SAM2Matting](https://github.com/FudanCVL/SAM2Matting) |
|---|---|---|
| 방식 | 트라이맵(trimap) 기반 refinement — plain ViT + detail capture module | SAM2/SAM3 트래커 + ROI 검출 + progressive alpha predictor (트라이맵 불필요, mask/point/box/text 프롬프트) |
| 라이선스 | **MIT** — 상업적 사용 가능 | **CC BY-NC-SA 4.0 — 비상업적 연구용 전용.** 상업적 사용은 저자(henghui.ding@gmail.com)에게 별도 문의 필요 |
| 입력 | 이미지 + 트라이맵(전경/배경/미확정 3구역 마스크) | 이미지(또는 비디오) + 프롬프트(마스크/포인트/박스/텍스트) |
| 성능 | Composition-1k/Distinctions-646 SOTA (2023) | 이미지·비디오 매팅 SOTA (ECCV 2026), SAM2.1-Tiny는 1080p에서 40FPS, <5GB VRAM |
| Hugging Face | `hustvl/vitmatte-small-composition-1k` (transformers 내장, `VitMatteImageProcessor`/`VitMatteForImageMatting`) | `FudanCVL/SAM2Matting` 체크포인트 (자체 추론 스크립트) |

### 결론 — 이번 구현은 ViTMatte 기반으로 진행

**SAM2Matting은 라이선스가 CC BY-NC-SA 4.0(비상업적 전용)이라 이 프로젝트(결제/구독 기능이 있는 상업 서비스)에는 그대로 못 씁니다.** 반면 ViTMatte는 MIT라 제약 없이 쓸 수 있어서, `backend/services/vitmatte_service.py`는 ViTMatte로 구현했습니다.

ViTMatte는 트라이맵이 필요하므로, 파이프라인은 다음처럼 3단계로 구성했습니다(★ rembg 미사용):

```
사진 업로드
  → ① YOLOv8로 피사체 bbox 검출 (ultralytics, 이미 backend에 있는 의존성 재사용)
  → ② bbox 시드로 OpenCV GrabCut 실행 → 전경/배경/미확정(unknown) 3구역 트라이맵 생성
  → ③ ViTMatte로 트라이맵의 unknown 영역만 정교하게 알파 추정 (털 경계 매팅)
  → RGBA PNG 저장
```

①·②는 rembg의 세그멘테이션 네트워크(u2net/isnet)를 전혀 쓰지 않는, 완전히 다른 방식입니다.

연구/품질 비교용으로 SAM2Matting을 실험해보고 싶다면 `docs/`에 별도로 정리하겠지만, **상업 배포 파이프라인에는 넣지 않았습니다.**

### 업데이트 — 1단계 세그멘테이션을 SAM2(베이스, Apache-2.0)로 교체 (2026-07)

위 SAM2Matting(비상업 전용)과 별개로, **베이스 SAM2**(`facebook/sam2.1-hiera-*`, Apache-2.0 — 트라이맵/매팅 헤드 없이 세그멘테이션만 하는 원본 모델)는 라이선스 제약이 없어서, GrabCut 대신 1단계 마스크 생성기로 교체했습니다. 파이프라인은:

```
사진 업로드
  → ① YOLOv8로 피사체 bbox 검출
  → ② bbox를 박스 프롬프트로 SAM2에 전달 → 정밀 전경 마스크 → erode/dilate로 트라이맵 생성
     (SAM2 로드 실패 시 자동으로 GrabCut 폴백)
  → ③ ViTMatte로 트라이맵의 unknown 영역만 정교하게 알파 추정 (털 경계 매팅)
  → RGBA PNG 저장
```

GrabCut은 색상 통계 기반이라 배경이 복잡하거나 피사체와 배경 색이 비슷하면 마스크가 거칠어지는데, SAM2는 학습된 세그멘테이션 모델이라 훨씬 정밀한 실루엣을 뽑아준다 — 이후 ViTMatte가 다듬을 unknown band(경계)가 더 깨끗해져 최종 알파 품질이 올라갑니다. 구현은 `backend/services/vitmatte_service.py`(`VITMATTE_SEGMENTER=sam2|grabcut` 환경변수로 전환 가능), 엔드포인트는 `/api/matting/cutout`.

## 2. "사진 → 스켈레톤 + 애니메이션" 오토 리깅 AI 서비스 조사

요청하신 것과 같은, "사진 한 장을 올리면 뼈대와 애니메이션 데이터를 바로 반환하는 호스팅 API"는 **찾지 못했습니다.** 조사한 후보와 왜 안 맞는지 정리했습니다.

| 서비스 | 형태 | 왜 우리 케이스에 안 맞는가 |
|---|---|---|
| [facebookresearch/AnimatedDrawings](https://github.com/facebookresearch/AnimatedDrawings) | 오픈소스, MIT(모델 가중치도 MIT) | 가장 근접한 사례. 사진→detector→pose estimator(TorchServe `.mar` 모델)로 관절 좌표(스켈레톤)를 뽑고 ARAP 리깅+BVH 리타겟으로 애니메이션까지 만듦. **단, 2족 보행 인간형 캐릭터 전용**(양팔·양다리 가정) — 강아지 등 4족 동물은 자체 지원 안 함(직접 커스텀 skeleton config를 만들어야 함). 그리고 이건 클라우드 API가 아니라 **직접 호스팅해야 하는 오픈소스**(TorchServe로 `.mar` 모델을 자체 서버에 올려야 함). |
| [DeepMotion Animate 3D REST API](https://github.com/DeepMotion/Animate-3D-REST-API) | 실제 호스팅 클라우드 API (가입 필요) | 진짜 API 스펙은 있음 (`POST {host}/process`, `processor=video2anim`, params로 `model_id` 지정 등 — [`dm-animate3d-api` PyPI 패키지](https://pypi.org/project/dm-animate3d-api/)도 있음). 하지만 입력이 **사람이 움직이는 비디오**이고, 출력은 **이미 리깅되어 있는 3D 휴머노이드 캐릭터(FBX/GLB/GLTF/VRM)에 모션을 리타겟**하는 것 — 사진 한 장에서 스켈레톤 자체를 만들어주지 않고, 애니메이션 캐릭터/동물도 명시적으로 지원 안 함. |
| [GenielabsOpenSource/spine-animation-ai](https://github.com/GenielabsOpenSource/spine-animation-ai) | Claude Code용 "에이전트 스킬" (API 아님) | 참조 이미지에서 SIFT+RANSAC으로 파츠 위치를 맞추고 Spine JSON을 생성 — 개념적으로 우리가 하려는 것과 가장 가깝지만, **호스팅 API가 아니라 로컬 스크립트/에이전트 워크플로**이고 사람 캐릭터 예시 기준입니다. |
| [K-ulucay/spine_anim_mcp](https://github.com/K-ulucay/spine_anim_mcp) | MCP 서버 (API 아님) | **레이어가 분리된 PSD**를 Spine 4.2 리그로 변환 — 입력이 사진이 아니라 이미 파츠별로 나뉜 PSD. |
| [MangoLion/stretchystudio](https://github.com/MangoLion/stretchystudio) | 데스크톱/웹 에디터 (API 아님) | AI 오토 리깅(DWPose) 기능이 있지만 사람이 에디터에서 조작하는 도구 — 서버에서 호출할 API가 없음. |

### 결론

"사진 업로드 → 스켈레톤+애니메이션 반환"을 **완전 자동으로, 호스팅 API로** 해주는 서비스는 지금 존재하지 않습니다(2026-07 기준). 가장 가까운 오픈소스는 Animated Drawings이지만 인간형 전용이라, 강아지에 적용하려면:

1. Animated Drawings의 detector/pose-estimator를 강아지용으로 새로 학습(또는 강아지 keypoint 데이터셋 + 기존 동물 pose estimation 모델, 예: [DeepLabCut](https://github.com/DeepLabCut/DeepLabCut), [Animal Kingdom](https://github.com/sutdcv/Animal-Kingdom) 계열)해서 자체 파이프라인을 만들거나
2. 지난 분석([unity-to-spine-migration-analysis](../unity-to-spine-migration-analysis.canvas.tsx))에서 제안한 "공용 템플릿 리그 + 스킨 교체" 방식으로 가는 것이 현실적입니다 — 이 경우 리깅 자체는 한 번만 수작업으로 하고, 사진마다 자동으로 하는 건 매팅(털 경계 알파, 이번에 구현한 부분)과 스킨 텍스처 교체뿐이면 됩니다.

필요하시면 DeepLabCut 기반 강아지 keypoint 검출 PoC나, Animated Drawings 파이프라인을 4족 스켈레톤으로 포크하는 작업을 다음 단계로 진행할 수 있습니다.
