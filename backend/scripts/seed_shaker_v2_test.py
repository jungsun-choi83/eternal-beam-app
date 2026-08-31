"""Publish the approved one-pet Shaker V2 fixture for real-device testing.

This is an explicit operator command, not an application startup seed.  It:

* reuses one existing canonical pet as the share/manifest identity;
* uploads the approved Goya packed-alpha asset to immutable V2 paths;
* uploads the matching baked Goya clip as the V1 visual fallback;
* publishes separate READY image/video manifests after local alpha QA passes;
* creates new OPS-only shares bound to those exact manifest snapshots.

It never creates a pet, generates media, or changes an existing share.  The
project ref must be supplied on the command line to prevent an accidental write
to a different Supabase project.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

# Importing backend.main applies the repository's normal dotenv precedence.
from backend import main as _backend_main  # noqa: F401
from backend.models.content import _supabase_client
from backend.services import (
    asset_url_refresh,
    pet_registry,
    shaker_layered_assets,
    shaker_share,
    supabase_assets,
)
from backend.services.layered_v2_pipeline import validate_packed_alpha


ROOT = Path(__file__).resolve().parents[2]
PET_ASSET = ROOT / "public" / "demo" / "goya_idle_packed.mp4"
V1_FALLBACK_ASSET = (
    ROOT / "public" / "prototypes" / "shaker-depth" / "goya-forest-baked-v2.mp4"
)
BACKGROUNDS = {
    "image": (ROOT / "public" / "theme-thumbs" / "fresh_forest.jpg", "image/jpeg"),
    "video": (ROOT / "public" / "demo" / "forest.mp4", "video/mp4"),
}
SCENES = {
    "image": "v2-test-image-placement-v2-20260828",
    "video": "v2-test-video-placement-v2-20260828",
}
PLACEMENT = {
    "mode": "anchored",
    "center_x_pct": 50,
    # Match the approved baked Goya fallback: the source silhouette spans
    # roughly 91% of its frame height, so a 90%-high anchored plane places the
    # visible pet at about 82% of the viewport instead of the previous 45%.
    "bottom_pct": 2,
    "height_pct": 90,
    "crop_x_min": 0.27,
    "crop_x_max": 0.73,
}
SHADOW = {
    "kind": "css-contact",
    "opacity": 0.24,
    "blur_px": 11,
    "center_x_pct": 50,
    "bottom_pct": 7,
    "width_pct": 38,
    "height_pct": 4,
}


def _configured_project_ref() -> str:
    host = (urlparse(os.getenv("SUPABASE_URL", "")).hostname or "").lower()
    return host.split(".", 1)[0] if host.endswith(".supabase.co") else host


def _assert_schema() -> None:
    client = _supabase_client()
    if not client:
        raise RuntimeError("Supabase service-role configuration is missing")
    try:
        client.table("shaker_layered_assets").select("asset_id").limit(1).execute()
        client.table("shaker_shares").select("share_id,scene_id,layered_asset_id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "V2 schema is missing. Apply "
            "supabase/migrations/20260923000000_shaker_layered_assets.sql first."
        ) from exc


async def _ready_asset(*, pet: pet_registry.RegisteredPet, mode: str, qa: dict) -> shaker_layered_assets.LayeredAsset:
    scene_id = SCENES[mode]
    existing = await shaker_layered_assets.latest_ready_for_scene(
        user_id=pet.user_id,
        pet_id=pet.pet_id,
        scene_id=scene_id,
    )
    if existing:
        return existing

    reservation = await shaker_layered_assets.reserve(
        user_id=pet.user_id,
        pet_id=pet.pet_id,
        content_id=pet.content_id or "",
        scene_id=scene_id,
        placement=PLACEMENT,
    )
    try:
        pet_object = shaker_layered_assets.versioned_object_path(
            pet_id=pet.pet_id,
            scene_id=scene_id,
            asset_version=reservation.asset_version,
            filename="pet_packed.mp4",
        )
        background_path, background_content_type = BACKGROUNDS[mode]
        background_name = "background.jpg" if mode == "image" else "background.mp4"
        background_object = shaker_layered_assets.versioned_object_path(
            pet_id=pet.pet_id,
            scene_id=scene_id,
            asset_version=reservation.asset_version,
            filename=background_name,
        )
        await supabase_assets.upload_asset_to_storage(
            pet_object,
            PET_ASSET.read_bytes(),
            "video/mp4",
        )
        await supabase_assets.upload_asset_to_storage(
            background_object,
            background_path.read_bytes(),
            background_content_type,
        )
        return await shaker_layered_assets.publish_ready(
            reservation.asset_id,
            pet=shaker_layered_assets.StorageRef(supabase_assets.BUCKET, pet_object),
            background_type=mode,
            background=shaker_layered_assets.StorageRef(
                supabase_assets.BUCKET,
                background_object,
            ),
            qa={**qa, "fixture": "approved-goya-v2-20260828"},
            shadow=SHADOW,
        )
    except Exception as exc:  # noqa: BLE001
        await shaker_layered_assets.mark_failed(reservation.asset_id, str(exc))
        raise


async def _share_url(
    *,
    pet: pet_registry.RegisteredPet,
    asset: shaker_layered_assets.LayeredAsset,
    public_base: str,
) -> dict[str, str]:
    # This is a visual renderer fixture: its V2 layer contains Goya, so its
    # loading/runtime fallback must contain Goya too. Using the selected
    # registry pet's unrelated BREATHING clip makes two different pets appear
    # in sequence while the READY V2 player starts, which is not a renderer
    # defect and invalidates the fallback test.
    fallback_object = shaker_layered_assets.versioned_object_path(
        pet_id=pet.pet_id,
        scene_id=asset.scene_id,
        asset_version=asset.asset_version,
        filename="v1_baked_fallback.mp4",
    )
    await supabase_assets.upload_asset_to_storage(
        fallback_object,
        V1_FALLBACK_ASSET.read_bytes(),
        "video/mp4",
    )
    breathing_url = asset_url_refresh.sign_object(
        asset_url_refresh.StorageObject(
            bucket=supabase_assets.BUCKET,
            path=fallback_object,
        )
    )
    if not breathing_url:
        raise RuntimeError("Could not sign the matching V1 test fallback")

    share_id, token = await shaker_share.create_share(
        user_id=pet.user_id,
        pet_id=pet.pet_id,
        breathing_url=breathing_url,
        breathing_bucket=supabase_assets.BUCKET,
        breathing_object_path=fallback_object,
        created_by="v2-test-seed",
        purpose="OPS",
        order_ref=f"V2-{asset.background_type.upper()}-TEST",
        scene_id=asset.scene_id,
        layered_asset_id=asset.asset_id,
    )
    base = public_base.rstrip("/")
    url = (
        f"{base}/shaker?petId={quote(pet.pet_id, safe='')}"
        f"&share={quote(token, safe='')}"
    )
    return {
        "background": asset.background_type or "",
        "asset_id": asset.asset_id,
        "scene_id": asset.scene_id,
        "share_id": share_id,
        "url": url,
    }


async def run(args: argparse.Namespace) -> list[dict[str, str]]:
    configured_ref = _configured_project_ref()
    if not configured_ref or args.confirm_project_ref != configured_ref:
        raise RuntimeError(
            "Project confirmation mismatch: pass --confirm-project-ref with the configured Supabase ref"
        )
    _assert_schema()
    pet = await pet_registry.get(args.pet_id)
    if not pet:
        raise RuntimeError("The selected canonical pet does not exist in public.pets")
    if pet.pet_id != f"pet_{pet.content_id}":
        raise RuntimeError("The selected pet does not satisfy canonical petId/contentId binding")

    qa = validate_packed_alpha(str(PET_ASSET))
    modes = ("image", "video") if args.background == "both" else (args.background,)
    results: list[dict[str, str]] = []
    for mode in modes:
        asset = await _ready_asset(pet=pet, mode=mode, qa=qa)
        results.append(
            await _share_url(pet=pet, asset=asset, public_base=args.public_base)
        )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pet-id", required=True)
    parser.add_argument("--confirm-project-ref", required=True)
    parser.add_argument("--background", choices=("image", "video", "both"), default="both")
    parser.add_argument("--public-base", default="http://localhost:5174")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = asyncio.run(run(args))
    rendered = json.dumps({"shares": results}, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} test share(s) to {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
