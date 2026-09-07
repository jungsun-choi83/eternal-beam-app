"""Phase 7C/7D durable orchestration for one theme-independent BREATHING pipeline.

The phase services and their version/candidate tables remain authoritative. This
module persists only coordination state and lineage, calls those services in
order, and projects a QA PASS result through Phase 7A.

Phase 7D runs this coordinator only in a worker. Recoverable provider adapters
submit once, persist their external job ID, then yield until a later worker tick.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
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

class PetGenerationRunError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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


_MOCK_RUNS: list[dict[str, Any]] = []
_LOCKS: dict[str, asyncio.Lock] = {}


def __reset_for_tests() -> None:
    _MOCK_RUNS.clear()
    _LOCKS.clear()
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


async def _insert_or_get(row: dict[str, Any]) -> tuple[PetGenerationRun, bool]:
    existing = await _row_by_key(
        user_id=row["user_id"], pet_id=row["pet_id"], motion_id=row["motion_id"],
        request_kind=row["request_kind"], idempotency_key=row["idempotency_key"],
    )
    if existing:
        return _to_run(existing), False

    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.table(_table()).insert(row).execute()
            rows = getattr(result, "data", None) or []
            if rows:
                return _to_run(rows[0]), True
        except Exception:
            # A concurrent request can win the unique key. Re-read before
            # classifying it as persistence failure.
            existing = await _row_by_key(
                user_id=row["user_id"], pet_id=row["pet_id"], motion_id=row["motion_id"],
                request_kind=row["request_kind"], idempotency_key=row["idempotency_key"],
            )
            if existing:
                return _to_run(existing), False
            raise PetGenerationRunError(
                "GENERATION_RUNS_UNAVAILABLE", "생성 실행을 저장하지 못했습니다.", status=503
            )
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


async def _claim_next(worker_id: str) -> Optional[PetGenerationRun]:
    client = _supabase() if _use_db() else None
    if client:
        try:
            result = client.rpc(
                "claim_next_pet_generation_run",
                {
                    "p_worker_id": worker_id,
                    "p_lease_seconds": _lease_seconds(),
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

    now = datetime.now(timezone.utc)
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
        next_attempt = row.get("next_attempt_at")
        due = True
        if status == STATUS_WAITING_PROVIDER and next_attempt:
            try:
                due = datetime.fromisoformat(str(next_attempt).replace("Z", "+00:00")) <= now
            except ValueError:
                due = True
        if status == STATUS_QUEUED or (status == STATUS_WAITING_PROVIDER and due) or lease_expired:
            eligible.append(row)
    if not eligible:
        return None
    current = min(
        eligible,
        key=lambda row: (
            0 if row.get("status") == STATUS_WAITING_PROVIDER else 1,
            str(row.get("updated_at") or ""),
        ),
    )
    token = str(uuid.uuid4())
    lease_until = datetime.fromtimestamp(now.timestamp() + _lease_seconds(), timezone.utc).isoformat()
    current.update(
        {
            "status": STATUS_RUNNING,
            "worker_id": worker_id,
            "execution_token": token,
            "lease_expires_at": lease_until,
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
    return {
        "stage": stage,
        "code": str(getattr(exc, "code", type(exc).__name__)),
        "message": str(getattr(exc, "message", str(exc)))[:1000],
        "provider_recovery_required": getattr(exc, "code", "") == "PROVIDER_RECOVERY_REQUIRED",
        "at": _now_iso(),
    }


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


async def _fail(run: PetGenerationRun, stage: str, exc: Exception) -> PetGenerationRun:
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
        },
    )
    # ── Phase 7H — 상용 실행의 종료 되돌림 판정 ──────────────────────────────
    # 레거시 세션의 예약 분기와 같은 정책(READY 하나라도 있으면 유지, 진행 중이면
    # 유예, 예약은 환불이 아니라 **해제**)을 실행용으로 옮긴 함수 하나를 부른다.
    # 판정 실패는 실행 상태를 바꾸지 못한다 — 다음 종료/재시도가 다시 판정한다.
    if failed.request_kind == REQUEST_PREMIUM_PRODUCT:
        try:
            from . import premium_run_fulfillment

            await premium_run_fulfillment.reconcile_failed_run(
                user_id=failed.user_id,
                pet_id=failed.pet_id,
                motion_id=failed.motion_id,
                reservation_ledger_id=failed.reservation_ledger_id,
            )
        except Exception:
            logger.exception(
                "프리미엄 실행 종료 되돌림 판정 실패 — 다음 종료에서 재판정 (run=%s)", failed.id
            )
    return failed


def _image_providers(run: PetGenerationRun, operation: str):
    providers = canonical_image_providers.resolve_providers()
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
            "재개 가능한 이미지 provider 가 설정되지 않았습니다. Phase 7D 는 Runway task API 만 지원합니다.",
            status=503,
        )
    return durable


def _video_providers(run: PetGenerationRun, motion_class: str):
    providers = video_motion_providers.routing_for_class(motion_class)
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
    if latest and (
        str(latest.reference_set_id or "") == str(run.reference_set_id or "")
        and latest.reference_set_version == run.reference_set_version
    ):
        if latest.status != canonical_pet_service.STATUS_BUILDING:
            return latest, run
    canonical = await canonical_pet_service.build_canonical(
        user_id=run.user_id,
        pet_id=run.pet_id,
        providers=_image_providers(run, durable_provider_jobs.OP_CANONICAL),
        skip_if_unchanged=True,
    )
    return canonical, run


async def _keyframe(run: PetGenerationRun, role: str):
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
    if latest and str(latest.canonical_version_id or "") == str(run.canonical_version_id or ""):
        if latest.status != action_keyframe_service.STATUS_BUILDING:
            return latest, run
    keyframe = await action_keyframe_service.build_keyframe(
        user_id=run.user_id,
        pet_id=run.pet_id,
        keyframe_role=role,
        providers=_image_providers(run, durable_provider_jobs.OP_KEYFRAME),
        skip_if_unchanged=True,
    )
    return keyframe, run


def _motion_matches(run: PetGenerationRun, motion: Any) -> bool:
    start_keyframe = run.keyframes.get("NEUTRAL_IDLE") or {}
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


async def _motion(run: PetGenerationRun):
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
        providers=_video_providers(run, spec.motion_class),
        skip_if_unchanged=not bool(latest and latest.id == replacement_source),
    )
    return motion, run


def _require_status(actual: str, expected: str, code: str, message: str) -> None:
    if actual != expected:
        raise PetGenerationRunError(code, message, status=409)


async def _execute(run: PetGenerationRun) -> PetGenerationRun:
    stage = run.current_stage or STAGE_QUEUED
    try:
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

        stage = STAGE_CANONICAL
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        canonical, run = await _canonical(run)
        _require_status(
            canonical.status, canonical_pet_service.STATUS_COMPLETE,
            "CANONICAL_NOT_COMPLETE", "Phase 4 canonical QA PASS 결과가 없습니다.",
        )
        if (
            str(canonical.reference_set_id or "") != str(run.reference_set_id or "")
            or canonical.reference_set_version != run.reference_set_version
        ):
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "canonical 의 레퍼런스 세트가 실행과 다릅니다.", status=409)
        run = await _progress(
            run,
            {
                "canonical_version_id": canonical.id,
                "canonical_version": canonical.version,
                "provider_state": _provider_state(run),
            },
        )

        spec = motion_spec.get_motion(run.motion_id)
        supported = (MOTION_BREATHING,) + premium_motion_finalization.PREMIUM_MOTIONS
        if not spec or run.motion_id not in supported:
            raise PetGenerationRunError(
                "UNSUPPORTED_MOTION",
                "지원하는 모션은 BREATHING + 상용 5종(아이들 4 + COME_CLOSER)입니다.",
                status=422,
            )

        stage = STAGE_KEYFRAMES
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        keyframe, run = await _keyframe(run, spec.start_keyframe_role)
        _require_status(
            keyframe.status, action_keyframe_service.STATUS_COMPLETE,
            "KEYFRAME_NOT_COMPLETE", "Phase 5 키프레임 QA PASS 결과가 없습니다.",
        )
        if (
            str(keyframe.canonical_version_id or "") != str(run.canonical_version_id or "")
            or keyframe.canonical_version != run.canonical_version
        ):
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "키프레임의 canonical 이 실행과 다릅니다.", status=409)
        keyframes = dict(run.keyframes)
        keyframes[spec.start_keyframe_role] = {
            "id": keyframe.id,
            "version": keyframe.version,
            "selected_candidate_id": keyframe.selected_candidate_id,
            "canonical_version_id": keyframe.canonical_version_id,
        }
        run = await _progress(
            run,
            {
                "keyframes": keyframes,
                "provider_state": _provider_state(run),
            },
        )

        stage = STAGE_MOTION_SPEC
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        contract = await motion_spec.resolve_video_generation_spec(
            user_id=run.user_id, pet_id=run.pet_id, motion_id=run.motion_id
        )
        if (
            contract.get("motion_id") != run.motion_id
            or str((contract.get("start_keyframe") or {}).get("keyframe_id") or "") != keyframe.id
            or (contract.get("start_keyframe") or {}).get("version") != keyframe.version
            or str(contract.get("canonical_version_id") or "") != str(run.canonical_version_id or "")
        ):
            raise PetGenerationRunError("RUN_LINEAGE_INVALID", "Phase 5.1 계약이 실행 lineage 와 다릅니다.", status=409)
        run = await _progress(
            run, {"motion_spec_version": str(contract.get("motion_spec_version") or "")}
        )

        stage = STAGE_MOTION_GENERATION
        run = await _progress(run, {"current_stage": stage})
        run = await _heartbeat(run)
        motion, run = await _motion(run)
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
        if exc.code == "WORKER_LEASE_LOST":
            current = await _row_by_id(run.id)
            return _to_run(current) if current else run
        latest_row = await _row_by_id(run.id)
        latest = _to_run(latest_row) if latest_row else run
        return await _fail(latest, stage, exc)
    except Exception as exc:  # Every stage failure must become durable run state.
        latest_row = await _row_by_id(run.id)
        latest = _to_run(latest_row) if latest_row else run
        return await _fail(latest, stage, exc)


async def process_next_generation_run(*, worker_id: str) -> Optional[PetGenerationRun]:
    """Claim and advance at most one run; intended for the durable worker only."""
    wid = (worker_id or "").strip()
    if not wid:
        raise PetGenerationRunError("WORKER_ID_REQUIRED", "worker_id 가 필요합니다.", status=422)
    run = await _claim_next(wid)
    if not run:
        return None
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
                "PREMIUM_PRODUCT 는 기존 상용 모션(아이들 4종 + COME_CLOSER)만 지원합니다.",
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
    updates: dict[str, Any] = {
        "status": STATUS_QUEUED,
        "last_error": None,
        "retry_count": run.retry_count + 1,
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
    return await _update(run.id, updates)


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
