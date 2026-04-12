import sys
from pathlib import Path

from video_player import play_simple_video, play_video_with_overlay


ROOT = Path(__file__).resolve().parents[1]

# 프로젝트 루트에 있는 파일 이름에 맞게 경로 구성
BACKGROUND = ROOT / "background1.mp4"
BLOSSOM = ROOT / "blossom.mp4"
GOYA = ROOT / "goya1.mp4"


def main():
  mode = sys.argv[1] if len(sys.argv) > 1 else "background"

  if mode == "background":
    print("배경 영상 재생:", BACKGROUND)
    play_simple_video(str(BACKGROUND))
  elif mode == "composite":
    print("합성 재생 (벚꽃 + 고야):", BLOSSOM, GOYA)
    play_video_with_overlay(str(BLOSSOM), str(GOYA), str(BACKGROUND))
  else:
    print("사용법:")
    print("  python test_videos.py background  # 배경만 재생")
    print("  python test_videos.py composite   # 벚꽃 + 고야 합성 재생")


if __name__ == "__main__":
  main()

