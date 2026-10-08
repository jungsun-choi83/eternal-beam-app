"""Reusable pet cutout generation and strict-lineage persistence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from . import pet_reference_service, supabase_assets


@dataclass(frozen=True)
class GeneratedCutout:
    png: bytes
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PreparedCutout:
    original: pet_reference_service.PetReference
    cutout: pet_reference_service.PetReference
    generated: bool


@dataclass(frozen=True)
class CutoutPreparationFailure:
    original: pet_reference_service.PetReference
    code: str


@dataclass(frozen=True)
class PreparedCutoutSet:
    pairs: tuple[PreparedCutout, ...]
    failures: tuple[CutoutPreparationFailure, ...]


def generate_vitmatte_cutout_sync(
    data: bytes,
    *,
    model_name: str | None = None,
    segmenter: str | None = None,
    debug_artifacts: dict[str, bytes] | None = None,
    matte_function: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    quality_function: Callable[[bytes], dict[str, Any]] | None = None,
) -> GeneratedCutout:
    """Run the existing ViTMatte implementation on the calling thread."""
    if not data:
        raise pet_reference_service.PetReferenceError(
            "PET_REFERENCE_EMPTY", "The original image is empty."
        )

    # Keep heavyweight model imports out of unrelated router import paths.
    if matte_function is None:
        from .vitmatte_service import matte_foreground_with_meta

        matte_function = matte_foreground_with_meta
    if quality_function is None:
        from .cutout_quality import analyze_alpha_fur_edge

        quality_function = analyze_alpha_fur_edge

    png, vitmatte_meta = matte_function(
        data,
        model_name=model_name,
        segmenter=segmenter,
        debug_artifacts=debug_artifacts,
    )
    diagnostics = {
        **quality_function(png),
        **vitmatte_meta,
        "refined": True,
        "refinement_type": "vitmatte",
        "second_pass": False,
    }
    return GeneratedCutout(png=png, diagnostics=diagnostics)


async def generate_vitmatte_cutout(
    data: bytes,
    *,
    model_name: str | None = None,
    segmenter: str | None = None,
    debug_artifacts: dict[str, bytes] | None = None,
    matte_function: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    quality_function: Callable[[bytes], dict[str, Any]] | None = None,
) -> GeneratedCutout:
    """Async-shaped wrapper that deliberately preserves same-thread inference."""
    return generate_vitmatte_cutout_sync(
        data,
        model_name=model_name,
        segmenter=segmenter,
        debug_artifacts=debug_artifacts,
        matte_function=matte_function,
        quality_function=quality_function,
    )


def select_preparation_original(
    refs: list[pet_reference_service.PetReference],
) -> pet_reference_service.PetReference:
    """Choose the oldest accepted, recorded original deterministically."""
    candidates = [
        ref
        for ref in pet_reference_service.active_originals(refs)
        if ref.recorded and ref.id and ref.content_hash and ref.object_path
    ]
    if not candidates:
        raise pet_reference_service.PetReferenceError(
            "PHASE1_ORIGINAL_MISSING",
            "An accepted recorded original reference is required.",
            status=409,
        )
    return min(
        candidates,
        key=lambda ref: (ref.version, ref.created_at or "", ref.id or ""),
    )


def _preparation_originals(
    refs: list[pet_reference_service.PetReference],
) -> list[pet_reference_service.PetReference]:
    candidates = [
        ref
        for ref in pet_reference_service.active_originals(refs)
        if ref.recorded and ref.id and ref.content_hash and ref.object_path
    ]
    return sorted(
        candidates,
        key=lambda ref: (ref.version, ref.created_at or "", ref.id or ""),
    )


def _valid_strict_cutout(
    refs: list[pet_reference_service.PetReference],
    original: pet_reference_service.PetReference,
) -> pet_reference_service.PetReference | None:
    cutout = pet_reference_service.strict_cutout_for_original(refs, original.id)
    if (
        cutout
        and cutout.recorded
        and cutout.id
        and cutout.parent_reference_id == original.id
        and cutout.user_id == original.user_id
        and cutout.pet_id == original.pet_id
        and cutout.content_id == original.content_id
        and cutout.acceptance_state == pet_reference_service.STATE_ACCEPTED
    ):
        return cutout
    return None


def _failure_code(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").strip()
    if code and code.replace("_", "").isalnum() and code.upper() == code:
        return code
    return "CUTOUT_PREPARATION_FAILED"


async def _prepare_original_cutout(
    *,
    user_id: str,
    original: pet_reference_service.PetReference,
) -> PreparedCutout:
    original_bytes = await supabase_assets.download_asset_from_storage(
        original.object_path, bucket=original.bucket
    )
    generated = await generate_vitmatte_cutout(original_bytes)
    cutout = await record_strict_cutout(
        user_id=user_id,
        content_id=original.content_id,
        original=original,
        cutout_png=generated.png,
        diagnostics=generated.diagnostics,
    )
    return PreparedCutout(original=original, cutout=cutout, generated=True)


async def prepare_strict_cutouts(
    *, user_id: str, pet_id: str
) -> PreparedCutoutSet:
    """Prepare every accepted original independently, retaining partial success."""
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    async with pet_reference_service.pet_input_gate(pid).shared():
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
        originals = _preparation_originals(refs)
        if not originals:
            # Preserve the existing, stable missing-original error contract.
            select_preparation_original(refs)

        pairs: list[PreparedCutout] = []
        failures: list[CutoutPreparationFailure] = []
        first_error: Exception | None = None

        for original in originals:
            existing = _valid_strict_cutout(refs, original)
            if existing:
                pairs.append(
                    PreparedCutout(
                        original=original,
                        cutout=existing,
                        generated=False,
                    )
                )
                continue

            try:
                prepared = await _prepare_original_cutout(
                    user_id=uid,
                    original=original,
                )
                pairs.append(prepared)
                # record_strict_cutout may have reactivated or replaced a row;
                # use a fresh ledger for every subsequent reuse decision.
                refs = await pet_reference_service.list_references(
                    user_id=uid, pet_id=pid
                )
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                failures.append(
                    CutoutPreparationFailure(
                        original=original,
                        code=_failure_code(exc),
                    )
                )

        if not pairs:
            if first_error is not None:
                raise first_error
            raise pet_reference_service.PetReferenceError(
                "PHASE1_INTAKE_INCOMPLETE",
                "No strict original and cutout pair could be prepared.",
                status=409,
            )

        refreshed = await pet_reference_service.list_references(
            user_id=uid, pet_id=pid
        )
        verified: list[PreparedCutout] = []
        for prepared in pairs:
            cutout = _valid_strict_cutout(refreshed, prepared.original)
            if cutout:
                verified.append(
                    PreparedCutout(
                        original=prepared.original,
                        cutout=cutout,
                        generated=prepared.generated,
                    )
                )

        if not verified:
            raise pet_reference_service.PetReferenceError(
                "PHASE1_INTAKE_INCOMPLETE",
                "No strict original and cutout pair could be verified.",
                status=409,
            )
        return PreparedCutoutSet(
            pairs=tuple(verified),
            failures=tuple(failures),
        )


async def record_strict_cutout(
    *,
    user_id: str,
    content_id: str,
    original: pet_reference_service.PetReference,
    cutout_png: bytes,
    diagnostics: dict[str, Any] | None = None,
) -> pet_reference_service.PetReference:
    """Persist one PNG cutout with an explicit, validated original parent."""
    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    expected_pet_id = pet_reference_service.pet_id_for_content(cid)
    if (
        not uid
        or not cid
        or not cutout_png
        or not original.id
        or not original.content_hash
        or not original.recorded
        or original.role != pet_reference_service.ROLE_ORIGINAL
        or original.acceptance_state != pet_reference_service.STATE_ACCEPTED
        or original.user_id != uid
        or original.content_id != cid
        or original.pet_id != expected_pet_id
    ):
        raise pet_reference_service.PetReferenceError(
            "PET_REFERENCE_PARENT_INVALID",
            "The cutout parent reference is invalid.",
            status=409,
        )

    cut_hash = hashlib.sha256(cutout_png).hexdigest()
    ledger = await pet_reference_service.list_references(
        user_id=uid, pet_id=original.pet_id
    )
    accepted = pet_reference_service.STATE_ACCEPTED
    linked = [
        ref
        for ref in ledger
        if ref.role == pet_reference_service.ROLE_DERIVED
        and (ref.derived_kind or "").startswith("cutout")
        and ref.parent_reference_id == original.id
    ]

    def _cut_hash(ref: pet_reference_service.PetReference) -> str:
        return str((ref.diagnostics or {}).get("content_hash") or "")

    match = next(
        (ref for ref in linked if _cut_hash(ref) == cut_hash), None
    ) or next(
        (
            ref
            for ref in linked
            if ref.acceptance_state == accepted and not _cut_hash(ref)
        ),
        None,
    )
    others_active = [
        ref for ref in linked if ref.acceptance_state == accepted and ref is not match
    ]
    if others_active:
        await pet_reference_service.supersede_cutouts(
            user_id=uid,
            pet_id=original.pet_id,
            reference_ids=[str(ref.id) for ref in others_active if ref.id],
        )

    if match and match.acceptance_state == accepted:
        return match

    if match:
        cut_path = match.object_path
    else:
        cut_path = f"{uid}/{cid}/references/cutout_{original.content_hash[:16]}.png"
        if any(
            ref.role == pet_reference_service.ROLE_DERIVED
            and ref.object_path == cut_path
            for ref in ledger
        ):
            cut_path = (
                f"{uid}/{cid}/references/"
                f"cutout_{original.content_hash[:16]}_{cut_hash[:16]}.png"
            )
        await supabase_assets.upload_asset_to_storage(cut_path, cutout_png, "image/png")

    derived = await pet_reference_service.record_derived(
        user_id=uid,
        content_id=cid,
        object_path=cut_path,
        derived_kind="cutout_reference",
        parent_reference_id=original.id,
        mime_type="image/png",
        diagnostics={
            **(diagnostics or {}),
            "content_hash": cut_hash,
            "bytes_size": len(cutout_png),
        },
    )
    if (
        not derived.recorded
        or not derived.id
        or derived.parent_reference_id != original.id
        or derived.user_id != uid
        or derived.content_id != cid
        or derived.pet_id != expected_pet_id
        or derived.acceptance_state != accepted
    ):
        raise pet_reference_service.PetReferenceError(
            "PHASE1_CUTOUT_PERSIST_FAILED",
            "The linked cutout reference could not be stored.",
            status=503,
        )
    return derived


async def prepare_strict_cutout(
    *, user_id: str, pet_id: str
) -> PreparedCutout:
    """Reuse or generate the single strict original/cutout pair intake needs."""
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    async with pet_reference_service.pet_input_gate(pid).shared():
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
        ready, original, cutout = pet_reference_service.intake_readiness(refs)
        if ready and original and cutout:
            return PreparedCutout(original=original, cutout=cutout, generated=False)

        original = select_preparation_original(refs)
        prepared = await _prepare_original_cutout(user_id=uid, original=original)
        cutout = prepared.cutout

        refreshed = await pet_reference_service.list_references(
            user_id=uid, pet_id=pid
        )
        ready, ready_original, ready_cutout = pet_reference_service.intake_readiness(
            refreshed
        )
        if (
            not ready
            or not ready_original
            or not ready_cutout
            or ready_cutout.parent_reference_id != ready_original.id
        ):
            raise pet_reference_service.PetReferenceError(
                "PHASE1_INTAKE_INCOMPLETE",
                "The strict original and cutout lineage is incomplete.",
                status=409,
            )
        return PreparedCutout(
            original=ready_original,
            cutout=ready_cutout,
            generated=True,
        )
