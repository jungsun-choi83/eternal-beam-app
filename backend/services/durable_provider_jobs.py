"""Phase 7D provider submissions with durable, restart-safe polling state.

The Phase 4/5/6 version and candidate tables remain generation authority. This
table is the submission receipt that those builders previously could not write
until a blocking provider call had already finished.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from .provider_job_contract import FAILED, PENDING, SUCCEEDED, TIMED_OUT, ProviderJobCheck

PREPARED = "PREPARED"
SUBMITTING = "SUBMITTING"
SUBMITTED = "SUBMITTED"
COLLECTED = "COLLECTED"
AMBIGUOUS = "AMBIGUOUS"

OP_CANONICAL = "CANONICAL_IMAGE"
OP_KEYFRAME = "KEYFRAME_IMAGE"
OP_MOTION = "MOTION_VIDEO"

_MOCK_JOBS: list[dict[str, Any]] = []


class ProviderWorkPending(Exception):
    def __init__(self, operation_id: str, provider_status: str):
        super().__init__(provider_status)
        self.operation_id = operation_id
        self.provider_status = provider_status


class ProviderRecoveryRequired(Exception):
    code = "PROVIDER_RECOVERY_REQUIRED"

    def __init__(self, operation_id: str, message: str):
        super().__init__(message)
        self.operation_id = operation_id
        self.message = message


def __reset_for_tests() -> None:
    _MOCK_JOBS.clear()


def _table() -> str:
    return os.getenv("PET_GENERATION_PROVIDER_JOBS_TABLE", "pet_generation_provider_jobs")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _client():
    if not _use_db():
        return None
    from ..models.content import _supabase_client

    return _supabase_client()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _job_timeout_seconds() -> int:
    return max(60, int(os.getenv("PROVIDER_JOB_TIMEOUT_SECONDS", "1800")))


def _job_max_consecutive_errors() -> int:
    return max(1, int(os.getenv("PROVIDER_JOB_MAX_CONSECUTIVE_ERRORS", "5")))


def _deadline_iso(from_time: datetime | None = None) -> str:
    base = from_time or _now()
    return datetime.fromtimestamp(
        base.timestamp() + _job_timeout_seconds(), timezone.utc
    ).isoformat()


def _deadline_passed(operation: dict[str, Any]) -> bool:
    deadline = _parse_iso(operation.get("deadline_at"))
    return bool(deadline and _now() > deadline)


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _provider_identity(provider: Any) -> dict[str, str]:
    logical_model = getattr(provider, "logical_model_id", None)
    if not logical_model or logical_model == "abstract":
        logical_model = getattr(provider, "name", "")
    vendor_model = getattr(provider, "vendor_model_id", None)
    if not vendor_model:
        model_name = getattr(provider, "model_name", None)
        vendor_model = model_name() if callable(model_name) else ""
    return {
        "logical_model": str(logical_model),
        "vendor": str(getattr(provider, "vendor_id", None) or "unknown"),
        "adapter": str(
            (
                None
                if getattr(provider, "adapter_id", None) == "VideoGenerationProvider"
                else getattr(provider, "adapter_id", None)
            )
            or type(getattr(provider, "delegate", provider)).__name__
        ),
        "vendor_model": str(vendor_model or ""),
    }


def _find(
    *, run_id: str, provider_operation: str, phase_version_id: str, provider: str, attempt: int
) -> dict[str, Any] | None:
    client = _client()
    if client:
        result = (
            client.table(_table())
            .select("*")
            .eq("run_id", run_id)
            .eq("provider_operation", provider_operation)
            .eq("phase_version_id", phase_version_id)
            .eq("provider", provider)
            .eq("attempt", attempt)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else None
    return next(
        (
            row
            for row in _MOCK_JOBS
            if row["run_id"] == run_id
            and row["provider_operation"] == provider_operation
            and row["phase_version_id"] == phase_version_id
            and row["provider"] == provider
            and row["attempt"] == attempt
        ),
        None,
    )


def _ensure(
    *,
    run_id: str,
    user_id: str,
    pet_id: str,
    provider_operation: str,
    phase_version_id: str,
    provider: str,
    model: str,
    provider_identity: dict[str, str],
    attempt: int,
    request_hash: str,
) -> dict[str, Any]:
    existing = _find(
        run_id=run_id,
        provider_operation=provider_operation,
        phase_version_id=phase_version_id,
        provider=provider,
        attempt=attempt,
    )
    if existing:
        if existing.get("request_fingerprint") != request_hash:
            raise ProviderRecoveryRequired(
                str(existing["id"]), "저장된 provider 요청과 재개 요청의 fingerprint 가 다릅니다."
            )
        return existing

    now = _now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "provider_operation": provider_operation,
        "phase_version_id": phase_version_id,
        "provider": provider,
        "model": model,
        "attempt": attempt,
        "request_fingerprint": request_hash,
        "submission_status": PREPARED,
        "external_job_id": None,
        "submitted_at": None,
        "deadline_at": None,
        "last_polled_at": None,
        "provider_status": None,
        "provider_error": None,
        "consecutive_poll_errors": 0,
        "consecutive_collect_errors": 0,
        "result_metadata": {"provider_identity": dict(provider_identity)},
        "created_at": now,
        "updated_at": now,
    }
    client = _client()
    if client:
        try:
            result = client.table(_table()).insert(row).execute()
            rows = getattr(result, "data", None) or []
            if rows:
                return rows[0]
        except Exception:
            existing = _find(
                run_id=run_id,
                provider_operation=provider_operation,
                phase_version_id=phase_version_id,
                provider=provider,
                attempt=attempt,
            )
            if existing:
                return existing
            raise
    else:
        _MOCK_JOBS.append(row)
        return row
    raise RuntimeError("provider operation insert returned no row")


def _update(operation_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = {**fields, "updated_at": _now_iso()}
    client = _client()
    if client:
        result = client.table(_table()).update(payload).eq("id", operation_id).execute()
        rows = getattr(result, "data", None) or []
        if rows:
            return rows[0]
        fetched = client.table(_table()).select("*").eq("id", operation_id).limit(1).execute()
        rows = getattr(fetched, "data", None) or []
        if rows:
            return rows[0]
        raise RuntimeError("provider operation disappeared")
    row = next((item for item in _MOCK_JOBS if item["id"] == operation_id), None)
    if not row:
        raise RuntimeError("provider operation disappeared")
    row.update(payload)
    return row


def list_for_run(run_id: str) -> list[dict[str, Any]]:
    client = _client()
    if client:
        result = (
            client.table(_table()).select("*").eq("run_id", run_id).order("created_at").execute()
        )
        return getattr(result, "data", None) or []
    return [dict(row) for row in _MOCK_JOBS if row["run_id"] == run_id]


def summary_for_run(run_id: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for row in list_for_run(run_id):
        item = {
            key: row.get(key)
            for key in (
                "provider",
                "model",
                "provider_operation",
                "phase_version_id",
                "external_job_id",
                "submission_status",
                "submitted_at",
                "last_polled_at",
                "provider_status",
                "provider_error",
                "attempt",
            )
        }
        identity = dict((row.get("result_metadata") or {}).get("provider_identity") or {})
        item.update(identity)
        summary[str(row["id"])] = item
    return summary


def _definitive_submit_error(exc: Exception) -> bool:
    return str(getattr(exc, "code", "")) in {
        "PROVIDER_CONTRACT",
        "PROVIDER_REJECTED",
        "PROVIDER_NOT_CONFIGURED",
        "NO_REFERENCE_URLS",
        "NO_REFERENCE_BYTES",
        "NO_START_IMAGE",
    }


class _DurableProviderBase:
    durable_execution = True

    def __init__(
        self,
        delegate: Any,
        *,
        run_id: str,
        user_id: str,
        pet_id: str,
        provider_operation: str,
    ):
        if not getattr(delegate, "supports_durable_jobs", False):
            raise ValueError(f"provider {delegate.name} does not expose durable jobs")
        self.delegate = delegate
        self.run_id = run_id
        self.user_id = user_id
        self.pet_id = pet_id
        self.provider_operation = provider_operation
        identity = _provider_identity(delegate)
        self.logical_model_id = identity["logical_model"]
        self.vendor_id = identity["vendor"]
        self.adapter_id = identity["adapter"]
        self.vendor_model_id = identity["vendor_model"]
        self.name = self.logical_model_id
        self.supports_end_frame = getattr(delegate, "supports_end_frame", False)
        self.supports_motion_reference = getattr(delegate, "supports_motion_reference", False)
        self.reference_budget = getattr(delegate, "reference_budget", 0)
        self.max_prompt_chars = getattr(delegate, "max_prompt_chars", None)

    def available(self) -> bool:
        return self.delegate.available()

    def model_name(self) -> str:
        return self.delegate.model_name()

    def _execute(self, phase_version_id: str, attempt: int, request_hash: str, submit, collect):
        identity = _provider_identity(self)
        operation = _ensure(
            run_id=self.run_id,
            user_id=self.user_id,
            pet_id=self.pet_id,
            provider_operation=self.provider_operation,
            phase_version_id=phase_version_id,
            provider=self.name,
            model=self.model_name(),
            provider_identity=identity,
            attempt=attempt,
            request_hash=request_hash,
        )
        status = str(operation.get("submission_status") or PREPARED)
        if status in (SUBMITTING, AMBIGUOUS):
            raise ProviderRecoveryRequired(
                str(operation["id"]),
                "provider 가 요청을 받았는지 확정할 수 없어 자동 재제출하지 않습니다.",
            )
        if status == FAILED:
            return None, ProviderJobCheck(
                FAILED,
                str(operation.get("provider_status") or FAILED),
                error=str(operation.get("provider_error") or "provider failed"),
            )
        if status == TIMED_OUT:
            # Terminal like FAILED: never re-polled, never resubmitted.
            return None, ProviderJobCheck(
                TIMED_OUT,
                str(operation.get("provider_status") or TIMED_OUT),
                error=str(operation.get("provider_error") or "provider job timed out"),
            )

        external_id = str(operation.get("external_job_id") or "")
        if not external_id:
            operation = _update(
                str(operation["id"]),
                {"submission_status": SUBMITTING, "provider_error": None},
            )
            try:
                submission = submit()
            except Exception as exc:
                if _definitive_submit_error(exc):
                    _update(
                        str(operation["id"]),
                        {
                            "submission_status": FAILED,
                            "provider_status": FAILED,
                            "provider_error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:1000],
                        },
                    )
                    raise
                _update(
                    str(operation["id"]),
                    {
                        "submission_status": AMBIGUOUS,
                        "provider_error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:1000],
                    },
                )
                raise ProviderRecoveryRequired(
                    str(operation["id"]),
                    "provider 제출 응답 전에 연결이 끊겨 접수 여부를 확정할 수 없습니다.",
                ) from exc
            external_id = str(submission.external_job_id or "")
            if not external_id:
                _update(str(operation["id"]), {"submission_status": AMBIGUOUS})
                raise ProviderRecoveryRequired(
                    str(operation["id"]), "provider 제출은 성공했지만 external_job_id 가 없습니다."
                )
            submitted_at = _now()
            operation = _update(
                str(operation["id"]),
                {
                    "submission_status": SUBMITTED,
                    "external_job_id": external_id,
                    "submitted_at": submitted_at.isoformat(),
                    "deadline_at": _deadline_iso(submitted_at),
                    "provider_status": submission.provider_status,
                    "provider_error": None,
                    "result_metadata": {
                        **dict(operation.get("result_metadata") or {}),
                        **dict(submission.metadata or {}),
                        "provider_identity": identity,
                    },
                },
            )
            # A synchronous provider (GPT Image) already ran the paid request
            # inside submit() and reported its terminal status — the receipt
            # above already persisted SUBMITTED with the completed result in
            # result_metadata, so a crash right here still recovers exactly
            # like the async path (external_job_id set → poll/collect, never
            # resubmitted). Only an async provider (provider_status still
            # PENDING) needs a later worker tick — fall through to the same
            # check/collect logic below instead of yielding, so the paid
            # result is consumed in this same tick rather than waiting for a
            # poll cycle that would just re-derive what we already know.
            if submission.provider_status != SUCCEEDED:
                raise ProviderWorkPending(str(operation["id"]), submission.provider_status)

        check_persisted = getattr(self.delegate, "check_persisted", None)

        def _poll_provider() -> ProviderJobCheck:
            return (
                check_persisted(external_id, dict(operation.get("result_metadata") or {}))
                if callable(check_persisted)
                else self.delegate.check(external_id)
            )

        if _deadline_passed(operation):
            # The receipt's deadline has passed, but the external job may still
            # be alive. Do NOT convert this straight to a terminal timeout that
            # would let the caller submit a paid fallback - reconcile with the
            # provider one final time first. external_job_id is never touched
            # here and submit() is never called again, so this can only ever
            # observe the existing attempt, never resubmit it.
            try:
                check = _poll_provider()
            except Exception as exc:
                error_message = (
                    f"provider job exceeded its {_job_timeout_seconds()}s deadline and the "
                    f"final status reconciliation could not confirm its outcome: "
                    f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
                )[:1000]
                _update(
                    str(operation["id"]),
                    {
                        "submission_status": AMBIGUOUS,
                        "provider_error": error_message,
                        "last_polled_at": _now_iso(),
                    },
                )
                raise ProviderRecoveryRequired(str(operation["id"]), error_message) from exc
        else:
            try:
                check = _poll_provider()
            except Exception as exc:
                poll_errors = int(operation.get("consecutive_poll_errors") or 0) + 1
                error_message = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:1000]
                if poll_errors >= _job_max_consecutive_errors():
                    timeout_error = (
                        f"provider poll failed {poll_errors} times in a row "
                        f"(limit {_job_max_consecutive_errors()}): {error_message}"
                    )
                    _update(
                        str(operation["id"]),
                        {
                            "submission_status": TIMED_OUT,
                            "provider_status": TIMED_OUT,
                            "provider_error": timeout_error,
                            "last_polled_at": _now_iso(),
                            "consecutive_poll_errors": poll_errors,
                        },
                    )
                    return None, ProviderJobCheck(TIMED_OUT, "POLL_ERROR", error=timeout_error)
                _update(
                    str(operation["id"]),
                    {
                        "last_polled_at": _now_iso(),
                        "provider_error": error_message,
                        "consecutive_poll_errors": poll_errors,
                    },
                )
                raise ProviderWorkPending(str(operation["id"]), "POLL_ERROR") from exc

        check_metadata = dict(check.metadata or {})
        # Synchronous durable providers (GPT Image) persist their completed
        # result in the submission receipt.  Their subsequent check has no
        # remote task body, so an empty check payload must not erase the
        # recoverable result.  Runway checks remain authoritative when present.
        result_metadata = {
            **dict(operation.get("result_metadata") or {}),
            **check_metadata,
            "provider_identity": identity,
        }
        operation = _update(
            str(operation["id"]),
            {
                "last_polled_at": _now_iso(),
                "provider_status": check.provider_status,
                "provider_error": check.error,
                "result_metadata": result_metadata,
                "consecutive_poll_errors": 0,
            },
        )
        if check.status == PENDING:
            raise ProviderWorkPending(str(operation["id"]), check.provider_status)
        if check.status == FAILED:
            _update(str(operation["id"]), {"submission_status": FAILED})
            return None, check

        _update(str(operation["id"]), {"submission_status": SUCCEEDED})
        try:
            collect_persisted = getattr(self.delegate, "collect_persisted", None)
            result = (
                collect_persisted(
                    external_id, dict(operation.get("result_metadata") or {})
                )
                if callable(collect_persisted)
                else collect(external_id)
            )
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            error_message = f"{code or type(exc).__name__}: {exc}"[:1000]
            if code in ("PROVIDER_SCHEMA", "PROVIDER_EMPTY"):
                _update(str(operation["id"]), {"provider_error": error_message})
                raise ProviderRecoveryRequired(
                    str(operation["id"]), "완료된 provider 결과를 안전하게 해석할 수 없습니다."
                ) from exc
            collect_errors = int(operation.get("consecutive_collect_errors") or 0) + 1
            if collect_errors >= _job_max_consecutive_errors():
                timeout_error = (
                    f"provider collect failed {collect_errors} times in a row "
                    f"(limit {_job_max_consecutive_errors()}): {error_message}"
                )
                _update(
                    str(operation["id"]),
                    {
                        "submission_status": TIMED_OUT,
                        "provider_status": TIMED_OUT,
                        "provider_error": timeout_error,
                        "consecutive_collect_errors": collect_errors,
                    },
                )
                return None, ProviderJobCheck(TIMED_OUT, "COLLECT_ERROR", error=timeout_error)
            _update(
                str(operation["id"]),
                {"provider_error": error_message, "consecutive_collect_errors": collect_errors},
            )
            raise ProviderWorkPending(str(operation["id"]), "COLLECT_ERROR") from exc
        _update(
            str(operation["id"]),
            {"submission_status": COLLECTED, "provider_error": None, "consecutive_collect_errors": 0},
        )
        return result, check


class DurableImageProvider(_DurableProviderBase):
    def generate(self, references, prompt, output_spec, metadata):
        from .canonical_image_providers import CanonicalProviderError

        phase_id = str(
            metadata.get("canonical_version_id") or metadata.get("keyframe_id") or ""
        )
        if not phase_id:
            raise CanonicalProviderError(
                "PROVIDER_CONTRACT", "durable image request 에 phase version id 가 없습니다."
            )
        attempt = int(metadata.get("attempt") or 1)
        request_hash = _fingerprint(
            {
                "operation": self.provider_operation,
                "phase_version_id": phase_id,
                "provider_identity": _provider_identity(self),
                "model": self.model_name(),
                "reference_ids": [getattr(reference, "reference_id", "") for reference in references],
                "prompt": prompt,
                "output_spec": output_spec,
            }
        )
        result, check = self._execute(
            phase_id,
            attempt,
            request_hash,
            lambda: self.delegate.submit(references, prompt, output_spec, metadata),
            self.delegate.collect,
        )
        if result is None:
            raise CanonicalProviderError(
                "PROVIDER_FAILED", f"{check.provider_status}: {check.error or 'provider failed'}"
            )
        return result


class DurableVideoProvider(_DurableProviderBase):
    def generate(self, request):
        from .video_motion_providers import VideoProviderError

        phase_id = str(request.metadata.get("motion_version_id") or "")
        if not phase_id:
            raise VideoProviderError(
                "PROVIDER_CONTRACT", "durable video request 에 motion_version_id 가 없습니다."
            )
        attempt = int(request.metadata.get("attempt") or 1)
        request_hash = _fingerprint(
            {
                "operation": self.provider_operation,
                "phase_version_id": phase_id,
                "provider_identity": _provider_identity(self),
                "model": self.model_name(),
                "prompt": request.prompt,
                "output_spec": request.output_spec,
                "start_keyframe_id": request.metadata.get("start_keyframe_id"),
                "target_keyframe_id": request.metadata.get("target_keyframe_id"),
                "motion_reference_id": request.metadata.get("motion_reference_id"),
            }
        )
        result, check = self._execute(
            phase_id,
            attempt,
            request_hash,
            lambda: self.delegate.submit(request),
            self.delegate.collect,
        )
        if result is None:
            raise VideoProviderError(
                "PROVIDER_FAILED", f"{check.provider_status}: {check.error or 'provider failed'}"
            )
        return result


def durable_image_providers(
    providers: Sequence[Any], *, run_id: str, user_id: str, pet_id: str, operation: str
) -> list[DurableImageProvider]:
    return [
        DurableImageProvider(
            provider,
            run_id=run_id,
            user_id=user_id,
            pet_id=pet_id,
            provider_operation=operation,
        )
        for provider in providers
        if getattr(provider, "supports_durable_jobs", False)
    ]


def durable_video_providers(
    providers: Sequence[Any], *, run_id: str, user_id: str, pet_id: str
) -> list[DurableVideoProvider]:
    def supports_contract(provider: Any) -> bool:
        return bool(getattr(provider, "supports_durable_jobs", False)) and all(
            callable(getattr(provider, method, None))
            for method in ("submit", "check", "collect")
        )

    return [
        DurableVideoProvider(
            provider,
            run_id=run_id,
            user_id=user_id,
            pet_id=pet_id,
            provider_operation=OP_MOTION,
        )
        for provider in providers
        if supports_contract(provider)
    ]
