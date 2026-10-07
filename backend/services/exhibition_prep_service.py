"""
EXHIBITION_PREP_RUN — 전시용 리깅 입력 패키지 생성 (일반 펫 생성과 분리).

  QUEUED → RUNNING[CUTOUT → MAPS → PACKAGE] → READY | NEEDS_REVIEW | FAILED
  READY(= PACKAGE_READY) → handoff_status: HANDOFF_SENDING → HANDOFF_CONFIRMED | HANDOFF_FAILED

  CUTOUT  : vitmatte_service.matte_foreground_with_meta() 를 **파이썬으로 직접**
            호출한다. /api/matting/cutout 을 거치지 않는 이유: 그 라우터는
            저장 시 user_assets 행과 pet_references 파생 행(cutout_vitmatte)을
            남기고, 그 행은 일반 파이프라인의 인테이크/정본 선택에 들어간다.
  MAPS    : exhibition_breathing_maps (OpenCV/NumPy, 추가 모델 추론 없음)
  PACKAGE : exhibition/{run_id}/ 에 subject_rgba.png, breathing_weight.png,
            locked_mask.png, preview_overlay.png, manifest.json (manifest 는 마지막)

우리 책임은 패키지 + 핸드오프(서명 URL 전달, ACK 확인)까지다. 2.5D 리깅·
호흡 애니메이션·디바이스 표시는 외부 시스템 몫이며 여기서 하지 않는다.
핸드오프는 저장된 패키지를 다시 보낼 뿐 CUTOUT/MAPS 를 다시 돌리지 않는다. 정본·키프레임·모션·QA·발행·크레딧·
pet_generation_runs 는 임포트조차 하지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, ImageOps

from . import exhibition_breathing_maps as maps_mod
from .exhibition_handoff_client import (
    ExhibitionHandoffClient,
    HandoffError,
    build_payload,
    redacted_payload,
)
from .exhibition_prep_store import (
    DISPLAY_WAITING,
    HANDOFF_CONFIRMED,
    HANDOFF_FAILED,
    HANDOFF_SENDING,
    MAX_ATTEMPTS,
    STAGE_CUTOUT,
    STAGE_MAPS,
    STAGE_PACKAGE,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_QUEUED,
    STATUS_READY,
    ArtifactStore,
    RunStore,
    run_object_path,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "EXHIBITION_RIG_INPUT_V1"
SOURCE_NAME = "source.png"
PACKAGE_FILES = {
    "subject_rgba": "subject_rgba.png",
    "breathing_weight": "breathing_weight.png",
    "locked_mask": "locked_mask.png",
    "preview_overlay": "preview_overlay.png",
    "manifest": "manifest.json",
}

#: 전시 실행 전용 입력 상한 (긴 변). 일반 누끼 경로는 리사이즈하지 않는다 —
#: 여기서만 줄여 12MP 사진의 ViTMatte 메모리 폭주를 막는다.
def max_input_side() -> int:
    try:
        return max(512, int(os.getenv("EXHIBITION_MAX_INPUT_SIDE", "2048")))
    except ValueError:
        return 2048


MAX_UPLOAD_BYTES = 25 * 1024 * 1024

MatteFn = Callable[[bytes], tuple[bytes, dict]]


class ExhibitionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _default_matte(image_bytes: bytes) -> tuple[bytes, dict]:
    # 지연 임포트: torch/transformers 는 실제 처리 시점(워커)에서만 로드한다.
    from .vitmatte_service import matte_foreground_with_meta

    return matte_foreground_with_meta(image_bytes)


# ── 입력 정규화 · 실행 생성 ───────────────────────────────────────────────────


def normalize_source(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    """EXIF 회전 반영 → RGB → 긴 변 상한 → PNG. (png_bytes, source_info)"""
    if not raw:
        raise ExhibitionInputError("EMPTY_FILE", "Empty file.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ExhibitionInputError("FILE_TOO_LARGE", "File exceeds 25 MB.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise ExhibitionInputError("UNREADABLE_IMAGE", "Could not decode image.") from e
    img = ImageOps.exif_transpose(img).convert("RGB")
    src_w, src_h = img.size
    limit = max_input_side()
    scale = min(1.0, limit / float(max(src_w, src_h)))
    if scale < 1.0:
        img = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_wh": [src_w, src_h],
        "processing_wh": list(img.size),
        "processing_scale": round(scale, 6),
    }


def create_run(
    raw: bytes,
    *,
    created_by: str,
    store: RunStore,
    artifacts: ArtifactStore,
    exhibition_id: Optional[str] = None,
    head_hint_xy: Optional[tuple[float, float]] = None,
    allocate_queue_number: Optional[Callable[[], int]] = None,
    pet_name: Optional[str] = None,
) -> dict[str, Any]:
    """원본을 exhibition/{run_id}/source.png 로 올린 **뒤** QUEUED 행을 만든다
    (행이 먼저 생기면 워커가 업로드 전에 집어갈 수 있다).

    head_hint_xy 는 원본(EXIF 회전 반영) 픽셀 좌표.
    allocate_queue_number 가 있으면 스태프 접수 건 — 사진 검증을 통과한 **뒤** 번호를 받아
    display_status=WAITING 으로 전시 대기열에 든다 (exhibition_queue_service.submit).
    """
    png, info = normalize_source(raw)
    queue_number = allocate_queue_number() if allocate_queue_number is not None else None
    run_id = str(uuid.uuid4())
    source_path = run_object_path(run_id, SOURCE_NAME)
    artifacts.put(source_path, png, "image/png")
    row = {
        "id": run_id,
        "exhibition_id": (exhibition_id or "").strip() or None,
        "created_by": created_by,
        "status": STATUS_QUEUED,
        "stage": None,
        "source_path": source_path,
        "source_info": info,
        "head_hint": (
            {"xy_in_source": [float(head_hint_xy[0]), float(head_hint_xy[1])]} if head_hint_xy else None
        ),
        "review_reasons": [],
    }
    if queue_number is not None:
        row.update(queue_number=int(queue_number), pet_name=pet_name, display_status=DISPLAY_WAITING)
    return store.insert(row)


# ── 처리 ─────────────────────────────────────────────────────────────────────


def _png(arr: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode=mode).save(buf, format="PNG")
    return buf.getvalue()


def _cutout_diagnostics(meta: dict) -> dict[str, Any]:
    keys = (
        "method", "model", "sam2_model", "segmenter", "segmenter_fallback", "fallback_reason",
        "subject_class", "detection_confidence", "mask_area_fraction", "alpha_area_fraction",
        "rectangle_like_mask",
    )
    out = {k: meta.get(k) for k in keys}
    shadow = meta.get("shadow_suppression")
    if isinstance(shadow, dict):
        out["shadow_suppression_applied"] = shadow.get("applied")
    return out


def build_package(
    run_id: str,
    source_png: bytes,
    source_info: dict[str, Any],
    *,
    head_hint_in_source: Optional[tuple[float, float]] = None,
    matte_fn: MatteFn = _default_matte,
    on_stage: Callable[[str], None] = lambda _s: None,
) -> tuple[str, list[str], dict[str, tuple[bytes, str]], dict[str, Any]]:
    """순수 처리부: (status, review_reasons, files{name: (bytes, type)}, manifest).

    CutoutError / ExhibitionMapError 는 그대로 올라간다 (호출자가 FAILED 처리).
    """
    on_stage(STAGE_CUTOUT)
    rgba_png, meta = matte_fn(source_png)
    rgba_full = np.array(Image.open(io.BytesIO(rgba_png)).convert("RGBA"))

    on_stage(STAGE_MAPS)
    alpha_full = rgba_full[:, :, 3]
    edge = maps_mod.detect_frame_edge_touch(alpha_full)
    x1, y1, x2, y2 = maps_mod.subject_crop_rect(alpha_full)
    canvas = maps_mod.clean_straight_rgba(rgba_full[y1:y2, x1:x2].copy())

    scale = float(source_info.get("processing_scale") or 1.0)
    hint_canvas = None
    if head_hint_in_source is not None:
        hint_canvas = (head_hint_in_source[0] * scale - x1, head_hint_in_source[1] * scale - y1)
    result = maps_mod.build_breathing_maps(canvas, head_hint_xy=hint_canvas)

    on_stage(STAGE_PACKAGE)
    segmenter_fallback = bool(meta.get("segmenter_fallback"))
    flags = {
        "touches_frame_edge": bool(edge["touches_frame_edge"]),
        "head_low_confidence": bool(result.head_low_confidence),
        "segmenter_fallback": segmenter_fallback,
        "interior_holes": bool(result.interior_holes),
    }
    review_reasons = [k for k, v in flags.items() if v]
    status = STATUS_NEEDS_REVIEW if review_reasons else STATUS_READY

    inv = 1.0 / scale
    crop_in_source = [round(v * inv, 1) for v in (x1, y1, x2, y2)]
    h, w = canvas.shape[:2]
    head = result.head
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "map_method": maps_mod.MAP_METHOD,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "review_reasons": review_reasons,
        "canvas_wh": [w, h],
        "crop_rect_in_source": crop_in_source,
        "source": {
            "sha256": source_info.get("source_sha256"),
            "source_wh": source_info.get("source_wh"),
            "processing_scale": scale,
            "note": "crop_rect_in_source is x1,y1,x2,y2 (end-exclusive) in EXIF-oriented source pixels; "
                    "canvas pixels map to source pixels via (canvas / processing_scale) + crop origin.",
        },
        "coordinate_space": "canvas pixels, origin top-left, +x right, +y down",
        "anchors": {
            "breathing_center": list(result.breathing_center),
            "breathing_axis": list(result.breathing_axis),
            "ground_y": result.ground_y,
            "head_point": {
                "xy": list(head.xy) if head.xy else None,
                "source": head.source,
                "confidence": head.confidence,
                "tier": head.tier,
                "method": head.method,
            },
        },
        "subject_class": meta.get("subject_class") or "",
        "flags": flags,
        "files": {
            "subject_rgba": {"path": PACKAGE_FILES["subject_rgba"], "format": "PNG RGBA 8-bit",
                             "color_space": "sRGB", "alpha": "straight (not premultiplied)",
                             "rgb_under_zero_alpha": "edge color bleed, otherwise 0 (source background removed)"},
            "breathing_weight": {"path": PACKAGE_FILES["breathing_weight"], "format": "PNG grayscale 16-bit",
                                 "range": "0..65535 => deformation weight 0..1", "premultiplied_by_alpha": True},
            "locked_mask": {"path": PACKAGE_FILES["locked_mask"], "format": "PNG grayscale 8-bit",
                            "semantics": "255 = locked / do not deform (head, ground contact), feathered"},
            "preview_overlay": {"path": PACKAGE_FILES["preview_overlay"], "format": "PNG RGB 8-bit",
                                "purpose": "operator review only"},
        },
        "cutout": _cutout_diagnostics(meta),
        "diagnostics": {**result.diagnostics, "frame_edge_sides": edge["sides"]},
    }

    files = {
        PACKAGE_FILES["subject_rgba"]: (_png(canvas, "RGBA"), "image/png"),
        PACKAGE_FILES["breathing_weight"]: (maps_mod.encode_weight_png16(result.breathing_weight), "image/png"),
        PACKAGE_FILES["locked_mask"]: (maps_mod.encode_mask_png8(result.locked), "image/png"),
        PACKAGE_FILES["preview_overlay"]: (_png(maps_mod.render_preview_overlay(canvas, result), "RGB"), "image/png"),
    }
    return status, review_reasons, files, manifest


def process_run(
    run: dict[str, Any],
    *,
    store: RunStore,
    artifacts: ArtifactStore,
    worker_id: Optional[str] = None,
    matte_fn: MatteFn = _default_matte,
) -> dict[str, Any]:
    """클레임된 실행 1건을 끝까지 처리하고 최종 필드를 반환한다. 예외를 올리지 않는다."""
    from .cutout_errors import CutoutError

    run_id = run["id"]

    def _finish(fields: dict[str, Any]) -> dict[str, Any]:
        if not store.update(run_id, fields, claimed_by=worker_id):
            logger.warning("exhibition run %s: final write fenced out (claimed elsewhere)", run_id)
        return fields

    if int(run.get("attempts") or 0) > MAX_ATTEMPTS:
        return _finish({"status": STATUS_FAILED, "error_code": "WORKER_RECOVERY_EXHAUSTED",
                        "error_message": f"run claimed more than {MAX_ATTEMPTS} times"})

    def _stage(stage: str) -> None:
        store.update(run_id, {"stage": stage}, claimed_by=worker_id)

    try:
        source_png = artifacts.get(run["source_path"])
        hint = (run.get("head_hint") or {}).get("xy_in_source")
        status, reasons, files, manifest = build_package(
            run_id,
            source_png,
            run.get("source_info") or {},
            head_hint_in_source=tuple(hint) if hint else None,
            matte_fn=matte_fn,
            on_stage=_stage,
        )
        outputs: dict[str, str] = {}
        for name, (data, ctype) in files.items():
            path = run_object_path(run_id, name)
            artifacts.put(path, data, ctype)
            outputs[name] = path
        manifest_path = run_object_path(run_id, PACKAGE_FILES["manifest"])
        artifacts.put(manifest_path, json.dumps(manifest, indent=2).encode("utf-8"), "application/json")
        outputs[PACKAGE_FILES["manifest"]] = manifest_path
        return _finish({
            "status": status,
            "review_reasons": reasons,
            "outputs_json": outputs,
            "manifest_json": manifest,
            "error_code": None,
            "error_message": None,
        })
    except CutoutError as e:
        logger.warning("exhibition run %s cutout rejected: %s %s", run_id, e.code, e.message)
        return _finish({"status": STATUS_FAILED, "error_code": e.code, "error_message": e.message[:2000]})
    except maps_mod.ExhibitionMapError as e:
        logger.warning("exhibition run %s maps failed: %s %s", run_id, e.code, e.message)
        return _finish({"status": STATUS_FAILED, "error_code": e.code, "error_message": e.message[:2000]})
    except Exception as e:  # noqa: BLE001 — 워커는 계속 돌아야 한다
        logger.exception("exhibition run %s failed unexpectedly", run_id)
        return _finish({"status": STATUS_FAILED, "error_code": "EXHIBITION_INTERNAL_ERROR",
                        "error_message": f"{type(e).__name__}: {e}"[:2000]})


# ── 핸드오프 ─────────────────────────────────────────────────────────────────


class HandoffStateError(Exception):
    """지금은 핸드오프할 수 없는 상태 (API 가 404/409/503 으로 바꾼다)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sending_is_stale(row: dict[str, Any], stale_sec: float) -> bool:
    started = row.get("handoff_requested_at")
    if not started:
        return True
    try:
        ts = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() > stale_sec


def hand_off_run(
    run_id: str,
    *,
    store: RunStore,
    artifacts: ArtifactStore,
    client: Optional[ExhibitionHandoffClient] = None,
) -> dict[str, Any]:
    """READY 실행의 **저장된** 패키지를 외부 시스템에 보내고 최신 행을 반환한다.

    · 처음 전송과 재시도가 같은 함수다 — 매번 같은 exhibition/{run_id}/ 파일의
      새 서명 URL 만 만든다. status/stage/outputs 는 건드리지 않으므로
      CUTOUT/MAPS 가 다시 돌 길이 없다.
    · HANDOFF_CONFIRMED 면 다시 보내지 않는다 (run_id 멱등).
    · SENDING 진입은 compare-and-set (handoff_attempt_id) — 동시 전송 1건만.
    · 전송 실패는 HANDOFF_FAILED 로 기록하고 예외를 올리지 않는다.
    상태 때문에 보낼 수 없으면 HandoffStateError.
    """
    client = client or ExhibitionHandoffClient()
    cfg = client.config
    if not cfg.enabled:
        raise HandoffStateError("HANDOFF_NOT_CONFIGURED", "Exhibition handoff is off (EXHIBITION_HANDOFF_MODE/URL).")
    row = store.get(run_id)
    if not row:
        raise HandoffStateError("EXHIBITION_RUN_NOT_FOUND", "Run not found.")
    if row.get("status") != STATUS_READY:
        raise HandoffStateError(
            "HANDOFF_PACKAGE_NOT_READY", f"Only READY packages can be handed off (status={row.get('status')})."
        )
    current = row.get("handoff_status")
    if current == HANDOFF_CONFIRMED:
        return row
    if current == HANDOFF_SENDING and not _sending_is_stale(row, cfg.stale_sec):
        raise HandoffStateError("HANDOFF_IN_PROGRESS", "Handoff is already being sent.")

    attempt_id = str(uuid.uuid4())
    claimed = store.update_if(
        run_id,
        {
            "handoff_status": HANDOFF_SENDING,
            "handoff_attempt_id": attempt_id,
            "handoff_attempts": int(row.get("handoff_attempts") or 0) + 1,
            "handoff_requested_at": datetime.now(timezone.utc).isoformat(),
            "handoff_error_code": None,
            "handoff_error_message": None,
        },
        match={"status": STATUS_READY, "handoff_status": current, "handoff_attempt_id": row.get("handoff_attempt_id")},
    )
    if not claimed:
        raise HandoffStateError("HANDOFF_IN_PROGRESS", "Handoff state changed concurrently.")

    def _finish(fields: dict[str, Any]) -> dict[str, Any]:
        mine = {"handoff_status": HANDOFF_SENDING, "handoff_attempt_id": attempt_id}
        if not store.update_if(run_id, fields, match=mine):
            logger.warning("exhibition run %s: handoff result fenced out (newer attempt)", run_id)
        return store.get(run_id) or row

    request_record = None
    try:
        payload = build_payload(row, artifacts, url_ttl_sec=cfg.url_ttl_sec)
        request_record = redacted_payload(payload, row)
        ack = client.send(payload)
    except HandoffError as e:
        logger.warning("exhibition run %s handoff failed: %s %s", run_id, e.code, e.message)
        return _finish({
            "handoff_status": HANDOFF_FAILED,
            "handoff_request": request_record,
            "handoff_error_code": e.code,
            "handoff_error_message": e.message[:2000],
        })
    except Exception as e:  # noqa: BLE001 — 핸드오프 실패가 워커/API 를 죽이지 않는다
        logger.exception("exhibition run %s handoff failed unexpectedly", run_id)
        return _finish({
            "handoff_status": HANDOFF_FAILED,
            "handoff_request": request_record,
            "handoff_error_code": "HANDOFF_INTERNAL_ERROR",
            "handoff_error_message": f"{type(e).__name__}: {e}"[:2000],
        })
    return _finish({
        "handoff_status": HANDOFF_CONFIRMED,
        "handoff_request": request_record,
        "handoff_ack": ack.raw,
        "handoff_confirmed_at": datetime.now(timezone.utc).isoformat(),
    })


def public_view(row: dict[str, Any], artifacts: Optional[ArtifactStore] = None) -> dict[str, Any]:
    """API 응답용 — 산출물은 짧은 서명 URL 로."""
    outputs = row.get("outputs_json") or {}
    urls = {}
    if artifacts is not None:
        for name, path in outputs.items():
            urls[name] = artifacts.signed_url(path)
    return {
        "run_id": row["id"],
        "exhibition_id": row.get("exhibition_id"),
        "status": row["status"],
        "stage": row.get("stage"),
        "review_reasons": row.get("review_reasons") or [],
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "outputs": outputs,
        "output_urls": urls,
        "manifest": row.get("manifest_json"),
        "handoff": {
            "status": row.get("handoff_status"),
            "attempts": int(row.get("handoff_attempts") or 0),
            "requested_at": row.get("handoff_requested_at"),
            "confirmed_at": row.get("handoff_confirmed_at"),
            "ack": row.get("handoff_ack"),
            "error_code": row.get("handoff_error_code"),
            "error_message": row.get("handoff_error_message"),
        },
    }
