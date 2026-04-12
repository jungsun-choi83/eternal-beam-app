import argparse
from pathlib import Path
import numpy as np
from PIL import Image
from zoe_depth_pipeline import load_image, estimate_depth


def save_depth_as_image(depth: np.ndarray, out_path: str):
    """
    깊이맵 (H, W) -> 0~255 범위의 흑백 PNG로 저장
    """
    # 0~1로 정규화
    d_min = float(depth.min())
    d_max = float(depth.max()) if float(depth.max()) > d_min else d_min + 1.0
    depth_norm = (depth - d_min) / (d_max - d_min + 1e-8)
    depth_img = (depth_norm * 255.0).astype(np.uint8)

    img = Image.fromarray(depth_img, mode="L")
    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="입력 이미지 경로")
    parser.add_argument("--output", required=True, help="출력 이미지 경로 (PNG)")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rgb = load_image(str(in_path))
    depth = estimate_depth(rgb)
    save_depth_as_image(depth, str(out_path))


if __name__ == "__main__":
    main()