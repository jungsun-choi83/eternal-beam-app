"""Durable READY-only asset contract for layered Shaker V2 playback.

This service never generates media.  It owns three narrow responsibilities:

* reserve an immutable pet/scene/version identity before processing starts;
* atomically publish a complete manifest after uploads and QA succeed;
* resolve only READY manifests that match the share owner, pet and scene.

All canonical media locations are bucket/object paths.  Signed URLs are created
by the public Shaker router for each request and are never stored here.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

PACKED_ENCODING = "packed-vstack-h264"
PACKED_LAYOUT = "rgb-top-alpha-bottom"
READY = "READY"
PROCESSING = "PROCESSING"
FAILED = "FAILED"

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,180}$")


class LayeredAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageRef:
    bucket: str
    object_path: str


@dataclass(frozen=True)
class LayeredAsset:
    asset_id: str
    user_id: str
    pet_id: str
    content_id: str
    scene_id: str
    asset_version: str
    status: str
    pet: Optional[StorageRef] = None
    pet_encoding: Optional[str] = None
    alpha_layout: Optional[str] = None
    background_type: Optional[str] = None
    background: Optional[StorageRef] = None
    placement: dict[str, Any] | None = None
    shadow: dict[str, Any] | None = None
    foreground_type: Optional[str] = None
    foreground: Optional[StorageRef] = None
    qa: dict[str, Any] | None = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def complete_ready(self) -> bool:
        return (
            self.status == READY
            and self.pet_id == f"pet_{self.content_id}"
            and self.pet is not None
            and self.pet_encoding == PACKED_ENCODING
            and self.alpha_layout == PACKED_LAYOUT
            and self.background_type in ("image", "video")
            and self.background is not None
            and isinstance(self.placement, dict)
            and self.placement.get("mode") in ("scene-frame", "anchored")
            and isinstance(self.qa, dict)
            and self.qa.get("passed") is True
            and (
                (self.foreground_type is None and self.foreground is None)
                or (
                    self.foreground_type in ("image", "video")
                    and self.foreground is not None
                )
            )
        )


def _table() -> str:
    return os.getenv("SHAKER_LAYERED_ASSETS_TABLE", "shaker_layered_assets")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_ASSETS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_ASSETS.clear()


def _clean_segment(value: str, label: str) -> str:
    v = (value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(v) or v in (".", ".."):
        raise LayeredAssetError(f"invalid {label}")
    return v


def mint_version() -> str:
    return f"v{uuid.uuid4().hex[:20]}"


def mint_asset_id() -> str:
    return f"lay_{uuid.uuid4().hex[:24]}"


def versioned_object_path(
    *, pet_id: str, scene_id: str, asset_version: str, filename: str
) -> str:
    """Return an immutable V2 path; no user/email appears in public metadata."""
    pet = _clean_segment(pet_id, "pet_id")
    scene = _clean_segment(scene_id, "scene_id")
    version = _clean_segment(asset_version, "asset_version")
    name = _clean_segment(filename, "filename")
    return f"layered/{pet}/{scene}/{version}/{name}"


def _ref(bucket: Any, path: Any) -> Optional[StorageRef]:
    b = str(bucket or "").strip()
    p = str(path or "").strip()
    return StorageRef(b, p) if b and p else None


def _to_asset(row: dict[str, Any]) -> LayeredAsset:
    return LayeredAsset(
        asset_id=str(row.get("asset_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        content_id=str(row.get("content_id") or ""),
        scene_id=str(row.get("scene_id") or ""),
        asset_version=str(row.get("asset_version") or ""),
        status=str(row.get("status") or ""),
        pet=_ref(row.get("pet_bucket"), row.get("pet_object_path")),
        pet_encoding=(row.get("pet_encoding") or None),
        alpha_layout=(row.get("alpha_layout") or None),
        background_type=(row.get("background_type") or None),
        background=_ref(row.get("background_bucket"), row.get("background_object_path")),
        placement=dict(row.get("placement") or {}),
        shadow=(dict(row["shadow"]) if isinstance(row.get("shadow"), dict) else None),
        foreground_type=(row.get("foreground_type") or None),
        foreground=_ref(row.get("foreground_bucket"), row.get("foreground_object_path")),
        qa=(dict(row["qa"]) if isinstance(row.get("qa"), dict) else None),
        error=(row.get("error") or None),
        created_at=(row.get("created_at") or None),
        updated_at=(row.get("updated_at") or None),
    )


def processing_is_stale(
    asset: LayeredAsset,
    *,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
) -> bool:
    """Allow an interrupted optional worker to be retried on the next V1 hit."""
    if asset.status != PROCESSING:
        return False
    raw = asset.updated_at or asset.created_at
    if not raw:
        return True
    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    current = now or datetime.now(timezone.utc)
    age_limit = max_age_seconds
    if age_limit is None:
        age_limit = int(os.getenv("SHAKER_LAYERED_PROCESSING_STALE_SECONDS", "7200"))
    return (current - updated).total_seconds() >= max(300, age_limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def reserve(
    *, user_id: str, pet_id: str, content_id: str, scene_id: str,
    placement: dict[str, Any] | None = None,
) -> LayeredAsset:
    uid = (user_id or "").strip()
    pid = _clean_segment(pet_id, "pet_id")
    cid = _clean_segment(content_id, "content_id")
    sid = _clean_segment(scene_id, "scene_id")
    if not uid:
        raise LayeredAssetError("user_id is required")
    if pid != f"pet_{cid}":
        raise LayeredAssetError("pet_id/content_id binding is invalid")

    row: dict[str, Any] = {
        "asset_id": mint_asset_id(),
        "user_id": uid,
        "pet_id": pid,
        "content_id": cid,
        "scene_id": sid,
        "asset_version": mint_version(),
        "status": PROCESSING,
        "placement": dict(placement or {"mode": "scene-frame"}),
        "created_at": _now(),
        "updated_at": _now(),
    }
    if _use_db() and _supabase():
        try:
            result = _supabase().table(_table()).insert(row).execute()
            data = getattr(result, "data", None) or [row]
            return _to_asset(data[0])
        except Exception as exc:  # noqa: BLE001
            raise LayeredAssetError("could not reserve layered asset") from exc
    _MOCK_ASSETS[row["asset_id"]] = row
    return _to_asset(row)


def _ready_patch(
    *, pet: StorageRef, background_type: str, background: StorageRef,
    qa: dict[str, Any], shadow: dict[str, Any] | None,
    placement: dict[str, Any] | None,
    foreground_type: str | None, foreground: StorageRef | None,
) -> dict[str, Any]:
    bg_type = (background_type or "").strip().lower()
    if bg_type not in ("image", "video"):
        raise LayeredAssetError("invalid background_type")
    if not pet.bucket or not pet.object_path or not background.bucket or not background.object_path:
        raise LayeredAssetError("required storage path missing")
    if not isinstance(qa, dict) or qa.get("passed") is not True:
        raise LayeredAssetError("QA must pass before publication")
    fg_type = (foreground_type or "").strip().lower() or None
    if bool(fg_type) != bool(foreground):
        raise LayeredAssetError("foreground type/path must be provided together")
    if fg_type and fg_type not in ("image", "video"):
        raise LayeredAssetError("invalid foreground_type")
    placement_patch: dict[str, Any] = {}
    if placement is not None:
        mode = str(placement.get("mode") or "")
        if mode not in ("scene-frame", "anchored"):
            raise LayeredAssetError("invalid placement mode")
        placement_patch["placement"] = dict(placement)
    return {
        "status": READY,
        "pet_bucket": pet.bucket,
        "pet_object_path": pet.object_path,
        "pet_encoding": PACKED_ENCODING,
        "alpha_layout": PACKED_LAYOUT,
        "background_type": bg_type,
        "background_bucket": background.bucket,
        "background_object_path": background.object_path,
        "shadow": shadow,
        "foreground_type": fg_type,
        "foreground_bucket": foreground.bucket if foreground else None,
        "foreground_object_path": foreground.object_path if foreground else None,
        "qa": qa,
        "error": None,
        "ready_at": _now(),
        "updated_at": _now(),
        **placement_patch,
    }


async def publish_ready(
    asset_id: str, *, pet: StorageRef, background_type: str, background: StorageRef,
    qa: dict[str, Any], shadow: dict[str, Any] | None = None,
    placement: dict[str, Any] | None = None,
    foreground_type: str | None = None, foreground: StorageRef | None = None,
) -> LayeredAsset:
    aid = _clean_segment(asset_id, "asset_id")
    patch = _ready_patch(
        pet=pet, background_type=background_type, background=background, qa=qa,
        shadow=shadow, placement=placement,
        foreground_type=foreground_type, foreground=foreground,
    )
    if _use_db() and _supabase():
        try:
            result = (
                _supabase().table(_table()).update(patch)
                .eq("asset_id", aid).eq("status", PROCESSING).execute()
            )
            data = getattr(result, "data", None) or []
            if not data:
                raise LayeredAssetError("layered reservation not found or already finalized")
            asset = _to_asset(data[0])
        except LayeredAssetError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LayeredAssetError("could not publish layered asset") from exc
    else:
        row = _MOCK_ASSETS.get(aid)
        if not row or row.get("status") != PROCESSING:
            raise LayeredAssetError("layered reservation not found or already finalized")
        row.update(patch)
        asset = _to_asset(row)
    if not asset.complete_ready:
        raise LayeredAssetError("published manifest is incomplete")
    return asset


async def mark_failed(asset_id: str, error: str) -> None:
    aid = _clean_segment(asset_id, "asset_id")
    patch = {"status": FAILED, "error": str(error or "unknown")[:2000], "updated_at": _now()}
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("asset_id", aid).execute()
        except Exception:  # noqa: BLE001 — failure recording must not mask the original failure
            logger.warning("layered asset failure could not be recorded (asset=%s)", aid, exc_info=True)
        return
    if aid in _MOCK_ASSETS:
        _MOCK_ASSETS[aid].update(patch)


async def get_ready(
    asset_id: str, *, user_id: str, pet_id: str, scene_id: str | None = None,
) -> Optional[LayeredAsset]:
    aid = (asset_id or "").strip()
    if not aid:
        return None
    row: dict[str, Any] | None = None
    if _use_db() and _supabase():
        try:
            query = (
                _supabase().table(_table()).select("*")
                .eq("asset_id", aid).eq("user_id", user_id).eq("pet_id", pet_id)
                .eq("status", READY)
            )
            if scene_id:
                query = query.eq("scene_id", scene_id)
            result = query.limit(1).execute()
            data = getattr(result, "data", None) or []
            row = data[0] if data else None
        except Exception as exc:  # noqa: BLE001
            raise LayeredAssetError("could not read layered manifest") from exc
    else:
        candidate = _MOCK_ASSETS.get(aid)
        if candidate:
            row = candidate
    asset = _to_asset(row) if row else None
    if not asset or not asset.complete_ready:
        return None
    if asset.user_id != user_id or asset.pet_id != pet_id:
        return None
    if scene_id and asset.scene_id != scene_id:
        return None
    return asset


async def latest_ready_for_scene(
    *, user_id: str, pet_id: str, scene_id: str,
) -> Optional[LayeredAsset]:
    sid = (scene_id or "").strip()
    if not sid:
        return None
    rows: list[dict[str, Any]] = []
    if _use_db() and _supabase():
        try:
            result = (
                _supabase().table(_table()).select("*")
                .eq("user_id", user_id).eq("pet_id", pet_id).eq("scene_id", sid)
                .eq("status", READY).order("created_at", desc=True).limit(1).execute()
            )
            rows = getattr(result, "data", None) or []
        except Exception as exc:  # noqa: BLE001
            raise LayeredAssetError("could not read layered manifest") from exc
    else:
        rows = sorted(
            (
                row for row in _MOCK_ASSETS.values()
                if row.get("user_id") == user_id and row.get("pet_id") == pet_id
                and row.get("scene_id") == sid and row.get("status") == READY
            ),
            key=lambda row: str(row.get("created_at") or ""), reverse=True,
        )[:1]
    asset = _to_asset(rows[0]) if rows else None
    return asset if asset and asset.complete_ready else None


async def processing_or_ready_for_scene(
    *, user_id: str, pet_id: str, scene_id: str,
) -> Optional[LayeredAsset]:
    """Idempotency guard for the optional post-process (FAILED may retry)."""
    rows: list[dict[str, Any]] = []
    if _use_db() and _supabase():
        try:
            result = (
                _supabase().table(_table()).select("*")
                .eq("user_id", user_id).eq("pet_id", pet_id).eq("scene_id", scene_id)
                .in_("status", [PROCESSING, READY])
                .order("created_at", desc=True).limit(1).execute()
            )
            rows = getattr(result, "data", None) or []
        except Exception as exc:  # noqa: BLE001
            raise LayeredAssetError("could not read layered manifest") from exc
    else:
        rows = sorted(
            (
                row for row in _MOCK_ASSETS.values()
                if row.get("user_id") == user_id and row.get("pet_id") == pet_id
                and row.get("scene_id") == scene_id
                and row.get("status") in (PROCESSING, READY)
            ),
            key=lambda row: str(row.get("created_at") or ""), reverse=True,
        )[:1]
    return _to_asset(rows[0]) if rows else None


async def resolve_for_share(
    *, user_id: str, pet_id: str, scene_id: str | None,
    layered_asset_id: str | None,
) -> Optional[LayeredAsset]:
    """Legacy shares have neither id nor scene and deliberately remain V1."""
    if layered_asset_id:
        return await get_ready(
            layered_asset_id, user_id=user_id, pet_id=pet_id, scene_id=scene_id
        )
    if scene_id:
        return await latest_ready_for_scene(user_id=user_id, pet_id=pet_id, scene_id=scene_id)
    return None
