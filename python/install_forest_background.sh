#!/usr/bin/env bash
# Pi — 숲 배경 mp4 설치 확인 (backgrounds/fresh_forest.mp4)
set -euo pipefail
cd "$(dirname "$0")"

BG_DIR="./backgrounds"
TARGET="$BG_DIR/fresh_forest.mp4"

mkdir -p "$BG_DIR"

if [[ -f "$TARGET" ]]; then
  echo "[OK] 이미 있음: $TARGET ($(du -h "$TARGET" | cut -f1))"
  ls -la "$BG_DIR/"
  exit 0
fi

# PC에서 복사한 fresh_forest.mp4 만 사용 (bg02 사용 금지)
for candidate in \
  "./forest.mp4" \
  "../public/demo/forest.mp4" \
  "./backgrounds/forest.mp4"; do
  if [[ -f "$candidate" ]]; then
    cp -f "$candidate" "$TARGET"
    echo "[OK] 복사함: $candidate → $TARGET"
    ls -la "$BG_DIR/"
    exit 0
  fi
done

echo "[!] $TARGET 없음"
echo ""
echo "VS Code에서 PC → Pi 로 아래 파일을 드래그해서 넣으세요:"
echo "  PC: eternal-beam-app/python/backgrounds/fresh_forest.mp4"
echo "  Pi: ~/eternal-beam/python/backgrounds/fresh_forest.mp4"
echo ""
echo "넣은 뒤 테스트:"
echo "  export DISPLAY=:0"
echo "  python3 pi_display_bg.py --videos-dir ./backgrounds --test-forest"
exit 1
