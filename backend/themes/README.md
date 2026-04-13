# Theme background MP4s

FFmpeg preview/composition loads `backend/themes/{slug}.mp4`. Slugs match `THEME_PREVIEW_IDS` in `src/components/memorial/preview-screen.tsx`.

| File | Source (Korean folder under `EternalBeam/Assets/background/`) |
|------|----------------------------------------------------------------|
| `celestial.mp4` | 눈숲속 |
| `golden_meadow.mp4` | 단풍숲 |
| `starlight.mp4` | 눈오솔길 |
| `aurora.mp4` | 크리스마스 (오로라 폴더가 비어 있을 때 대체) |
| `sunset.mp4` | 벚꽃숲 |
| `ocean_deep.mp4` | 바다 |

Regenerate from source folders:

```bash
python scripts/sync_theme_backgrounds.py
```

Optional: set `THEMES_VIDEO_DIR` to another directory (see `backend/README.md`).
