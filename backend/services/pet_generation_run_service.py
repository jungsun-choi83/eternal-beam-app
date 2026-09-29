"""Phase 7C/7D durable orchestration for one theme-independent BREATHING pipeline.

The phase services and their version/candidate tables remain authoritative. This
module persists only coordination state and lineage, calls those services in
order, and projects a QA PASS result through Phase 7A.

Phase 7D runs this coordinator only in a worker. Recoverable provider adapters
submit once, persist their external job ID, then yield until a later worker tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from . import (
    action_keyframe_service,
    canonical_image_providers,
    canonical_pet_service,
    durable_provider_jobs,
    motion_delivery_service,
    motion_publication_service,
    motion_spec,
    motion_video_service,
    pet_identity_service,
    pet_reference_service,
    pet_reference_set_service,
    premium_motion_finalization,
    video_motion_providers,
)

logger = logging.getLogger(__name__)

MOTION_BREATHING = "BREATHING"
REQUEST_FREE_HOME = "FREE_HOME"
#: Phase 7H — 기존 상용 상품의 이행 요청. 상거래 검증/예약은 premium_purchase 가
#: 이미 마친 뒤이고, 실행은 생성·QA·포장·이행 확정만 담당한다.
REQUEST_PREMIUM_PRODUCT = "PREMIUM_PRODUCT"

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_WAITING_PROVIDER = "WAITING_PROVIDER"
STATUS_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

STAGE_QUEUED = "QUEUED"
STAGE_IDENTITY = "IDENTITY"
STAGE_REFERENCE_SET = "REFERENCE_SET"
STAGE_CANONICAL = "CANONICAL"
STAGE_KEYFRAMES = "KEYFRAMES"
STAGE_MOTION_SPEC = "MOTION_SPEC"
STAGE_MOTION_GENERATION = "MOTION_GENERATION"
STAGE_QA = "QA"
#: Phase 7G — QA 통과/REVIEW 후보를 packed-alpha 파생물로 포장 (Phase 7F).
STAGE_DELIVERY = "DELIVERY"
STAGE_PUBLICATION = "PUBLICATION"
STAGE_PUBLISHED = "PUBLISHED"

#: 실행 파이프라인의 정본 순서 — WAITING_PROVIDER 깨어남에서 이미 지난 단계를
#: 다시 읽지 않기 위한 기준. current_stage 가 어떤 단계보다 "뒤"라는 것 자체가
#: 그 단계의 핀 필드가 유효하다는 보증이다: 그 핀을 지우는 모든 경로
#: (request_canonical_replacement_generation / request_keyframe_replacement_
#: generation 등)는 반드시 current_stage 를 그 단계로 되감고 나서만 핀을
#: 지운다 — 되감김 없이 그 단계를 앞서가는 current_stage 는 존재할 수 없다.
_STAGE_ORDER: tuple[str, ...] = (
    STAGE_IDENTITY,
    STAGE_REFERENCE_SET,
    STAGE_CANONICAL,
    STAGE_KEYFRAMES,
    STAGE_MOTION_SPEC,
    STAGE_MOTION_GENERATION,
    STAGE_QA,
    STAGE_DELIVERY,
    STAGE_PUBLICATION,
)


def _stage_index(stage: Optional[str]) -> int:
    """모르거나 QUEUED 인 단계는 -1 — 처음부터 실행하는 안전한 폴백이다."""
    try:
        return _STAGE_ORDER.index(stage)
    except ValueError:
        return -1


class PetGenerationRunError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


@dataclass(frozen=True)
class PetGenerationRun:
    id: str
    user_id: str
    pet_id: str
    content_id: str
    motion_id: str
    request_kind: str
    idempotency_key: str
    status: str
    current_stage: str
    identity_profile_id: Optional[str] = None
    identity_profile_version: Optional[int] = None
    reference_set_id: Optional[str] = None
    reference_set_version: Optional[int] = None
    canonical_version_id: Optional[str] = None
    canonical_version: Optional[int] = None
    keyframes: dict[str, Any] = field(default_factory=dict)
    motion_spec_version: Optional[str] = None
    motion_version_id: Optional[str] = None
    motion_version: Optional[int] = None
    selected_candidate_id: Optional[str] = None
    publication_id: Optional[str] = None
    provider_state: dict[str, Any] = field(default_factory=dict)
    last_error: Optional[dict[str, Any]] = None
    retry_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    worker_id: Optional[str] = None
    execution_token: Optional[str] = None
    lease_expires_at: Optional[str] = None
    next_attempt_at: Optional[str] = None
    #: Phase 7H — PREMIUM_PRODUCT 실행의 상거래 맥락. FREE_HOME 이면 전부 기본값.
    product_key: Optional[str] = None
    reservation_ledger_id: Optional[str] = None
    credits_reserved: int = 0
    #: lease 만료 후 워커가 이 실행을 다시 집어 간 횟수 (마지막 사용자 행동 이후).
    lease_recoveries: int = 0


_MOCK_RUNS: list[dict[str, Any]] = []
_LOCKS: dict[str, asyncio.Lock] = {}

#: 이 상태가 아니면(PUBLISHED/FAILED/CANCELLED) 실행은 종료된 것으로 본다 —
#: 종료된 실행은 같은 pet/motion/request_kind 의 새 시도를 막지 않는다.
ACTIVE_RUN_STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_WAITING_PROVIDER,
    STATUS_RECOVERY_REQUIRED,
)

#: 워커가 스스로 다시 집어 갈 수 있는 상태. RECOVERY_REQUIRED 는 "활성"이지만
#: 사용자/운영자 행동 없이는 절대 재개되지 않는다 — 여기 없다.
CLAIMABLE_RUN_STATUSES = (STATUS_QUEUED, STATUS_WAITING_PROVIDER, STATUS_RUNNING)

#: 더 이상 워커가 손대지 않는 상태. 여기서 나가는 유일한 길은
#: retry_generation_run() (사용자 행동) 뿐이다.
TERMINAL_RUN_STATUSES = (STATUS_PUBLISHED, STATUS_FAILED, STATUS_CANCELLED)

#: 사용자 행동으로 다시 QUEUED 가 될 수 있는 상태.
RETRYABLE_RUN_STATUSES = (STATUS_FAILED, STATUS_CANCELLED, STATUS_RECOVERY_REQUIRED)

#: 워커 lease 만료 후 자동 재점유(복구) 횟수 상한. 이 횟수를 넘긴 실행은 다시
#: 되살아나지 않고 FAILED(WORKER_RECOVERY_EXHAUSTED) 로 종료된다 — 사용자가
#: Retry 를 눌러야만 다시 QUEUED 가 된다. 카운터는 retry 에서만 0 으로 돌아간다.
ERROR_WORKER_RECOVERY_EXHAUSTED = "WORKER_RECOVERY_EXHAUSTED"
ERROR_RUN_CANCELLED = "RUN_CANCELLED"

#: start_generation_run() 의 확인-후-삽입 구간을 프로세스 내에서 직렬화한다 —
#: (user_id, pet_id, motion_id, request_kind) 별로 하나씩. 이것만으로는
#: 다중 프로세스/인스턴스를 못 막으므로 DB 쪽 부분 unique 인덱스
#: (마이그레이션 20261029)가 최종 보증이고, 이 락은 빠른 경로 + 테스트가 쓰는
#: in-memory 폴백(_MOCK_RUNS, DB 제약이 전혀 없음)의 정확성을 책임진다.
_START_LOCKS: dict[str, asyncio.Lock] = {}


def _start_lock(user_id: str, pet_id: str, motion_id: str, request_kind: str) -> asyncio.Lock:
    key = f"{user_id}:{pet_id}:{motion_id}:{request_kind}"
    lock = _START_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _START_LOCKS[key] = lock
    return lock


def __reset_for_tests() -> None:
    _MOCK_RUNS.clear()
    _LOCKS.clear()
    _START_LOCKS.clear()
    durable_provider_jobs.__reset_for_tests()


def _table() -> str:
    return os.getenv("PET_GENERATION_RUNS_TABLE", "pet_generation_runs")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_run(row: dict[str, Any]) -> PetGenerationRun:
    return PetGenerationRun(
        id=str(row.get("id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        content_id=str(row.get("content_id") or ""),
        motion_id=str(row.get("motion_id") or ""),
        request_kind=str(row.get("request_kind") or ""),
        idempotency_key=str(row.get("idempotency_key") or ""),
        status=str(row.get("status") or STATUS_QUEUED),
        current_stage=str(row.get("current_stage") or STAGE_QUEUED),
        identity_profile_id=(str(row["identity_profile_id"]) if row.get("identity_profile_id") else None),
        identity_profile_version=row.get("identity_profile_version"),
        reference_set_id=(str(row["reference_set_id"]) if row.get("reference_set_id") else None),
        reference_set_version=row.get("reference_set_version"),
        canonical_version_id=(str(row["canonical_version_id"]) if row.get("canonical_version_id") else None),
        canonical_version=row.get("canonical_version"),
        keyframes=dict(row.get("keyframes") or {}),
        motion_spec_version=(row.get("motion_spec_version") or None),
        motion_version_id=(str(row["motion_version_id"]) if row.get("motion_version_id") else None),
        motion_version=row.get("motion_version"),
        selected_candidate_id=(
            str(row["selected_candidate_id"]) if row.get("selected_candidate_id") else None
        ),
        publication_id=(str(row["publication_id"]) if row.get("publication_id") else None),
        provider_state=dict(row.get("provider_state") or {}),
        last_error=(dict(row["last_error"]) if isinstance(row.get("last_error"), dict) else None),
        retry_count=int(row.get("retry_count") or 0),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        updated_at=(str(row["updated_at"]) if row.get("updated_at") else None),
        completed_at=(str(row["completed_at"]) if row.get("completed_at") else None),
        worker_id=(str(row["worker_id"]) if row.get("worker_id") else None),
        execution_token=(str(row["execution_token"]) if row.get("execution_token") else None),
        lease_expires_at=(str(row["lease_expires_at"]) if row.get("lease_expires_at") else None),
        next_attempt_at=(str(row["next_attempt_at"]) if row.get("next_attempt_at") else None),
        product_key=(str(row["product_key"]) if row.get("product_key") else None),
        reservation_ledger_id=(
            str(row["reservation_ledger_id"]) if row.get("reservation_ledger_id") else None
        ),
        credits_reserved=int(row.get("credits_reserved") or 0),
        lease_recoveries=int(row.get("lease_recoveries") or 0),
    )


async def _row_by_id(run_id: str) -> Optional[dict[str, Any]]:
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.table(_table()).select("*").eq("id", run_id).limit(1).execute()
            rows = getattr(result, "data", None) or []
            return rows[0] if rows else None
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 확인하지 못했습니다.", status=503
            ) from exc
    return next((r for r in _MOCK_RUNS if str(r.get("id")) == run_id), None)


async def _row_by_key(
    *, user_id: str, pet_id: str, motion_id: str, request_kind: str, idempotency_key: str
) -> Optional[dict[str, Any]]:
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(_table())
                .select("*")
                .eq("user_id", user_id)
                .eq("pet_id", pet_id)
                .eq("motion_id", motion_id)
                .eq("request_kind", request_kind)
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            return rows[0] if rows else None
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 확인하지 못했습니다.", status=503
            ) from exc
    return next(
        (
            r
            for r in _MOCK_RUNS
            if r.get("user_id") == user_id
            and r.get("pet_id") == pet_id
            and r.get("motion_id") == motion_id
            and r.get("request_kind") == request_kind
            and r.get("idempotency_key") == idempotency_key
        ),
        None,
    )


async def _row_by_scope_active(
    *, user_id: str, pet_id: str, motion_id: str, request_kind: str
) -> Optional[dict[str, Any]]:
    """
    이 pet/motion/request_kind 의 유료 작업을 이미 맡고 있는 종료 전 실행 —
    idempotency_key 와 무관하다. start_generation_run() 이 동등한 동시 요청을
    합류시키는 대상이고, retry_generation_run()/replacement 계열이 옛 실행을
    되살리기 전에도 같은 확인을 거친다(다른 실행이 이미 활성이면 되살리지
    않고 그 실행에 합류한다).
    """
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(_table())
                .select("*")
                .eq("user_id", user_id)
                .eq("pet_id", pet_id)
                .eq("motion_id", motion_id)
                .eq("request_kind", request_kind)
                .in_("status", list(ACTIVE_RUN_STATUSES))
                .order("created_at")
                .limit(1)
                .execute()
            )
            rows = getattr(result, "data", None) or []
            return rows[0] if rows else None
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 확인하지 못했습니다.", status=503
            ) from exc
    candidates = [
        r
        for r in _MOCK_RUNS
        if r.get("user_id") == user_id
        and r.get("pet_id") == pet_id
        and r.get("motion_id") == motion_id
        and r.get("request_kind") == request_kind
        and r.get("status") in ACTIVE_RUN_STATUSES
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: str(r.get("created_at") or ""))


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "duplicate" in msg or "unique" in msg or "23505" in msg


async def _update(
    run_id: str, fields: dict[str, Any], *, execution_token: str | None = None
) -> PetGenerationRun:
    payload = {**fields, "updated_at": _now_iso()}
    client = _supabase() if _use_db() else None
    if client:
        try:
            query = client.table(_table()).update(payload).eq("id", run_id)
            if execution_token:
                query = query.eq("execution_token", execution_token)
            result = query.execute()
            if execution_token and not (getattr(result, "data", None) or []):
                raise PetGenerationRunError(
                    "WORKER_LEASE_LOST", "생성 실행 lease 소유권을 잃었습니다.", status=409
                )
        except PetGenerationRunError:
            # lease 유실은 실제 신호다(취소/다른 워커의 인수) — 503 으로 뭉개면
            # _execute 가 그 실행을 다시 FAILED 로 덮어쓰려 든다.
            raise
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행 상태를 저장하지 못했습니다.", status=503
            ) from exc
    else:
        row = next((r for r in _MOCK_RUNS if r.get("id") == run_id), None)
        if not row:
            raise PetGenerationRunError("GENERATION_RUN_NOT_FOUND", "생성 실행이 없습니다.", status=404)
        if execution_token and row.get("execution_token") != execution_token:
            raise PetGenerationRunError(
                "WORKER_LEASE_LOST", "생성 실행 lease 소유권을 잃었습니다.", status=409
            )
        row.update(payload)
    refreshed = await _row_by_id(run_id)
    if not refreshed:
        raise PetGenerationRunError("GENERATION_RUN_NOT_FOUND", "생성 실행이 없습니다.", status=404)
    return _to_run(refreshed)


async def _progress(run: PetGenerationRun, fields: dict[str, Any]) -> PetGenerationRun:
    if not run.execution_token:
        raise PetGenerationRunError("WORKER_LEASE_REQUIRED", "worker lease 가 필요합니다.", status=409)
    return await _update(run.id, fields, execution_token=run.execution_token)


async def _transition(
    run_id: str, fields: dict[str, Any], *, from_statuses: tuple[str, ...]
) -> Optional[PetGenerationRun]:
    """
    사용자 행동(취소/재시도)의 상태 전이 — 워커 토큰 없이, 그러나 **현재 상태가
    from_statuses 안일 때만** 쓴다. 읽기와 쓰기 사이에 워커가 발행을 끝냈다면
    (PUBLISHED) 아무것도 덮어쓰지 않고 None 을 돌려준다.
    """
    payload = {**fields, "updated_at": _now_iso()}
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = (
                client.table(_table())
                .update(payload)
                .eq("id", run_id)
                .in_("status", list(from_statuses))
                .execute()
            )
            rows = getattr(result, "data", None) or []
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행 상태를 저장하지 못했습니다.", status=503
            ) from exc
        if not rows:
            return None
    else:
        row = next((r for r in _MOCK_RUNS if r.get("id") == run_id), None)
        if not row or row.get("status") not in from_statuses:
            return None
        row.update(payload)
    refreshed = await _row_by_id(run_id)
    if not refreshed:
        raise PetGenerationRunError("GENERATION_RUN_NOT_FOUND", "생성 실행이 없습니다.", status=404)
    return _to_run(refreshed)


async def _insert_or_get(row: dict[str, Any]) -> tuple[PetGenerationRun, bool]:
    """
    실행을 새로 만들거나, 이미 같은 유료 작업을 맡고 있는 실행에 합류한다.

    두 단계로 찾는다: (1) 정확히 같은 idempotency_key — 기존 재시도 동작
    그대로 유지, (2) 없으면 같은 (user_id, pet_id, motion_id, request_kind)
    의 **활성** 실행 — 호출자의 idempotency_key 가 서로 달라도 같은 논리적
    작업이면 새로 만들지 않고 합류한다. 두 확인 모두 통과해야 삽입하고,
    그마저도 동시 요청과 경합해 질 수 있으므로 실패는 항상 재조회 후
    판정한다 — 절대 맹목적으로 재제출하지 않는다.
    """
    existing = await _row_by_key(
        user_id=row["user_id"], pet_id=row["pet_id"], motion_id=row["motion_id"],
        request_kind=row["request_kind"], idempotency_key=row["idempotency_key"],
    )
    if existing:
        return _to_run(existing), False

    active = await _row_by_scope_active(
        user_id=row["user_id"], pet_id=row["pet_id"],
        motion_id=row["motion_id"], request_kind=row["request_kind"],
    )
    if active:
        return _to_run(active), False

    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.table(_table()).insert(row).execute()
            rows = getattr(result, "data", None) or []
            if rows:
                return _to_run(rows[0]), True
        except Exception as exc:
            # A concurrent request can win either the idempotency-key unique
            # constraint or the active-scope partial unique index (migration
            # 20261029). Re-read under both before classifying it as a real
            # persistence failure.
            if not _is_unique_violation(exc):
                raise PetGenerationRunError(
                    "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 저장하지 못했습니다.", status=503
                ) from exc
            existing = await _row_by_key(
                user_id=row["user_id"], pet_id=row["pet_id"], motion_id=row["motion_id"],
                request_kind=row["request_kind"], idempotency_key=row["idempotency_key"],
            )
            if existing:
                return _to_run(existing), False
            active = await _row_by_scope_active(
                user_id=row["user_id"], pet_id=row["pet_id"],
                motion_id=row["motion_id"], request_kind=row["request_kind"],
            )
            if active:
                return _to_run(active), False
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 저장하지 못했습니다.", status=503
            ) from exc
    else:
        _MOCK_RUNS.append(dict(row))
        return _to_run(row), True

    existing = await _row_by_id(str(row["id"]))
    if not existing:
        raise PetGenerationRunError(
            "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 저장하지 못했습니다.", status=503
        )
    return _to_run(existing), True


def _lease_seconds() -> int:
    return max(60, int(os.getenv("GENERATION_RUN_LEASE_SECONDS", "300")))


def _max_lease_recoveries() -> int:
    """lease 만료 후 자동 재점유 상한. 0 이면 한 번 죽은 RUNNING 은 바로 FAILED."""
    return max(0, int(os.getenv("GENERATION_RUN_MAX_LEASE_RECOVERIES", "2")))


def _recovery_exhausted(run: PetGenerationRun) -> bool:
    """claim 이 상한을 넘겨 건네준 실행 — 일을 시키지 않고 종료시켜야 한다."""
    return run.lease_recoveries > _max_lease_recoveries()


async def _claim_next(worker_id: str) -> Optional[PetGenerationRun]:
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.rpc(
                "claim_next_pet_generation_run",
                {
                    "p_worker_id": worker_id,
                    "p_lease_seconds": _lease_seconds(),
                    "p_max_lease_recoveries": _max_lease_recoveries(),
                },
            ).execute()
            data = getattr(result, "data", None) or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            claimed = bool(data.get("claimed")) if isinstance(data, dict) else False
            row = data.get("run") if isinstance(data, dict) else None
            if not claimed:
                return None
            if isinstance(row, dict):
                return _to_run(row)
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 점유하지 못했습니다.", status=503
            ) from exc
        return None

    # In-memory mirror of claim_next_pet_generation_run() (migration 20261032):
    # same predicate, same cap, same ordering, same self-heal.
    now = datetime.now(timezone.utc)
    max_recoveries = _max_lease_recoveries()
    eligible = []
    for row in _MOCK_RUNS:
        status = row.get("status")
        lease = row.get("lease_expires_at")
        lease_expired = False
        if status == STATUS_RUNNING and lease:
            try:
                lease_expired = datetime.fromisoformat(str(lease).replace("Z", "+00:00")) <= now
            except ValueError:
                lease_expired = True
        recoveries = int(row.get("lease_recoveries") or 0)
        if lease_expired and recoveries > max_recoveries:
            # Self-heal: the exhausted hand-out itself died. Never claim again.
            row.update(
                {
                    "status": STATUS_FAILED,
                    "last_error": {
                        "stage": row.get("current_stage"),
                        "code": ERROR_WORKER_RECOVERY_EXHAUSTED,
                        "message": "worker lease expired too many times; manual retry required",
                        "lease_recoveries": recoveries,
                        "at": _now_iso(),
                    },
                    "execution_token": None,
                    "lease_expires_at": None,
                    "worker_id": None,
                    "next_attempt_at": None,
                    "completed_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            )
            continue
        next_attempt = row.get("next_attempt_at")
        due = True
        if status == STATUS_WAITING_PROVIDER and next_attempt:
            try:
                due = datetime.fromisoformat(str(next_attempt).replace("Z", "+00:00")) <= now
            except ValueError:
                due = True
        if (
            status == STATUS_QUEUED
            or (status == STATUS_WAITING_PROVIDER and due)
            or (lease_expired and recoveries <= max_recoveries)
        ):
            eligible.append(row)
    if not eligible:
        return None

    def _rank(row: dict[str, Any]) -> int:
        if row.get("status") == STATUS_WAITING_PROVIDER:
            return 0
        if row.get("status") == STATUS_QUEUED:
            return 1
        return 2  # stale-lease RUNNING recovery goes last — never ahead of fresh intent

    current = min(
        eligible,
        key=lambda row: (
            _rank(row),
            str(row.get("updated_at") or ""),
            str(row.get("created_at") or ""),
        ),
    )
    was_recovery = current.get("status") == STATUS_RUNNING
    token = str(uuid.uuid4())
    lease_until = datetime.fromtimestamp(now.timestamp() + _lease_seconds(), timezone.utc).isoformat()
    current.update(
        {
            "status": STATUS_RUNNING,
            "worker_id": worker_id,
            "execution_token": token,
            "lease_expires_at": lease_until,
            "lease_recoveries": int(current.get("lease_recoveries") or 0) + (1 if was_recovery else 0),
            "next_attempt_at": None,
            "updated_at": _now_iso(),
        }
    )
    return _to_run(current)


async def _heartbeat(run: PetGenerationRun) -> PetGenerationRun:
    if not run.execution_token:
        raise PetGenerationRunError("WORKER_LEASE_REQUIRED", "worker lease 가 필요합니다.", status=409)
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.rpc(
                "heartbeat_pet_generation_run",
                {
                    "p_run_id": run.id,
                    "p_execution_token": run.execution_token,
                    "p_lease_seconds": _lease_seconds(),
                },
            ).execute()
            if getattr(result, "data", False) is not True:
                raise PetGenerationRunError(
                    "WORKER_LEASE_LOST", "생성 실행 lease 소유권을 잃었습니다.", status=409
                )
        except PetGenerationRunError:
            raise
        except Exception as exc:
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "worker heartbeat 를 저장하지 못했습니다.", status=503
            ) from exc
    else:
        row = next((item for item in _MOCK_RUNS if item.get("id") == run.id), None)
        if not row or row.get("execution_token") != run.execution_token:
            raise PetGenerationRunError(
                "WORKER_LEASE_LOST", "생성 실행 lease 소유권을 잃었습니다.", status=409
            )
        row["lease_expires_at"] = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + _lease_seconds(), timezone.utc
        ).isoformat()
        row["updated_at"] = _now_iso()
    refreshed = await _row_by_id(run.id)
    return _to_run(refreshed) if refreshed else run


class _LeaseHeartbeater:
    """Keep a worker lease alive while synchronous provider/storage/QA code runs."""

    def __init__(self, run: PetGenerationRun):
        self.run = run
        self.stop_event = threading.Event()
        self.lost = False
        self.thread: threading.Thread | None = None

    def _pulse(self) -> None:
        interval = max(5.0, _lease_seconds() / 3.0)
        while not self.stop_event.wait(interval):
            try:
                client = _supabase() if _use_db() else None
                if client:
                    result = client.rpc(
                        "heartbeat_pet_generation_run",
                        {
                            "p_run_id": self.run.id,
                            "p_execution_token": self.run.execution_token,
                            "p_lease_seconds": _lease_seconds(),
                        },
                    ).execute()
                    if getattr(result, "data", False) is not True:
                        self.lost = True
                        return
                else:
                    row = next(
                        (item for item in _MOCK_RUNS if item.get("id") == self.run.id), None
                    )
                    if not row or row.get("execution_token") != self.run.execution_token:
                        self.lost = True
                        return
                    now = datetime.now(timezone.utc)
                    row["lease_expires_at"] = datetime.fromtimestamp(
                        now.timestamp() + _lease_seconds(), timezone.utc
                    ).isoformat()
            except Exception:
                # Do not abandon the running call immediately on one transient
                # heartbeat error. The fencing token is checked on every run update.
                time.sleep(min(1.0, interval / 2.0))

    def __enter__(self):
        self.thread = threading.Thread(target=self._pulse, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)


async def _validate_intake(user_id: str, pet_id: str) -> str:
    try:
        refs = await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as exc:
        raise PetGenerationRunError(exc.code, exc.message, status=exc.status) from exc

    ready, original, cutout = pet_reference_service.intake_readiness(refs)
    if not ready or not original or not cutout:
        raise PetGenerationRunError(
            "PHASE1_INTAKE_INCOMPLETE",
            "Phase 7B 원본과 연결된 누끼가 모두 준비되어야 합니다.",
            status=409,
        )
    expected_pet_id = pet_reference_service.pet_id_for_content(original.content_id)
    if (
        original.user_id != user_id
        or original.pet_id != pet_id
        or expected_pet_id != pet_id
        or cutout.user_id != user_id
        or cutout.pet_id != pet_id
        or cutout.content_id != original.content_id
        or cutout.parent_reference_id != original.id
    ):
        raise PetGenerationRunError(
            "PHASE1_IDENTITY_MISMATCH", "Phase 1 원본과 누끼의 신원 연결이 일치하지 않습니다.", status=409
        )
    return original.content_id


def _phase_error(stage: str, exc: Exception) -> dict[str, Any]:
    error = {
        "stage": stage,
        "code": str(getattr(exc, "code", type(exc).__name__)),
        "message": str(getattr(exc, "message", str(exc)))[:1000],
        "provider_recovery_required": getattr(exc, "code", "") == "PROVIDER_RECOVERY_REQUIRED",
        "at": _now_iso(),
    }
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        # e.g. {"keyframe_role": "LIE"} — which of two keyframe stages a
        # KEYFRAME_QA_REVIEW failure came from, so the replacement request can
        # target the right one.
        error.update(details)
    return error


def _next_poll_iso() -> str:
    delay = max(0.0, float(os.getenv("GENERATION_PROVIDER_POLL_SECONDS", "10")))
    now = datetime.now(timezone.utc)
    return datetime.fromtimestamp(now.timestamp() + delay, timezone.utc).isoformat()


def _provider_state(run: PetGenerationRun) -> dict[str, Any]:
    """Refresh receipt summaries without discarding durable operator intent."""
    state = durable_provider_jobs.summary_for_run(run.id)
    operator = dict(run.provider_state.get("_operator") or {})
    if operator:
        state["_operator"] = operator
    return state


async def _reconcile_premium_after_stop(stopped: PetGenerationRun) -> None:
    # ── Phase 7H — 상용 실행의 종료 되돌림 판정 ──────────────────────────────
    # 레거시 세션의 예약 분기와 같은 정책(READY 하나라도 있으면 유지, 진행 중이면
    # 유예, 예약은 환불이 아니라 **해제**)을 실행용으로 옮긴 함수 하나를 부른다.
    # 판정 실패는 실행 상태를 바꾸지 못한다 — 다음 종료/재시도가 다시 판정한다.
    if stopped.request_kind != REQUEST_PREMIUM_PRODUCT:
        return
    try:
        from . import premium_run_fulfillment

        await premium_run_fulfillment.reconcile_failed_run(
            user_id=stopped.user_id,
            pet_id=stopped.pet_id,
            motion_id=stopped.motion_id,
            reservation_ledger_id=stopped.reservation_ledger_id,
        )
    except Exception:
        logger.exception(
            "프리미엄 실행 종료 되돌림 판정 실패 — 다음 종료에서 재판정 (run=%s)", stopped.id
        )


async def _fail(run: PetGenerationRun, stage: str, exc: Exception) -> PetGenerationRun:
    try:
        failed = await _progress(
            run,
            {
                "status": STATUS_FAILED,
                "current_stage": stage,
                "last_error": _phase_error(stage, exc),
                "provider_state": _provider_state(run),
                "execution_token": None,
                "lease_expires_at": None,
                "worker_id": None,
                "next_attempt_at": None,
                "completed_at": _now_iso(),
            },
        )
    except PetGenerationRunError as lease_exc:
        if lease_exc.code in ("WORKER_LEASE_LOST", "WORKER_LEASE_REQUIRED"):
            # 우리 lease 는 이미 끝났다 — 사용자가 취소했거나 다른 워커가 인수했다.
            # 그쪽의 상태(CANCELLED / RUNNING …)가 정본이므로 덮어쓰지 않는다.
            current = await _row_by_id(run.id)
            return _to_run(current) if current else run
        raise
    await _reconcile_premium_after_stop(failed)
    return failed


def _image_providers(run: PetGenerationRun, operation: str):
    # 정본과 키프레임은 각자의 env 순서를 쓴다 — 여기서 갈린다.
    providers = (
        canonical_image_providers.resolve_keyframe_providers()
        if operation == durable_provider_jobs.OP_KEYFRAME
        else canonical_image_providers.resolve_providers()
    )
    durable = durable_provider_jobs.durable_image_providers(
        providers,
        run_id=run.id,
        user_id=run.user_id,
        pet_id=run.pet_id,
        operation=operation,
    )
    if not durable:
        raise PetGenerationRunError(
            "DURABLE_PROVIDER_NOT_CONFIGURED",
            "재개 가능한 이미지 provider 가 설정되지 않았습니다.",
            status=503,
        )
    return durable


def _video_providers(run: PetGenerationRun, motion_id: str):
    try:
        providers = video_motion_providers.resolve_provider_order(
            list(motion_spec.provider_order_for_motion(motion_id))
        )
    except video_motion_providers.VideoProviderError as exc:
        raise PetGenerationRunError(exc.code, exc.message, status=503) from exc
    durable = durable_provider_jobs.durable_video_providers(
        providers, run_id=run.id, user_id=run.user_id, pet_id=run.pet_id
    )
    if not durable:
        raise PetGenerationRunError(
            "DURABLE_PROVIDER_NOT_CONFIGURED",
            "재개 가능한 BREATHING 비디오 provider 가 설정되지 않았습니다. "
            "Phase 7D 는 Runway Seedance task API 만 지원합니다.",
            status=503,
        )
    return durable


async def _identity(run: PetGenerationRun):
    if run.identity_profile_id and run.identity_profile_version:
        profile = await pet_identity_service.get_profile(
            user_id=run.user_id, pet_id=run.pet_id, version=run.identity_profile_version
        )
        if not profile or str(profile.id) != run.identity_profile_id:
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "저장된 신원 프로필을 찾지 못했습니다.", status=409)
        return profile
    return await pet_identity_service.build_identity_profile(
        user_id=run.user_id, pet_id=run.pet_id, skip_if_unchanged=True
    )


async def _reference_set(run: PetGenerationRun):
    if run.reference_set_id and run.reference_set_version:
        refset = await pet_reference_set_service.get_set(
            user_id=run.user_id, pet_id=run.pet_id, version=run.reference_set_version
        )
        if not refset or str(refset.id) != run.reference_set_id:
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "저장된 레퍼런스 세트를 찾지 못했습니다.", status=409)
        return refset
    return await pet_reference_set_service.build_reference_set(
        user_id=run.user_id, pet_id=run.pet_id, skip_if_unchanged=True
    )


async def _canonical(run: PetGenerationRun):
    if run.canonical_version_id and run.canonical_version:
        canonical = await canonical_pet_service.get_canonical(
            user_id=run.user_id, pet_id=run.pet_id, version=run.canonical_version
        )
        if not canonical or canonical.id != run.canonical_version_id:
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "저장된 canonical 버전을 찾지 못했습니다.", status=409)
        return canonical, run

    latest = await canonical_pet_service.get_canonical(user_id=run.user_id, pet_id=run.pet_id)
    operator_state = dict(run.provider_state.get("_operator") or {})
    replacement = dict(operator_state.get("canonical_replacement_request") or {})
    replacement_source = str(replacement.get("source_canonical_version_id") or "")
    if latest and (
        str(latest.reference_set_id or "") == str(run.reference_set_id or "")
        and latest.reference_set_version == run.reference_set_version
    ):
        # An explicit REVIEW replacement request deliberately refuses to reuse
        # its source version. Once the worker has built the next version,
        # normal durable resume takes over via the pinned-version branch above.
        if latest.id != replacement_source and latest.status != canonical_pet_service.STATUS_BUILDING:
            return latest, run
    canonical = await canonical_pet_service.build_canonical(
        user_id=run.user_id,
        pet_id=run.pet_id,
        providers=_image_providers(run, durable_provider_jobs.OP_CANONICAL),
        skip_if_unchanged=not bool(latest and latest.id == replacement_source),
    )
    return canonical, run


async def _keyframe(run: PetGenerationRun, role: str, *, allow_canonical_reuse: bool = False):
    saved = dict(run.keyframes.get(role) or {})
    if saved.get("id") and saved.get("version"):
        keyframe = await action_keyframe_service.get_keyframe(
            user_id=run.user_id, pet_id=run.pet_id, keyframe_role=role, version=int(saved["version"])
        )
        if not keyframe or keyframe.id != str(saved["id"]):
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "저장된 키프레임을 찾지 못했습니다.", status=409)
        return keyframe, run

    latest = await action_keyframe_service.get_keyframe(
        user_id=run.user_id, pet_id=run.pet_id, keyframe_role=role
    )
    operator_state = dict(run.provider_state.get("_operator") or {})
    replacements = dict(operator_state.get("keyframe_replacement_requests") or {})
    replacement = dict(replacements.get(role) or {})
    replacement_source = str(replacement.get("source_keyframe_id") or "")
    if latest and str(latest.canonical_version_id or "") == str(run.canonical_version_id or ""):
        # See _canonical(): an explicit REVIEW replacement request refuses to
        # reuse its source version until the worker builds the next one.
        if latest.id != replacement_source and latest.status != action_keyframe_service.STATUS_BUILDING:
            return latest, run
    # An explicit replacement request for this role must always bypass the
    # Canonical-reuse shortcut and go through real keyframe generation — an
    # operator asking for a fresh candidate should never get an alias of the
    # very Canonical the replacement may be trying to move away from.
    is_replacement_build = bool(latest and latest.id == replacement_source)
    keyframe = await action_keyframe_service.build_keyframe(
        user_id=run.user_id,
        pet_id=run.pet_id,
        keyframe_role=role,
        providers=_image_providers(run, durable_provider_jobs.OP_KEYFRAME),
        skip_if_unchanged=not is_replacement_build,
        allow_canonical_reuse=allow_canonical_reuse and not is_replacement_build,
    )
    return keyframe, run


def _motion_matches(run: PetGenerationRun, motion: Any) -> bool:
    # 시작 키프레임 역할은 모션마다 다르다 (LIE_IDLE/STAND_UP 은 LIE) —
    # NEUTRAL_IDLE 하드코딩은 BREATHING 전용 시절의 잔재였다.
    spec = motion_spec.get_motion(run.motion_id)
    start_role = spec.start_keyframe_role if spec else "NEUTRAL_IDLE"
    start_keyframe = run.keyframes.get(start_role) or {}
    return bool(
        motion
        and motion.pet_id == run.pet_id
        and motion.user_id == run.user_id
        and motion.motion_id == run.motion_id
        and motion.motion_spec_version == run.motion_spec_version
        and str(motion.start_keyframe_id or "") == str(start_keyframe.get("id") or "")
        and motion.start_keyframe_version == start_keyframe.get("version")
        and str(motion.canonical_version_id or "") == str(run.canonical_version_id or "")
    )


async def _motion(run: PetGenerationRun, contract: Optional[dict[str, Any]] = None):
    if run.motion_version_id:
        motion = await motion_video_service.get_motion_version(
            user_id=run.user_id,
            pet_id=run.pet_id,
            motion_id=run.motion_id,
            version=run.motion_version,
        )
        if not motion or motion.id != run.motion_version_id or not _motion_matches(run, motion):
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "저장된 모션 버전을 찾지 못했습니다.", status=409)
        return motion, run

    latest = await motion_video_service.get_motion_version(
        user_id=run.user_id, pet_id=run.pet_id, motion_id=run.motion_id
    )
    operator_state = dict(run.provider_state.get("_operator") or {})
    replacement = dict(operator_state.get("replacement_request") or {})
    replacement_source = str(replacement.get("source_motion_version_id") or "")
    if latest and _motion_matches(run, latest):
        # A REVIEW replacement request deliberately refuses to reuse its source
        # version. Once the worker has created the next building version, normal
        # durable resume takes over and the existing external job ID is reused.
        if latest.id != replacement_source and latest.status != motion_video_service.STATUS_BUILDING:
            return latest, run
    spec = motion_spec.get_motion(run.motion_id)
    if not spec:
        raise PetGenerationRunError("UNSUPPORTED_MOTION", "BREATHING 모션 스펙이 없습니다.", status=422)
    motion = await motion_video_service.build_motion_video(
        user_id=run.user_id,
        pet_id=run.pet_id,
        motion_id=run.motion_id,
        providers=_video_providers(run, spec.motion_id),
        skip_if_unchanged=not bool(latest and latest.id == replacement_source),
        precomputed_contract=contract,
    )
    return motion, run


def _require_status(actual: str, expected: str, code: str, message: str) -> None:
    if actual != expected:
        raise PetGenerationRunError(code, message, status=409)


async def _advance_keyframe_stage(
    run: PetGenerationRun,
    role: str,
    keyframe: Any,
    keyframes: dict[str, Any],
) -> PetGenerationRun:
    """
    Pin one keyframe role's version before judging its status (mirrors
    _canonical()/_motion()) so a REVIEW keyframe is remembered across retries —
    _keyframe() then takes the pinned-version branch and never calls
    build_keyframe() again for the same version. Raises a distinct, recoverable
    KEYFRAME_QA_REVIEW (never the generic NOT_COMPLETE) when the role is
    REVIEW, since that state needs a human decision, not a repeated failure.
    """
    provider_state = _provider_state(run)
    operator_state = dict(provider_state.get("_operator") or {})
    replacements = dict(operator_state.get("keyframe_replacement_requests") or {})
    replacement = dict(replacements.get(role) or {})
    if replacement and str(replacement.get("source_keyframe_id") or "") != keyframe.id:
        replacement.update(
            {
                "status": "GENERATED",
                "replacement_keyframe_id": keyframe.id,
                "completed_at": _now_iso(),
            }
        )
        replacements[role] = replacement
        operator_state["keyframe_replacement_requests"] = replacements
        provider_state["_operator"] = operator_state
    keyframes[role] = {
        "id": keyframe.id,
        "version": keyframe.version,
        "selected_candidate_id": keyframe.selected_candidate_id,
        "canonical_version_id": keyframe.canonical_version_id,
    }
    run = await _progress(run, {"keyframes": keyframes, "provider_state": provider_state})
    if keyframe.status == action_keyframe_service.STATUS_REVIEW:
        raise PetGenerationRunError(
            "KEYFRAME_QA_REVIEW",
            f"Phase 5 키프레임({role}) 후보가 사람 검토를 요구합니다 — "
            "재구매 없이 QA 재실행 또는 명시적 교체 요청으로만 진행됩니다.",
            status=409,
            details={"keyframe_role": role},
        )
    _require_status(
        keyframe.status, action_keyframe_service.STATUS_COMPLETE,
        "KEYFRAME_NOT_COMPLETE", "Phase 5 키프레임 QA PASS 결과가 없습니다.",
    )
    if (
        str(keyframe.canonical_version_id or "") != str(run.canonical_version_id or "")
        or keyframe.canonical_version != run.canonical_version
    ):
        raise PetGenerationRunError("RUN_LINEAGE_INVALID", "키프레임의 canonical 이 실행과 다릅니다.", status=409)
    return run


async def _execute(run: PetGenerationRun) -> PetGenerationRun:
    stage = run.current_stage or STAGE_QUEUED
    # 재개 지점: WAITING_PROVIDER 깨어남은 process_next_generation_run 을 통해
    # 이 함수를 처음부터가 아니라 여기서 다시 부른다. current_stage 가 어떤
    # 단계보다 뒤에 있으면 그 단계는 이미 끝나서 핀됐다는 뜻이다 — 그 핀을
    # 지우는 모든 경로(교체 요청 등)는 반드시 current_stage 를 그 단계로
    # 되감고 나서만 지운다. 그래서 이미 지난 단계는 다시 읽지도 검증하지도
    # 않는다. 다만 핀 필드가 실제로는 비어 있으면(비정상/불일치 상태) 그
    # 단계는 안전하게 처음부터 다시 돈다 — 검증되지 않은 상태를 믿지 않는다.
    resume_from = _stage_index(stage)
    try:
        if resume_from <= _stage_index(STAGE_IDENTITY) or not (
            run.identity_profile_id and run.identity_profile_version
        ):
            stage = STAGE_IDENTITY
            run = await _progress(run, {"current_stage": stage})
            run = await _heartbeat(run)
            profile = await _identity(run)
            _require_status(
                profile.status, pet_identity_service.STATUS_COMPLETE,
                "IDENTITY_NOT_COMPLETE", "Phase 2 신원 프로필이 complete 상태가 아닙니다.",
            )
            run = await _progress(
                run,
                {"identity_profile_id": profile.id, "identity_profile_version": profile.version},
            )

        if resume_from <= _stage_index(STAGE_REFERENCE_SET) or not (
            run.reference_set_id and run.reference_set_version
        ):
            stage = STAGE_REFERENCE_SET
            run = await _progress(run, {"current_stage": stage})
            run = await _heartbeat(run)
            refset = await _reference_set(run)
            _require_status(
                refset.status, pet_reference_set_service.STATUS_COMPLETE,
                "REFERENCE_SET_NOT_COMPLETE", "Phase 3 신뢰 레퍼런스 세트가 complete 상태가 아닙니다.",
            )
            if (
                str(refset.identity_profile_id or "") != str(run.identity_profile_id or "")
                or refset.identity_profile_version != run.identity_profile_version
            ):
                raise PetGenerationRunError("RUN_LINEAGE_INVALID", "레퍼런스 세트의 신원 프로필이 실행과 다릅니다.", status=409)
            run = await _progress(
                run,
                {"reference_set_id": refset.id, "reference_set_version": refset.version},
            )

        if resume_from <= _stage_index(STAGE_CANONICAL) or not (
            run.canonical_version_id and run.canonical_version
        ):
            stage = STAGE_CANONICAL
            run = await _progress(run, {"current_stage": stage})
            run = await _heartbeat(run)
            canonical, run = await _canonical(run)
            # Pin the version **before** judging its status (mirrors _motion()) so a
            # REVIEW canonical is remembered across retries — _canonical() then takes
            # the pinned-version branch and never calls build_canonical() again for
            # the same version, and request_canonical_replacement_generation() has a
            # concrete source version to replace.
            provider_state = _provider_state(run)
            operator_state = dict(provider_state.get("_operator") or {})
            canonical_replacement = dict(operator_state.get("canonical_replacement_request") or {})
            if canonical_replacement and str(canonical_replacement.get("source_canonical_version_id") or "") != canonical.id:
                canonical_replacement.update(
                    {
                        "status": "GENERATED",
                        "replacement_canonical_version_id": canonical.id,
                        "completed_at": _now_iso(),
                    }
                )
                operator_state["canonical_replacement_request"] = canonical_replacement
                provider_state["_operator"] = operator_state
            run = await _progress(
                run,
                {
                    "canonical_version_id": canonical.id,
                    "canonical_version": canonical.version,
                    "provider_state": provider_state,
                },
            )
            if canonical.status == canonical_pet_service.STATUS_REVIEW:
                # Distinct from CANONICAL_NOT_COMPLETE: this is a recoverable,
                # non-terminal state. Reusing this REVIEW version is not a bug — it
                # is the whole point of skip_if_unchanged — but the run cannot make
                # forward progress without a human decision (QA rerun or an
                # explicit replacement request), so it must not raise the same
                # generic "not complete" error as an actual failure.
                raise PetGenerationRunError(
                    "CANONICAL_QA_REVIEW",
                    "Phase 4 canonical 후보가 사람 검토를 요구합니다 — 재구매 없이 QA 재실행 또는 명시적 교체 요청으로만 진행됩니다.",
                    status=409,
                )
            _require_status(
                canonical.status, canonical_pet_service.STATUS_COMPLETE,
                "CANONICAL_NOT_COMPLETE", "Phase 4 canonical QA PASS 결과가 없습니다.",
            )
            if (
                str(canonical.reference_set_id or "") != str(run.reference_set_id or "")
                or canonical.reference_set_version != run.reference_set_version
            ):
                raise PetGenerationRunError("RUN_LINEAGE_INVALID", "canonical 의 레퍼런스 세트가 실행과 다릅니다.", status=409)

        spec = motion_spec.get_motion(run.motion_id)
        supported = (MOTION_BREATHING,) + premium_motion_finalization.PREMIUM_MOTIONS
        if not spec or run.motion_id not in supported:
            raise PetGenerationRunError(
                "UNSUPPORTED_MOTION",
                "지원하는 모션은 BREATHING + 상용 5종(아이들 4 + COME_CLOSER)입니다.",
                status=422,
            )

        keyframes_pinned = bool((run.keyframes.get(spec.start_keyframe_role) or {}).get("id")) and (
            not (spec.requires_target_keyframe and spec.target_keyframe_role)
            or bool((run.keyframes.get(spec.target_keyframe_role) or {}).get("id"))
        )
        if resume_from <= _stage_index(STAGE_KEYFRAMES) or not keyframes_pinned:
            stage = STAGE_KEYFRAMES
            run = await _progress(run, {"current_stage": stage})
            run = await _heartbeat(run)
            # BREATHING → NEUTRAL_IDLE Canonical reuse: only this motion's start
            # keyframe may skip real keyframe generation when the approved
            # Canonical already satisfies the NEUTRAL_IDLE contract on its own.
            # Every other motion (incl. the other NEUTRAL_IDLE-starting idles)
            # keeps generating a real keyframe, same as before.
            keyframe, run = await _keyframe(
                run,
                spec.start_keyframe_role,
                allow_canonical_reuse=(
                    run.motion_id == MOTION_BREATHING and spec.start_keyframe_role == "NEUTRAL_IDLE"
                ),
            )
            keyframes = dict(run.keyframes)
            run = await _advance_keyframe_stage(run, spec.start_keyframe_role, keyframe, keyframes)
            # ── 전이(TRANSITION) 목표 키프레임 (2026-09-08) ─────────────────────
            # resolve_video_generation_spec 은 requires_target_keyframe 모션에서
            # **승인된** 목표 키프레임을 요구한다. 예전 파이프라인은 시작 역할만
            # 만들었으므로 LIE_DOWN 류는 MOTION_SPEC 에서 TARGET_KEYFRAME_REQUIRED
            # 로 영원히 죽었다. 시작과 같은 규칙(COMPLETE + canonical 일치)으로
            # 목표 역할도 여기서 만든다.
            if spec.requires_target_keyframe and spec.target_keyframe_role:
                target_kf, run = await _keyframe(run, spec.target_keyframe_role)
                keyframes = dict(run.keyframes)
                run = await _advance_keyframe_stage(run, spec.target_keyframe_role, target_kf, keyframes)

        # MOTION_SPEC 계보 검증은 항상 run.keyframes 의 영속 핀을 본다 — 위
        # 블록이 이번 틱에 스킵됐어도(재개) 이전 틱이 이미 같은 값을 박아
        # 뒀으므로 동일하다.
        start_kf_pin = dict(run.keyframes.get(spec.start_keyframe_role) or {})

        if resume_from <= _stage_index(STAGE_MOTION_SPEC) or not run.motion_spec_version:
            stage = STAGE_MOTION_SPEC
            run = await _progress(run, {"current_stage": stage})
            run = await _heartbeat(run)
            contract = await motion_spec.resolve_video_generation_spec(
                user_id=run.user_id, pet_id=run.pet_id, motion_id=run.motion_id
            )
            if (
                contract.get("motion_id") != run.motion_id
                or str((contract.get("start_keyframe") or {}).get("keyframe_id") or "") != str(start_kf_pin.get("id") or "")
                or (contract.get("start_keyframe") or {}).get("version") != start_kf_pin.get("version")
                or str(contract.get("canonical_version_id") or "") != str(run.canonical_version_id or "")
            ):
                raise PetGenerationRunError("RUN_LINEAGE_INVALID", "Phase 5.1 계약이 실행 lineage 와 다릅니다.", status=409)
            run = await _progress(
                run, {"motion_spec_version": str(contract.get("motion_spec_version") or "")}
            )
        elif not run.motion_version_id:
            # 계약 검증/핀(motion_spec_version)은 이미 끝났다 — 하지만 모션
            # 생성 자체는 아직 완료되지 않았다(재개 직후, provider 대기 중
            # 멈췄던 지점). 계약 "값"은 실행 행에 영속화되지 않으므로
            # build_motion_video 에 넘기려면 여기서 한 번은 다시 해석해야
            # 한다 — 계보 재검증/motion_spec_version 재기록은 하지 않는다.
            contract = await motion_spec.resolve_video_generation_spec(
                user_id=run.user_id, pet_id=run.pet_id, motion_id=run.motion_id
            )
        else:
            # 모션까지 이미 핀됐다(run.motion_version_id) — _motion() 의 핀
            # 분기는 계약을 전혀 쓰지 않으므로 다시 해석할 이유가 없다.
            contract = None

        stage = STAGE_MOTION_GENERATION
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        motion, run = await _motion(run, contract)
        provider_state = _provider_state(run)
        operator_state = dict(provider_state.get("_operator") or {})
        replacement = dict(operator_state.get("replacement_request") or {})
        if replacement and str(replacement.get("source_motion_version_id") or "") != motion.id:
            replacement.update(
                {
                    "status": "GENERATED",
                    "replacement_motion_version_id": motion.id,
                    "completed_at": _now_iso(),
                }
            )
            operator_state["replacement_request"] = replacement
            provider_state["_operator"] = operator_state
        run = await _progress(
            run,
            {
                "motion_version_id": motion.id,
                "motion_version": motion.version,
                "selected_candidate_id": motion.selected_candidate_id,
                "provider_state": provider_state,
            },
        )

        stage = STAGE_QA
        run = await _progress(run, {"current_stage": stage})
        selected = None
        if motion.status == motion_video_service.STATUS_COMPLETE:
            selected = next(
                (
                    candidate
                    for candidate in motion.candidates
                    if candidate.id == motion.selected_candidate_id
                    and candidate.selected
                    and candidate.decision == "PASS"
                ),
                None,
            )
            if not selected:
                raise PetGenerationRunError("MOTION_QA_INVALID", "선택된 QA PASS 후보를 확인하지 못했습니다.", status=409)

        # ── Phase 7G: QA 결정은 절대 바꾸지 않는다 — REVIEW 는 REVIEW 로 남는다.
        # 다만 PASS 든 REVIEW 든 재생 가능한 후보는 packed-alpha 파생물로 포장한다
        # (Phase 7F, 멱등). PASS 는 이어서 발행되고, REVIEW 는 발행 없이 개발/
        # 현재-실행 재생 리졸버(GET /generation-runs/{id}/playback)로만 보인다.
        review_candidate = None
        if motion.status == motion_video_service.STATUS_REVIEW:
            review_candidates = [
                c for c in motion.candidates if getattr(c, "decision", "") == "REVIEW"
            ]
            review_candidate = max(
                review_candidates,
                key=lambda c: float(
                    ((getattr(c, "qa_result", None) or {}).get("identity_similarity")) or -1.0
                ),
                default=None,
            )
        packageable = selected or review_candidate
        if packageable is not None:
            stage = STAGE_DELIVERY
            try:
                run = await _progress(
                    run, {"current_stage": stage, "selected_candidate_id": packageable.id}
                )
            except Exception:
                # 배포 순서 내성: 마이그레이션(20261021) 이전 DB 는 DELIVERY
                # 라벨을 모른다. 단계 라벨은 진단용이므로 QA 라벨로 유지하고
                # 포장은 계속한다 — 라벨 때문에 실행을 죽이지 않는다.
                stage = STAGE_QA
                run = await _progress(
                    run, {"current_stage": stage, "selected_candidate_id": packageable.id}
                )
            run = await _heartbeat(run)
            try:
                await motion_delivery_service.package_breathing_for_delivery(
                    user_id=run.user_id,
                    pet_id=run.pet_id,
                    motion_version_id=motion.id,
                    candidate_id=packageable.id,
                )
            except motion_delivery_service.MotionDeliveryError as exc:
                raise PetGenerationRunError(exc.code, exc.message, status=exc.status) from exc

        if motion.status == motion_video_service.STATUS_REVIEW:
            # 위 포장 블록은 진행 상황 표시를 위해 stage 를 DELIVERY 로 올렸을 수
            # 있다 — 하지만 REVIEW 는 QA 에서 비롯된 상태이므로, 실패로 남는
            # current_stage 는 원래 단계(QA)를 보존해야 한다. 그러지 않으면
            # request_replacement_generation 의 `current_stage == STAGE_QA` 가드가
            # (REVIEW 후보가 존재하는 모든 실제 REVIEW 실행에서 포장이 항상
            # 일어나므로) 영원히 도달 불가능해진다.
            stage = STAGE_QA
            raise PetGenerationRunError("MOTION_QA_REVIEW", "Phase 6 후보가 사람 검토를 요구합니다.", status=409)
        if motion.status != motion_video_service.STATUS_COMPLETE:
            raise PetGenerationRunError("MOTION_QA_FAILED", "Phase 6 QA PASS 후보가 없습니다.", status=409)

        stage = STAGE_PUBLICATION
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        if run.motion_id == MOTION_BREATHING:
            publication = await motion_publication_service.publish_breathing(
                user_id=run.user_id, pet_id=run.pet_id, motion_version_id=motion.id
            )
            if publication.selected_candidate_id != selected.id:
                raise PetGenerationRunError("RUN_LINEAGE_INVALID", "발행 후보가 실행의 QA PASS 후보와 다릅니다.", status=409)
            publication_id = publication.publication_id
        else:
            # ── Phase 7H — 상용 모션 이행 확정 ────────────────────────────
            # 발행 원장 + 예약 확정 + 소유 + generated_motions 현재 포인터가
            # 한 번에(멱등 앵커들로) 투영된다. 확정 실패는 실행 실패다 —
            # 과금이 확정됐는데 소유/포인터가 없는 상태를 만들지 않는다.
            try:
                finalization = await premium_motion_finalization.finalize_premium_motion(
                    run_id=run.id,
                    user_id=run.user_id,
                    pet_id=run.pet_id,
                    motion_id=run.motion_id,
                    motion_version_id=motion.id,
                    motion_version=int(motion.version or 1),
                    candidate_id=selected.id,
                    product_key=run.product_key,
                    reservation_ledger_id=run.reservation_ledger_id,
                    credits_reserved=run.credits_reserved,
                )
            except premium_motion_finalization.PremiumFinalizationError as exc:
                raise PetGenerationRunError(exc.code, exc.message, status=exc.status) from exc
            publication_id = finalization.publication_id

        return await _progress(
            run,
            {
                "status": STATUS_PUBLISHED,
                "current_stage": STAGE_PUBLISHED,
                "publication_id": publication_id,
                "completed_at": _now_iso(),
                "last_error": None,
                "execution_token": None,
                "lease_expires_at": None,
                "worker_id": None,
                "next_attempt_at": None,
            },
        )
    except durable_provider_jobs.ProviderWorkPending:
        latest_row = await _row_by_id(run.id)
        latest = _to_run(latest_row) if latest_row else run
        return await _progress(
            latest,
            {
                "status": STATUS_WAITING_PROVIDER,
                "current_stage": stage,
                "provider_state": _provider_state(run),
                "last_error": None,
                "execution_token": None,
                "lease_expires_at": None,
                "worker_id": None,
                "next_attempt_at": _next_poll_iso(),
            },
        )
    except durable_provider_jobs.ProviderRecoveryRequired as exc:
        latest_row = await _row_by_id(run.id)
        latest = _to_run(latest_row) if latest_row else run
        return await _progress(
            latest,
            {
                "status": STATUS_RECOVERY_REQUIRED,
                "current_stage": stage,
                "provider_state": _provider_state(run),
                "last_error": _phase_error(stage, exc),
                "execution_token": None,
                "lease_expires_at": None,
                "worker_id": None,
                "next_attempt_at": None,
            },
        )
    except PetGenerationRunError as exc:
        if exc.code in ("WORKER_LEASE_LOST", "WORKER_LEASE_REQUIRED"):
            current = await _row_by_id(run.id)
            return _to_run(current) if current else run
        return await _fail(await _latest_with_our_lease(run), stage, exc)
    except Exception as exc:  # Every stage failure must become durable run state.
        return await _fail(await _latest_with_our_lease(run), stage, exc)


async def _latest_with_our_lease(run: PetGenerationRun) -> PetGenerationRun:
    """
    실패 기록 전에 행을 다시 읽되 **우리가 점유한 토큰**으로 쓴다. 다시 읽은
    행의 토큰을 그대로 쓰면, 그 사이 다른 워커가 인수했을 때 그 워커의 실행을
    FAILED 로 덮어쓰고, 사용자가 취소했을 때는(토큰 없음) 예외로 튄다.
    """
    latest_row = await _row_by_id(run.id)
    if not latest_row:
        return run
    return replace(_to_run(latest_row), execution_token=run.execution_token)


async def process_next_generation_run(*, worker_id: str) -> Optional[PetGenerationRun]:
    """Claim and advance at most one run; intended for the durable worker only."""
    wid = (worker_id or "").strip()
    if not wid:
        raise PetGenerationRunError("WORKER_ID_REQUIRED", "worker_id 가 필요합니다.", status=422)
    run = await _claim_next(wid)
    if not run:
        return None
    if _recovery_exhausted(run):
        # claim 이 상한을 넘겨 한 번 더 건네준 실행 — 워커가 이 실행 위에서
        # 계속 죽고 있다는 뜻이다(OOM 등). 일을 시키지 않고 종료시킨다. 이후엔
        # 사용자가 Retry 를 눌러야만 다시 QUEUED 가 된다.
        logger.warning(
            "run %s exhausted %s lease recoveries at stage %s — failing, not resurrecting",
            run.id, run.lease_recoveries - 1, run.current_stage,
        )
        return await _fail(
            run,
            run.current_stage,
            PetGenerationRunError(
                ERROR_WORKER_RECOVERY_EXHAUSTED,
                "워커가 이 실행 위에서 반복해서 중단됐습니다. 다시 시도해 주세요.",
                status=503,
                details={"lease_recoveries": run.lease_recoveries - 1},
            ),
        )
    lock = _LOCKS.setdefault(run.id, asyncio.Lock())
    async with lock:
        with _LeaseHeartbeater(run):
            content_id = await _validate_intake(run.user_id, run.pet_id)
            if content_id != run.content_id:
                return await _fail(
                    run,
                    run.current_stage,
                    PetGenerationRunError(
                        "PHASE1_IDENTITY_MISMATCH",
                        "현재 Phase 1 intake 가 생성 실행의 content_id 와 일치하지 않습니다.",
                        status=409,
                    ),
                )
            return await _execute(run)


def _validate_request(motion_id: str, request_kind: str, idempotency_key: str) -> tuple[str, str, str]:
    motion = (motion_id or "").strip().upper()
    kind = (request_kind or "").strip().upper()
    key = (idempotency_key or "").strip()
    # ── 모션 × 요청 종류 짝 (Phase 7H) ────────────────────────────────────
    # FREE_HOME 은 BREATHING 전용, PREMIUM_PRODUCT 는 기존 상용 5종 전용이다.
    # 새 모션(PET_HEAD, RUN …)은 카탈로그에 명시적으로 추가되기 전까지 여기서도
    # 열리지 않는다 — 기술 지원과 판매 가능은 별개다.
    if kind == REQUEST_FREE_HOME:
        if motion != MOTION_BREATHING:
            raise PetGenerationRunError(
                "UNSUPPORTED_MOTION", "FREE_HOME 은 BREATHING 만 지원합니다.", status=422
            )
    elif kind == REQUEST_PREMIUM_PRODUCT:
        if motion not in premium_motion_finalization.PREMIUM_MOTIONS:
            raise PetGenerationRunError(
                "UNSUPPORTED_MOTION",
                "PREMIUM_PRODUCT 는 상용 모션 레지스트리(PREMIUM_MOTIONS) 밖의 모션을 지원하지 않습니다.",
                status=422,
            )
    else:
        raise PetGenerationRunError(
            "UNSUPPORTED_REQUEST_KIND",
            "지원하는 요청 종류는 FREE_HOME / PREMIUM_PRODUCT 뿐입니다.",
            status=422,
        )
    if not key or len(key) > 200:
        raise PetGenerationRunError("IDEMPOTENCY_KEY_INVALID", "유효한 idempotency_key 가 필요합니다.", status=422)
    return motion, kind, key


async def start_generation_run(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str = MOTION_BREATHING,
    request_kind: str = REQUEST_FREE_HOME,
    idempotency_key: str,
    product_key: Optional[str] = None,
    reservation_ledger_id: Optional[str] = None,
    credits_reserved: int = 0,
) -> PetGenerationRun:
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetGenerationRunError("GENERATION_RUN_INVALID", "user_id 와 pet_id 가 필요합니다.")
    motion, kind, key = _validate_request(motion_id, request_kind, idempotency_key)
    content_id = await _validate_intake(uid, pid)
    now = _now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "pet_id": pid,
        "content_id": content_id,
        "motion_id": motion,
        "request_kind": kind,
        "idempotency_key": key,
        "status": STATUS_QUEUED,
        "current_stage": STAGE_QUEUED,
        "identity_profile_id": None,
        "identity_profile_version": None,
        "reference_set_id": None,
        "reference_set_version": None,
        "canonical_version_id": None,
        "canonical_version": None,
        "keyframes": {},
        "motion_spec_version": None,
        "motion_version_id": None,
        "motion_version": None,
        "selected_candidate_id": None,
        "publication_id": None,
        "provider_state": {},
        "last_error": None,
        "retry_count": 0,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "worker_id": None,
        "execution_token": None,
        "lease_expires_at": None,
        "next_attempt_at": None,
    }
    if kind == REQUEST_PREMIUM_PRODUCT:
        # 상거래 맥락은 프리미엄 실행에만 싣는다 — FREE_HOME 행은 마이그레이션
        # (20261022) 이전 DB 에서도 예전 컬럼 집합 그대로 들어가야 한다.
        row["product_key"] = (product_key or "").strip() or None
        row["reservation_ledger_id"] = (reservation_ledger_id or "").strip() or None
        row["credits_reserved"] = int(credits_reserved or 0)
    async with _start_lock(uid, pid, motion, kind):
        run, _created = await _insert_or_get(row)
    return run


#: 더 기다릴 것이 없는 프로바이더 작업 상태. 이 둘이 아니면 — PREPARED /
#: SUBMITTING / SUBMITTED / AMBIGUOUS — 외부 작업이 살아 있거나 복구 판정이
#: 모호하다는 뜻이고, 그때 핀을 풀면 같은 생성에 두 번 과금될 수 있다.
_TERMINAL_SUBMISSION_STATUSES = (durable_provider_jobs.COLLECTED, "FAILED")


async def _stale_motion_pin(run: PetGenerationRun) -> bool:
    """
    FAILED 실행의 모션 핀이 **스펙 버전 격차 때문에** 재시도 불능인가.

    배경: 실행은 durable resume 을 위해 motion_version_id 를 고정하고, _motion 의
    lineage 검사는 고정된 버전의 motion_spec_version 이 실행과 다르면 하드하게
    거절한다(RUN_LINEAGE_INVALID). 옳은 가드지만, 시도 사이에 모션 스펙이
    올라가면(v5 → v6) 재시도가 매번 현재 스펙을 다시 해석해 실행에 쓰고, 고정된
    옛 버전과 영원히 어긋난다 — 구독 모드 구매는 멱등 키가 고정이라 탈출구가
    없었다(TAIL_WAGGING 라이브 실측, run ebbc11f5).

    True 는 "핀만 풀면 같은 실행이 현재 스펙으로 다음 버전을 만든다"가 **증명될
    때만**이다. 하나라도 애매하면 False — 기존 하드 가드가 그대로 판정한다:

      * FAILED 가 아니면 손대지 않는다 (RECOVERY_REQUIRED 는 정의상 모호하다)
      * 발행/이행이 이미 있으면 손대지 않는다
      * 운영자 교체 요청이 걸려 있으면 그 흐름에 맡긴다
      * 고정된 버전 행을 못 읽으면 (다른 종류의 손상) 손대지 않는다
      * 스펙 버전이 현재와 같으면 스테일이 아니다 — 기존 재사용 경로가 맞다
      * 종료되지 않은 프로바이더 작업이 하나라도 있으면 손대지 않는다
    """
    if run.status != STATUS_FAILED:
        return False
    if not run.motion_version_id or run.publication_id:
        return False
    if dict((run.provider_state or {}).get("_operator") or {}).get("replacement_request"):
        return False
    try:
        motion = await motion_video_service.get_motion_version(
            user_id=run.user_id,
            pet_id=run.pet_id,
            motion_id=run.motion_id,
            version=run.motion_version,
        )
    except Exception:
        return False
    if not motion or str(motion.id) != str(run.motion_version_id):
        return False
    if str(motion.motion_spec_version or "") == motion_spec.MOTION_SPEC_VERSION:
        return False
    for job in durable_provider_jobs.list_for_run(run.id):
        if str(job.get("submission_status") or "") not in _TERMINAL_SUBMISSION_STATUSES:
            return False
    return True


async def _join_other_active_run(run: PetGenerationRun) -> Optional[PetGenerationRun]:
    """
    `run` 을 QUEUED 로 되살리기 직전의 마지막 확인 — 같은 pet/motion/
    request_kind 를 이미 다른 실행이 활성으로 맡고 있다면(예: 이 실행이
    FAILED 로 끝난 사이 새 idempotency_key 로 새 실행이 이미 시작됨) `run`
    자신을 되살리지 않고 그 실행을 대신 돌려준다 — 되살리기도 결국
    start_generation_run() 과 같은 "이 pet 의 이 작업은 활성 실행이 하나뿐"
    불변조건을 지켜야 하는 삽입/전이이기 때문이다.
    """
    other = await _row_by_scope_active(
        user_id=run.user_id, pet_id=run.pet_id,
        motion_id=run.motion_id, request_kind=run.request_kind,
    )
    if other and str(other.get("id") or "") != run.id:
        return _to_run(other)
    return None


async def retry_generation_run(*, user_id: str, run_id: str) -> PetGenerationRun:
    run = await get_generation_run(user_id=user_id, run_id=run_id)
    content_id = await _validate_intake(run.user_id, run.pet_id)
    if content_id != run.content_id:
        raise PetGenerationRunError(
            "PHASE1_IDENTITY_MISMATCH",
            "현재 Phase 1 intake 가 생성 실행의 content_id 와 일치하지 않습니다.",
            status=409,
        )
    if run.status == STATUS_PUBLISHED:
        return run
    if run.status in (STATUS_QUEUED, STATUS_RUNNING, STATUS_WAITING_PROVIDER):
        return run
    joined = await _join_other_active_run(run)
    if joined:
        return joined
    updates: dict[str, Any] = {
        "status": STATUS_QUEUED,
        "last_error": None,
        "retry_count": run.retry_count + 1,
        # 사용자 행동이 복구 예산을 되돌린다 — 워커는 절대 이 값을 내리지 않는다.
        "lease_recoveries": 0,
        "worker_id": None,
        "execution_token": None,
        "lease_expires_at": None,
        "completed_at": None,
        "next_attempt_at": None,
    }
    if await _stale_motion_pin(run):
        # 하류 모션 계보만 비운다 — 옛 버전/후보 행은 역사로 남고(삭제 없음),
        # 신원/레퍼런스/canonical/키프레임 핀은 그대로라 상류는 재사용된다.
        # 다음 시도는 MOTION_SPEC 에서 현재 스펙을 해석하고, _motion 이 (핀이
        # 없으므로) 최신 버전 재사용 검사를 거쳐 현재 스펙으로 다음 버전을 만든다.
        updates.update(
            {
                "motion_version_id": None,
                "motion_version": None,
                "selected_candidate_id": None,
                "publication_id": None,
                "current_stage": STAGE_MOTION_SPEC,
            }
        )
    # FAILED / CANCELLED / RECOVERY_REQUIRED 에서만 QUEUED 로 — 읽기와 쓰기 사이에
    # 상태가 바뀌었으면(동시 재시도가 먼저 QUEUED 로 옮김 등) 그 결과를 돌려준다.
    retried = await _transition(run.id, updates, from_statuses=RETRYABLE_RUN_STATUSES)
    if retried:
        return retried
    return await get_generation_run(user_id=user_id, run_id=run_id)


async def cancel_generation_run(
    *, user_id: str, run_id: str, reason: str = "user_cancelled"
) -> PetGenerationRun:
    """
    사용자/클라이언트의 명시적 정지. 종료되지 않은 실행(QUEUED / RUNNING /
    WAITING_PROVIDER / RECOVERY_REQUIRED)을 CANCELLED 로 옮기고 lease 와
    execution_token 을 비운다 — 지금 이 실행을 돌리고 있는 워커는 다음 fenced
    쓰기(_progress / heartbeat)에서 WORKER_LEASE_LOST 를 받고 그 자리에서
    멈추며, claim_next_pet_generation_run 은 CANCELLED 를 절대 집지 않는다.

    이미 끝난 실행(PUBLISHED / FAILED / CANCELLED)은 그대로 돌려준다 — 취소는
    멱등이고, 발행된 결과를 되돌리지 않는다. 다시 돌리려면 Retry 다.
    """
    run = await get_generation_run(user_id=user_id, run_id=run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    why = (reason or "").strip()[:200] or "user_cancelled"
    cancelled = await _transition(
        run.id,
        {
            "status": STATUS_CANCELLED,
            "last_error": {
                "stage": run.current_stage,
                "code": ERROR_RUN_CANCELLED,
                "message": "생성 실행이 중단됐습니다.",
                "reason": why,
                "at": _now_iso(),
            },
            "execution_token": None,
            "lease_expires_at": None,
            "worker_id": None,
            "next_attempt_at": None,
            "completed_at": _now_iso(),
        },
        from_statuses=ACTIVE_RUN_STATUSES,
    )
    if not cancelled:
        # 그 사이 워커가 끝냈다(PUBLISHED 등) — 그 결과가 정본이다.
        return await get_generation_run(user_id=user_id, run_id=run_id)
    await _reconcile_premium_after_stop(cancelled)
    return cancelled


async def request_replacement_generation(
    *, user_id: str, run_id: str, idempotency_key: str, reason: str
) -> PetGenerationRun:
    """Queue exactly one durable replacement for a QA-REVIEW motion version.

    The API records intent only. The worker creates the next Phase 6 version and
    owns every provider submission/recovery step.
    """
    run = await get_generation_run(user_id=user_id, run_id=run_id)
    key = (idempotency_key or "").strip()
    why = (reason or "").strip()
    if not key or len(key) > 200 or not why:
        raise PetGenerationRunError(
            "REPLACEMENT_REQUEST_INVALID", "idempotency_key 와 사유가 필요합니다.", status=422
        )
    provider_state = dict(run.provider_state)
    operator_state = dict(provider_state.get("_operator") or {})
    existing = dict(operator_state.get("replacement_request") or {})
    if existing:
        if str(existing.get("idempotency_key") or "") == key:
            return run
        raise PetGenerationRunError(
            "REPLACEMENT_ALREADY_REQUESTED",
            "이 실행에는 이미 한 번의 교체 생성이 요청되었습니다.",
            status=409,
        )
    if run.status == STATUS_PUBLISHED:
        raise PetGenerationRunError("RUN_ALREADY_PUBLISHED", "발행된 실행은 교체할 수 없습니다.", status=409)
    if (
        run.current_stage != STAGE_QA
        or not run.motion_version_id
        or str((run.last_error or {}).get("code") or "") != "MOTION_QA_REVIEW"
    ):
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "QA REVIEW 상태의 모션만 교체 요청할 수 있습니다.", status=409
        )
    motion = await motion_video_service.get_motion_version(
        user_id=run.user_id,
        pet_id=run.pet_id,
        motion_id=run.motion_id,
        version=run.motion_version,
    )
    if not motion or motion.id != run.motion_version_id or motion.status != motion_video_service.STATUS_REVIEW:
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "현재 Phase 6 버전이 REVIEW 상태가 아닙니다.", status=409
        )
    joined = await _join_other_active_run(run)
    if joined:
        return joined

    operator_state["replacement_request"] = {
        "idempotency_key": key,
        "reason": why[:1000],
        "source_motion_version_id": motion.id,
        "source_candidate_ids": [candidate.id for candidate in motion.candidates],
        "status": "QUEUED",
        "requested_at": _now_iso(),
    }
    provider_state["_operator"] = operator_state
    return await _update(
        run.id,
        {
            "status": STATUS_QUEUED,
            "current_stage": STAGE_MOTION_GENERATION,
            "motion_version_id": None,
            "motion_version": None,
            "selected_candidate_id": None,
            "publication_id": None,
            "last_error": None,
            "completed_at": None,
            "worker_id": None,
            "execution_token": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "provider_state": provider_state,
        },
    )


async def request_canonical_replacement_generation(
    *, user_id: str, run_id: str, idempotency_key: str, reason: str
) -> PetGenerationRun:
    """Queue exactly one durable replacement for a QA-REVIEW canonical version.

    Mirrors request_replacement_generation (Phase 6) for Phase 4. The API
    records intent only; the worker (_canonical(), driven by _execute()) builds
    the next version and owns every provider submission/recovery step. Every
    downstream pin (keyframes, motion spec/version, publication) is cleared
    because they were all derived from the REVIEW source canonical and must be
    re-derived once the replacement lands.
    """
    run = await get_generation_run(user_id=user_id, run_id=run_id)
    key = (idempotency_key or "").strip()
    why = (reason or "").strip()
    if not key or len(key) > 200 or not why:
        raise PetGenerationRunError(
            "REPLACEMENT_REQUEST_INVALID", "idempotency_key 와 사유가 필요합니다.", status=422
        )
    provider_state = dict(run.provider_state)
    operator_state = dict(provider_state.get("_operator") or {})
    existing = dict(operator_state.get("canonical_replacement_request") or {})
    if existing:
        if str(existing.get("idempotency_key") or "") == key:
            return run
        raise PetGenerationRunError(
            "REPLACEMENT_ALREADY_REQUESTED",
            "이 실행에는 이미 한 번의 정본 교체 생성이 요청되었습니다.",
            status=409,
        )
    if run.status == STATUS_PUBLISHED:
        raise PetGenerationRunError("RUN_ALREADY_PUBLISHED", "발행된 실행은 교체할 수 없습니다.", status=409)
    if (
        run.current_stage != STAGE_CANONICAL
        or not run.canonical_version_id
        or str((run.last_error or {}).get("code") or "") != "CANONICAL_QA_REVIEW"
    ):
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "QA REVIEW 상태의 canonical 만 교체 요청할 수 있습니다.", status=409
        )
    canonical = await canonical_pet_service.get_canonical(
        user_id=run.user_id, pet_id=run.pet_id, version=run.canonical_version
    )
    if (
        not canonical
        or canonical.id != run.canonical_version_id
        or canonical.status != canonical_pet_service.STATUS_REVIEW
    ):
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "현재 Phase 4 버전이 REVIEW 상태가 아닙니다.", status=409
        )
    joined = await _join_other_active_run(run)
    if joined:
        return joined

    operator_state["canonical_replacement_request"] = {
        "idempotency_key": key,
        "reason": why[:1000],
        "source_canonical_version_id": canonical.id,
        "source_candidate_ids": [candidate.id for candidate in canonical.candidates],
        "status": "QUEUED",
        "requested_at": _now_iso(),
    }
    provider_state["_operator"] = operator_state
    return await _update(
        run.id,
        {
            "status": STATUS_QUEUED,
            "current_stage": STAGE_CANONICAL,
            "canonical_version_id": None,
            "canonical_version": None,
            "keyframes": {},
            "motion_spec_version": None,
            "motion_version_id": None,
            "motion_version": None,
            "selected_candidate_id": None,
            "publication_id": None,
            "last_error": None,
            "completed_at": None,
            "worker_id": None,
            "execution_token": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "provider_state": provider_state,
        },
    )


async def request_keyframe_replacement_generation(
    *, user_id: str, run_id: str, keyframe_role: str, idempotency_key: str, reason: str
) -> PetGenerationRun:
    """Queue exactly one durable replacement for a QA-REVIEW keyframe role.

    Mirrors request_replacement_generation (Phase 6) for Phase 5. Only the
    named role's pin is cleared — the other keyframe role (start vs. target),
    if any, and the canonical pin are left untouched. Downstream motion pins
    are cleared because they were derived from the REVIEW source keyframe.
    """
    run = await get_generation_run(user_id=user_id, run_id=run_id)
    role = (keyframe_role or "").strip().upper()
    key = (idempotency_key or "").strip()
    why = (reason or "").strip()
    if not role or not key or len(key) > 200 or not why:
        raise PetGenerationRunError(
            "REPLACEMENT_REQUEST_INVALID", "keyframe_role, idempotency_key, 사유가 필요합니다.", status=422
        )
    provider_state = dict(run.provider_state)
    operator_state = dict(provider_state.get("_operator") or {})
    replacements = dict(operator_state.get("keyframe_replacement_requests") or {})
    existing = dict(replacements.get(role) or {})
    if existing:
        if str(existing.get("idempotency_key") or "") == key:
            return run
        raise PetGenerationRunError(
            "REPLACEMENT_ALREADY_REQUESTED",
            "이 키프레임 역할에는 이미 한 번의 교체 생성이 요청되었습니다.",
            status=409,
        )
    if run.status == STATUS_PUBLISHED:
        raise PetGenerationRunError("RUN_ALREADY_PUBLISHED", "발행된 실행은 교체할 수 없습니다.", status=409)
    saved = dict(run.keyframes.get(role) or {})
    if (
        run.current_stage != STAGE_KEYFRAMES
        or not saved.get("id")
        or str((run.last_error or {}).get("code") or "") != "KEYFRAME_QA_REVIEW"
        or str((run.last_error or {}).get("keyframe_role") or "") != role
    ):
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "QA REVIEW 상태의 키프레임만 교체 요청할 수 있습니다.", status=409
        )
    keyframe = await action_keyframe_service.get_keyframe(
        user_id=run.user_id, pet_id=run.pet_id, keyframe_role=role, version=int(saved["version"])
    )
    if (
        not keyframe
        or keyframe.id != str(saved.get("id") or "")
        or keyframe.status != action_keyframe_service.STATUS_REVIEW
    ):
        raise PetGenerationRunError(
            "REPLACEMENT_NOT_JUSTIFIED", "현재 Phase 5 키프레임이 REVIEW 상태가 아닙니다.", status=409
        )
    joined = await _join_other_active_run(run)
    if joined:
        return joined

    replacements[role] = {
        "idempotency_key": key,
        "reason": why[:1000],
        "source_keyframe_id": keyframe.id,
        "source_candidate_ids": [candidate.id for candidate in keyframe.candidates],
        "status": "QUEUED",
        "requested_at": _now_iso(),
    }
    operator_state["keyframe_replacement_requests"] = replacements
    provider_state["_operator"] = operator_state
    keyframes = dict(run.keyframes)
    keyframes.pop(role, None)
    return await _update(
        run.id,
        {
            "status": STATUS_QUEUED,
            "current_stage": STAGE_KEYFRAMES,
            "keyframes": keyframes,
            "motion_spec_version": None,
            "motion_version_id": None,
            "motion_version": None,
            "selected_candidate_id": None,
            "publication_id": None,
            "last_error": None,
            "completed_at": None,
            "worker_id": None,
            "execution_token": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "provider_state": provider_state,
        },
    )


async def get_generation_run(*, user_id: str, run_id: str) -> PetGenerationRun:
    row = await _row_by_id((run_id or "").strip())
    if not row:
        raise PetGenerationRunError("GENERATION_RUN_NOT_FOUND", "생성 실행이 없습니다.", status=404)
    run = _to_run(row)
    if run.user_id != (user_id or "").strip():
        raise PetGenerationRunError("PET_NOT_OWNED", "이 생성 실행에 접근할 권한이 없습니다.", status=403)
    return run


def run_dict(run: PetGenerationRun) -> dict[str, Any]:
    return asdict(run)
