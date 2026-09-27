"""motion_spec-owned video provider routing."""

from __future__ import annotations

from backend.services import motion_spec as ms
from backend.services import motion_video_service as mvs
from backend.services import video_motion_providers as vp


class _Provider:
    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        supports_end_frame: bool = True,
        supports_motion_reference: bool = False,
    ):
        self.name = name
        self._available = available
        self.supports_end_frame = supports_end_frame
        self.supports_motion_reference = supports_motion_reference

    def available(self) -> bool:
        return self._available


def test_breathing_resolves_wan_then_seedance():
    assert ms.provider_order_for_motion("BREATHING") == (
        ms.PROVIDER_WAN_3_STANDARD,
        ms.PROVIDER_SEEDANCE,
    )


def test_pet_head_resolves_seedance_then_kling():
    assert ms.provider_order_for_motion("PET_HEAD") == (
        ms.PROVIDER_SEEDANCE,
        ms.PROVIDER_KLING_3,
    )


def test_come_closer_resolves_kling_then_wan():
    assert ms.provider_order_for_motion("COME_CLOSER") == (
        ms.PROVIDER_KLING_3,
        ms.PROVIDER_WAN_3_STANDARD,
    )


def test_per_motion_override_wins_over_class_default(monkeypatch):
    override = (ms.PROVIDER_KLING_3, ms.PROVIDER_SEEDANCE)
    monkeypatch.setitem(ms.MOTION_PROVIDER_ORDER_OVERRIDES, "LOOK_UP", override)

    assert ms.provider_order_for_motion("LOOK_UP") == override
    assert ms.provider_order_for_motion("BREATHING") != override


def test_swapping_motion_spec_order_changes_service_resolution(monkeypatch):
    providers = {
        ms.PROVIDER_SEEDANCE: _Provider("seedance"),
        ms.PROVIDER_KLING_3: _Provider("kling"),
    }
    monkeypatch.setattr(vp, "get_provider", lambda provider_id: providers.get(provider_id))
    monkeypatch.setitem(
        ms.MOTION_PROVIDER_ORDER_BY_CLASS,
        ms.CLASS_MICRO,
        (ms.PROVIDER_KLING_3, ms.PROVIDER_SEEDANCE),
    )

    contract = ms.motion_snapshot(ms.MOTIONS["BREATHING"])
    resolved, available, _requirements = mvs._resolve_providers_for_contract(contract)

    assert contract["provider_order"] == [ms.PROVIDER_KLING_3, ms.PROVIDER_SEEDANCE]
    assert [provider.name for provider in available] == ["kling", "seedance"]
    assert [provider.name for provider in resolved] == ["kling", "seedance"]


def test_capability_filtering_removes_incompatible_provider(monkeypatch):
    providers = {
        ms.PROVIDER_KLING_3: _Provider("kling", supports_end_frame=True),
        ms.PROVIDER_WAN_3_STANDARD: _Provider(
            "wan_3_standard", supports_end_frame=False
        ),
    }
    monkeypatch.setattr(vp, "get_provider", lambda provider_id: providers.get(provider_id))

    contract = ms.motion_snapshot(ms.MOTIONS["LIE_DOWN"])
    resolved, available, requirements = mvs._resolve_providers_for_contract(contract)

    assert [provider.name for provider in available] == ["kling", "wan_3_standard"]
    assert requirements["required_all"] == ["supports_end_frame"]
    assert [provider.name for provider in resolved] == ["kling"]


def test_unknown_or_unavailable_provider_falls_through_safely(monkeypatch):
    fallback = _Provider("seedance")
    unavailable = _Provider("wan_3_standard", available=False)
    providers = {
        "unavailable": unavailable,
        ms.PROVIDER_SEEDANCE: fallback,
    }
    monkeypatch.setattr(vp, "get_provider", lambda provider_id: providers.get(provider_id))

    contract = {
        "provider_order": ["unknown", "unavailable", ms.PROVIDER_SEEDANCE],
        "requirements": {"provider_capabilities": {}},
    }
    resolved, available, _requirements = mvs._resolve_providers_for_contract(contract)
    assert available == [fallback]
    assert resolved == [fallback]

    contract["provider_order"] = ["unknown", "unavailable"]
    resolved, available, _requirements = mvs._resolve_providers_for_contract(contract)
    assert available == []
    assert resolved == []
