# Eternal Beam MVP — 단일 스크립트 실행 가이드

마스터 프롬프트 기반: **input.jpg → 감동의 output.mp4** (PC에서 즉시 변환 후 전체 화면 재생)

## 파이프라인 요약

| 단계 | 기술 | 설명 |
|------|------|------|
| 1. 피사체 분리 | **U2-Net** (u2net_human_seg) + **Alpha Matting** | 머리카락·털 끝까지 정교 분리 |
| 2. 깊이 추정 | **ZoeDepth** (선택) | Z-depth 엔진, DoF/파라랙스용 |
| 3. 합성 | FFmpeg | 테마 배경 + **Breathing** + **Soft Glow** + **DoF**(배경 블러) + **True Black** + **Film Grain** |
| 4. BGM | FFmpeg | 슬픈 피아노 스타일, Seamless Loop (선택) |
| 5. 재생 | 시스템 기본 플레이어 | 완료 시 전체 화면 팝업 |

## 설치

```bash
cd python
pip install -r requirements-mvp.txt
```

**ffmpeg**가 PATH에 있어야 합니다.

- Windows: `choco install ffmpeg`
- Mac: `brew install ffmpeg`

## 실행

```bash
# 기본 (입력 → input_eternal.mp4, 완료 후 팝업 재생)
python eternal_beam_mvp.py input.jpg

# 출력 파일 지정
python eternal_beam_mvp.py input.jpg output.mp4

# 테마 배경 MP4 지정 (없으면 그라데이션)
python eternal_beam_mvp.py input.jpg --theme backend/themes/rainbow_heaven.mp4

# BGM 추가 (로열티 프리 피아노 등)
python eternal_beam_mvp.py input.jpg --bgm piano_memorial.mp3

# 1080p, 20초, 팝업 없이
python eternal_beam_mvp.py input.jpg result.mp4 --full-hd --duration 20 --no-popup
```

## 옵션

- `--theme PATH` : 배경 영상 MP4 (미지정 시 `backend/themes/` 또는 그라데이션)
- `--duration N` : 영상 길이(초), 기본 12
- `--bgm PATH` : BGM 오디오 파일 (영상 길이만큼 루프)
- `--full-hd` : 1080p 출력 (기본 720p)
- `--no-popup` : 완료 후 재생 창 띄우지 않음

## True Black / 하드웨어

영상의 가장 어두운 부분은 **#000000**에 가깝게 클램핑됩니다. 프로젝터 등 하드웨어에서 검은 영역을 투명으로 처리하면 피사체가 공중에 떠 있는 것처럼 보입니다.

## 참고

- **Stable Diffusion Inpainting**·**다층 파라랙스**는 현재 스크립트에서 단순화되어 있습니다 (배경 DoF 블러 + 단일 레이어 오버레이). 고도화 시 별도 모듈로 확장 가능합니다.
- BGM이 없으면 무음 영상으로 출력됩니다. 슬픈 피아노 등 로열티 프리 음원을 `--bgm`으로 지정하면 Seamless Loop로 합성됩니다.
