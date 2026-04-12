import torch
import numpy as np
from PIL import Image
from transformers import AutoImageProcessor, ZoeDepthForDepthEstimation

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_ID = "Intel/zoedepth-nyu"

print(f"Loading ZoeDepth model ({MODEL_ID}) on {DEVICE}...")
processor = AutoImageProcessor.from_pretrained(MODEL_ID)
model = ZoeDepthForDepthEstimation.from_pretrained(MODEL_ID).to(DEVICE)
model.eval()


def load_image(path: str) -> Image.Image:
    return Image.open(path).convert("RGB")


def estimate_depth(image: Image.Image) -> np.ndarray:
    """
    입력: PIL Image
    출력: (H, W) float32 depth 맵
    """
    inputs = processor(images=image, return_tensors="pt")
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        predicted_depth = outputs.predicted_depth  # (1, 1, H, W)

    depth = predicted_depth.squeeze().cpu().numpy()
    depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
    depth = np.clip(depth, 0, np.percentile(depth, 99))
    return depth