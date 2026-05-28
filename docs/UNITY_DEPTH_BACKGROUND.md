# Unity 배경 깊이감 (2층 RGB)

`BackgroundDepthStack`은 **알파 채널 없는** MP4 두 개로 깊이를 만듭니다.

| 레이어 | 파일 | Unity | 역할 |
|--------|------|--------|------|
| 후경 | `background_forest.mp4` | `backgroundFarRoot` | 숲·길 베이스 (하이라이트 제거) |
| 전경 | `foreground_light.mp4` | `foregroundLightRoot` | 빛내림·안개 (Additive/Screen) |

## 폴더 구조

```
Assets/Backgrounds/light_rgb/
  snow_forest/
    background_forest.mp4
    foreground_light.mp4
```

인스펙터 `lightRgbRoot` = `.../Assets/Backgrounds/light_rgb`  
`ThemeSessionController` / `ApplyTheme("snow_forest")` 로 재생.

## bgluma.mp4 (Alpha Packed) → Unity용

`bgluma.mp4`가 **상단 RGB / 하단 마스크** 형이면, Unity에는 **상단만** 쓰고 빛·숲으로 나눕니다.

```powershell
cd python
python unity_light_depth_prepare.py `
  -i "C:\Users\choi jungsun\Desktop\EternalBeam_Demo\assets\backgrounds\bgluma.mp4" `
  -o "C:\Users\choi jungsun\Desktop\EternalBeam_Demo\assets\Backgrounds\light_rgb" `
  --theme-id snow_forest `
  --mode light `
  --preset fast
```

### 모드 선택

| `--mode` | 용도 |
|----------|------|
| `light` (기본) | **빛**으로 전·후경 — 안개/빛줄기 전경 + 숲 후경 |
| `focus` | **초점(선명도)** — Luma 보케(앞 선명·뒤 흐림)일 때 앞나무/뒤숲 |

보정: `--luma-threshold 0.68` (빛 더 많이), `--bg-suppress 0.92` (후경에서 빛 더 제거)

## Unity 씬

1. 뒤 Quad → `backgroundFarRoot` + Unlit/Texture  
2. 앞 Quad → `foregroundLightRoot` + Additive 블렌드 머티리얼  
3. Z는 `HologramController` 깊이와 맞춤 (후경 더 뒤, 전경 더 앞)

Packed(vstack) MP4는 **PetHologram 셰이더·강아지 누끼**용이고, 배경 깊이 2층은 **일반 RGB**만 사용합니다.
