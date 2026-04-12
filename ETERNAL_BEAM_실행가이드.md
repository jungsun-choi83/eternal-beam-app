# Eternal Beam 실행 가이드

## 1. 모니터에 보이는 화면 시뮬레이션

### ① 실행 직후 ~ 영상 생성 완료까지 (몇 분~십 수 분)

- **모니터에는 터미널(콘솔) 창만** 보입니다.  
  별도의 이미지/영상 창은 뜨지 않습니다.

- 터미널에는 아래와 같은 **텍스트 로그**만 순서대로 출력됩니다.

```
[Eternal Beam] 이미지 로드 중...
[Eternal Beam] Segmentation (U2-Net) 실행 중...
[Eternal Beam] Depth (MiDaS) 실행 중...
[Eternal Beam] Inpainting (Stable Diffusion) 실행 중... (시간이 걸릴 수 있습니다)
[Eternal Beam] 감성 프레임 생성 중...
[Eternal Beam] 비디오 인코딩 중 (H.264, 1920x1080, 30fps)...
[Eternal Beam] 저장 완료: output.mp4
[Eternal Beam] 전체 화면 재생을 시작합니다. 종료하려면 ESC를 누르세요.
```

- 첫 실행이면 중간에 **MiDaS, rembg, Stable Diffusion** 모델 다운로드로 인해 더 오래 걸릴 수 있고, 터미널에 다운로드 진행 메시지가 보일 수 있습니다.

---

### ② 영상 생성이 끝난 직후

- 터미널 로그가 끝나면 **곧바로 전체 화면 창**이 열립니다.

- **처음 1~2초**:  
  화면 전체가 **검은색**으로 채워집니다. (PyQt5 재생 창이 뜨고 비디오가 로드되는 시간)

- **이후 ~6초**:  
  - **1920×1080** 해상도의 **6초짜리 감성 영상**이 전체 화면으로 재생됩니다.  
  - 내용: `input.jpg`를 기반으로 한,  
    - 아주 느린 줌/슬라이드  
    - 가장자리 어두운 비네팅  
    - 부드러운 글로우·필름 그레인  
    - 마지막 0.5초는 첫 장면으로 부드럽게 페이드되는 루프 연출  
  - **0.5초 동안은 무음** → 그다음 **잔잔한 피아노 BGM**(볼륨 15~20% 수준)이 영상과 함께 재생됩니다.  
  - BGM 다운로드에 실패했으면 **영상만 재생**되고 소리는 없습니다.

- **6초가 지나면**:  
  현재 코드에서는 **한 번만 재생**하고 끝납니다.  
  (반복 재생이 아니라, 6초 재생 후 재생이 멈춘 상태로 전체 화면 창이 남아 있습니다.)

- **종료 방법**:  
  키보드에서 **ESC** 키를 누르면 전체 화면 재생 창이 닫히고, 터미널로 돌아갑니다.

---

### ③ 정리: 모니터에 보이는 것 요약

| 시점 | 모니터에 보이는 것 |
|------|---------------------|
| `python eternal_beam.py` 입력 후 | 터미널만 보임, 로그만 출력 |
| Segmentation ~ 인코딩 진행 중 | 계속 터미널만 보임 |
| "전체 화면 재생을 시작합니다" 직후 | 전체 화면 검은색 → 곧 6초 영상 재생 (BGM 또는 무음) |
| 영상 재생 중 | 1920×1080 전체 화면 영상 + (가능하면) 피아노 BGM |
| ESC 입력 시 | 재생 창 닫힘, 다시 터미널만 보임 |

---

## 2. 터미널에 입력할 설치 명령어

아래는 **Windows 기준**이며, `python`이 Python 3를 가리킨다고 가정합니다.  
Mac이면 `python` 대신 `python3`, `pip` 대신 `pip3`를 사용하면 됩니다.

### (1) 가상환경 사용 권장 (선택)

```bash
python -m venv .venv
.venv\Scripts\activate
```

### (2) 한 번에 설치 (권장)

프로젝트 루트에 `requirements-eternal-beam.txt`가 있는 경우:

```bash
pip install -r requirements-eternal-beam.txt
```

### (3) 패키지를 하나씩 설치하는 경우

```bash
pip install numpy opencv-python Pillow
pip install rembg
pip install torch torchvision
pip install diffusers transformers accelerate
pip install PyQt5
```

- **GPU(CUDA) 사용**하려면:  
  [PyTorch 공식](https://pytorch.org/get-started/locally/)에서 CUDA 버전에 맞는 `torch`/`torchvision` 설치 명령을 확인한 뒤, 위의 `torch`/`torchvision` 설치를 그 명령으로 **한 번만** 대체하면 됩니다.

### (4) ffmpeg (필수 – 영상 인코딩용)

Python 패키지가 아니라 **시스템에 설치**해야 합니다.

- **Windows (Chocolatey)**  
  ```bash
  choco install ffmpeg
  ```
- **Windows (winget)**  
  ```bash
  winget install ffmpeg
  ```
- **Mac**  
  ```bash
  brew install ffmpeg
  ```

설치 후 터미널에서 `ffmpeg -version` 이 동작하면 PATH 설정은 된 것입니다.

---

## 3. 실행 명령어

설치가 끝난 뒤, **프로젝트 루트**에서:

```bash
# input.jpg 가 프로젝트 루트에 있어야 함
python eternal_beam.py
```

- **입력/출력 파일을 지정**하려면:  
  `python eternal_beam.py --input 사진.jpg --output 결과.mp4`  
- **Inpainting 없이 빠르게 테스트**:  
  `python eternal_beam.py --skip-inpainting`  
- **영상만 만들고 재생 창은 띄우지 않기**:  
  `python eternal_beam.py --no-play`

이렇게 실행하면 위 “모니터 시뮬레이션” 순서대로 동작합니다.
