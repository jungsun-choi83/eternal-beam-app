"""
YOLO 입력 채널 순서 진단 — 실패하는 사진을 직접 넣어 확인하는 로컬 도구.

ultralytics 는 `np.ndarray` 를 BGR 로 해석한다. 이 저장소는 예전에 RGB 배열을
그대로 넘겨서 R/B 가 뒤바뀐 채 추론했다. 이 스크립트는 같은 이미지를 세 가지
입력 방식으로 넣고 검출 결과를 비교한다.

사용법 (개인 사진은 저장소에 커밋하지 말 것):

    python -m backend.scripts.diagnose_yolo_color path/to/photo.jpg
    python -m backend.scripts.diagnose_yolo_color photo1.jpg photo2.jpg --conf 0.10

읽는 법:
  - `numpy_bgr` 와 `pil_rgb` 가 같고 `numpy_rgb` 만 다르면 → ndarray=BGR 확정.
  - `numpy_rgb` 만 검출에 실패하면 → 그 사진이 예전 코드에서 실패하던 케이스.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.vitmatte_service import _COCO_ANIMAL_CLASS_IDS  # noqa: E402
from backend.services.yolo_input import compare_yolo_color_paths, load_yolo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="검사할 이미지 경로")
    parser.add_argument(
        "--weights",
        default=os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt"),
        help="YOLO 가중치 (기본: yolov8n.pt)",
    )
    parser.add_argument("--conf", type=float, default=0.10, help="신뢰도 임계값")
    parser.add_argument(
        "--all-classes",
        action="store_true",
        help="COCO 동물 클래스 필터를 끄고 전체 클래스로 검출",
    )
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = parser.parse_args()

    yolo = load_yolo(args.weights)
    classes = None if args.all_classes else list(_COCO_ANIMAL_CLASS_IDS)

    results: dict[str, dict] = {}
    for path in args.images:
        if not os.path.isfile(path):
            print(f"[skip] not found: {path}", file=sys.stderr)
            continue
        img = Image.open(path).convert("RGB")
        report = compare_yolo_color_paths(img, yolo, classes=classes, conf=args.conf)
        results[path] = report

        if args.json:
            continue

        print(f"\n=== {path}  ({img.size[0]}x{img.size[1]}) ===")
        for label in ("numpy_rgb", "numpy_bgr", "pil_rgb"):
            entries = report[label]
            tag = " (기존 버그 경로)" if label == "numpy_rgb" else ""
            tag = " (표준 경로)" if label == "pil_rgb" else tag
            if not entries:
                print(f"  {label:10s}{tag:14s} -> 검출 없음")
                continue
            top = entries[0]
            print(
                f"  {label:10s}{tag:14s} -> {top['class_name']} "
                f"conf={top['confidence']:.4f} box={top['bbox']} "
                f"(총 {len(entries)}건)"
            )

        rgb_top = report["numpy_rgb"][0]["confidence"] if report["numpy_rgb"] else None
        pil_top = report["pil_rgb"][0]["confidence"] if report["pil_rgb"] else None
        if rgb_top is None and pil_top is not None:
            print("  ** 기존 코드에서는 검출 실패, 표준 경로에서는 성공 **")
        elif rgb_top is not None and pil_top is not None:
            print(f"  delta(conf) = {pil_top - rgb_top:+.4f}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
