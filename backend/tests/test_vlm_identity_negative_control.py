"""Negative-control harness logic (no network): verdicts and the spend cap."""

from __future__ import annotations

import pytest

from backend.scripts import vlm_identity_negative_control as nc

_FRAMES = [(b"f0", "image/jpeg"), (b"f1", "image/jpeg")]
_SAME, _OTHER = (b"pet-a-reference", "image/png"), (b"pet-b-reference", "image/png")


def _ask_correct(frames, *, reference_image):
    same = reference_image == _SAME
    return {"same_pet_all_frames": "yes" if same else "no", "anatomy_plausible_all_frames": "yes"}


def test_cases_cover_a_positive_and_a_negative_control():
    cases = nc.plan_cases(_SAME, _OTHER)
    assert [(c["name"], c["expect"]) for c in cases] == [
        ("positive_control_same_pet", "yes"), ("negative_control_other_pet", "no"),
    ]


def test_correct_client_passes_both_controls():
    results = nc.run_cases(nc.plan_cases(_SAME, _OTHER), _FRAMES, ask=_ask_correct, max_calls=2)
    assert [row["ok"] for row in results] == [True, True]


@pytest.mark.parametrize("answer", [{"same_pet_all_frames": "yes"}, {"same_pet_all_frames": "unknown"}, None])
def test_client_that_never_says_no_fails_the_negative_control(answer):
    results = nc.run_cases(
        nc.plan_cases(_SAME, _OTHER), _FRAMES, ask=lambda frames, **kw: answer, max_calls=2
    )
    negative = results[1]
    assert negative["ok"] is False
    assert negative["returned_nothing"] is (answer is None)


def test_spend_cap_refuses_before_any_call():
    calls = []

    def ask(frames, **kw):
        calls.append(1)
        return {"same_pet_all_frames": "no"}

    with pytest.raises(SystemExit):
        nc.run_cases(nc.plan_cases(_SAME, _OTHER), _FRAMES, ask=ask, max_calls=1)
    assert calls == []
