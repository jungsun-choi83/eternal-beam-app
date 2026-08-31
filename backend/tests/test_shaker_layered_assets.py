from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone

import anyio
import pytest

from backend.services import shaker_layered_assets as assets


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    assets.__reset_for_tests()
    yield
    assets.__reset_for_tests()


def _sync(fn, *args, **kwargs):
    return anyio.run(functools.partial(fn, *args, **kwargs))


def _reserve(**overrides):
    values = {
        "user_id": "owner@example.com",
        "pet_id": "pet_content-1",
        "content_id": "content-1",
        "scene_id": "scene-1",
    }
    values.update(overrides)
    return _sync(assets.reserve, **values)


def test_paths_are_immutable_and_versioned():
    item = _reserve()
    path = assets.versioned_object_path(
        pet_id=item.pet_id,
        scene_id=item.scene_id,
        asset_version=item.asset_version,
        filename="pet_packed.mp4",
    )
    assert path == f"layered/{item.pet_id}/{item.scene_id}/{item.asset_version}/pet_packed.mp4"
    assert "owner@example.com" not in path
    assert "idle_loop.mp4" not in path


def test_reservation_rejects_a_second_pet_identity():
    with pytest.raises(assets.LayeredAssetError, match="binding"):
        _reserve(pet_id="unrelated-pet")


def test_only_complete_qa_passed_manifest_can_be_ready():
    item = _reserve()
    with pytest.raises(assets.LayeredAssetError):
        _sync(
            assets.publish_ready,
            item.asset_id,
            pet=assets.StorageRef("user-assets", "pet.mp4"),
            background_type="image",
            background=assets.StorageRef("user-assets", "bg.jpg"),
            qa={"passed": False},
        )

    ready = _sync(
        assets.publish_ready,
        item.asset_id,
        pet=assets.StorageRef("user-assets", "pet.mp4"),
        background_type="image",
        background=assets.StorageRef("user-assets", "bg.jpg"),
        qa={"passed": True, "alpha_samples": 8},
        shadow={"kind": "css-contact", "opacity": 0.28},
    )
    assert ready.complete_ready
    assert ready.pet_encoding == assets.PACKED_ENCODING
    assert ready.alpha_layout == assets.PACKED_LAYOUT


def test_ready_manifest_publishes_validated_anchored_placement():
    item = _reserve()
    placement = {
        "mode": "anchored",
        "center_x_pct": 50.0,
        "bottom_pct": 3.0,
        "height_pct": 91.0,
        "crop_x_min": 0.16,
        "crop_x_max": 0.84,
        "crop_y_min": 0.06,
        "crop_y_max": 0.97,
    }
    ready = _sync(
        assets.publish_ready,
        item.asset_id,
        pet=assets.StorageRef("user-assets", "pet.mp4"),
        background_type="image",
        background=assets.StorageRef("user-assets", "bg.jpg"),
        qa={"passed": True},
        placement=placement,
    )

    assert ready.placement == placement


def test_ready_manifest_rejects_unknown_placement_mode():
    item = _reserve()
    with pytest.raises(assets.LayeredAssetError, match="placement mode"):
        _sync(
            assets.publish_ready,
            item.asset_id,
            pet=assets.StorageRef("user-assets", "pet.mp4"),
            background_type="image",
            background=assets.StorageRef("user-assets", "bg.jpg"),
            qa={"passed": True},
            placement={"mode": "distort-pet"},
        )


def test_complete_ready_rejects_malformed_optional_or_qa_metadata():
    item = _reserve()
    ready = _sync(
        assets.publish_ready,
        item.asset_id,
        pet=assets.StorageRef("user-assets", "pet.mp4"),
        background_type="image",
        background=assets.StorageRef("user-assets", "bg.jpg"),
        qa={"passed": True},
    )
    malformed_qa = assets.LayeredAsset(
        **{**ready.__dict__, "qa": {"passed": False}}
    )
    malformed_foreground = assets.LayeredAsset(
        **{
            **ready.__dict__,
            "foreground_type": "image",
        }
    )
    assert malformed_qa.complete_ready is False
    assert malformed_foreground.complete_ready is False


def test_failed_or_processing_assets_are_not_resolved():
    processing = _reserve()
    assert _sync(
        assets.resolve_for_share,
        user_id=processing.user_id,
        pet_id=processing.pet_id,
        scene_id=processing.scene_id,
        layered_asset_id=processing.asset_id,
    ) is None
    _sync(assets.mark_failed, processing.asset_id, "matte failed")
    assert _sync(
        assets.resolve_for_share,
        user_id=processing.user_id,
        pet_id=processing.pet_id,
        scene_id=processing.scene_id,
        layered_asset_id=processing.asset_id,
    ) is None


def test_scene_and_owner_binding_are_enforced():
    item = _reserve()
    _sync(
        assets.publish_ready,
        item.asset_id,
        pet=assets.StorageRef("user-assets", "pet.mp4"),
        background_type="video",
        background=assets.StorageRef("user-assets", "bg.mp4"),
        qa={"passed": True},
    )
    assert _sync(
        assets.resolve_for_share,
        user_id=item.user_id,
        pet_id=item.pet_id,
        scene_id=item.scene_id,
        layered_asset_id=item.asset_id,
    ) is not None
    assert _sync(
        assets.resolve_for_share,
        user_id="other@example.com",
        pet_id=item.pet_id,
        scene_id=item.scene_id,
        layered_asset_id=item.asset_id,
    ) is None
    assert _sync(
        assets.resolve_for_share,
        user_id=item.user_id,
        pet_id=item.pet_id,
        scene_id="other-scene",
        layered_asset_id=item.asset_id,
    ) is None


def test_legacy_share_without_scene_or_asset_remains_v1():
    item = _reserve()
    _sync(
        assets.publish_ready,
        item.asset_id,
        pet=assets.StorageRef("user-assets", "pet.mp4"),
        background_type="image",
        background=assets.StorageRef("user-assets", "bg.jpg"),
        qa={"passed": True},
    )
    assert _sync(
        assets.resolve_for_share,
        user_id=item.user_id,
        pet_id=item.pet_id,
        scene_id=None,
        layered_asset_id=None,
    ) is None


def test_interrupted_processing_can_be_retried_after_stale_timeout():
    item = _reserve()
    now = datetime.now(timezone.utc)
    recent = assets.LayeredAsset(
        **{
            **item.__dict__,
            "updated_at": (now - timedelta(minutes=10)).isoformat(),
        }
    )
    stale = assets.LayeredAsset(
        **{
            **item.__dict__,
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
    )
    assert assets.processing_is_stale(recent, now=now, max_age_seconds=7200) is False
    assert assets.processing_is_stale(stale, now=now, max_age_seconds=7200) is True
