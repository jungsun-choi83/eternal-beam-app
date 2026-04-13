"""
Copy theme background MP4s from EternalBeam/Assets/background (Korean subfolders)
into backend/themes/ (English slugs for FFmpeg API) and EternalBeam/Assets/Backgrounds/ (Unity).

Run from repo root: python scripts/sync_theme_backgrounds.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "EternalBeam" / "Assets" / "background"
BACKEND_THEMES = ROOT / "backend" / "themes"
UNITY_BG = ROOT / "EternalBeam" / "Assets" / "Backgrounds"

# Korean folder name -> single mp4 inside (glob)
# App theme slugs (see src/components/memorial/preview-screen.tsx THEME_PREVIEW_IDS)
THEME_SLUGS: dict[str, str] = {
    "눈숲속": "celestial",
    "눈오솔길": "starlight",
    "단풍숲": "golden_meadow",
    "바다": "ocean_deep",
    "벚꽃숲": "sunset",
    "크리스마스": "aurora",
}

# Unity-friendly English filenames (all sources for testing)
UNITY_NAMES: dict[str, str] = {
    "눈숲속": "snow_forest.mp4",
    "눈오솔길": "winter_forest_path.mp4",
    "단풍숲": "autumn_maple_lane.mp4",
    "바다": "ocean_beach.mp4",
    "벚꽃숲": "cherry_blossom_tunnel.mp4",
    "크리스마스": "christmas_festive.mp4",
    "오로라": "aurora_northern_lights.mp4",
}


def _find_mp4(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    mp4s = sorted(folder.glob("*.mp4"))
    if not mp4s:
        return None
    return mp4s[0]


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"Source not found: {SRC}")

    BACKEND_THEMES.mkdir(parents=True, exist_ok=True)
    UNITY_BG.mkdir(parents=True, exist_ok=True)

    # Remove old theme mp4s in backend/themes (keep README if any)
    for p in BACKEND_THEMES.glob("*.mp4"):
        p.unlink()

    for old in UNITY_BG.glob("*.mp4"):
        old.unlink()

    copied_backend = 0
    copied_unity = 0

    for korean, slug in THEME_SLUGS.items():
        src_file = _find_mp4(SRC / korean)
        if not src_file:
            print(f"[skip] No MP4 in folder: {korean} -> {slug}")
            continue
        dst = BACKEND_THEMES / f"{slug}.mp4"
        shutil.copy2(src_file, dst)
        copied_backend += 1
        print(f"[backend] {src_file.name} -> {dst.relative_to(ROOT)}")

    for korean, fname in UNITY_NAMES.items():
        src_file = _find_mp4(SRC / korean)
        if not src_file:
            print(f"[skip] Unity: empty folder {korean}")
            continue
        dst = UNITY_BG / fname
        shutil.copy2(src_file, dst)
        copied_unity += 1
        print(f"[unity] {src_file.name} -> {dst.relative_to(ROOT)}")

    print(f"Done. backend/themes: {copied_backend} file(s), Unity Backgrounds: {copied_unity} file(s).")
    aurora_dir = SRC / "오로라"
    if aurora_dir.is_dir() and not list(aurora_dir.glob("*.mp4")):
        print(
            "Note: folder '오로라' has no MP4. Backend 'aurora' uses the Christmas clip. Add aurora.mp4 under 오로라/ and re-run."
        )


if __name__ == "__main__":
    main()
