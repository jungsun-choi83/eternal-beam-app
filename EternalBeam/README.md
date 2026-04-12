# EternalBeam Unity 프로젝트

15cm Pepper's Ghost 기기용 — 피사체만 표시 (배경 없음)

## 폴더 구조

```
EternalBeam/
├── Assets/
│   ├── Shaders/
│   │   ├── PetShader.shader    ← Blend One OneMinusSrcAlpha, Premult Alpha
│   │   └── PetHologram.shader  ← fix_dog_video용 Pre-multiplied 전용
│   ├── Scripts/
│   │   ├── VideoLayer.cs       ← RGBA / RGB+Alpha 비디오 렌더링
│   │   └── HologramController.cs ← Z-Depth 140/75/20mm, subjectOnly
│   └── Videos/                 ← subject_only.mp4 넣기
```

## 사용법 (기존 프로젝트에 복사)

1. **C:\Users\choi jungsun\EternalBeam** 프로젝트를 Unity Hub에서 열기

2. 이 폴더의 `Assets/` 내용을 해당 프로젝트 `Assets/`에 복사:
   - `Shaders/PetShader.shader`
   - `Scripts/VideoLayer.cs`, `HologramController.cs`

3. **PetMat 머티리얼 생성**
   - Create → Material
   - Shader: EternalBeam/PetShader
   - Alpha is Transparency ✓
   - Premultiply Alpha ✓ (Import Settings에서)
   - Sharpen: 0.09

4. **SubjectQuad 설정**
   - VideoPlayer: clips에 subject_only.mp4 등록
   - Material: PetMat
   - HologramController: subjectOnly = true, backgroundLayer 비활성화

5. **비디오 소스**
   - eternal-beam-app 백엔드: `python -m uvicorn backend.main:app --port 8000`
   - API: `POST /api/compose-video` + `subject_only=true` → MP4
   - MP4를 Assets/Videos/에 넣고 VideoPlayer clips에 등록
   - (선택) ProRes 4444 RGBA: `python scripts/to_prores4444.py subject.mp4 -o subject.mov`

## 강아지 비디오 할로 제거 테스트 (fix_dog_video)

rembg 처리된 MP4에서 벚꽃 할로가 스며든 경우:

1. **프로젝트 루트에서** `fix_dog_video.py` 실행:
   ```bash
   # idle1_mp4.mp4를 같은 폴더에 두고
   python fix_dog_video.py
   ```
   → `dog_rgb.mp4`, `dog_alpha.mp4` 생성

2. **생성된 파일을** `Assets/Videos/`로 복사

3. **Unity에서** 메뉴 `EternalBeam > Setup Dog Video Test` 실행
   - SubjectQuad + PetHologram 머티리얼 자동 생성
   - dog_rgb/dog_alpha가 Videos에 있으면 자동 할당

4. **플레이 모드**로 확인

### 수동 설정 (메뉴 미사용 시)
- Shader: **Custom/PetHologram** (Pre-multiplied 전용)
- Clips Element 0: dog_rgb.mp4
- Alpha Clips Element 0: dog_alpha.mp4

## QA 체크리스트

- [ ] 검은 코/눈이 솔리드한 검정으로 보이는가?
- [ ] 꼬리/다리 외곽 파란/흰 Halo 제거되었는가?
- [ ] 배경 없이 강아지만 보이는가?
