"""
QA VLM 호출 캐시 (VLM_QA_CACHE) — qa_canonical_image / qa_action_keyframe / qa_motion_video.

계약:
  * off(기본) 는 이전 동작 그대로 — 조회도 저장도 없다.
  * on 은 같은 입력(모델 + 호출 버전 + 프롬프트/파라미터 + 보낸 이미지 바이트)이면
    유료 호출을 다시 내지 않는다. 어떤 입력이든 하나만 바뀌면 miss.
  * 실패/거절/파싱 불가는 저장하지 않는다. 동시 동일 호출은 정확히 한 번 나간다.
  * refresh 는 조회를 건너뛰고 새 답으로 항목을 덮어쓴다.
  * 저장 행에는 이미지 데이터가 없다 — 해시 키와 파싱된 답뿐이다.
"""

from __future__ import annotations

import json
import sys
import threading
import types

import pytest

from backend.services import vlm_identity as vlm

CANON_OK = {
    "same_pet": "yes", "same_pet_confidence": "high",
    "face_head_consistent": "yes", "ear_muzzle_consistent": "yes",
    "distinctive_markings_consistent": "yes", "persistent_morphology_consistent": "yes",
    "presentation_difference_only": "no",
    "anatomy_plausible": "yes", "single_pet": "yes",
    "human_present": "no", "accessories_present": "no", "background_neutral": "yes", "pose_neutral": "yes",
    "full_body_visible": "yes", "major_occlusion": "no", "identity_notes": "",
}


def test_keyframe_v2_explicitly_adopts_canonical_semantic_identity_fields():
    phase2_fields = {
        "face_head_consistent",
        "ear_muzzle_consistent",
        "distinctive_markings_consistent",
        "persistent_morphology_consistent",
        "presentation_difference_only",
    }
    assert phase2_fields <= set(vlm.CANONICAL_QA_SCHEMA["required"])
    assert phase2_fields <= set(vlm.KEYFRAME_QA_SCHEMA["required"])
    assert vlm.VLM_CANONICAL_QA_VERSION == "vlm-canonical-qa-v2"
    assert vlm.VLM_KEYFRAME_QA_VERSION == "vlm-keyframe-qa-v2"


KF_OK = {
    key: value
    for key, value in CANON_OK.items()
    if key in vlm.KEYFRAME_QA_SCHEMA["properties"]
}
KF_OK.update({"pose_matches": "yes", "pose_confidence": "high",
              "body_orientation_ok": "yes", "required_regions_visible": "yes"})
MOTION_OK = {
    "same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes", "requested_motion_occurs": "yes",
    "locomotion_form_correct": "yes", "direction_travel_correct": "yes",
    "interaction_correct": "yes", "human_hand_policy_ok": "yes",
    "unintended_large_motion": "no", "single_pet": "yes", "duplicated_pet": "no", "human_present": "no",
    "scene_cut": "no", "major_flicker": "no", "camera_stable": "yes", "background_neutral": "yes",
    "ends_in_target_pose": "unknown", "notes": "",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "on")
    monkeypatch.delenv("PET_VLM_MODEL", raising=False)
    vlm.clear_semantic_cache()
    yield
    vlm.clear_semantic_cache()


def install_fake_anthropic(monkeypatch, *, payloads=None, hold: threading.Event | None = None,
                           release: threading.Event | None = None):
    """스키마별로 답을 돌려주는 가짜 anthropic. 나간 호출을 (schema, content) 로 기록한다."""
    calls: list[dict] = []
    answers = {
        id(vlm.CANONICAL_QA_SCHEMA): CANON_OK,
        id(vlm.KEYFRAME_QA_SCHEMA): KF_OK,
        id(vlm.MOTION_QA_SCHEMA): MOTION_OK,
    }
    for schema, payload in (payloads or ()):   # [(schema, payload), ...] — 스키마 dict 는 해시 불가
        answers[id(schema)] = payload

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            if hold is not None:
                hold.set()          # "첫 호출이 나갔다" 신호
            if release is not None:
                release.wait(5)     # 두 번째 호출자가 도착할 때까지 붙잡는다
            schema = kwargs["output_config"]["format"]["schema"]
            payload = answers.get(id(schema))
            if payload is None:
                # Phase 9 targeted schemas are strict subsets assembled per
                # unresolved question. Project the established fixture answer
                # onto that schema instead of coupling this fake to dict identity.
                available = {**CANON_OK, **KF_OK, **MOTION_OK}
                payload = {
                    key: available.get(key, "unknown")
                    for key in schema.get("properties", {})
                }

            class _TextBlock:
                type = "text"
                text = json.dumps(payload)

            class _Response:
                model = "test-stub"
                stop_reason = "end_turn"
                content = [_TextBlock()]

            return _Response()

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return calls


def _canon(candidate=b"cand-A", refs=(b"ref-1", b"ref-2"), mime="image/png", **kw):
    return vlm.qa_canonical_image(candidate, [(r, "image/jpeg") for r in refs], candidate_mime=mime, **kw)


def _kf(candidate=b"kf-A", refs=(b"anchor", b"ref-1"), pose="sitting", vis=("face", "full_body"), **kw):
    return vlm.qa_action_keyframe(candidate, [(r, "image/png") for r in refs],
                                  required_pose=pose, required_visibility=vis, **kw)


def _motion(frames=(b"f0", b"f1", b"f2"), desc="breathing", cls="MICRO", fractions=(0.0, 0.5, 1.0),
            ref=(b"start", "image/png"), target=None, **kw):
    return vlm.qa_motion_video([(f, "image/jpeg") for f in frames], motion_description=desc, motion_class=cls,
                               sample_fractions=fractions, reference_image=ref, target_image=target, **kw)


# ── 모드 ─────────────────────────────────────────────────────────────────────


def test_mode_defaults_to_on_and_unknown_values_are_on(monkeypatch):
    monkeypatch.delenv(vlm.VLM_QA_CACHE_ENV, raising=False)
    assert vlm.qa_cache_mode() == "on"
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "sometimes")
    assert vlm.qa_cache_mode() == "on"
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "ON")
    assert vlm.qa_cache_mode() == "on"
    assert vlm.qa_cache_mode("refresh") == "refresh"   # 인자가 환경보다 우선
    assert vlm.qa_cache_mode("bogus") == "on"


def test_flag_off_keeps_previous_behavior_no_reads_no_writes(monkeypatch):
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "off")
    calls = install_fake_anthropic(monkeypatch)
    assert _canon() is not None and _canon() is not None
    assert len(calls) == 2
    assert vlm._MOCK_DURABLE_CACHE == {}


def test_off_result_shape_equals_on_result_shape(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "off")
    off = _canon()
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "on")
    on_miss = _canon()
    on_hit = _canon()
    assert off == on_miss == on_hit
    assert len(calls) == 2


# ── hit / miss 매트릭스 ───────────────────────────────────────────────────────


def test_canonical_identical_inputs_hit_and_any_input_change_misses(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    assert _canon()["source"] == vlm.VLM_CANONICAL_QA_VERSION
    _canon()
    assert len(calls) == 1, "같은 입력 → 재호출 없음"
    _canon(candidate=b"cand-B");                    assert len(calls) == 2
    _canon(refs=(b"ref-1", b"ref-CHANGED"));       assert len(calls) == 3
    _canon(refs=(b"ref-2", b"ref-1"));             assert len(calls) == 4   # 순서도 입력이다
    _canon(mime="image/jpeg");                     assert len(calls) == 5
    monkeypatch.setenv("PET_VLM_MODEL", "other-model")
    _canon();                                      assert len(calls) == 6
    monkeypatch.delenv("PET_VLM_MODEL")
    monkeypatch.setattr(vlm, "VLM_CANONICAL_QA_VERSION", "vlm-canonical-qa-v999")
    _canon();                                      assert len(calls) == 7
    monkeypatch.setattr(vlm, "_CANONICAL_QA_PROMPT", vlm._CANONICAL_QA_PROMPT + " (edited)")
    _canon();                                      assert len(calls) == 8


def test_only_the_images_actually_sent_are_hashed(monkeypatch):
    """MAX_IMAGES 를 넘는 레퍼런스는 보내지 않으므로 키에도 들어가지 않는다."""
    calls = install_fake_anthropic(monkeypatch)
    many = tuple(f"ref-{i}".encode() for i in range(vlm.MAX_IMAGES + 2))
    _canon(refs=many)
    _canon(refs=many[:vlm.MAX_IMAGES] + (b"different-tail",))
    assert len(calls) == 1


def test_keyframe_pose_and_visibility_are_part_of_the_key(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    assert _kf()["source"] == vlm.VLM_KEYFRAME_QA_VERSION
    _kf();                                   assert len(calls) == 1
    _kf(pose="lying down");                  assert len(calls) == 2
    _kf(vis=("face",));                      assert len(calls) == 3
    _kf(refs=(b"anchor-v2", b"ref-1"));      assert len(calls) == 4
    _kf(candidate=b"kf-B");                  assert len(calls) == 5
    monkeypatch.setattr(vlm, "VLM_KEYFRAME_QA_VERSION", "vlm-keyframe-qa-v999")
    _kf();                                   assert len(calls) == 6


def test_motion_frames_and_parameters_are_part_of_the_key(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    assert _motion()["source"] == vlm.VLM_MOTION_QA_VERSION
    _motion();                                          assert len(calls) == 1
    _motion(frames=(b"f0", b"f1-changed", b"f2"));      assert len(calls) == 2
    _motion(frames=(b"f2", b"f1", b"f0"));              assert len(calls) == 3   # 시간 순서
    _motion(desc="tail wagging");                       assert len(calls) == 4
    _motion(cls="LOCOMOTION");                          assert len(calls) == 5
    _motion(fractions=(0.0, 0.25, 1.0));                assert len(calls) == 6
    _motion(ref=(b"start-v2", "image/png"));            assert len(calls) == 7
    _motion(target=(b"target", "image/png"));           assert len(calls) == 8
    monkeypatch.setattr(vlm, "VLM_MOTION_QA_VERSION", "vlm-motion-qa-v999")
    _motion();                                          assert len(calls) == 9


def test_motion_only_the_first_twelve_frames_are_sent_and_hashed(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    frames = tuple(f"f{i}".encode() for i in range(14))
    _motion(frames=frames, fractions=tuple(i / 13 for i in range(14)))
    _motion(frames=frames[:12] + (b"x", b"y"), fractions=tuple(i / 13 for i in range(14)))
    assert len(calls) == 1
    assert len([b for b in calls[0]["messages"][0]["content"] if b["type"] == "image"]) == 13  # 12 + reference


def test_same_bytes_do_not_collide_across_call_kinds(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    _canon(candidate=b"same", refs=(b"r",))
    _kf(candidate=b"same", refs=(b"r",))
    assert len(calls) == 2
    kinds = {row["kind"] for row in vlm._MOCK_DURABLE_CACHE.values()}
    assert kinds == {vlm.KIND_CANONICAL_QA, vlm.KIND_KEYFRAME_QA}


def test_motion_class_context_is_in_prompt_and_cache_identity(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)

    first = _motion(
        cls="LOCOMOTION",
        desc="walk toward the viewer",
        expected_direction="toward camera",
    )
    same = _motion(
        cls="LOCOMOTION",
        desc="walk toward the viewer",
        expected_direction="toward camera",
    )
    changed = _motion(
        cls="LOCOMOTION",
        desc="walk toward the viewer",
        expected_direction="screen left",
    )

    assert first == same == changed
    assert len(calls) == 2
    prompt = next(
        block["text"]
        for block in calls[0]["messages"][0]["content"]
        if block["type"] == "text"
    )
    assert "Expected direction/travel: toward camera" in prompt
    assert "locomotion_form_correct" in prompt
    assert "direction_travel_correct" in prompt


# ── 실패는 굳지 않는다 / 동시 호출은 한 번 ────────────────────────────────────


@pytest.mark.parametrize("failure", ["refusal", "exception", "garbage"])
def test_failures_are_never_cached(monkeypatch, failure):
    class _Messages:
        def create(self, **kwargs):
            if failure == "exception":
                raise RuntimeError("api down")

            class _Response:
                model = "test-stub"
                stop_reason = "refusal" if failure == "refusal" else "end_turn"
                content = [] if failure == "refusal" else [type("T", (), {"type": "text", "text": "not json"})()]

            return _Response()

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert _canon() is None and _kf() is None and _motion() is None
    assert vlm._MOCK_DURABLE_CACHE == {}

    calls = install_fake_anthropic(monkeypatch)
    assert _canon() is not None and _kf() is not None and _motion() is not None
    assert len(calls) == 3


def test_concurrent_identical_calls_make_exactly_one_request(monkeypatch):
    first_out = threading.Event()
    release = threading.Event()
    calls = install_fake_anthropic(monkeypatch, hold=first_out, release=release)
    results: list = []

    def worker():
        results.append(_motion())

    t1 = threading.Thread(target=worker)
    t1.start()
    assert first_out.wait(5), "첫 호출이 나가야 한다"
    t2 = threading.Thread(target=worker)
    t2.start()
    release.set()
    t1.join(5); t2.join(5)
    assert len(results) == 2 and results[0] == results[1] is not None
    assert len(calls) == 1


def test_durable_layer_survives_in_memory_reset(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    _kf()
    with vlm._result_cache_lock:
        vlm._result_cache.clear()          # 워커 재시작 흉내
    _kf()
    assert len(calls) == 1


def test_durable_lookup_failure_falls_back_to_a_direct_call(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)

    def _boom(cache_key):
        raise RuntimeError("durable store unavailable")

    monkeypatch.setattr(vlm, "_durable_cache_get", _boom)
    monkeypatch.setattr(vlm, "_durable_cache_put", lambda *a, **k: None)
    assert _canon() is not None and len(calls) == 1


# ── refresh ──────────────────────────────────────────────────────────────────


def test_refresh_makes_one_new_call_and_overwrites_the_entry(monkeypatch):
    calls = install_fake_anthropic(monkeypatch)
    first = _canon()
    assert len(calls) == 1 and first["identity_notes"] == ""

    changed = {**CANON_OK, "identity_notes": "second opinion"}
    calls = install_fake_anthropic(monkeypatch, payloads=[(vlm.CANONICAL_QA_SCHEMA, changed)])
    refreshed = _canon(cache_mode="refresh")
    assert len(calls) == 1 and refreshed["identity_notes"] == "second opinion"

    again = _canon()                       # on: 덮어쓴 항목을 읽는다, 새 호출 없음
    assert again["identity_notes"] == "second opinion" and len(calls) == 1
    assert len(vlm._MOCK_DURABLE_CACHE) == 1


def test_refresh_via_env_bypasses_reads_but_still_writes(monkeypatch):
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "refresh")
    calls = install_fake_anthropic(monkeypatch)
    _motion(); _motion()
    assert len(calls) == 2 and len(vlm._MOCK_DURABLE_CACHE) == 1


# ── 저장 행 내용 ─────────────────────────────────────────────────────────────


def test_cached_rows_hold_hash_key_and_parsed_answer_only(monkeypatch):
    install_fake_anthropic(monkeypatch)
    candidate = b"\x89PNG-fake-image-bytes"
    _canon(candidate=candidate)
    _kf(candidate=candidate)
    _motion(frames=(candidate,))
    assert len(vlm._MOCK_DURABLE_CACHE) == 3
    for key, row in vlm._MOCK_DURABLE_CACHE.items():
        assert set(row) == {"cache_key", "kind", "analyzer_version", "model", "result"}
        assert len(key) == 64 and row["cache_key"] == key
        assert row["kind"] in (vlm.KIND_CANONICAL_QA, vlm.KIND_KEYFRAME_QA, vlm.KIND_MOTION_QA)
        assert row["analyzer_version"].startswith("vlm-")
        blob = json.dumps(row["result"])
        assert "PNG-fake" not in blob and "base64" not in blob
        assert set(row["result"]) <= {*CANON_OK, *KF_OK, *MOTION_OK, "source", "model"}
