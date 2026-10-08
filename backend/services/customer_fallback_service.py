"""Business QA vNext customer fallback resolution.

This is the one authoritative fallback ladder after the automatic candidate
budget is exhausted.  It is deliberately read-only with respect to
publication, ownership, and current playback pointers: showing an older asset
for one request must not silently make it the customer's new canonical choice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from . import (
    asset_url_refresh,
    business_qa,
    canonical_pet_service,
    motion_delivery_service,
    motion_publication_service,
    motion_video_service,
    owned_assets,
)


FALLBACK_POLICY_VERSION = "customer-fallback-v1"

TIER_CURRENT_RUN = "CURRENT_RUN_CANDIDATE"
TIER_PREVIOUS_MOTION = "PREVIOUS_PUBLISHED_OR_OWNED_MOTION"
TIER_CANONICAL_IDLE = "CANONICAL_LOCAL_IDLE"
TIER_CANONICAL_STILL = "CANONICAL_STILL"

FORMAT_PACKED_ALPHA = motion_delivery_service.DELIVERY_PACKED_ALPHA
FORMAT_CANONICAL_IDLE = "canonical_local_idle"
FORMAT_CANONICAL_STILL = "canonical_still"


class FallbackInfrastructureError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CustomerFallbackAsset:
    tier: str
    asset_kind: str
    user_id: str
    pet_id: str
    requested_motion_id: str
    delivery_format: Optional[str]
    bucket: Optional[str] = None
    object_path: Optional[str] = None
    source_url: Optional[str] = None
    publication_id: Optional[str] = None
    ownership_asset_id: Optional[str] = None
    motion_version_id: Optional[str] = None
    candidate_id: Optional[str] = None
    canonical_version_id: Optional[str] = None
    canonical_candidate_id: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["version"] = FALLBACK_POLICY_VERSION
        value["provenance"] = dict(self.provenance or {})
        return value


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _qa_result(candidate: Any) -> dict[str, Any]:
    value = _field(candidate, "qa_result", {}) or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _has_hard_failure(candidate: Any) -> bool:
    """True only for an explicit integrity contradiction, never uncertainty."""

    qa_result = _qa_result(candidate)
    receipt = business_qa.receipt(qa_result)
    if receipt is not None:
        return str(receipt.get("integrity_status") or "").upper() == business_qa.FAIL
    # Historical published rows predate business-v1.  Reuse the existing
    # integrity-only classifier; cosmetic legacy FAIL remains eligible while a
    # real integrity reason remains forbidden.
    return not motion_video_service.candidate_is_publishable(
        candidate, mode=motion_video_service.SEVERITY_GATE_INTEGRITY_ONLY
    )


def _storage_url(
    *,
    bucket: Optional[str],
    object_path: Optional[str],
    source_url: Optional[str],
    sign_fn: Callable[[asset_url_refresh.StorageObject], Optional[str]],
) -> Optional[str]:
    b = str(bucket or "").strip()
    path = str(object_path or "").strip()
    if b and path:
        return sign_fn(asset_url_refresh.StorageObject(bucket=b, path=path))
    url = str(source_url or "").strip()
    return asset_url_refresh.refresh_url(url) if url else None


def resolve_fallback_url(
    asset: Mapping[str, Any],
    *,
    sign_fn: Optional[Callable[[asset_url_refresh.StorageObject], Optional[str]]] = None,
) -> str:
    """Resolve a persisted stable fallback locator to a fresh playback URL."""

    url = _storage_url(
        bucket=asset.get("bucket"),
        object_path=asset.get("object_path"),
        source_url=asset.get("source_url"),
        sign_fn=sign_fn or asset_url_refresh.sign_object,
    )
    if not url:
        raise FallbackInfrastructureError(
            "FALLBACK_ASSET_UNAVAILABLE",
            "The selected fallback asset is no longer readable.",
        )
    return str(url)


def _current_candidate_asset(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    candidates: Sequence[Any],
    sign_fn: Callable[[asset_url_refresh.StorageObject], Optional[str]],
) -> Optional[CustomerFallbackAsset]:
    selected = business_qa.best_available_candidate(list(candidates))
    if selected is None or _has_hard_failure(selected):
        return None
    path = str(_field(selected, "derived_video_path", "") or "").strip()
    selected_row = dict(selected) if isinstance(selected, Mapping) else dict(vars(selected))
    fmt = motion_delivery_service.candidate_delivery_format(selected_row)
    if not path or fmt != FORMAT_PACKED_ALPHA:
        return None
    bucket = str(
        _field(selected, "raw_bucket", None) or asset_url_refresh.default_bucket()
    ).strip()
    if not _storage_url(
        bucket=bucket, object_path=path, source_url=None, sign_fn=sign_fn
    ):
        return None
    return CustomerFallbackAsset(
        tier=TIER_CURRENT_RUN,
        asset_kind="motion_video",
        user_id=user_id,
        pet_id=pet_id,
        requested_motion_id=motion_id,
        delivery_format=FORMAT_PACKED_ALPHA,
        bucket=bucket,
        object_path=path,
        motion_version_id=str(_field(selected, "motion_version_id", "") or "") or None,
        candidate_id=str(_field(selected, "id", "") or "") or None,
        provenance={
            "source": "pet_motion_candidates",
            "qa": dict(business_qa.receipt(_qa_result(selected)) or {}),
        },
    )


async def _candidate_id_is_safe(candidate_id: Optional[str]) -> bool:
    cid = str(candidate_id or "").strip()
    if not cid:
        return True  # legacy publication/ownership predating candidate lineage
    try:
        candidate = await motion_delivery_service._load_candidate(cid)
    except motion_delivery_service.MotionDeliveryError as exc:
        if exc.code == "CANDIDATE_NOT_FOUND":
            return True  # retained legacy ledger row; no contradictory evidence
        raise FallbackInfrastructureError(exc.code, exc.message) from exc
    return not _has_hard_failure(candidate)


async def _previous_motion_asset(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    sign_fn: Callable[[asset_url_refresh.StorageObject], Optional[str]],
) -> Optional[CustomerFallbackAsset]:
    mid = motion_id.upper()

    if mid == motion_publication_service.BREATHING:
        rows = await motion_publication_service.list_breathing_publications(user_id, pet_id)
        for row in rows:
            candidate_id = str(row.get("selected_candidate_id") or "") or None
            if not await _candidate_id_is_safe(candidate_id):
                continue
            bucket = str(row.get("bucket") or asset_url_refresh.default_bucket()).strip()
            path = str(row.get("object_path") or "").strip()
            if not path or not _storage_url(
                bucket=bucket, object_path=path, source_url=None, sign_fn=sign_fn
            ):
                continue
            return CustomerFallbackAsset(
                tier=TIER_PREVIOUS_MOTION,
                asset_kind="motion_video",
                user_id=user_id,
                pet_id=pet_id,
                requested_motion_id=mid,
                delivery_format=motion_publication_service.delivery_format_for(None, path),
                bucket=bucket,
                object_path=path,
                publication_id=str(row.get("id") or row.get("publication_id") or "") or None,
                motion_version_id=str(row.get("motion_version_id") or "") or None,
                candidate_id=candidate_id,
                provenance={"source": "pet_motion_publications", "published_at": row.get("published_at")},
            )

    product_key = owned_assets.product_key_for_action(mid)
    for owned in await owned_assets.list_for_pet(user_id, pet_id):
        if owned.product_key != product_key:
            continue
        lineage = dict(owned.lineage or {})
        candidate_id = str(lineage.get("selected_candidate_id") or "") or None
        if not await _candidate_id_is_safe(candidate_id):
            continue
        if not _storage_url(
            bucket=owned.bucket,
            object_path=owned.object_path,
            source_url=owned.video_url,
            sign_fn=sign_fn,
        ):
            continue
        return CustomerFallbackAsset(
            tier=TIER_PREVIOUS_MOTION,
            asset_kind="motion_video",
            user_id=user_id,
            pet_id=pet_id,
            requested_motion_id=mid,
            delivery_format=(
                str(lineage.get("delivery_format"))
                if lineage.get("delivery_format")
                else motion_publication_service.delivery_format_for(None, owned.object_path or "")
            ),
            bucket=owned.bucket,
            object_path=owned.object_path,
            source_url=(owned.video_url if not (owned.bucket and owned.object_path) else None),
            publication_id=str(lineage.get("publication_id") or "") or None,
            ownership_asset_id=owned.asset_id,
            motion_version_id=str(lineage.get("pet_motion_version_id") or "") or None,
            candidate_id=candidate_id,
            provenance={
                "source": "owned_generated_assets",
                "product_key": owned.product_key,
                "source_job_id": owned.source_job_id,
                "created_at": owned.created_at.isoformat(),
            },
        )
    return None


async def _approved_canonical_candidates(
    user_id: str, pet_id: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    versions = await canonical_pet_service._version_rows(pet_id)
    approved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for version in sorted(versions, key=lambda row: int(row.get("version") or 0), reverse=True):
        if (
            str(version.get("user_id") or "") != user_id
            or str(version.get("status") or "") != canonical_pet_service.STATUS_COMPLETE
        ):
            continue
        selected_id = str(version.get("selected_candidate_id") or "").strip()
        if not selected_id:
            continue
        candidates = await canonical_pet_service._candidate_rows(str(version.get("id") or ""))
        candidate = next((row for row in candidates if str(row.get("id")) == selected_id), None)
        if not candidate or not business_qa.is_deliverable(candidate.get("qa_result") or {}):
            continue
        receipt = business_qa.receipt(candidate.get("qa_result") or {})
        if receipt and str(receipt.get("integrity_status") or "").upper() == business_qa.FAIL:
            continue
        approved.append((version, candidate))
    return approved


def _canonical_asset(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    approved: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    local_idle: bool,
    sign_fn: Callable[[asset_url_refresh.StorageObject], Optional[str]],
) -> Optional[CustomerFallbackAsset]:
    path_key = "cutout_object_path" if local_idle else "raw_object_path"
    bucket_key = "cutout_bucket" if local_idle else "raw_bucket"
    for version, candidate in approved:
        path = str(candidate.get(path_key) or "").strip()
        bucket = str(candidate.get(bucket_key) or asset_url_refresh.default_bucket()).strip()
        if not path or not _storage_url(
            bucket=bucket, object_path=path, source_url=None, sign_fn=sign_fn
        ):
            continue
        return CustomerFallbackAsset(
            tier=TIER_CANONICAL_IDLE if local_idle else TIER_CANONICAL_STILL,
            asset_kind="canonical_image",
            user_id=user_id,
            pet_id=pet_id,
            requested_motion_id=motion_id,
            delivery_format=FORMAT_CANONICAL_IDLE if local_idle else FORMAT_CANONICAL_STILL,
            bucket=bucket,
            object_path=path,
            canonical_version_id=str(version.get("id") or "") or None,
            canonical_candidate_id=str(candidate.get("id") or "") or None,
            provenance={
                "source": "pet_canonical_candidates",
                "canonical_version": int(version.get("version") or 0),
                "qa": dict(business_qa.receipt(candidate.get("qa_result") or {}) or {}),
            },
        )
    return None


async def resolve_best_safe_fallback(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    current_candidates: Sequence[Any] = (),
    sign_fn: Optional[Callable[[asset_url_refresh.StorageObject], Optional[str]]] = None,
) -> Optional[CustomerFallbackAsset]:
    """Resolve the first readable, integrity-safe asset in the product ladder."""

    uid, pid, mid = user_id.strip(), pet_id.strip(), motion_id.strip().upper()
    if not uid or not pid or not mid:
        raise FallbackInfrastructureError("FALLBACK_INVALID", "Fallback scope is incomplete.")
    signer = sign_fn or asset_url_refresh.sign_object
    errors: list[str] = []

    try:
        current = _current_candidate_asset(
            user_id=uid,
            pet_id=pid,
            motion_id=mid,
            candidates=current_candidates,
            sign_fn=signer,
        )
        if current:
            return current
    except Exception as exc:
        errors.append(f"current_candidate:{getattr(exc, 'code', type(exc).__name__)}")

    try:
        previous = await _previous_motion_asset(
            user_id=uid, pet_id=pid, motion_id=mid, sign_fn=signer
        )
        if previous:
            return previous
    except Exception as exc:
        errors.append(f"previous_motion:{getattr(exc, 'code', type(exc).__name__)}")

    try:
        approved = await _approved_canonical_candidates(uid, pid)
        idle = _canonical_asset(
            user_id=uid,
            pet_id=pid,
            motion_id=mid,
            approved=approved,
            local_idle=True,
            sign_fn=signer,
        )
        if idle:
            return idle
        still = _canonical_asset(
            user_id=uid,
            pet_id=pid,
            motion_id=mid,
            approved=approved,
            local_idle=False,
            sign_fn=signer,
        )
        if still:
            return still
    except Exception as exc:
        errors.append(f"canonical:{getattr(exc, 'code', type(exc).__name__)}")

    if errors:
        raise FallbackInfrastructureError(
            "FALLBACK_RESOLUTION_UNAVAILABLE",
            "Fallback stores could not be read: " + ", ".join(errors),
        )
    return None
