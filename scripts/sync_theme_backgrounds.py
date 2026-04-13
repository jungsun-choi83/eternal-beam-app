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
SRC_IMAGES = ROOT / "EternalBeam" / "Assets" / "background image"
BACKEND_THEMES = ROOT / "backend" / "themes"
UNITY_BG = ROOT / "EternalBeam" / "Assets" / "Backgrounds"
PUBLIC_THUMBS = ROOT / "public" / "theme-thumbs"
UNITY_THUMBS = ROOT / "EternalBeam" / "Assets" / "Backgrounds" / "Thumbnails"

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

# Theme slug -> candidate thumbnail filenames under EternalBeam/Assets/background image/
THEME_THUMB_CANDIDATES: dict[str, list[str]] = {
    "celestial": ["눈숲속.jpg", "눈오솔길.jpg"],
    "golden_meadow": ["단풍길.jpg"],
    "starlight": ["은하수.jpg", "눈오솔길.jpg"],
    "aurora": ["크리스마스난로.jpg", "눈숲속.jpg"],
    "sunset": ["숲길.jpg", "단풍길.jpg"],
    "ocean_deep": ["일몰바다.jpg"],
}


def _find_mp4(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    mp4s = sorted(folder.glob("*.mp4"))
    if not mp4s:
        return None
    return mp4s[0]


def _find_image(candidates: list[str]) -> Path | None:
    if not SRC_IMAGES.is_dir():
        return None
    for name in candidates:
        p = SRC_IMAGES / name
        if p.is_file():
            return p
    return None


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"Source not found: {SRC}")

    BACKEND_THEMES.mkdir(parents=True, exist_ok=True)
    UNITY_BG.mkdir(parents=True, exist_ok=True)
    PUBLIC_THUMBS.mkdir(parents=True, exist_ok=True)
    UNITY_THUMBS.mkdir(parents=True, exist_ok=True)

    # Remove old theme mp4s in backend/themes (keep README if any)
    for p in BACKEND_THEMES.glob("*.mp4"):
        p.unlink()

    for old in UNITY_BG.glob("*.mp4"):
        old.unlink()
    for old in PUBLIC_THUMBS.glob("*.*"):
        old.unlink()
    for old in UNITY_THUMBS.glob("*.*"):
        old.unlink()

    copied_backend = 0
    copied_unity = 0
    copied_thumbs = 0

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

    for slug, candidates in THEME_THUMB_CANDIDATES.items():
        src_img = _find_image(candidates)
        if not src_img:
            print(f"[skip] Thumb missing for {slug}: {candidates}")
            continue
        ext = src_img.suffix.lower() or ".jpg"
        public_dst = PUBLIC_THUMBS / f"{slug}{ext}"
        unity_dst = UNITY_THUMBS / f"{slug}{ext}"
        shutil.copy2(src_img, public_dst)
        shutil.copy2(src_img, unity_dst)
        copied_thumbs += 1
        print(f"[thumb] {src_img.name} -> {public_dst.relative_to(ROOT)}")

    print(
        f"Done. backend/themes: {copied_backend} file(s), "
        f"Unity Backgrounds: {copied_unity} file(s), theme thumbs: {copied_thumbs} file(s)."
    )
    aurora_dir = SRC / "오로라"
    if aurora_dir.is_dir() and not list(aurora_dir.glob("*.mp4")):
        print(
            "Note: folder '오로라' has no MP4. Backend 'aurora' uses the Christmas clip. Add aurora.mp4 under 오로라/ and re-run."
        )


if __name__ == "__main__":
    main()
