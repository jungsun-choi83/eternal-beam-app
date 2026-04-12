# Eternal Beam - 최종 기술 표준

## 1. 데이터 입력

**Input**: ProRes 4444 (RGBA) 영상
- H.264 / RGB+Black 배경 방식 폐기
- Unity Texture: Alpha is Transparency, Premultiply Alpha 활성화

## 2. 셰이더 (PetShader)

- **Blend**: `One OneMinusSrcAlpha` (Premultiplied Alpha)
- **Alpha**: col.a 그대로 사용 (Luma 기반 계산 없음)
- **Sharpen**: 0.08~0.1 (하프미러 뭉개짐 보정)

## 3. Z-Depth (15cm)

| 레이어 | 위치 | Unity 값 |
|--------|------|----------|
| 배경 | 140mm | backgroundZ = 14 |
| 피사체 | 75mm | subjectZ = 7.5 |
| UI | 20mm | - |

## 4. 피사체만 출력 (subject_only)

**15cm 기기용**: 배경 없이 강아지만 검은 배경 위에.

```bash
# API: subject_only=true
POST /api/compose-video
  cutout_file: PNG
  subject_only: true   # 배경 없음, Fringe/Halo 방지
```

- Glow 비활성화 → Halo 원천 차단
- 검은 배경 = DLP 투명
- Cutout 모델: `isnet-general-use` (강아지·털·검은 코·눈 보존)

## 5. ProRes 4444 변환

```bash
# RGB + Alpha 분리 → ProRes 4444
python scripts/to_prores4444.py rgb.mp4 alpha.mp4 -o output.mov

# 단일 RGBA → ProRes 4444
python scripts/to_prores4444.py input.mp4 -o output.mov
```

## 6. QA 체크리스트

- [ ] 검은 코/눈이 솔리드한 검정으로 보이는가?
- [ ] 꼬리/다리 외곽 파란/흰 Halo 제거되었는가?
- [ ] 터치 시 경계면 픽셀 깨짐 없는가?
- [ ] 배경 없이 강아지만 보이는가?
