"""
전시 포즈/시점 적합성 게이트 — **로컬 경량**, 외부 API 없음, 키포인트 모델 없음.

  원본 → YOLO 동물 bbox (vitmatte_service 와 같은 모델·캐시) → 크롭 축소
       → CLIP 제로샷 (고정 프롬프트 9개: GOOD 4 / BAD 5) → 판정

  GOOD_FOR_DEFORMATION    원본 그대로 기존 EXHIBITION_PREP_RUN
  NEEDS_AI_NORMALIZATION  exhibition_pose_normalizer 로 고정 포즈 재생성 후 EXHIBITION_PREP_RUN

── 판정 규칙 ────────────────────────────────────────────────────────────────
softmax 확률에서 best_good = GOOD 프롬프트 중 최대, best_bad = BAD 프롬프트 중 최대.
  NEEDS_AI_NORMALIZATION  ⇔ best_bad ≥ BAD_MIN_PROB  그리고  best_bad − best_good ≥ BAD_MARGIN
  그 외 전부(GOOD 우세, 근소한 차이, 점수 없음, 검출 실패, 모델 오류, mode=off) → GOOD
불확실하면 **생성(과금)을 트리거하지 않는다** — 원본으로 진행하고 사유를 기록한다.

── 캐시 ────────────────────────────────────────────────────────────────────
CLIP 모델/프로세서와 프롬프트 텍스트 임베딩(피사체 클래스별)은 프로세스 캐시다 —
워커가 한 번 올리고 이미지마다 다시 읽지 않는다 (preload() 로 기동 시 미리 올린다).
YOLO 는 yolo_input.load_yolo 캐시를 그대로 쓴다.

환경변수:
  EXHIBITION_POSE_GATE_MODE          clip (기본) | off (항상 GOOD, 모델 안 올림)
  EXHIBITION_POSE_GATE_MODEL         기본 "openai/clip-vit-base-patch32" (transformers 로컬 로드)
  EXHIBITION_POSE_GATE_BAD_MARGIN    기본 0.20
  EXHIBITION_POSE_GATE_BAD_MIN_PROB  기본 0.40
  VITMATTE_YOLO_MODEL / VITMATTE_YOLO_CONF  누끼와 같은 검출 설정을 공유한다
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

from PIL import Image

logger = logging.getLogger(__name__)

GATE_VERSION = "exhibition-pose-gate-v1"

DECISION_GOOD = "GOOD_FOR_DEFORMATION"
DECISION_NEEDS_NORMALIZATION = "NEEDS_AI_NORMALIZATION"

MODE_CLIP = "clip"
MODE_OFF = "off"

DEFAULT_MODEL = "openai/clip-vit-base-patch32"
#: YOLO bbox 를 이만큼 넓혀 크롭한다 (발끝/귀 잘림 방지).
CROP_PAD_FRAC = 0.10
#: CLIP 에 넣기 전 크롭 긴 변 상한 — 프로세서가 224 로 다시 줄이므로 품질 손실 없음.
CROP_MAX_SIDE = 320
BAD_MARGIN_DEFAULT = 0.20
BAD_MIN_PROB_DEFAULT = 0.40

#: 고정 프롬프트 — 라벨당 3개 문구의 텍스트 임베딩을 평균한다(CLIP 제로샷 앙상블).
#: 문구 구조를 GOOD/BAD 대칭으로 맞춰 "full body visible" 같은 범용 구절이 한쪽을 끌어당기지
#: 않게 한다. {subject} 는 subject_for_prompt() 결과(dog / cat / pet).
GOOD_PROMPTS: dict[str, tuple[str, ...]] = {
    "front_standing": (
        "a photo of a {subject} standing up and facing the camera",
        "a {subject} standing on its four legs, looking straight at the camera",
        "front view of a standing {subject}",
    ),
    "three_quarter_standing": (
        "a photo of a {subject} standing up, turned slightly to the side",
        "a {subject} standing on its four legs at a three-quarter angle",
        "three-quarter view of a standing {subject}",
    ),
    "sitting": (
        "a photo of a {subject} sitting up and facing the camera",
        "a {subject} sitting on its haunches, looking at the camera",
        "front view of a sitting {subject}",
    ),
    "side_standing": (
        "a photo of a {subject} standing up, seen from the side",
        "a {subject} standing on its four legs in profile",
        "side view of a standing {subject}",
    ),
}
BAD_PROMPTS: dict[str, tuple[str, ...]] = {
    "top_down": (
        "a photo of a {subject} taken from directly above",
        "a {subject} seen from above, looking down on its back",
        "bird's-eye view of a {subject}",
    ),
    "back_facing": (
        "a photo of a {subject} facing away from the camera",
        "a {subject} seen from behind, showing its back and tail",
        "rear view of a {subject}",
    ),
    "lying": (
        "a photo of a {subject} lying down on the floor",
        "a {subject} lying on its side",
        "a {subject} lying down and resting",
    ),
    "curled": (
        "a photo of a {subject} curled up asleep",
        "a {subject} curled up in a ball",
        "a sleeping {subject} curled up with its head tucked in",
    ),
    "turned_away": (
        "a photo of a {subject} turned away from the camera, head turned to the side",
        "a {subject} with its body facing away and its head looking sideways",
        "a {subject} looking over its shoulder away from the camera",
    ),
}
GOOD_LABELS = tuple(GOOD_PROMPTS)
BAD_LABELS = tuple(BAD_PROMPTS)
ALL_LABELS = GOOD_LABELS + BAD_LABELS
#: YOLO 클래스명 중 프롬프트에 그대로 쓰는 것. 그 외(COCO 가 들개를 bear 로 잡는 식)는 "pet".
PROMPT_SUBJECTS = ("dog", "cat")

DetectFn = Callable[[Image.Image], tuple[Optional[Image.Image], dict[str, Any]]]
ScoreFn = Callable[[Image.Image, str], dict[str, float]]


def mode_from_env() -> str:
    mode = (os.getenv("EXHIBITION_POSE_GATE_MODE") or MODE_CLIP).strip().lower()
    return mode if mode in (MODE_CLIP, MODE_OFF) else MODE_CLIP


def model_name() -> str:
    return (os.getenv("EXHIBITION_POSE_GATE_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def bad_margin() -> float:
    return _float_env("EXHIBITION_POSE_GATE_BAD_MARGIN", BAD_MARGIN_DEFAULT)


def bad_min_prob() -> float:
    return _float_env("EXHIBITION_POSE_GATE_BAD_MIN_PROB", BAD_MIN_PROB_DEFAULT)


def subject_for_prompt(class_name: Optional[str]) -> str:
    s = (class_name or "").strip().lower()
    return s if s in PROMPT_SUBJECTS else "pet"


def prompts_for(subject: str) -> dict[str, list[str]]:
    s = subject_for_prompt(subject)
    return {label: [t.format(subject=s) for t in texts] for label, texts in {**GOOD_PROMPTS, **BAD_PROMPTS}.items()}


# ── 판정 (순수 함수) ───────────────────────────────────────────────────────────


def decide(scores: dict[str, float]) -> dict[str, Any]:
    """라벨별 확률 → 판정. 결측 라벨은 0 으로 본다. 불확실하면 GOOD."""
    probs = {label: float(scores.get(label, 0.0) or 0.0) for label in ALL_LABELS}
    best_good_label = max(GOOD_LABELS, key=lambda l: probs[l])
    best_bad_label = max(BAD_LABELS, key=lambda l: probs[l])
    best_good, best_bad = probs[best_good_label], probs[best_bad_label]
    margin = best_bad - best_good
    if not any(probs.values()):
        decision, reason = DECISION_GOOD, "no_scores"
    elif best_bad >= bad_min_prob() and margin >= bad_margin():
        decision, reason = DECISION_NEEDS_NORMALIZATION, "bad_label_clearly_strongest"
    elif margin > 0:
        decision, reason = DECISION_GOOD, "bad_margin_too_small"
    else:
        decision, reason = DECISION_GOOD, "good_label_strongest"
    return {
        "decision": decision,
        "reason": reason,
        "top_label": best_bad_label if margin > 0 else best_good_label,
        "best_good_label": best_good_label,
        "best_good": round(best_good, 4),
        "best_bad_label": best_bad_label,
        "best_bad": round(best_bad, 4),
        "margin": round(margin, 4),
        "bad_margin": bad_margin(),
        "bad_min_prob": bad_min_prob(),
        "scores": {label: round(p, 4) for label, p in probs.items()},
    }


# ── YOLO 크롭 (기존 검출 재사용) ─────────────────────────────────────────────


def detect_pet_crop(image: Image.Image) -> tuple[Optional[Image.Image], dict[str, Any]]:
    """vitmatte_service 의 동물 검출(같은 YOLO 모델·conf·캐시)로 가장 큰 동물 bbox 를 크롭한다."""
    from .vitmatte_service import _detect_subject, _expand_bbox

    yolo_model = os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt")
    conf = float(os.getenv("VITMATTE_YOLO_CONF", "0.25"))
    det = _detect_subject(image, yolo_model, conf=conf)
    if det is None:
        return None, {"detected": False, "yolo_model": yolo_model}
    w, h = image.size
    crop_box = _expand_bbox(det.bbox, w, h, CROP_PAD_FRAC)
    crop = image.crop(crop_box)
    cw, ch = crop.size
    scale = min(1.0, CROP_MAX_SIDE / float(max(cw, ch)))
    if scale < 1.0:
        crop = crop.resize((max(1, round(cw * scale)), max(1, round(ch * scale))), Image.LANCZOS)
    return crop, {
        "detected": True,
        "yolo_model": yolo_model,
        "class_name": det.class_name,
        "confidence": det.confidence,
        "bbox": list(det.bbox),
        "crop_bbox": list(crop_box),
        "crop_wh": list(crop.size),
    }


# ── CLIP 제로샷 (프로세스 캐시) ───────────────────────────────────────────────

_lock = threading.Lock()
_clip_cache: dict[str, tuple[Any, Any]] = {}
_text_cache: dict[tuple[str, str], Any] = {}


def _load_clip(name: str) -> tuple[Any, Any]:
    if name in _clip_cache:
        return _clip_cache[name]
    with _lock:
        if name in _clip_cache:
            return _clip_cache[name]
        import torch
        from transformers import CLIPModel, CLIPProcessor

        t0 = time.perf_counter()
        model = CLIPModel.from_pretrained(name)
        model.eval()
        processor = CLIPProcessor.from_pretrained(name)
        torch.set_grad_enabled(False)
        _clip_cache[name] = (model, processor)
        logger.info("exhibition pose gate: CLIP loaded %s in %.1fs", name, time.perf_counter() - t0)
        return _clip_cache[name]


def _embedding(out: Any) -> Any:
    """get_*_features 반환 정규화 — transformers 5.x 는 ModelOutput(pooler_output = 투영 임베딩),
    4.x 는 텐서를 돌려준다."""
    pooled = getattr(out, "pooler_output", None)
    if pooled is not None:
        return pooled
    if isinstance(out, (tuple, list)):
        return out[0]
    return out


def _text_features(name: str, subject: str) -> Any:
    key = (name, subject)
    if key in _text_cache:
        return _text_cache[key]
    import torch

    model, processor = _load_clip(name)
    rendered = prompts_for(subject)
    per_label = []
    with torch.inference_mode():
        for label in ALL_LABELS:
            tokens = processor(text=rendered[label], return_tensors="pt", padding=True)
            feats = _embedding(model.get_text_features(**tokens))
            feats = feats / feats.norm(dim=-1, keepdim=True)
            mean = feats.mean(dim=0)  # 문구 앙상블 — 정규화 임베딩 평균 후 재정규화
            per_label.append(mean / mean.norm())
        stacked = torch.stack(per_label)
    _text_cache[key] = stacked
    return stacked


def clip_scores(crop: Image.Image, subject: str) -> dict[str, float]:
    """크롭 1장 → 라벨별 softmax 확률 (CLIP logit_scale 적용)."""
    import torch

    name = model_name()
    model, processor = _load_clip(name)
    text = _text_features(name, subject)
    with torch.inference_mode():
        pixel = processor(images=crop.convert("RGB"), return_tensors="pt")
        img = _embedding(model.get_image_features(**pixel))
        img = img / img.norm(dim=-1, keepdim=True)
        logits = model.logit_scale.exp() * img @ text.T
        probs = logits.softmax(dim=-1)[0].tolist()
    return {label: float(p) for label, p in zip(ALL_LABELS, probs)}


def preload() -> None:
    """워커 기동 시 호출 — 첫 방문객이 모델 로드를 기다리지 않게. off 면 아무것도 안 한다."""
    if mode_from_env() != MODE_CLIP:
        return
    from .yolo_input import load_yolo

    load_yolo(os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt"))
    _load_clip(model_name())
    _text_features(model_name(), "pet")


# ── 진입점 ───────────────────────────────────────────────────────────────────


def assess_pose(
    source_png: bytes,
    *,
    detect_fn: Optional[DetectFn] = None,
    score_fn: Optional[ScoreFn] = None,
) -> dict[str, Any]:
    """원본 1장 → 판정 레코드 (manifest.pose_suitability.gate 에 그대로 들어간다).

    어떤 경우에도 예외를 올리지 않는다 — 실패는 GOOD + reason 으로 기록된다.
    detect_fn / score_fn 은 테스트 주입용 (기본: YOLO 크롭 + CLIP).
    """
    t0 = time.perf_counter()
    mode = mode_from_env()
    record: dict[str, Any] = {
        "version": GATE_VERSION,
        "mode": mode,
        "model": model_name() if score_fn is None else "injected",
        "subject_class": "",
        "decision": DECISION_GOOD,
        "reason": "",
    }
    if score_fn is None and mode == MODE_OFF:
        record["reason"] = "gate_off"
        record["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        return record
    try:
        image = Image.open(io.BytesIO(source_png))
        image.load()
        image = image.convert("RGB")
        crop, detection = (detect_fn or detect_pet_crop)(image)
        record["detection"] = detection
        record["subject_class"] = str(detection.get("class_name") or "")
        if crop is None:
            # 누끼 단계가 같은 검출기로 SubjectNotDetected 를 낼 것이다 — 여기서 과금하지 않는다.
            record["reason"] = "no_subject_detected"
        else:
            subject = subject_for_prompt(record["subject_class"])
            scores = (score_fn or clip_scores)(crop, subject)
            record["prompt_subject"] = subject
            record.update(decide(scores))
    except Exception as e:  # noqa: BLE001 — 게이트 실패가 실행을 막거나 과금을 부르지 않는다
        logger.warning("exhibition pose gate failed (%s: %s) — treating as GOOD", type(e).__name__, e)
        record["decision"] = DECISION_GOOD
        record["reason"] = f"gate_error:{type(e).__name__}"
    record["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return record
