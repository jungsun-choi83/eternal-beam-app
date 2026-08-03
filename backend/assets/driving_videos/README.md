# 드라이빙 영상(driving videos) 폴더

LivePortrait "액션 20종" 배치 파이프라인(`backend/services/live_portrait_batch.py`)이
이 폴더를 순회하며 각 mp4를 드라이빙(모션 레퍼런스) 영상으로 사용합니다.

## 아직 실제 파일이 없습니다

이 폴더에는 실제 20개 영상 파일이 **아직 없습니다** — 코드 저장소를 뒤져봤지만
기존에 준비된 "드라이빙 영상" 에셋이 없었습니다(`driving`/`live_portrait`/`LivePortrait`
키워드로 검색해도 코드/문서 언급만 있고 실제 영상 파일은 없음). 사용자가 실제 촬영/수집한
20개 영상을 이 폴더에 넣어야 파이프라인이 실제로 20개 결과를 만듭니다.

## 기대하는 형식

- 파일명 = 액션 이름 (영문 소문자, 스네이크/케밥 무관). 예:
  `sit.mp4`, `run.mp4`, `jump.mp4`, `sniff.mp4`, `tail_wag.mp4`, `head_tilt.mp4`,
  `ear_perk.mp4`, `bark.mp4`, `lie_down.mp4`, `roll_over.mp4`, `shake_paw.mp4`,
  `spin.mp4`, `stretch.mp4`, `yawn.mp4`, `walk.mp4`, `look_around.mp4`, `nuzzle.mp4`,
  `sneeze.mp4`, `scratch.mp4`, `wake_up.mp4` (이 20개는 예시 — 실제 액션 목록은
  기획에 맞춰 자유롭게 정하면 됩니다. 파일명이 결과 매니페스트의 `action` 키로 그대로
  쓰입니다.)
- 컨테이너: `.mp4` (다른 확장자는 무시됩니다)
- 내용: 강아지(또는 유사 동물)가 해당 동작을 수행하는 짧은 영상 — LivePortrait
  Animals 모드가 이 영상의 "모션"만 뽑아 우리 소스 사진(강아지)에 이식합니다.
  얼굴/몸 윤곽이 프레임 안에 안정적으로 보이는 영상일수록 결과가 좋습니다.
- 개수: 정확히 20개를 권장하지만, 파이프라인은 **폴더에 실제로 있는 파일 개수만큼만
  처리**하고 20개가 아니어도 에러 내지 않습니다(로그로 경고만 남김) — 부분적으로
  먼저 테스트하면서 점진적으로 채워도 됩니다.

## 경로를 바꾸고 싶다면

기본 경로는 이 폴더(`backend/assets/driving_videos/`)입니다.
`LIVE_PORTRAIT_DRIVING_VIDEOS_DIR` 환경변수로 다른 경로를 지정할 수 있습니다.
