"""
전시 대기열 — 스태프 접수 번호와 전시 표시 순서 (일반 펫 생성과 분리).

  스태프 폰/태블릿 → 사진 → 번호 배정(#018) → 처리 → READY → UP_NEXT → NOW_SHOWING → COMPLETE

── 두 상태는 따로다 ─────────────────────────────────────────────────────────
처리 상태 (exhibition_prep_runs.status 에서 파생 — 이 모듈은 쓰지 않는다)
  QUEUED · PROCESSING(RUNNING) · READY · FAILED(FAILED, NEEDS_REVIEW)
표시 상태 (display_status — 이 모듈만 쓴다)
  WAITING → UP_NEXT → NOW_SHOWING → COMPLETE

── 규칙 ─────────────────────────────────────────────────────────────────────
· 번호는 전시(exhibition_id)마다 1 부터 순차 (DB 카운터의 원자적 upsert).
· 표시 대기열 자격 = 처리 READY (+ 핸드오프가 켜져 있으면 HANDOFF_CONFIRMED —
  외부 기기가 아직 받지 못한 패키지를 "다음 순서" 로 부르지 않는다).
  처리 실패·검토 필요 건은 WAITING 에 머물 뿐 절대 UP_NEXT 가 되지 않는다.
· UP_NEXT 가 비어 있으면 자격 있는 WAITING 중 번호가 가장 작은 건이 UP_NEXT.
· SHOW NEXT (스태프 수동 — 외부 기기가 아직 DISPLAY_STARTED/FINISHED 를 보내지 않는다):
    NOW_SHOWING → COMPLETE,  UP_NEXT → NOW_SHOWING,  다음 자격 WAITING → UP_NEXT
  화면이 본 상태(expected_*)와 다르면 거절한다 — 두 번 누름·두 기기 동시 누름이
  한 칸 더 넘기지 않게.
· 누끼·맵·패키지·핸드오프는 다시 만들지 않는다. 처리 status 는 읽기만 한다.

동시성: 각 전이는 compare-and-set(update_if). DB 는 전시마다 UP_NEXT·NOW_SHOWING
을 한 건씩만 허용하는 부분 유니크 인덱스로 이중 안전장치를 둔다.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .exhibition_handoff_client import HandoffConfig
from .exhibition_prep_service import create_run
from .exhibition_prep_store import (
    DISPLAY_COMPLETE,
    DISPLAY_NOW_SHOWING,
    DISPLAY_UP_NEXT,
    DISPLAY_WAITING,
    HANDOFF_CONFIRMED,
    HANDOFF_FAILED,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_QUEUED,
    STATUS_READY,
    STATUS_RUNNING,
    ArtifactStore,
    RunStore,
)

logger = logging.getLogger(__name__)

PROCESSING_QUEUED = "QUEUED"
PROCESSING_PROCESSING = "PROCESSING"
PROCESSING_READY = "READY"
PROCESSING_FAILED = "FAILED"

PET_NAME_MAX = 40
#: 대기열 화면에서 한 번에 읽는 활성 건 상한.
ACTIVE_LIMIT = 300
#: 공개 화면 UP NEXT 목록 길이.
PUBLIC_UP_NEXT_LIMIT = 5
RECENT_COMPLETE_LIMIT = 5

_EXHIBITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ACTIVE = [DISPLAY_WAITING, DISPLAY_UP_NEXT, DISPLAY_NOW_SHOWING]


class QueueError(Exception):
    """대기열 요청 거절 — code 는 API detail.code, status 는 HTTP 상태."""

    def __init__(self, code: str, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 입력 정규화 ──────────────────────────────────────────────────────────────


def default_exhibition_id() -> str:
    return (os.getenv("EXHIBITION_DEFAULT_ID") or "default").strip() or "default"


def normalize_exhibition_id(value: Optional[str]) -> str:
    eid = (value or "").strip() or default_exhibition_id()
    if not _EXHIBITION_ID_RE.match(eid):
        raise QueueError("INVALID_EXHIBITION_ID", "exhibition_id must be 1-64 chars of A-Z a-z 0-9 _ -", status=400)
    return eid


def normalize_pet_name(value: Optional[str]) -> Optional[str]:
    name = " ".join((value or "").split())
    return name[:PET_NAME_MAX] or None


# ── 상태 파생 ────────────────────────────────────────────────────────────────


def processing_status(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status == STATUS_QUEUED:
        return PROCESSING_QUEUED
    if status == STATUS_RUNNING:
        return PROCESSING_PROCESSING
    if status == STATUS_READY:
        return PROCESSING_READY
    return PROCESSING_FAILED  # FAILED · NEEDS_REVIEW


def is_display_eligible(row: dict[str, Any], *, handoff_enabled: bool) -> bool:
    """READY 만 표시 대기열에 든다. 핸드오프가 켜져 있으면 외부 시스템 ACK 까지 받아야 한다."""
    if row.get("status") != STATUS_READY:
        return False
    return not handoff_enabled or row.get("handoff_status") == HANDOFF_CONFIRMED


def _handoff_enabled() -> bool:
    return HandoffConfig.from_env().enabled


def _attention_detail(row: dict[str, Any]) -> Optional[str]:
    if row.get("status") == STATUS_NEEDS_REVIEW:
        return "NEEDS_REVIEW: " + ", ".join(row.get("review_reasons") or [])
    if row.get("status") == STATUS_FAILED:
        return row.get("error_code") or "FAILED"
    if row.get("handoff_status") == HANDOFF_FAILED:
        return row.get("handoff_error_code") or HANDOFF_FAILED
    return None


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["id"],
        "queue_number": row.get("queue_number"),
        "pet_name": row.get("pet_name"),
        "created_at": row.get("created_at"),
        "display_status": row.get("display_status"),
        "processing_status": processing_status(row),
        "handoff_status": row.get("handoff_status"),
        "detail": _attention_detail(row),
    }


def _public_item(row: dict[str, Any]) -> dict[str, Any]:
    return {"queue_number": row.get("queue_number"), "pet_name": row.get("pet_name")}


def _by_display(rows: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    return sorted((r for r in rows if r.get("display_status") == status), key=lambda r: r.get("queue_number") or 0)


# ── 접수 ─────────────────────────────────────────────────────────────────────


def submit(
    raw: bytes,
    *,
    created_by: str,
    store: RunStore,
    artifacts: ArtifactStore,
    exhibition_id: Optional[str] = None,
    pet_name: Optional[str] = None,
) -> dict[str, Any]:
    """사진 1장 → 번호 배정 + QUEUED 실행 (display_status=WAITING). 처리는 기존 워커가 한다.

    잘못된 사진은 번호를 받기 전에 거절된다 (create_run 이 사진 검증 뒤에 번호를 받는다).
    """
    eid = normalize_exhibition_id(exhibition_id)
    return create_run(
        raw,
        created_by=created_by,
        store=store,
        artifacts=artifacts,
        exhibition_id=eid,
        allocate_queue_number=lambda: store.next_queue_number(eid),
        pet_name=normalize_pet_name(pet_name),
    )


# ── 전이 ─────────────────────────────────────────────────────────────────────


def _cas(store: RunStore, row: dict[str, Any], to: str) -> bool:
    try:
        return store.update_if(
            row["id"],
            {"display_status": to, "display_changed_at": _now()},
            match={"display_status": row.get("display_status")},
        )
    except Exception:  # noqa: BLE001 — 유니크 인덱스 위반 = 다른 요청이 먼저 채웠다
        logger.info("exhibition queue: %s → %s lost a race", row["id"], to, exc_info=True)
        return False


def promote_up_next(
    exhibition_id: str, *, store: RunStore, handoff_enabled: Optional[bool] = None
) -> Optional[dict[str, Any]]:
    """UP_NEXT 가 비어 있으면 자격 있는 WAITING 중 번호가 가장 작은 건을 올린다. 멱등."""
    enabled = _handoff_enabled() if handoff_enabled is None else handoff_enabled
    rows = store.list_queue(exhibition_id, [DISPLAY_WAITING, DISPLAY_UP_NEXT], limit=ACTIVE_LIMIT)
    if any(r.get("display_status") == DISPLAY_UP_NEXT for r in rows):
        return None
    for row in _by_display(rows, DISPLAY_WAITING):
        if not is_display_eligible(row, handoff_enabled=enabled):
            continue
        if _cas(store, row, DISPLAY_UP_NEXT):
            return dict(row, display_status=DISPLAY_UP_NEXT)
        return None  # 다른 요청이 먼저 승격했다
    return None


def show_next(
    exhibition_id: str,
    *,
    expected_now_showing: Optional[str],
    expected_up_next: Optional[str],
    store: RunStore,
    handoff_enabled: Optional[bool] = None,
) -> dict[str, Any]:
    """SHOW NEXT: NOW_SHOWING → COMPLETE, UP_NEXT → NOW_SHOWING, 다음 자격 WAITING → UP_NEXT.

    expected_* 는 스태프 화면이 본 run_id (없으면 None). 현재와 다르면 QUEUE_STATE_CHANGED.
    """
    enabled = _handoff_enabled() if handoff_enabled is None else handoff_enabled
    rows = store.list_queue(exhibition_id, [DISPLAY_UP_NEXT, DISPLAY_NOW_SHOWING], limit=10)
    showing = _by_display(rows, DISPLAY_NOW_SHOWING)
    up_next = _by_display(rows, DISPLAY_UP_NEXT)
    now_row = showing[0] if showing else None
    next_row = up_next[0] if up_next else None
    if (now_row or {}).get("id") != expected_now_showing or (next_row or {}).get("id") != expected_up_next:
        raise QueueError("QUEUE_STATE_CHANGED", "The queue changed since this screen loaded. Refresh and try again.")
    if now_row is None and next_row is None:
        raise QueueError("QUEUE_EMPTY", "Nothing is showing or up next.")

    # 현재 상영 건을 먼저 끝낸다 — 이 CAS 가 동시 요청의 잠금 역할을 한다.
    if now_row is not None and not _cas(store, now_row, DISPLAY_COMPLETE):
        raise QueueError("QUEUE_STATE_CHANGED", "Another device advanced the queue first.")
    if next_row is not None and not _cas(store, next_row, DISPLAY_NOW_SHOWING):
        logger.warning("exhibition queue %s: UP_NEXT %s could not move to NOW_SHOWING", exhibition_id, next_row["id"])
        raise QueueError("QUEUE_STATE_CHANGED", "Another device advanced the queue first.")
    promote_up_next(exhibition_id, store=store, handoff_enabled=enabled)
    return staff_view(exhibition_id, store=store, handoff_enabled=enabled, reconcile=False)


# ── 화면 ─────────────────────────────────────────────────────────────────────


def staff_view(
    exhibition_id: str,
    *,
    store: RunStore,
    handoff_enabled: Optional[bool] = None,
    reconcile: bool = True,
) -> dict[str, Any]:
    """스태프 화면: NOW SHOWING · UP NEXT · READY · PREPARING · NEEDS ATTENTION · 최근 COMPLETE.

    reconcile=True 면 먼저 비어 있는 UP_NEXT 를 채운다 (폴링이 안전망 역할).
    """
    enabled = _handoff_enabled() if handoff_enabled is None else handoff_enabled
    if reconcile:
        promote_up_next(exhibition_id, store=store, handoff_enabled=enabled)
    rows = store.list_queue(exhibition_id, _ACTIVE, limit=ACTIVE_LIMIT)
    done = store.list_queue(exhibition_id, [DISPLAY_COMPLETE], limit=RECENT_COMPLETE_LIMIT, newest_first=True)

    ready, preparing, attention = [], [], []
    for row in _by_display(rows, DISPLAY_WAITING):
        if is_display_eligible(row, handoff_enabled=enabled):
            ready.append(_item(row))
        elif _attention_detail(row):
            attention.append(_item(row))
        else:
            preparing.append(_item(row))  # QUEUED · PROCESSING · READY 인데 핸드오프 대기
    showing = _by_display(rows, DISPLAY_NOW_SHOWING)
    up_next = _by_display(rows, DISPLAY_UP_NEXT)
    return {
        "exhibition_id": exhibition_id,
        "now_showing": _item(showing[0]) if showing else None,
        "up_next": _item(up_next[0]) if up_next else None,
        "ready": ready,
        "preparing": preparing,
        "needs_attention": attention,
        "recently_complete": [_item(r) for r in done],
        "handoff_required": enabled,
    }


def public_view(
    exhibition_id: str, *, store: RunStore, handoff_enabled: Optional[bool] = None
) -> dict[str, Any]:
    """공개 화면: NOW SHOWING 한 건 + UP NEXT 목록(UP_NEXT 뒤에 자격 있는 WAITING). 읽기 전용.

    번호와 이름만 — run_id·처리 상태·오류는 내보내지 않는다.
    """
    enabled = _handoff_enabled() if handoff_enabled is None else handoff_enabled
    rows = store.list_queue(exhibition_id, _ACTIVE, limit=ACTIVE_LIMIT)
    showing = _by_display(rows, DISPLAY_NOW_SHOWING)
    upcoming = _by_display(rows, DISPLAY_UP_NEXT) + [
        r for r in _by_display(rows, DISPLAY_WAITING) if is_display_eligible(r, handoff_enabled=enabled)
    ]
    return {
        "now_showing": _public_item(showing[0]) if showing else None,
        "up_next": [_public_item(r) for r in upcoming[:PUBLIC_UP_NEXT_LIMIT]],
    }
