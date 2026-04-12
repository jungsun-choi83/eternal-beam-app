# Eternal Beam - 비디오 파이프라인

## 전략: RGBA 전용

**Unity는 투명 영상(RGBA)을 그대로 출력만 한다. 배경 제거는 Unity 이전 단계에서 완료.**

- 입력: WebM(VP8/VP9+Alpha), ProRes 4444, MP4 yuva420p, PNG 시퀀스
- Blend SrcAlpha OneMinusSrcAlpha
- texture alpha 그대로 사용 (Luma, threshold, cutout 제거)

---

## 1. 셰이더 (PetShader)

- Blend SrcAlpha OneMinusSrcAlpha
- _MainTex RGBA 그대로 출력 (Alpha 연산 없음)

---

## 3. RGBA 전처리 (선택)

```bash
python scripts/preprocess_video.py input_rgba.mp4 -o ./output
```

**처리 단계**:
- edge despill (배경색 경계 제거)
- 1~2px erosion on alpha boundary
- cyan/blue fringe suppression
- slight denoise
- edge blur ~0.5px

**옵션**:
- `--erosion 2` : 2px erosion
- `--edge-blur 0.5` : 경계 블러 sigma
- `--no-denoise` : 디노이즈 끄기
- `--no-despill` : despill 끄기
- `--no-fringe` : fringe 억제 끄기

---

## 4. Unity 설정

1. **VideoLayer clips**: RGBA 클립만 등록
2. **Material**: PetShader (RGBA 그대로 출력)

---

## 5. 워크플로우

```
Runway 영상
    ↓ video_to_rgba.py (rembg 프레임별)
RGBA PNG 시퀀스 / RGBA MP4
    ↓ (선택) preprocess_video.py (경계 정제)
Unity VideoLayer (clips)
    ↓
PetShader (RGBA 그대로 출력)
    ↓
투명 영상 그대로 출력
```
