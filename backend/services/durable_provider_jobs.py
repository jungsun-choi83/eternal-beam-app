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

from .provider_job_contract import FAILED, PENDING, SUCCEEDED, ProviderJobCheck

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


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


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
        "last_polled_at": None,
        "provider_status": None,
        "provider_error": None,
        "result_metadata": {},
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
    return {
        str(row["id"]): {
            key: row.get(key)
            for key in (
                "provider",
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
        for row in list_for_run(run_id)
    }


def _definitive_submit_error(exc: Exception) -> bool:
    return str(getattr(exc, "code", "")) in {
        "PROVIDER_CONTRACT",
        "PROVIDER_REJECTED",
        "PROVIDER_NOT_CONFIGURED",
        "NO_REFERENCE_URLS",
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
        self.name = delegate.name
        self.supports_end_frame = getattr(delegate, "supports_end_frame", False)
        self.supports_motion_reference = getattr(delegate, "supports_motion_reference", False)
        self.reference_budget = getattr(delegate, "reference_budget", 0)
        self.max_prompt_chars = getattr(delegate, "max_prompt_chars", None)

    def available(self) -> bool:
        return self.delegate.available()

    def model_name(self) -> str:
        return self.delegate.model_name()

    def _execute(self, phase_version_id: str, attempt: int, request_hash: str, submit, collect):
        operation = _ensure(
            run_id=self.run_id,
            user_id=self.user_id,
            pet_id=self.pet_id,
            provider_operation=self.provider_operation,
            phase_version_id=phase_version_id,
            provider=self.name,
            model=self.model_name(),
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
            operation = _update(
                str(operation["id"]),
                {
                    "submission_status": SUBMITTED,
                    "external_job_id": external_id,
                    "submitted_at": _now_iso(),
                    "provider_status": submission.provider_status,
                    "provider_error": None,
                    "result_metadata": dict(submission.metadata or {}),
                },
            )
            raise ProviderWorkPending(str(operation["id"]), submission.provider_status)

        try:
            check = self.delegate.check(external_id)
        except Exception as exc:
            _update(
                str(operation["id"]),
                {
                    "last_polled_at": _now_iso(),
                    "provider_error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:1000],
                },
            )
            raise ProviderWorkPending(str(operation["id"]), "POLL_ERROR") from exc

        operation = _update(
            str(operation["id"]),
            {
                "last_polled_at": _now_iso(),
                "provider_status": check.provider_status,
                "provider_error": check.error,
                "result_metadata": dict(check.metadata or {}),
            },
        )
        if check.status == PENDING:
            raise ProviderWorkPending(str(operation["id"]), check.provider_status)
        if check.status == FAILED:
            _update(str(operation["id"]), {"submission_status": FAILED})
            return None, check

        _update(str(operation["id"]), {"submission_status": SUCCEEDED})
        try:
            result = collect(external_id)
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            _update(
                str(operation["id"]),
                {"provider_error": f"{code or type(exc).__name__}: {exc}"[:1000]},
            )
            if code in ("PROVIDER_SCHEMA", "PROVIDER_EMPTY"):
                raise ProviderRecoveryRequired(
                    str(operation["id"]), "완료된 provider 결과를 안전하게 해석할 수 없습니다."
                ) from exc
            raise ProviderWorkPending(str(operation["id"]), "COLLECT_ERROR") from exc
        _update(
            str(operation["id"]),
            {"submission_status": COLLECTED, "provider_error": None},
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
    return [
        DurableVideoProvider(
            provider,
            run_id=run_id,
            user_id=user_id,
            pet_id=pet_id,
            provider_operation=OP_MOTION,
        )
        for provider in providers
        if getattr(provider, "supports_durable_jobs", False)
    ]
