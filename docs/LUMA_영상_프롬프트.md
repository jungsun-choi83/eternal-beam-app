# Luma 영상 프롬프트 (강아지만 — 사람·목줄 제외)

누끼는 **rembg** (`/api/cutout`).  
아래는 **Luma I2V** 에 들어가는 문장입니다. 코드: `backend/services/luma_prompts.py`

---

## 공통 규칙 (모든 영상에 자동 포함)

**Subject**

```text
Animate ONLY the single dog from the reference keyframe image.
The dog is alone in the frame.
No humans, no people, no hands, no arms, no fingers, no legs of a person.
No leash, no lead rope, no collar strap, no harness, no chain, no owner walking the dog.
No second animal. Stable camera, photorealistic fur detail, natural lighting.
```

**금지 (Do not show)**

```text
person, human, man, woman, child, hand, hands, arm, arms, finger, fingers,
leash, lead, rope, strap, harness, collar band, chain,
walking, holding, owner, pedestrian, walker, blurry, morphing, extra limbs,
text, watermark, logo.
```

---

## 행동 4종 (배경 선택 × 크레딧 API)

| 행동 | 프롬프트 요지 |
|------|----------------|
| **IDLE** | 앉아 숨쉬기·깜빡임만, 목줄 없음 |
| **TOUCH** | 쓰다듬힘 **반응만**, 손·팔은 화면에 안 나옴 |
| **VOICE** | 소리 듣고 귀·고개만, 말하는 사람 없음 |
| **NFC** | 장소 둘러보기·냄새, 주인·목줄 없음 |

---

## 예시: 눈 숲 + IDLE (전체 한 줄)

```text
Image-to-video from keyframe https://....png.
Environment: a quiet snow-covered pine forest with soft falling snow...
Motion: The dog sits calmly, blinking naturally, gentle chest breathing, minimal motion, looking toward camera. Nobody enters the scene. No leash visible.
Animate ONLY the single dog from the reference keyframe image. ...
Do not show or invent: person, human, ... leash, ... logo.
```

---

## 참고

- Luma API는 **한 줄 prompt** 필드만 사용 → 위 금지 문장은 `Do not show:` 로 같은 문자열에 포함됩니다.
- 원본 사진에 사람·목줄이 크면 영상에도 남을 수 있으므로, **누끼 PNG(강아지만)** 를 키프레임으로 쓰는 것이 가장 좋습니다.
