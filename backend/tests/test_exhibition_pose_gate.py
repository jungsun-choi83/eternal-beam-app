"""
exhibition_pose_gate — 로컬 포즈/시점 게이트 (YOLO 크롭 → CLIP 제로샷).

판정 규칙은 순수 함수(decide)로 검증하고, 모델은 detect_fn / score_fn 주입으로 대체한다.
실제 가중치를 쓰는 테스트는 `-m integration` 으로만 돈다.
"""

from __future__ import annotations

import ast
import io
import pathlib
import socket

import numpy as np
import pytest
from PIL import Image

from backend.services import exhibition_pose_gate as gate

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "services" / "exhibition_pose_gate.py"


def _probs(**overrides: float) -> dict[str, float]:
    base = {label: 0.02 for label in gate.ALL_LABELS}
    base.update(overrides)
    return base


def _png(w: int = 640, h: int = 480) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 100, 80)).save(buf, format="PNG")
    return buf.getvalue()


def _detect(crop_size=(300, 240), class_name="dog"):
    calls: list[Image.Image] = []

    def _fn(image: Image.Image):
        calls.append(image)
        return Image.new("RGB", crop_size, (150, 110, 70)), {
            "detected": True, "class_name": class_name, "confidence": 0.9,
            "bbox": [10, 10, 310, 250], "crop_bbox": [0, 0, 320, 260], "crop_wh": list(crop_size),
        }

    _fn.calls = calls
    return _fn


def _score(probs: dict[str, float]):
    calls: list[tuple[Image.Image, str]] = []

    def _fn(crop: Image.Image, subject: str):
        calls.append((crop, subject))
        return probs

    _fn.calls = calls
    return _fn


# ── 판정 규칙: GOOD 라벨이 뚜렷하면 GOOD ───────────────────────────────────────


def test_front_standing_is_good():
    d = gate.decide(_probs(front_standing=0.70))
    assert d["decision"] == gate.DECISION_GOOD
    assert d["reason"] == "good_label_strongest" and d["top_label"] == "front_standing"


def test_slight_three_quarter_standing_is_good():
    d = gate.decide(_probs(three_quarter_standing=0.62, front_standing=0.20))
    assert d["decision"] == gate.DECISION_GOOD and d["top_label"] == "three_quarter_standing"


def test_sitting_is_good():
    d = gate.decide(_probs(sitting=0.66))
    assert d["decision"] == gate.DECISION_GOOD and d["top_label"] == "sitting"


def test_side_standing_is_good():
    d = gate.decide(_probs(side_standing=0.58, lying=0.20))
    assert d["decision"] == gate.DECISION_GOOD and d["top_label"] == "side_standing"


# ── 판정 규칙: BAD 라벨이 뚜렷하면 NEEDS_AI_NORMALIZATION ─────────────────────


def test_top_down_needs_normalization():
    d = gate.decide(_probs(top_down=0.72, front_standing=0.10))
    assert d["decision"] == gate.DECISION_NEEDS_NORMALIZATION
    assert d["reason"] == "bad_label_clearly_strongest" and d["top_label"] == "top_down"


def test_back_facing_needs_normalization():
    d = gate.decide(_probs(back_facing=0.65, side_standing=0.15))
    assert d["decision"] == gate.DECISION_NEEDS_NORMALIZATION and d["top_label"] == "back_facing"


@pytest.mark.parametrize("label", ["lying", "curled"])
def test_lying_or_curled_needs_normalization(label):
    d = gate.decide(_probs(**{label: 0.68, "sitting": 0.12}))
    assert d["decision"] == gate.DECISION_NEEDS_NORMALIZATION and d["top_label"] == label


def test_severely_turned_needs_normalization():
    d = gate.decide(_probs(turned_away=0.60, three_quarter_standing=0.25))
    assert d["decision"] == gate.DECISION_NEEDS_NORMALIZATION and d["top_label"] == "turned_away"


# ── 판정 규칙: 불확실하면 GOOD (과금 없음) ────────────────────────────────────


@pytest.mark.parametrize(
    "probs, reason",
    [
        (_probs(top_down=0.34, front_standing=0.30), "bad_margin_too_small"),  # 근소한 차이
        (_probs(lying=0.55, sitting=0.40), "bad_margin_too_small"),  # 차이 0.15 < 0.20
        (_probs(back_facing=0.38, side_standing=0.10), "bad_margin_too_small"),  # 차이는 커도 BAD 확률 < 0.40
        ({label: 1.0 / len(gate.ALL_LABELS) for label in gate.ALL_LABELS}, "good_label_strongest"),  # 전부 동률
        ({}, "no_scores"),
    ],
)
def test_uncertain_scores_are_good(probs, reason):
    d = gate.decide(probs)
    assert d["decision"] == gate.DECISION_GOOD
    assert d["reason"] == reason


def test_exact_threshold_boundaries():
    # margin == 0.20 and best_bad == 0.40 → 경계 포함 → NEEDS
    d = gate.decide(_probs(top_down=0.40, front_standing=0.20))
    assert d["decision"] == gate.DECISION_NEEDS_NORMALIZATION
    # 한 끗 아래 → GOOD
    d = gate.decide(_probs(top_down=0.399, front_standing=0.20))
    assert d["decision"] == gate.DECISION_GOOD


def test_thresholds_are_env_tunable(monkeypatch):
    probs = _probs(lying=0.55, sitting=0.40)
    assert gate.decide(probs)["decision"] == gate.DECISION_GOOD
    monkeypatch.setenv("EXHIBITION_POSE_GATE_BAD_MARGIN", "0.10")
    assert gate.decide(probs)["decision"] == gate.DECISION_NEEDS_NORMALIZATION


def test_decision_record_is_complete_and_rounded():
    d = gate.decide(_probs(front_standing=0.123456, top_down=0.3))
    assert set(d["scores"]) == set(gate.ALL_LABELS)
    assert d["best_good"] == 0.1235 and d["best_bad"] == 0.3 and d["margin"] == 0.1765
    assert d["bad_margin"] == gate.BAD_MARGIN_DEFAULT and d["bad_min_prob"] == gate.BAD_MIN_PROB_DEFAULT


# ── 프롬프트 ─────────────────────────────────────────────────────────────────


def test_prompts_are_fixed_good_four_bad_five_and_render_subject():
    assert gate.GOOD_LABELS == ("front_standing", "three_quarter_standing", "sitting", "side_standing")
    assert gate.BAD_LABELS == ("top_down", "back_facing", "lying", "curled", "turned_away")
    rendered = gate.prompts_for("dog")
    assert set(rendered) == set(gate.ALL_LABELS)
    for texts in rendered.values():
        assert len(texts) == 3  # 라벨당 3문구 앙상블
        assert all("dog" in text and "{subject}" not in text for text in texts)
    assert gate.prompts_for("")["front_standing"][0] == "a photo of a pet standing up and facing the camera"


def test_prompt_subject_is_dog_cat_or_pet():
    assert gate.subject_for_prompt("dog") == "dog" and gate.subject_for_prompt("Cat") == "cat"
    assert gate.subject_for_prompt("bear") == "pet"  # COCO 가 들개를 bear 로 잡는 경우
    assert gate.subject_for_prompt(None) == "pet" and gate.subject_for_prompt("") == "pet"


# ── assess_pose: YOLO 크롭 → 분류기 순서, 주입, 실패 처리 ───────────────────────


def test_assess_pose_crops_with_yolo_then_classifies_the_crop():
    detect = _detect(class_name="cat")
    score = _score(_probs(top_down=0.8))
    record = gate.assess_pose(_png(), detect_fn=detect, score_fn=score)

    assert len(detect.calls) == 1 and detect.calls[0].size == (640, 480)
    assert len(score.calls) == 1
    crop, subject = score.calls[0]
    assert crop.size == (300, 240) and subject == "cat"  # 원본이 아니라 크롭, YOLO 클래스명이 프롬프트 주어
    assert record["decision"] == gate.DECISION_NEEDS_NORMALIZATION
    assert record["subject_class"] == "cat" and record["prompt_subject"] == "cat"
    # dog/cat 이 아닌 YOLO 클래스는 프롬프트에서 "pet" 이 된다 (subject_class 는 원래 값 유지)
    record2 = gate.assess_pose(_png(), detect_fn=_detect(class_name="bear"), score_fn=score)
    assert record2["subject_class"] == "bear" and record2["prompt_subject"] == "pet"
    assert score.calls[-1][1] == "pet"
    assert record["detection"]["class_name"] == "cat"
    assert record["version"] == gate.GATE_VERSION and record["elapsed_ms"] >= 0


def test_assess_pose_without_detection_is_good_and_never_scores():
    def _none(image):
        return None, {"detected": False}

    score = _score(_probs(top_down=0.9))
    record = gate.assess_pose(_png(), detect_fn=_none, score_fn=score)
    assert record["decision"] == gate.DECISION_GOOD and record["reason"] == "no_subject_detected"
    assert score.calls == []


def test_assess_pose_classifier_failure_is_good_not_an_exception():
    def _boom(crop, subject):
        raise RuntimeError("model unavailable")

    record = gate.assess_pose(_png(), detect_fn=_detect(), score_fn=_boom)
    assert record["decision"] == gate.DECISION_GOOD
    assert record["reason"] == "gate_error:RuntimeError"


def test_assess_pose_undecodable_image_is_good():
    record = gate.assess_pose(b"not an image", detect_fn=_detect(), score_fn=_score(_probs(lying=0.9)))
    assert record["decision"] == gate.DECISION_GOOD and record["reason"].startswith("gate_error:")


def test_mode_off_answers_good_without_touching_models(monkeypatch):
    monkeypatch.setenv("EXHIBITION_POSE_GATE_MODE", "off")
    detect = _detect()
    record = gate.assess_pose(_png(), detect_fn=detect)  # score_fn 없음 → 기본(CLIP) 경로가 될 자리
    assert record["decision"] == gate.DECISION_GOOD and record["reason"] == "gate_off"
    assert detect.calls == []
    gate.preload()  # off 면 아무것도 올리지 않는다
    assert gate._clip_cache == {}


def test_mode_defaults_to_clip_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("EXHIBITION_POSE_GATE_MODE", raising=False)
    assert gate.mode_from_env() == gate.MODE_CLIP
    monkeypatch.setenv("EXHIBITION_POSE_GATE_MODE", "banana")
    assert gate.mode_from_env() == gate.MODE_CLIP


# ── YOLO 크롭: 기존 검출기 재사용, 여백, 축소 ─────────────────────────────────


def test_detect_pet_crop_reuses_vitmatte_detector_and_downscales(monkeypatch):
    from backend.services import vitmatte_service

    seen = {}

    def _fake_detect(image, yolo_model, *, conf):
        seen["size"], seen["model"], seen["conf"] = image.size, yolo_model, conf
        return vitmatte_service.SubjectDetection(bbox=(400, 300, 1400, 1100), class_id=16, class_name="dog", confidence=0.93)

    monkeypatch.setattr(vitmatte_service, "_detect_subject", _fake_detect)
    monkeypatch.setenv("VITMATTE_YOLO_MODEL", "yolov8n.pt")
    monkeypatch.setenv("VITMATTE_YOLO_CONF", "0.3")

    crop, info = gate.detect_pet_crop(Image.new("RGB", (2000, 1500)))
    assert seen == {"size": (2000, 1500), "model": "yolov8n.pt", "conf": 0.3}
    assert info["detected"] and info["class_name"] == "dog" and info["bbox"] == [400, 300, 1400, 1100]
    x1, y1, x2, y2 = info["crop_bbox"]
    assert x1 < 400 and y1 < 300 and x2 > 1400 and y2 > 1100  # 여백 확장
    assert max(crop.size) == gate.CROP_MAX_SIDE  # 작은 입력으로 축소


def test_detect_pet_crop_without_animal_returns_none(monkeypatch):
    from backend.services import vitmatte_service

    monkeypatch.setattr(vitmatte_service, "_detect_subject", lambda image, yolo_model, *, conf: None)
    crop, info = gate.detect_pet_crop(Image.new("RGB", (640, 480)))
    assert crop is None and info["detected"] is False


# ── 외부 호출 없음 ───────────────────────────────────────────────────────────


def test_pose_gate_imports_no_http_client_or_remote_api():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    banned = ("httpx", "requests", "aiohttp", "urllib", "anthropic", "openai", "vlm_identity", "canonical", "modal", "fal")
    bad = [n for n in names if any(n == b or n.startswith(b + ".") or b in n for b in banned)]
    assert bad == [], f"pose gate imports a remote/API module: {bad}"


def test_pose_gate_makes_no_network_calls(monkeypatch):
    def _no_net(*args, **kwargs):
        raise AssertionError("network call from pose gate")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    record = gate.assess_pose(_png(), detect_fn=_detect(), score_fn=_score(_probs(back_facing=0.7)))
    assert record["decision"] == gate.DECISION_NEEDS_NORMALIZATION


# ── 실제 가중치 (기본 실행 제외) ──────────────────────────────────────────────


@pytest.mark.integration
def test_real_clip_gate_runs_locally_and_caches(monkeypatch):
    monkeypatch.setenv("EXHIBITION_POSE_GATE_MODE", "clip")
    gate.preload()

    def _no_net(*args, **kwargs):
        raise AssertionError("network call from pose gate after preload")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    photo = pathlib.Path(__file__).resolve().parents[2] / "public" / "home" / "hero-golden-retriever.jpg"
    buf = io.BytesIO()
    Image.open(photo).convert("RGB").save(buf, format="PNG")
    first = gate.assess_pose(buf.getvalue())
    second = gate.assess_pose(buf.getvalue())
    assert first["decision"] in (gate.DECISION_GOOD, gate.DECISION_NEEDS_NORMALIZATION)
    assert first["scores"] == second["scores"]
    assert len(gate._clip_cache) == 1  # 두 번째 호출이 모델을 다시 올리지 않았다
