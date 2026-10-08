"""
VLM 시맨틱 특성 분석 (Phase 2) — **격리된** 비전-언어 모델 인터페이스.

── 위치와 원칙 ─────────────────────────────────────────────────────────────
결정론적 분석(pet_identity_service)이 잴 수 없는 시맨틱 특성 — 귀 모양, 주둥이
색, 무늬 서술 — 만 여기서 얻는다. 원칙:

  * 출력은 **구조화**된다 (JSON schema 강제). 자유 서술이 스키마 밖으로 새지 않는다.
  * 증거가 부족한 항목은 "unknown" 이다 — 프롬프트와 스키마 둘 다 이를 강제한다.
  * 이 출력은 **정본이 아니다.** 원본 이미지가 정본이고, 이 결과는 프로필의
    semantic_traits 네임스페이스에만 들어간다 — 결정론적 필드를 덮지 않는다.
  * 어떤 모델/버전이 만든 값인지 항상 함께 기록된다.

── 왜 기본 꺼짐인가 ────────────────────────────────────────────────────────
프로덕션 이미지에는 anthropic 의존성이 포함되지만, 명시적으로 켜고 키를 넣기
전에는 호출하지 않는다. 켜려면:
  PET_VLM_IDENTITY_ENABLED=1  +  ANTHROPIC_API_KEY
꺼져 있거나 실패하면 None 을 돌려주고, 호출자는 semantic_traits 를 unknown 으로
기록한다 — 신원 분석 실패가 파이프라인을 막지 않는다.

거절(stop_reason == "refusal")도 같은 경로다: 분석 불가 → None → unknown.
그래서 server-side fallback 베타는 붙이지 않았다 — 여기서 거절의 올바른 처리는
"다른 모델로 재시도"가 아니라 "증거 없음"이다.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import copy
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

VLM_ANALYZER_VERSION = "vlm-identity-v1"
VLM_CLASSIFIER_VERSION = "vlm-view-pose-v1"
VLM_CANONICAL_QA_VERSION = "vlm-canonical-qa-v2"
VLM_TARGETED_QA_VERSION = "vlm-targeted-qa-v1"

_ENABLED_ENV = "PET_VLM_IDENTITY_ENABLED"
_MODEL_ENV = "PET_VLM_MODEL"
_DEFAULT_MODEL = "claude-opus-5"

#: 한 번의 분석에 보낼 최대 이미지 수 (원본 레퍼런스 앞에서부터).
MAX_IMAGES = 3

UNKNOWN = "unknown"

#: 구조화 출력 스키마. 모든 문자열 필드는 증거가 부족하면 "unknown" 이어야 한다.
SEMANTIC_TRAITS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "species": {"type": "string"},
        "breed_estimate": {"type": "string"},
        "breed_confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "face": {
            "type": "object",
            "properties": {
                "muzzle_color": {"type": "string"},
                "facial_markings": {"type": "string"},
            },
            "required": ["muzzle_color", "facial_markings"],
            "additionalProperties": False,
        },
        "eyes": {
            "type": "object",
            "properties": {
                "color": {"type": "string"},
                "surrounding_markings": {"type": "string"},
            },
            "required": ["color", "surrounding_markings"],
            "additionalProperties": False,
        },
        "ears": {
            "type": "object",
            "properties": {
                "shape": {
                    "type": "string",
                    "enum": ["erect", "semi_erect", "floppy", "rose", "cropped", "unknown"],
                },
                "color_markings": {"type": "string"},
            },
            "required": ["shape", "color_markings"],
            "additionalProperties": False,
        },
        "coat": {
            "type": "object",
            "properties": {
                "dominant_colors": {"type": "array", "items": {"type": "string"}},
                "secondary_colors": {"type": "array", "items": {"type": "string"}},
                "length": {
                    "type": "string",
                    "enum": ["hairless", "short", "medium", "long", "unknown"],
                },
                "texture": {
                    "type": "string",
                    "enum": ["smooth", "wiry", "curly", "double", "silky", "unknown"],
                },
                "marking_distribution": {"type": "string"},
            },
            "required": [
                "dominant_colors",
                "secondary_colors",
                "length",
                "texture",
                "marking_distribution",
            ],
            "additionalProperties": False,
        },
        "body": {
            "type": "object",
            "properties": {
                "chest_markings": {"type": "string"},
                "torso_markings": {"type": "string"},
            },
            "required": ["chest_markings", "torso_markings"],
            "additionalProperties": False,
        },
        "paws": {
            "type": "object",
            "properties": {"colors_markings": {"type": "string"}},
            "required": ["colors_markings"],
            "additionalProperties": False,
        },
        "tail": {
            "type": "object",
            "properties": {
                "appearance": {"type": "string"},
                "tip_marking": {"type": "string"},
            },
            "required": ["appearance", "tip_marking"],
            "additionalProperties": False,
        },
        "unique_features": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "species",
        "breed_estimate",
        "breed_confidence",
        "face",
        "eyes",
        "ears",
        "coat",
        "body",
        "paws",
        "tail",
        "unique_features",
    ],
    "additionalProperties": False,
}

_PROMPT = (
    "You are documenting the visual identity of ONE pet from its owner's reference "
    "photos, for later image-generation fidelity checks. Report ONLY what is clearly "
    "visible in these exact images.\n"
    "Rules:\n"
    "- If a body part is not visible, occluded, blurry, or you are not confident, use "
    'the exact string "unknown" for that field (or an empty list for list fields).\n'
    "- Never guess hidden regions. A photo that hides the tail means tail fields are "
    '"unknown".\n'
    "- Describe colors and markings concretely (e.g. \"white blaze from forehead to "
    'nose", "dark saddle over back").\n'
    "- unique_features: only clearly visible distinctive traits (heterochromia, torn "
    "ear, specific spot patterns). Empty list if none are visible."
)


def is_enabled() -> bool:
    return os.getenv(_ENABLED_ENV, "0").strip().lower() in ("1", "true", "yes")


def model_name() -> str:
    return (os.getenv(_MODEL_ENV) or "").strip() or _DEFAULT_MODEL


def unavailable_reason() -> Optional[str]:
    """
    지금 구성으로 VLM 을 **부를 수 없는** 이유. 부를 수 있으면 None.

    네트워크 호출은 없다 — 유료 생성 전에 "이 QA 구성으로 PASS 가 가능한가"를
    판단하는 사전 점검용이다 (canonical_pet_service.build_canonical).
    """
    if not is_enabled():
        return f"{_ENABLED_ENV} 가 꺼져 있습니다"
    import importlib.util

    if importlib.util.find_spec("anthropic") is None:
        return "anthropic 패키지가 설치되어 있지 않습니다"
    if not ((os.getenv("ANTHROPIC_API_KEY") or "").strip() or (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()):
        return "ANTHROPIC_API_KEY 가 설정되어 있지 않습니다"
    return None


# ── VLM 결과 캐시 (같은 이미지 중복 과금 방지) ──────────────────────────────
# 한 번의 인테이크에서 pet_identity_service 와 pet_morphology_service 가 **같은**
# 원본 레퍼런스를 각각 분석하고(pet_reference_set_service 가 두 프로필을 연달아
# 빌드한다), 그 세트 빌드가 이어서 같은 레퍼런스를 뷰/포즈로도 분류한다. 캐시가
# 없으면 이미지 1장당 유료 VLM 호출이 여러 번 나간다.
#
# 키는 내용 주소다 — 모델 + 분석기 버전 + 이미지 바이트 해시. 원본이나 모델이
# 바뀌면 자동으로 무효화되므로 stale 결과가 프로필에 실릴 수 없다. semantic
# traits 와 reference classification 은 분석기 버전이 서로 달라 키가 절대
# 겹치지 않는다 — 같은 바이트라도 두 목적의 결과가 섞이지 않는다.
#
# 실패(None)는 **캐시하지 않는다**: 일시적 오류를 프로세스 수명 내내 "증거 없음"
# 으로 굳혀 프로필을 영구 unknown 으로 만드는 쪽이, 실패 경로에서 호출이 한 번 더
# 나가는 것보다 나쁘다.
#
# ── 2단계: in-memory(빠름, 64건, 재시작 시 소실) + durable(느리지만 워커
# 재시작/여러 프로세스에도 남는다) ─────────────────────────────────────────
# durable 계층이 없으면 같은 이미지가 배포마다, 워커 프로세스마다 다시 과금된다
# — in-memory LRU 만으로는 "이 펫은 이미 분석했다"가 프로세스 수명에 갇힌다.
# durable 조회/기록 실패는 폴백일 뿐이다(호출자는 그냥 다시 계산한다) —
# 이 캐시가 없어도 기존 동작(직접 호출) 그대로 동작해야 한다.
#
# ── single-flight (동시 요청 중복 호출 방지) ────────────────────────────────
# 캐시는 "이미 끝난" 호출만 막는다. identity/morphology 프로필 빌드를 동시에
# 돌리면(레퍼런스 다운로드/분석 레이턴시를 줄이려는 게 이 병렬화 작업의 목적
# 이다) 같은 이미지에 대해 **둘 다 캐시 미스**를 보고 동시에 유료 호출을 내보낼
# 수 있다 — 캐시 딕셔너리 접근만 잠갔을 뿐 "지금 이 키를 누군가 계산 중"이라는
# 상태는 없었기 때문이다. 키별 락으로 두 번째 호출자를 첫 번째 뒤에 세우고,
# 첫 번째가 캐시에 쓴 값을 그대로 재사용하게 한다. 참조 카운트로 락을 정리해
# 딕셔너리가 무한정 자라지 않는다.
_RESULT_CACHE_MAX = 64
_result_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_result_cache_lock = threading.Lock()

_semantic_inflight_locks: dict[str, threading.Lock] = {}
_semantic_inflight_refcount: dict[str, int] = {}
_semantic_inflight_guard = threading.Lock()


def _durable_cache_table() -> str:
    return os.getenv("PET_VLM_ANALYSIS_CACHE_TABLE", "pet_vlm_analysis_cache")


def _durable_cache_use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _durable_cache_client():
    from ..models.content import _supabase_client

    return _supabase_client()


#: HYBRID_USE_SUPABASE=0 이거나 supabase 클라이언트가 없을 때 쓰는 목업 저장소
#: (다른 서비스들의 _MOCK_PROFILES 관례와 동일).
_MOCK_DURABLE_CACHE: dict[str, dict[str, Any]] = {}


def _durable_cache_get(cache_key: str) -> Optional[dict[str, Any]]:
    if _durable_cache_use_db():
        client = _durable_cache_client()
        if not client:
            return None
        try:
            r = (
                client.table(_durable_cache_table())
                .select("result")
                .eq("cache_key", cache_key)
                .limit(1)
                .execute()
            )
            rows = getattr(r, "data", None) or []
            return copy.deepcopy(rows[0]["result"]) if rows else None
        except Exception:
            logger.warning("VLM durable 캐시 조회 실패 — 재계산으로 폴백", exc_info=True)
            return None
    row = _MOCK_DURABLE_CACHE.get(cache_key)
    return copy.deepcopy(row["result"]) if row else None


def _durable_cache_put(
    cache_key: str, *, kind: str, analyzer_version: str, result: dict[str, Any]
) -> None:
    row = {
        "cache_key": cache_key,
        "kind": kind,
        "analyzer_version": analyzer_version,
        "model": model_name(),
        "result": result,
    }
    if _durable_cache_use_db():
        client = _durable_cache_client()
        if not client:
            return
        try:
            client.table(_durable_cache_table()).upsert(row, on_conflict="cache_key").execute()
        except Exception:
            # 순수 캐시 — 기록 실패는 다음 호출이 다시 계산하게 둔다.
            logger.warning("VLM durable 캐시 기록 실패", exc_info=True)
        return
    _MOCK_DURABLE_CACHE[cache_key] = dict(row)


def __reset_durable_cache_for_tests() -> None:
    _MOCK_DURABLE_CACHE.clear()


class _InflightLock:
    """참조 카운트로 스스로를 청소하는 키별 락 — with 문으로 쓴다."""

    def __init__(self, key: str):
        self._key = key
        with _semantic_inflight_guard:
            _semantic_inflight_refcount[key] = _semantic_inflight_refcount.get(key, 0) + 1
            self._lock = _semantic_inflight_locks.setdefault(key, threading.Lock())

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *exc_info: Any) -> None:
        self._lock.release()
        with _semantic_inflight_guard:
            remaining = _semantic_inflight_refcount.get(self._key, 1) - 1
            if remaining <= 0:
                _semantic_inflight_refcount.pop(self._key, None)
                _semantic_inflight_locks.pop(self._key, None)
            else:
                _semantic_inflight_refcount[self._key] = remaining


def _mem_cache_get(cache_key: str) -> Optional[dict[str, Any]]:
    with _result_cache_lock:
        cached = _result_cache.get(cache_key)
        if cached is not None:
            _result_cache.move_to_end(cache_key)
            return copy.deepcopy(cached)
    return None


def _mem_cache_put(cache_key: str, result: dict[str, Any]) -> None:
    with _result_cache_lock:
        _result_cache[cache_key] = copy.deepcopy(result)
        _result_cache.move_to_end(cache_key)
        while len(_result_cache) > _RESULT_CACHE_MAX:
            _result_cache.popitem(last=False)


def _cached_result(cache_key: str) -> Optional[dict[str, Any]]:
    """in-memory(빠름) → durable(프로세스 경계를 넘음) 순으로 조회."""
    cached = _mem_cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        durable = _durable_cache_get(cache_key)
    except Exception:
        # 순수 캐시 — 조회 자체가 죽어도 호출자는 그냥 다시 계산해야 한다.
        logger.warning("VLM durable 캐시 조회 중 예외 — 재계산으로 폴백", exc_info=True)
        return None
    if durable is not None:
        _mem_cache_put(cache_key, durable)
        return copy.deepcopy(durable)
    return None


def _store_result(
    cache_key: str, *, kind: str, analyzer_version: str, result: dict[str, Any]
) -> None:
    _mem_cache_put(cache_key, result)
    _durable_cache_put(cache_key, kind=kind, analyzer_version=analyzer_version, result=result)


def _semantic_cache_key(images: Sequence[tuple[bytes, str]]) -> str:
    h = hashlib.sha256()
    h.update(model_name().encode("utf-8"))
    h.update(b"\0")
    h.update(VLM_ANALYZER_VERSION.encode("utf-8"))
    for data, mime in images:
        h.update(b"\0")
        h.update(mime.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def _classification_cache_key(data: bytes, mime_type: str) -> str:
    h = hashlib.sha256()
    h.update(model_name().encode("utf-8"))
    h.update(b"\0")
    h.update(VLM_CLASSIFIER_VERSION.encode("utf-8"))
    h.update(b"\0")
    h.update((mime_type or "image/jpeg").encode("utf-8"))
    h.update(b"\0")
    h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


# ── QA 호출 캐시 (VLM_QA_CACHE) ─────────────────────────────────────────────
# qa_canonical_image / qa_action_keyframe / qa_motion_video 는 위 두 분석기와 달리
# 캐시가 전혀 없었다 — raw 저장 뒤 QA 기록 전 크래시, RAW_STORE_FAILED 복구,
# QA 전용 버전 범프 뒤 qa-rerun 이 전부 같은 입력으로 유료 호출을 다시 냈다.
#
#   VLM_QA_CACHE = off | on (기본) | refresh
#   refresh 는 읽기를 건너뛰고 새 답을 **써서** 항목을 덮어쓴다 — 운영자가 후보
#   하나를 강제로 다시 묻는 경로(reevaluate_* 의 vlm_cache_mode).
#
# 키 = 모델 + 종류 + 호출 버전 + 실제로 보낸 프롬프트/파라미터 + **실제로 보낸**
# 이미지 바이트의 해시 (모션은 샘플 프레임 JPEG 그대로). 이미지 바이트는 저장하지
# 않는다 — 행에는 해시 키와 파싱된 VLM 답만 남는다. 판정 이전의 원 답을 캐시하므로
# 규칙이 바뀌어도 캐시된 답으로 다시 판정한다. 실패/거절/파싱 불가는 캐시하지 않는다.
VLM_QA_CACHE_ENV = "VLM_QA_CACHE"
QA_CACHE_OFF = "off"
QA_CACHE_ON = "on"
QA_CACHE_REFRESH = "refresh"
QA_CACHE_MODES = (QA_CACHE_OFF, QA_CACHE_ON, QA_CACHE_REFRESH)

KIND_CANONICAL_QA = "canonical_qa"
KIND_KEYFRAME_QA = "keyframe_qa"
KIND_MOTION_QA = "motion_qa"
KIND_TARGETED_QA = "targeted_qa"


def qa_cache_mode(override: Optional[str] = None) -> str:
    """인자 > 환경 변수 > on. 알 수 없는 값은 on 으로 안전하게 캐시한다."""
    value = str(override or os.getenv(VLM_QA_CACHE_ENV) or QA_CACHE_ON).strip().lower()
    return value if value in QA_CACHE_MODES else QA_CACHE_ON


def _qa_cache_key(
    kind: str, version: str, images: Sequence[tuple[bytes, str]], params: dict[str, Any]
) -> str:
    h = hashlib.sha256()
    for part in (model_name(), kind, version, json.dumps(params, sort_keys=True, default=str)):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    for data, mime in images:
        h.update((mime or "").encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
        h.update(b"\0")
    return h.hexdigest()


def _image_block(data: bytes, mime: str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


# ── VLM 호출 실패 분류/계수 ──────────────────────────────────────────────────
# _structured_qa_call 은 계약상 실패를 전부 None 으로 접는다. 그 이유(실패 종류)는
# 여기서 따로 남긴다: 호출자가 capture_call_failures() 로 수집해 QA 영수증에
# 영속화하고, 프로세스 카운터는 "VLM 이 아무것도 돌려주지 않은" 비율을 낸다.
VLM_FAILURE_API_ERROR = "api_error"
VLM_FAILURE_TIMEOUT = "timeout"
VLM_FAILURE_REFUSAL = "refusal"
VLM_FAILURE_PARSE = "parse_failure"
VLM_FAILURE_SDK_MISSING = "sdk_missing"

_failure_capture: contextvars.ContextVar[Optional[list[dict[str, Any]]]] = contextvars.ContextVar(
    "vlm_failure_capture", default=None
)
_call_stats_lock = threading.Lock()
_call_stats: dict[str, int] = {}


@contextlib.contextmanager
def capture_call_failures():
    """Collect the failure records of every VLM call made inside the block.

    asyncio.to_thread copies the context, so calls made on a worker thread
    append to the same list.
    """

    sink: list[dict[str, Any]] = []
    token = _failure_capture.set(sink)
    try:
        yield sink
    finally:
        _failure_capture.reset(token)


def call_stats() -> dict[str, Any]:
    """Process-lifetime VLM call counters, including the returned-nothing rate."""

    with _call_stats_lock:
        stats = dict(_call_stats)
    calls = int(stats.get("calls", 0))
    nothing = int(stats.get("returned_nothing", 0))
    return {
        **stats,
        "calls": calls,
        "returned_nothing": nothing,
        "returned_nothing_rate": (round(nothing / calls, 4) if calls else None),
    }


def reset_call_stats() -> None:
    with _call_stats_lock:
        _call_stats.clear()


def _record_call(label: str, failure: Optional[str] = None, **detail: Any) -> None:
    with _call_stats_lock:
        _call_stats["calls"] = _call_stats.get("calls", 0) + 1
        if failure:
            _call_stats["returned_nothing"] = _call_stats.get("returned_nothing", 0) + 1
            key = f"returned_nothing:{failure}"
            _call_stats[key] = _call_stats.get(key, 0) + 1
    if not failure:
        return
    record = {
        "label": label,
        "failure_class": failure,
        **{k: v for k, v in detail.items() if v is not None},
    }
    logger.warning("[vlm-returned-nothing] %s", json.dumps(record, default=str))
    sink = _failure_capture.get()
    if sink is not None:
        sink.append(record)


def _structured_qa_call(
    content: list[dict[str, Any]], schema: dict[str, Any], *, label: str
) -> Optional[dict[str, Any]]:
    """한 번의 구조화 QA 호출. 비활성/실패/거절/파싱 실패는 전부 None (이전과 동일)."""
    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        _record_call(label, VLM_FAILURE_SDK_MISSING)
        return None
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except Exception as exc:
        logger.warning("VLM %s 호출 실패", label, exc_info=True)
        # SDK 의 타입 예외로 분류한다 (가장 구체적인 것부터). 타임아웃은
        # APIConnectionError 의 하위 클래스라 먼저 본다.
        timeout_error = getattr(anthropic, "APITimeoutError", None)
        status_error = getattr(anthropic, "APIStatusError", None)
        if isinstance(timeout_error, type) and isinstance(exc, timeout_error):
            _record_call(label, VLM_FAILURE_TIMEOUT, error_type=type(exc).__name__)
        elif isinstance(status_error, type) and isinstance(exc, status_error):
            _record_call(
                label,
                VLM_FAILURE_API_ERROR,
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
                request_id=getattr(exc, "request_id", None),
                message=str(getattr(exc, "message", "") or "")[:300] or None,
            )
        else:  # APIConnectionError, 인증 미설정, 요청 구성 오류 등
            _record_call(
                label,
                VLM_FAILURE_API_ERROR,
                error_type=type(exc).__name__,
                message=str(exc)[:300] or None,
            )
        return None
    stop_reason = getattr(response, "stop_reason", None)
    request_id = getattr(response, "_request_id", None)
    if stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        _record_call(
            label,
            VLM_FAILURE_REFUSAL,
            category=getattr(details, "category", None),
            request_id=request_id,
        )
        return None
    try:
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
    except Exception as exc:
        logger.warning("VLM %s 응답 파싱 실패", label, exc_info=True)
        # stop_reason=max_tokens 는 잘린 JSON 을 뜻한다 — 원인 구분용으로 남긴다.
        _record_call(
            label,
            VLM_FAILURE_PARSE,
            error_type=type(exc).__name__,
            stop_reason=stop_reason,
            request_id=request_id,
        )
        return None
    if not isinstance(result, dict):
        _record_call(
            label, VLM_FAILURE_PARSE, error_type="non_object_json",
            stop_reason=stop_reason, request_id=request_id,
        )
        return None
    _record_call(label)
    result["model"] = getattr(response, "model", model_name())
    return result


def _cached_qa_call(
    *,
    kind: str,
    version: str,
    images: Sequence[tuple[bytes, str]],
    params: dict[str, Any],
    call: Any,
    cache_mode: Optional[str],
    with_receipt: bool = False,
) -> Optional[dict[str, Any]]:
    """
    QA 호출 1건을 캐시 정책으로 감싼다. off 면 call() 그대로 (락도 없다 — 이전 동작).
    on 은 in-memory → durable 조회 뒤 single-flight 로 한 번만 호출하고 저장한다.
    refresh 는 조회를 건너뛰되 저장은 한다. None 결과는 어떤 모드에서도 저장하지 않는다.
    로그에는 종류/모드/키 접두어만 남긴다 — 이미지 데이터나 식별 정보는 없다.
    """
    mode = qa_cache_mode(cache_mode)
    key = _qa_cache_key(kind, version, images, params)

    def _result_with_receipt(
        result: Optional[dict[str, Any]], status: str
    ) -> Optional[dict[str, Any]]:
        if result is None or not with_receipt:
            return result
        return {
            **copy.deepcopy(result),
            "cache_receipt": {
                "status": status,
                "cache_key": key,
                "kind": kind,
                "qa_version": version,
                "model": model_name(),
                "prompt_params_hash": hashlib.sha256(
                    json.dumps(params, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
                "input_hashes": [
                    {
                        "mime": mime,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for data, mime in images
                ],
            },
        }

    if mode == QA_CACHE_OFF:
        return _result_with_receipt(call(), "computed")
    short = key[:12]
    if mode == QA_CACHE_ON:
        cached = _cached_result(key)
        if cached is not None:
            logger.info("[vlm-qa-cache] hit kind=%s key=%s", kind, short)
            return _result_with_receipt(cached, "cache_hit")
    with _InflightLock(key):
        if mode == QA_CACHE_ON:
            cached = _cached_result(key)
            if cached is not None:
                logger.info("[vlm-qa-cache] hit kind=%s key=%s (after in-flight wait)", kind, short)
                return _result_with_receipt(cached, "cache_hit")
        logger.info(
            "[vlm-qa-cache] %s kind=%s key=%s", "refresh" if mode == QA_CACHE_REFRESH else "miss", kind, short
        )
        result = call()
        if result is None:
            logger.info("[vlm-qa-cache] not-stored kind=%s key=%s (no usable answer)", kind, short)
            return None
        _store_result(key, kind=kind, analyzer_version=version, result=result)
        return _result_with_receipt(result, "computed")


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Build a strict schema for one narrow VLM question."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _targeted_qa_calls(
    *,
    stage: str,
    images: Sequence[tuple[bytes, str]],
    tasks: Sequence[str],
    questions: Sequence[str],
    task_specs: dict[str, tuple[dict[str, Any], str]],
    params: dict[str, Any],
    cache_mode: Optional[str],
) -> Optional[dict[str, Any]]:
    """Run and merge independent narrow calls while retaining each raw answer."""

    called: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    merged: dict[str, Any] = {}
    models: list[str] = []
    for task in dict.fromkeys(str(value) for value in tasks):
        spec = task_specs.get(task)
        if spec is None:
            continue
        schema, prompt = spec
        content = [_image_block(data, mime) for data, mime in images]
        content.append({"type": "text", "text": prompt})

        def _call(
            content: list[dict[str, Any]] = content,
            schema: dict[str, Any] = schema,
            task: str = task,
        ) -> Optional[dict[str, Any]]:
            result = _structured_qa_call(content, schema, label=f"{stage} {task}")
            if result is not None:
                result["source"] = VLM_TARGETED_QA_VERSION
                result["task"] = task
            return result

        result = _cached_qa_call(
            kind=f"{KIND_TARGETED_QA}:{stage.lower()}:{task.lower()}",
            version=f"{VLM_TARGETED_QA_VERSION}:{stage.lower()}:{task.lower()}",
            images=images,
            params={
                **params,
                "prompt": prompt,
                "task": task,
                "questions": list(questions),
            },
            call=_call,
            cache_mode=cache_mode,
            with_receipt=True,
        )
        if result is None:
            continue
        called.append(task)
        evidence[task] = copy.deepcopy(result)
        if result.get("model"):
            models.append(str(result["model"]))
        for key, value in result.items():
            if key not in {"source", "model", "task", "cache_receipt"}:
                merged[key] = value

    if not evidence:
        return None
    return {
        **merged,
        "source": VLM_TARGETED_QA_VERSION,
        "model": models[0] if models else model_name(),
        "called_tasks": called,
        "targeted_vlm_evidence": evidence,
    }


def clear_semantic_cache() -> None:
    """VLM 결과 캐시를 비운다 (테스트/운영 훅) — in-memory + durable(목업) 둘 다."""
    with _result_cache_lock:
        _result_cache.clear()
    __reset_durable_cache_for_tests()


def analyze_semantic_traits(
    images: Sequence[tuple[bytes, str]],
) -> Optional[dict[str, Any]]:
    """
    원본 이미지들 → 구조화된 시맨틱 특성. 실패/비활성은 None.

    images: (bytes, mime_type) 목록. 앞에서부터 MAX_IMAGES 장만 쓴다.
    반환: {"traits": <스키마 준수 dict>, "model": ..., "analyzer": ..., "image_count": n}
    """
    if not is_enabled():
        return None
    if not images:
        return None

    prepared: list[tuple[bytes, str]] = [
        (data, (mime or "image/jpeg")) for data, mime in images[:MAX_IMAGES] if data
    ]
    if not prepared:
        return None
    used = len(prepared)

    # 같은 이미지에 대한 앞선 성공 결과가 있으면 유료 호출을 건너뛴다.
    cache_key = _semantic_cache_key(prepared)
    cached = _cached_result(cache_key)
    if cached is not None:
        return cached

    # identity/morphology 프로필 빌드를 동시에 돌릴 때, 같은 이미지에 대한
    # 두 번째 호출자를 여기서 첫 번째 뒤에 세운다 — 그러지 않으면 위 캐시
    # 체크를 둘 다 통과한 뒤 유료 호출을 각자 내보낼 수 있다.
    with _InflightLock(cache_key):
        # 락을 기다리는 동안 다른 스레드가 채웠을 수 있다 — 재확인.
        cached = _cached_result(cache_key)
        if cached is not None:
            return cached

        try:
            import anthropic
        except ImportError:
            logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
            return None

        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
            for data, mime in prepared
        ]
        content.append({"type": "text", "text": _PROMPT})

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=model_name(),
                max_tokens=4096,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "format": {"type": "json_schema", "schema": SEMANTIC_TRAITS_SCHEMA}
                },
            )
        except Exception:
            logger.warning("VLM 시맨틱 분석 호출 실패", exc_info=True)
            return None

        # 거절은 "증거 제공 불가"로 처리한다 — 호출자가 unknown 으로 기록한다.
        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning(
                "VLM 시맨틱 분석이 거절됨 (stop_details=%s)", getattr(response, "stop_details", None)
            )
            return None

        try:
            text = next(b.text for b in response.content if b.type == "text")
            traits = json.loads(text)
        except Exception:
            logger.warning("VLM 응답 파싱 실패", exc_info=True)
            return None

        result = {
            "traits": traits,
            "model": getattr(response, "model", model_name()),
            "analyzer": VLM_ANALYZER_VERSION,
            "image_count": used,
        }
        _store_result(
            cache_key, kind="semantic_traits", analyzer_version=VLM_ANALYZER_VERSION, result=result
        )
        return result


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 1장 뷰/포즈/가시성 분류 (Phase 3)
# ══════════════════════════════════════════════════════════════════════════

#: pet_reference_images.view_label CHECK 의 상위집합 — 같은 문자열 체계 하나만 쓴다.
VIEW_LABELS = (
    "FRONT",
    "FRONT_LEFT_3Q",
    "FRONT_RIGHT_3Q",
    "LEFT",
    "RIGHT",
    "BACK",
    "TOP",
    "FULL_BODY",
    "FACE_CLOSEUP",
    "UNKNOWN",
)

POSE_LABELS = ("STANDING", "SITTING", "LYING", "SLEEPING", "CLOSEUP", "UNKNOWN")

_YNU = {"type": "string", "enum": ["yes", "no", "unknown"]}
_CONF = {"type": "string", "enum": ["high", "medium", "low"]}

REFERENCE_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "view_label": {"type": "string", "enum": list(VIEW_LABELS)},
        "view_confidence": _CONF,
        "pose_label": {"type": "string", "enum": list(POSE_LABELS)},
        "pose_confidence": _CONF,
        "visibility": {
            "type": "object",
            "properties": {
                "face_visible": _YNU,
                "full_body_visible": _YNU,
                "left_side_visible": _YNU,
                "right_side_visible": _YNU,
                "paws_visible": _YNU,
                "tail_visible": _YNU,
                "ears_visible": _YNU,
                "distinct_markings_visible": _YNU,
                "heavy_occlusion": _YNU,
                "person_obstruction": _YNU,
            },
            "required": [
                "face_visible",
                "full_body_visible",
                "left_side_visible",
                "right_side_visible",
                "paws_visible",
                "tail_visible",
                "ears_visible",
                "distinct_markings_visible",
                "heavy_occlusion",
                "person_obstruction",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["view_label", "view_confidence", "pose_label", "pose_confidence", "visibility"],
    "additionalProperties": False,
}

_CLASSIFY_PROMPT = (
    "Classify this single pet reference photo for an identity-reference catalog.\n"
    "Rules:\n"
    '- LEFT/RIGHT are from the PET\'s perspective (its left flank visible = "LEFT").\n'
    '- Be conservative: if the camera angle is ambiguous, use view_label "UNKNOWN" '
    "with low confidence rather than guessing a side.\n"
    '- visibility answers are about what is actually visible in THIS photo; use '
    '"unknown" when you cannot tell.\n'
    "- Never infer hidden anatomy: a tail out of frame means tail_visible is "
    '"no", not a guess.'
)


def classify_reference(data: bytes, mime_type: str = "image/jpeg") -> Optional[dict[str, Any]]:
    """
    원본 1장 → {view_label, pose_label, visibility, ...}. 실패/비활성은 None —
    호출자는 결정론적 UNKNOWN 폴백을 쓴다.

    analyze_semantic_traits 와 같은 내용-주소 캐시(§ VLM 결과 캐시)를 쓴다 —
    이 함수는 전에 캐시가 전혀 없었다: 세트가 다시 빌드될 때마다(원본이
    하나 추가되기만 해도) 바뀌지 않은 레퍼런스까지 매번 재과금됐다.
    """
    if not is_enabled() or not data:
        return None

    mime = mime_type or "image/jpeg"
    cache_key = _classification_cache_key(data, mime)
    cached = _cached_result(cache_key)
    if cached is not None:
        return cached

    with _InflightLock(cache_key):
        cached = _cached_result(cache_key)
        if cached is not None:
            return cached

        try:
            import anthropic
        except ImportError:
            logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
            return None

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=model_name(),
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": base64.standard_b64encode(data).decode("ascii"),
                                },
                            },
                            {"type": "text", "text": _CLASSIFY_PROMPT},
                        ],
                    }
                ],
                output_config={
                    "format": {"type": "json_schema", "schema": REFERENCE_CLASSIFICATION_SCHEMA}
                },
            )
        except Exception:
            logger.warning("VLM 레퍼런스 분류 호출 실패", exc_info=True)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            return None

        try:
            text = next(b.text for b in response.content if b.type == "text")
            result = json.loads(text)
        except Exception:
            logger.warning("VLM 분류 응답 파싱 실패", exc_info=True)
            return None

        result["source"] = VLM_CLASSIFIER_VERSION
        result["model"] = getattr(response, "model", model_name())
        _store_result(
            cache_key,
            kind="reference_classification",
            analyzer_version=VLM_CLASSIFIER_VERSION,
            result=result,
        )
        return result


# ══════════════════════════════════════════════════════════════════════════
# 정본 후보 QA (Phase 4) — 생성된 마스터 이미지가 "같은 펫"인가
# ══════════════════════════════════════════════════════════════════════════

CANONICAL_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_pet": _YNU,
        "same_pet_confidence": _CONF,
        "face_head_consistent": _YNU,
        "ear_muzzle_consistent": _YNU,
        "distinctive_markings_consistent": _YNU,
        "persistent_morphology_consistent": _YNU,
        "presentation_difference_only": _YNU,
        "anatomy_plausible": _YNU,
        "single_pet": _YNU,
        "human_present": _YNU,
        "accessories_present": _YNU,
        "background_neutral": _YNU,
        "pose_neutral": _YNU,
        "full_body_visible": _YNU,
        "major_occlusion": _YNU,
        "identity_notes": {"type": "string"},
    },
    "required": [
        "same_pet",
        "same_pet_confidence",
        "face_head_consistent",
        "ear_muzzle_consistent",
        "distinctive_markings_consistent",
        "persistent_morphology_consistent",
        "presentation_difference_only",
        "anatomy_plausible",
        "single_pet",
        "human_present",
        "accessories_present",
        "background_neutral",
        "pose_neutral",
        "full_body_visible",
        "major_occlusion",
        "identity_notes",
    ],
    "additionalProperties": False,
}

_CANONICAL_QA_PROMPT = (
    "The FIRST image is a GENERATED candidate for a canonical reference image of a "
    "pet. The following images are REAL reference photos of the actual pet.\n"
    "Judge whether this is recognizably the SAME INDIVIDUAL pet, prioritizing stable "
    "identity and anatomy rather than photographic pixel/color reproduction.\n"
    '- same_pet: use "no" only for a clear individual-identity contradiction. Prefer '
    'face/head proportions, muzzle geometry, eye spacing, ear form, persistent morphology, '
    'tail form when visible, and meaningful distinctive-marking placement/topology. Use '
    '"unknown" when those traits are not sufficiently visible.\n'
    "- face_head_consistent: compare facial/head proportions and eye spacing.\n"
    "- ear_muzzle_consistent: compare ear form and muzzle geometry, not lighting color.\n"
    "- distinctive_markings_consistent: compare meaningful marking placement/topology; "
    "use unknown when markings are weak, hidden, blurry, or ambiguous.\n"
    "- persistent_morphology_consistent: compare persistent body proportions and tail form "
    "when visible across references; ignore pose, crop, and perspective differences and use "
    "unknown when evidence is not repeatable.\n"
    "- presentation_difference_only: yes when visible differences are reasonably explained "
    "by brighter lighting, exposure or white-balance correction, plausible richer coat "
    "presentation, sharper/cleaner fur, denoising, contrast improvement, or normalized "
    "background rather than a different individual. These enhancements are acceptable and "
    "must not by themselves make same_pet=no.\n"
    "- anatomy_plausible: correct number of visible limbs, natural joints, no "
    "merged/extra body parts.\n"
    "- The candidate should contain a single pet, no human, neutral plain "
    "background, neutral pose, full body visible, no major occlusion.\n"
    '- Do not use breed as an identity template. Use "unknown" whenever evidence is '
    "insufficient. Be conservative about claiming contradictions."
)

_IMAGE_IDENTITY_PROPERTIES = {
    key: CANONICAL_QA_SCHEMA["properties"][key]
    for key in (
        "same_pet",
        "same_pet_confidence",
        "face_head_consistent",
        "ear_muzzle_consistent",
        "distinctive_markings_consistent",
        "persistent_morphology_consistent",
        "presentation_difference_only",
        "identity_notes",
    )
}

_IMAGE_ANATOMY_PROPERTIES = {
    key: CANONICAL_QA_SCHEMA["properties"][key]
    for key in (
        "anatomy_plausible",
        "single_pet",
        "human_present",
        "major_occlusion",
    )
}


def _image_identity_prompt(stage: str, questions: Sequence[str]) -> str:
    primary = (
        "approved Canonical and supporting references"
        if stage == "KEYFRAME"
        else "owner reference photos"
    )
    return (
        f"IDENTITY_VLM only. The first image is the generated {stage.lower()} candidate; "
        f"the following images are {primary}. Resolve only these unresolved identity "
        f"questions: {list(questions)}. Do not judge anatomy, pose, motion, composition, "
        "lighting quality, or exact color/exposure. Enhancement, white-balance, sharper "
        "fur, and plausible coat presentation are not identity contradictions. Compare "
        "stable face/head proportions, ear/muzzle form, meaningful marking topology, and "
        "persistent morphology only where the named question needs it. Use unknown when "
        "visibility is insufficient."
    )


def _image_anatomy_prompt(stage: str) -> str:
    return (
        f"ANATOMY_VLM only. The first image is a generated {stage.lower()} candidate. "
        "Judge only severe anatomy/integrity: plausible major limbs and joints, no merged "
        "or extra body parts, one intended pet, no forbidden human contamination, and "
        "whether occlusion prevents that judgement. Do not reassess pet identity, coat, "
        "lighting, pose correctness, motion, or presentation quality. Use unknown when "
        "the image cannot support an answer."
    )


def _image_targeted_specs(
    stage: str, questions: Sequence[str]
) -> dict[str, tuple[dict[str, Any], str]]:
    from . import vlm_escalation

    identity_questions = [
        q
        for q in questions
        if vlm_escalation.targeted_tasks(stage, [q])
        == [vlm_escalation.IDENTITY_VLM]
    ]
    if "full_stage_review" in questions or not identity_questions:
        identity_properties = _IMAGE_IDENTITY_PROPERTIES
    else:
        identity_keys = {
            "same_pet",
            "same_pet_confidence",
            "presentation_difference_only",
            "identity_notes",
        }
        if any("marking" in q for q in identity_questions):
            identity_keys.add("distinctive_markings_consistent")
        if "persistent_morphology" in identity_questions:
            identity_keys.add("persistent_morphology_consistent")
        identity_properties = {
            key: value for key, value in _IMAGE_IDENTITY_PROPERTIES.items() if key in identity_keys
        }
    return {
        vlm_escalation.IDENTITY_VLM: (
            _schema(identity_properties),
            _image_identity_prompt(stage, identity_questions or questions),
        ),
        vlm_escalation.ANATOMY_VLM: (
            _schema(_IMAGE_ANATOMY_PROPERTIES),
            _image_anatomy_prompt(stage),
        ),
    }


def qa_canonical_image(
    candidate: bytes,
    references: Sequence[tuple[bytes, str]],
    candidate_mime: str = "image/png",
    *,
    cache_mode: Optional[str] = None,
    tasks: Optional[Sequence[str]] = None,
    unresolved_questions: Sequence[str] = (),
    evidence_context: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """생성 후보 vs 실제 레퍼런스 — 구조화 QA. 실패/비활성은 None.

    cache_mode: VLM_QA_CACHE 환경 변수를 이 호출에 한해 덮어쓴다 (운영자 refresh 용).
    """
    if not is_enabled() or not candidate:
        return None

    images: list[tuple[bytes, str]] = [(candidate, candidate_mime)]
    images += [(data, (mime or "image/jpeg")) for data, mime in references[:MAX_IMAGES] if data]
    if tasks is not None:
        return _targeted_qa_calls(
            stage="CANONICAL",
            images=images,
            tasks=tasks,
            questions=unresolved_questions,
            task_specs=_image_targeted_specs("CANONICAL", unresolved_questions),
            params={
                "candidate_mime": candidate_mime,
                "evidence_context": dict(evidence_context or {}),
            },
            cache_mode=cache_mode,
        )
    content: list[dict[str, Any]] = [_image_block(data, mime) for data, mime in images]
    content.append({"type": "text", "text": _CANONICAL_QA_PROMPT})

    def _call() -> Optional[dict[str, Any]]:
        result = _structured_qa_call(content, CANONICAL_QA_SCHEMA, label="정본 QA")
        if result is not None:
            result["source"] = VLM_CANONICAL_QA_VERSION
        return result

    return _cached_qa_call(
        kind=KIND_CANONICAL_QA,
        version=VLM_CANONICAL_QA_VERSION,
        images=images,
        params={"prompt": _CANONICAL_QA_PROMPT, "candidate_mime": candidate_mime},
        call=_call,
        cache_mode=cache_mode,
    )


# ══════════════════════════════════════════════════════════════════════════
# 액션 키프레임 QA (Phase 5) — 정본 QA + 포즈 확인
# ══════════════════════════════════════════════════════════════════════════

VLM_KEYFRAME_QA_VERSION = "vlm-keyframe-qa-v2"

_KEYFRAME_CANONICAL_FIELDS = (
    "same_pet",
    "same_pet_confidence",
    "face_head_consistent",
    "ear_muzzle_consistent",
    "distinctive_markings_consistent",
    "persistent_morphology_consistent",
    "presentation_difference_only",
    "anatomy_plausible",
    "single_pet",
    "human_present",
    "accessories_present",
    "background_neutral",
    "pose_neutral",
    "full_body_visible",
    "major_occlusion",
    "identity_notes",
)

KEYFRAME_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            key: CANONICAL_QA_SCHEMA["properties"][key]
            for key in _KEYFRAME_CANONICAL_FIELDS
        },
        "pose_matches": _YNU,
        "pose_confidence": _CONF,
        "body_orientation_ok": _YNU,
        "required_regions_visible": _YNU,
    },
    "required": list(_KEYFRAME_CANONICAL_FIELDS)
    + ["pose_matches", "pose_confidence", "body_orientation_ok", "required_regions_visible"],
    "additionalProperties": False,
}

_KEYFRAME_QA_PROMPT_TEMPLATE = (
    "The FIRST image is a GENERATED action-keyframe candidate of a pet. The "
    "following images are identity references. For a generated keyframe, the "
    "APPROVED CANONICAL is first and is the PRIMARY identity reference; any "
    "remaining real photos are secondary support. When evaluating reuse of the "
    "Canonical itself, the references are independent real photos only.\n"
    "Requested pose for this keyframe: {required_pose}\n"
    "Required visible regions: {required_visibility}\n"
    "Judge strictly:\n"
    "- same_pet: is the candidate recognizably the SAME individual as the approved "
    "Canonical, prioritizing face/head proportions, muzzle geometry, eye spacing, "
    "ear form, distinctive marking placement, tail form, and persistent morphology?\n"
    "- Report face_head_consistent, ear_muzzle_consistent, "
    "distinctive_markings_consistent, and persistent_morphology_consistent "
    "separately. Use confidence and all visible reference evidence.\n"
    "- presentation_difference_only=yes when apparent differences are limited to "
    "lighting, exposure, white balance, plausible richer coat presentation, sharper "
    "or cleaner fur, denoising, background normalization, or contrast. These "
    "enhancements must NOT make the pet a different individual.\n"
    "- Exact HSV/RGB, brightness, exposure, and minor bbox or pose-dependent body "
    "proportion changes are not identity failures. Do not use breed templates.\n"
    "- pose_matches: does the candidate actually show the requested pose (not the "
    "reference pose, not a different pose)?\n"
    "- body_orientation_ok: is the body orientation natural and appropriate for "
    "the requested pose?\n"
    "- required_regions_visible: are the required regions listed above visible?\n"
    "- required_regions_visible is visibility evidence, not by itself proof of a "
    "different pet or wrong pose.\n"
    "- anatomy_plausible: correct limb count, natural joints, no merged/extra parts; "
    "answer no only for a clear severe anatomy defect.\n"
    "- The candidate must contain one pet, no human, plain neutral background, no "
    "scene objects.\n"
    '- Use "unknown" whenever evidence is insufficient. Be conservative.'
)

_POSE_PROPERTIES = {
    key: KEYFRAME_QA_SCHEMA["properties"][key]
    for key in (
        "pose_matches",
        "pose_confidence",
        "body_orientation_ok",
        "required_regions_visible",
    )
}


def _keyframe_targeted_specs(
    *,
    questions: Sequence[str],
    required_pose: str,
    required_visibility: Sequence[str],
) -> dict[str, tuple[dict[str, Any], str]]:
    from . import vlm_escalation

    specs = _image_targeted_specs("KEYFRAME", questions)
    specs[vlm_escalation.POSE_VLM] = (
        _schema(_POSE_PROPERTIES),
        (
            "POSE_VLM only. The first image is a generated action-keyframe candidate. "
            f"Required pose: {required_pose}. Required visible regions: "
            f"{list(required_visibility)}. Decide only whether the requested pose is "
            "present, its body orientation is compatible, and the named regions are "
            "visible. Visibility uncertainty is unknown, not a pose contradiction. Do "
            "not reassess identity, coat/color, anatomy, motion, or presentation quality."
        ),
    )
    return specs


def qa_action_keyframe(
    candidate: bytes,
    references: Sequence[tuple[bytes, str]],
    *,
    required_pose: str,
    required_visibility: Sequence[str] = (),
    candidate_mime: str = "image/png",
    cache_mode: Optional[str] = None,
    tasks: Optional[Sequence[str]] = None,
    unresolved_questions: Sequence[str] = (),
    evidence_context: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """키프레임 후보 vs 정본/실제 레퍼런스 + 요구 포즈 — 구조화 QA. 실패/비활성 None."""
    if not is_enabled() or not candidate:
        return None

    images: list[tuple[bytes, str]] = [(candidate, candidate_mime)]
    images += [(data, (mime or "image/jpeg")) for data, mime in references[:MAX_IMAGES] if data]
    if tasks is not None:
        return _targeted_qa_calls(
            stage="KEYFRAME",
            images=images,
            tasks=tasks,
            questions=unresolved_questions,
            task_specs=_keyframe_targeted_specs(
                questions=unresolved_questions,
                required_pose=required_pose,
                required_visibility=required_visibility,
            ),
            params={
                "required_pose": required_pose,
                "required_visibility": list(required_visibility),
                "candidate_mime": candidate_mime,
                "evidence_context": dict(evidence_context or {}),
            },
            cache_mode=cache_mode,
        )
    prompt = _KEYFRAME_QA_PROMPT_TEMPLATE.format(
        required_pose=required_pose,
        required_visibility=", ".join(required_visibility) or "(none specified)",
    )
    content: list[dict[str, Any]] = [_image_block(data, mime) for data, mime in images]
    content.append({"type": "text", "text": prompt})

    def _call() -> Optional[dict[str, Any]]:
        result = _structured_qa_call(content, KEYFRAME_QA_SCHEMA, label="키프레임 QA")
        if result is not None:
            result["source"] = VLM_KEYFRAME_QA_VERSION
        return result

    return _cached_qa_call(
        kind=KIND_KEYFRAME_QA,
        version=VLM_KEYFRAME_QA_VERSION,
        images=images,
        params={
            "prompt": prompt,
            "required_pose": required_pose,
            "required_visibility": list(required_visibility),
            "candidate_mime": candidate_mime,
        },
        call=_call,
        cache_mode=cache_mode,
    )


# ══════════════════════════════════════════════════════════════════════════
# 모션 비디오 QA (Phase 6) — 샘플 프레임 시퀀스에 대한 확인
# ══════════════════════════════════════════════════════════════════════════

VLM_MOTION_QA_VERSION = "vlm-motion-qa-v3"

MOTION_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_pet_all_frames": _YNU,
        "anatomy_plausible_all_frames": _YNU,
        "requested_motion_occurs": _YNU,
        "locomotion_form_correct": _YNU,
        "direction_travel_correct": _YNU,
        "interaction_correct": _YNU,
        "human_hand_policy_ok": _YNU,
        "unintended_large_motion": _YNU,
        "single_pet": _YNU,
        "duplicated_pet": _YNU,
        "human_present": _YNU,
        "scene_cut": _YNU,
        "major_flicker": _YNU,
        "camera_stable": _YNU,
        "background_neutral": _YNU,
        "ends_in_target_pose": _YNU,
        "notes": {"type": "string"},
    },
    "required": [
        "same_pet_all_frames",
        "anatomy_plausible_all_frames",
        "requested_motion_occurs",
        "locomotion_form_correct",
        "direction_travel_correct",
        "interaction_correct",
        "human_hand_policy_ok",
        "unintended_large_motion",
        "single_pet",
        "duplicated_pet",
        "human_present",
        "scene_cut",
        "major_flicker",
        "camera_stable",
        "background_neutral",
        "ends_in_target_pose",
        "notes",
    ],
    "additionalProperties": False,
}

_MOTION_QA_PROMPT_TEMPLATE = (
    "You are judging a GENERATED pet motion video via {n} frames sampled in "
    "chronological order at fractions {fractions} of its duration. The LAST "
    "supplied image (after the sampled frames) is the pet's reference keyframe"
    "{target_note}.\n"
    "Requested motion: {motion_description}\nMotion class: {motion_class}\n"
    "Expected direction/travel: {expected_direction}\n"
    "Interaction type: {interaction_type}\n"
    "Human/hand policy: {human_hand_policy}\n"
    "Judge strictly across ALL frames:\n"
    "- same_pet_all_frames: identical individual pet in every frame (coat, "
    "markings, ears, face)?\n"
    "- anatomy_plausible_all_frames: correct limbs, no melting/merging/extra parts "
    "in any frame?\n"
    "- requested_motion_occurs: does the requested motion visibly happen?\n"
    "- locomotion_form_correct: for LOCOMOTION only, is the requested gait/motion "
    "recognizable and physically appropriate? Otherwise answer unknown.\n"
    "- direction_travel_correct: for LOCOMOTION only, does travel follow the expected "
    "direction and make appropriate positional progress? Otherwise answer unknown.\n"
    "- interaction_correct: for INTERACTION only, does the requested interaction "
    "actually occur correctly? Otherwise answer unknown.\n"
    "- human_hand_policy_ok: enforce the stated human/hand policy. An allowed hand "
    "does not permit a face, full person, extra body, or unrelated human contamination.\n"
    "  For MICRO BREATHING, compare the chronological sequence for a coherent "
    "chest/ribcage expansion-contraction or upper-torso rise-fall cycle while "
    "the paws and framing remain stable. Do not infer breathing from a single "
    "still image; answer unknown when the sequence itself is insufficient.\n"
    "- unintended_large_motion: any large movement beyond what was requested?\n"
    "- duplicated_pet / scene_cut / major_flicker / camera_stable / "
    "background_neutral: temporal and composition quality.\n"
    "- ends_in_target_pose: only when a target pose is specified{target_note2}; "
    'otherwise "unknown".\n'
    '- Use "unknown" whenever the frames are insufficient to judge. Be conservative.'
)


def _motion_targeted_specs(
    *,
    questions: Sequence[str],
    motion_description: str,
    motion_class: str,
    expected_direction: Optional[str],
    interaction_type: Optional[str],
    allow_generated_hand: bool,
    has_target: bool,
) -> dict[str, tuple[dict[str, Any], str]]:
    from . import vlm_escalation

    props = MOTION_QA_SCHEMA["properties"]
    identity_schema = _schema(
        {key: props[key] for key in ("same_pet_all_frames", "notes")}
    )
    anatomy_schema = _schema(
        {
            key: props[key]
            for key in (
                "anatomy_plausible_all_frames",
                "single_pet",
                "duplicated_pet",
                "human_present",
                "notes",
            )
        }
    )
    full = "full_stage_review" in questions
    motion_keys: set[str] = {"notes"}
    if full or "motion_correctness" in questions:
        motion_keys.add("requested_motion_occurs")
        if str(motion_class).upper() == "LOCOMOTION":
            motion_keys.update({"locomotion_form_correct", "direction_travel_correct"})
        if str(motion_class).upper() == "INTERACTION":
            motion_keys.update({"interaction_correct", "human_hand_policy_ok"})
        if has_target:
            motion_keys.add("ends_in_target_pose")
    if full or "temporal_integrity" in questions:
        motion_keys.update(
            {
                "unintended_large_motion",
                "scene_cut",
                "major_flicker",
                "camera_stable",
                "background_neutral",
            }
        )
    motion_schema = _schema({key: props[key] for key in props if key in motion_keys})
    common = (
        f"The supplied generated-video frames are chronological. Requested motion: "
        f"{motion_description}. Motion class: {motion_class}. "
    )
    return {
        vlm_escalation.IDENTITY_VLM: (
            identity_schema,
            common
            + "IDENTITY_VLM only. Decide whether the same individual pet persists across "
            "the frames. Do not judge anatomy, pose, motion correctness, camera, color, "
            "or presentation. Use unknown when sampled frames are insufficient.",
        ),
        vlm_escalation.ANATOMY_VLM: (
            anatomy_schema,
            common
            + "ANATOMY_VLM only. Check severe limb/joint/body corruption, pet duplication, "
            "and forbidden human contamination across frames. Do not judge identity, pose, "
            "requested motion, camera, color, or presentation. Use unknown when insufficient.",
        ),
        vlm_escalation.IDENTITY_ANATOMY_VLM: (
            _schema(
                {
                    key: props[key]
                    for key in (
                        "same_pet_all_frames",
                        "anatomy_plausible_all_frames",
                        "single_pet",
                        "duplicated_pet",
                        "human_present",
                        "notes",
                    )
                }
            ),
            common
            + "IDENTITY_ANATOMY_VLM only. The last supplied image is the approved "
            "reference keyframe of the pet. Answer two things across the chronological "
            "frames: (1) whether the same individual pet persists in every frame, and "
            "(2) whether anatomy stays plausible — no severe limb/joint/body corruption, "
            "no pet duplication, no forbidden human contamination. Do not judge whether "
            "the requested motion occurs, motion size, camera, color, or presentation. "
            "Use unknown when the sampled frames are insufficient.",
        ),
        vlm_escalation.MOTION_VLM: (
            motion_schema,
            common
            + f"MOTION_VLM only. Resolve only: {list(questions)}. Expected direction: "
            f"{expected_direction or 'as requested'}. Interaction: "
            f"{interaction_type or 'not applicable'}. Human/hand allowed: "
            f"{bool(allow_generated_hand)}. Do not reassess pet identity or anatomy. For "
            "BREATHING, deterministic temporal QA owns breathing correctness; do not "
            "override it. Use unknown where the sampled sequence is insufficient.",
        ),
    }


def qa_motion_video(
    frame_images: Sequence[tuple[bytes, str]],
    *,
    motion_description: str,
    motion_class: str,
    sample_fractions: Sequence[float],
    reference_image: Optional[tuple[bytes, str]] = None,
    target_image: Optional[tuple[bytes, str]] = None,
    expected_direction: Optional[str] = None,
    interaction_type: Optional[str] = None,
    allow_generated_hand: bool = False,
    cache_mode: Optional[str] = None,
    tasks: Optional[Sequence[str]] = None,
    unresolved_questions: Sequence[str] = (),
    evidence_context: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """샘플 프레임들 + 레퍼런스 → 구조화 모션 QA. 실패/비활성은 None."""
    if not is_enabled() or not frame_images:
        return None

    # v1 truncated to six frames. That aliases a roughly two-cycle/5-second
    # BREATHING clip and can omit both intermediate phases and the true last
    # frame. Nine v2 samples fit within the provider's image-input contract.
    supplied_frames = list(frame_images[:12])
    images: list[tuple[bytes, str]] = [
        (data, (mime or "image/png")) for data, mime in supplied_frames if data
    ]
    for extra in (reference_image, target_image):
        if extra and extra[0]:
            images.append((extra[0], (extra[1] or "image/png")))
    if not images:
        return None
    if tasks is not None:
        return _targeted_qa_calls(
            stage="MOTION",
            images=images,
            tasks=tasks,
            questions=unresolved_questions,
            task_specs=_motion_targeted_specs(
                questions=unresolved_questions,
                motion_description=motion_description,
                motion_class=motion_class,
                expected_direction=expected_direction,
                interaction_type=interaction_type,
                allow_generated_hand=allow_generated_hand,
                has_target=bool(target_image and target_image[0]),
            ),
            params={
                "motion_description": motion_description,
                "motion_class": motion_class,
                "expected_direction": expected_direction,
                "interaction_type": interaction_type,
                "allow_generated_hand": bool(allow_generated_hand),
                "sample_fractions": [float(f) for f in sample_fractions[: len(supplied_frames)]],
                "frame_count": len(supplied_frames),
                "has_reference": bool(reference_image and reference_image[0]),
                "has_target": bool(target_image and target_image[0]),
                "evidence_context": dict(evidence_context or {}),
            },
            cache_mode=cache_mode,
        )
    target_note = (
        "; the very last image is the TARGET pose keyframe" if target_image else ""
    )
    prompt = _MOTION_QA_PROMPT_TEMPLATE.format(
        n=len(supplied_frames),
        fractions=list(sample_fractions[: len(supplied_frames)]),
        motion_description=motion_description,
        motion_class=motion_class,
        expected_direction=(expected_direction or "as described by the requested motion"),
        interaction_type=(interaction_type or "not applicable"),
        human_hand_policy=(
            "a brief interaction hand is allowed; all other human contamination is forbidden"
            if allow_generated_hand
            else "no human or hand is allowed"
        ),
        target_note=target_note,
        target_note2=(" (last image)" if target_image else ""),
    )
    content: list[dict[str, Any]] = [_image_block(data, mime) for data, mime in images]
    content.append({"type": "text", "text": prompt})

    def _call() -> Optional[dict[str, Any]]:
        result = _structured_qa_call(content, MOTION_QA_SCHEMA, label="모션 QA")
        if result is not None:
            result["source"] = VLM_MOTION_QA_VERSION
        return result

    return _cached_qa_call(
        kind=KIND_MOTION_QA,
        version=VLM_MOTION_QA_VERSION,
        images=images,
        params={
            "prompt": prompt,
            "motion_description": motion_description,
            "motion_class": motion_class,
            "expected_direction": expected_direction,
            "interaction_type": interaction_type,
            "allow_generated_hand": bool(allow_generated_hand),
            "sample_fractions": [float(f) for f in sample_fractions[: len(supplied_frames)]],
            "frame_count": len(supplied_frames),
            "has_reference": bool(reference_image and reference_image[0]),
            "has_target": bool(target_image and target_image[0]),
        },
        call=_call,
        cache_mode=cache_mode,
    )
