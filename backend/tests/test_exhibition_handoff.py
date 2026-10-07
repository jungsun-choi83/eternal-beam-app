"""
EXHIBITION HANDOFF — READY 패키지 → 외부 전시 시스템 (HANDOFF_SENDING → CONFIRMED | FAILED).

외부 엔드포인트는 transport 주입으로 대체한다 (네트워크 없음).
"""

from __future__ import annotations

import pytest
import requests

from backend.services import exhibition_handoff_client as hc
from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.tests.test_exhibition_breathing_maps import frontal_dog
from backend.tests.test_exhibition_prep_run import (
    _FakeSupabase,
    _app,
    _jpeg,
    _run,
    fake_matte,
)

URL = "https://exhibit.example/api/rig-inputs"
PAYLOAD_KEYS = {
    "run_id", "exhibition_id", "schema_version", "motion",
    "subject_rgba_url", "breathing_weight_url", "locked_mask_url", "manifest_url",
}


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("EXHIBITION_HANDOFF_MODE", "EXHIBITION_HANDOFF_URL", "EXHIBITION_HANDOFF_TOKEN",
              "EXHIBITION_HANDOFF_TIMEOUT_SEC", "EXHIBITION_HANDOFF_URL_TTL_SEC", "EXHIBITION_HANDOFF_STALE_SEC"):
        monkeypatch.delenv(k, raising=False)


class FakeEndpoint:
    """외부 수신기 흉내. responses 를 차례로 돌려준다 (예외면 raise)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(payload)
        return r


def ack(payload):
    return 200, {"run_id": payload["run_id"], "accepted": True}, ""


def http_client(endpoint, **cfg):
    return hc.ExhibitionHandoffClient(hc.HandoffConfig(mode=hc.MODE_HTTP, url=URL, **cfg), transport=endpoint)


def ready_run(stores, **create_kw):
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), **create_kw)
    assert final["status"] == st.STATUS_READY
    return final


def handoff(stores, run_id, client):
    store, artifacts = stores
    return svc.hand_off_run(run_id, store=store, artifacts=artifacts, client=client)


# ── 성공 · 페이로드 ──────────────────────────────────────────────────────────


def test_successful_ack_confirms_handoff(stores):
    run = ready_run(stores, exhibition_id="expo-7")
    endpoint = FakeEndpoint(ack)
    row = handoff(stores, run["id"], http_client(endpoint, token="sekret"))

    assert row["handoff_status"] == st.HANDOFF_CONFIRMED
    assert row["handoff_ack"] == {"run_id": run["id"], "accepted": True}
    assert row["handoff_attempts"] == 1 and row["handoff_confirmed_at"]
    assert row["handoff_error_code"] is None
    # prep 상태는 그대로 — 핸드오프는 별도 컬럼이다.
    assert row["status"] == st.STATUS_READY and row["stage"] == st.STAGE_PACKAGE

    (call,) = endpoint.calls
    assert call["url"] == URL
    assert call["headers"]["Idempotency-Key"] == run["id"]
    assert call["headers"]["Authorization"] == "Bearer sekret"
    assert call["headers"]["Content-Type"] == "application/json"


def test_payload_is_exactly_the_contract_with_signed_urls(stores):
    run = ready_run(stores, exhibition_id="expo-7")
    endpoint = FakeEndpoint(ack)
    row = handoff(stores, run["id"], http_client(endpoint))

    payload = endpoint.calls[0]["payload"]
    assert set(payload) == PAYLOAD_KEYS
    assert payload["run_id"] == run["id"]
    assert payload["exhibition_id"] == "expo-7"
    assert payload["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    assert payload["motion"] == "BREATHING"
    rid = run["id"]
    assert payload["subject_rgba_url"] == f"memory://exhibition/{rid}/subject_rgba.png"
    assert payload["breathing_weight_url"] == f"memory://exhibition/{rid}/breathing_weight.png"
    assert payload["locked_mask_url"] == f"memory://exhibition/{rid}/locked_mask.png"
    assert payload["manifest_url"] == f"memory://exhibition/{rid}/manifest.json"
    # preview_overlay(운영자용)는 보내지 않는다.
    assert not any("preview_overlay" in str(v) for v in payload.values())
    # DB 에는 서명 URL 대신 저장 경로만 남는다.
    rec = row["handoff_request"]
    assert "subject_rgba_url" not in rec and rec["files"]["manifest_url"] == f"exhibition/{rid}/manifest.json"


def test_signed_urls_use_configured_ttl(stores):
    run = ready_run(stores)
    store, artifacts = stores
    seen = []
    real = artifacts.signed_url
    artifacts.signed_url = lambda path, ttl=3600: seen.append(ttl) or real(path, ttl)
    handoff(stores, run["id"], http_client(FakeEndpoint(ack), url_ttl_sec=7200))
    assert seen == [7200] * 4


# ── 실패 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response, code",
    [
        (hc.HandoffError("HANDOFF_TIMEOUT", "No response within 10s.", retryable=True), "HANDOFF_TIMEOUT"),
        (hc.HandoffError("HANDOFF_NETWORK_ERROR", "ConnectionError", retryable=True), "HANDOFF_NETWORK_ERROR"),
        ((500, None, "boom"), "HANDOFF_HTTP_5XX"),
        ((503, None, "down"), "HANDOFF_HTTP_5XX"),
        ((429, None, "slow down"), "HANDOFF_HTTP_RETRYABLE"),
        ((400, {"error": "bad"}, "bad"), "HANDOFF_REJECTED"),
        ((200, {"run_id": "other", "accepted": True}, ""), "HANDOFF_ACK_MISMATCH"),
        (lambda p: (200, {"run_id": p["run_id"], "accepted": False}, ""), "HANDOFF_NOT_ACCEPTED"),
        ((200, None, "<html>ok</html>"), "HANDOFF_BAD_ACK"),
        (RuntimeError("unexpected"), "HANDOFF_INTERNAL_ERROR"),
    ],
)
def test_failures_mark_handoff_failed_without_touching_package(stores, response, code):
    run = ready_run(stores)
    store, artifacts = stores
    before = dict(artifacts.objects)
    row = handoff(stores, run["id"], http_client(FakeEndpoint(response)))
    assert row["handoff_status"] == st.HANDOFF_FAILED
    assert row["handoff_error_code"] == code and row["handoff_error_message"]
    assert row["status"] == st.STATUS_READY and row["outputs_json"] == run["outputs_json"]
    assert artifacts.objects == before


def test_requests_transport_maps_timeout_and_network_errors(monkeypatch):
    def _timeout(*a, **k):
        raise requests.Timeout("read timed out")

    def _conn(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _timeout)
    with pytest.raises(hc.HandoffError) as e:
        hc._requests_transport(URL, {}, {}, 3.0)
    assert e.value.code == "HANDOFF_TIMEOUT" and e.value.retryable

    monkeypatch.setattr(requests, "post", _conn)
    with pytest.raises(hc.HandoffError) as e:
        hc._requests_transport(URL, {}, {}, 3.0)
    assert e.value.code == "HANDOFF_NETWORK_ERROR" and e.value.retryable


def test_requests_transport_passes_timeout_and_parses_json(monkeypatch):
    seen = {}

    class _Res:
        status_code = 200
        text = '{"run_id": "r", "accepted": true}'

        def json(self):
            return {"run_id": "r", "accepted": True}

    def _post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Res()

    monkeypatch.setattr(requests, "post", _post)
    status, body, _text = hc._requests_transport(URL, {"run_id": "r"}, {"X": "1"}, 4.0)
    assert (status, body) == (200, {"run_id": "r", "accepted": True})
    assert seen == {"url": URL, "json": {"run_id": "r"}, "headers": {"X": "1"}, "timeout": 4.0}


def test_missing_package_file_fails_handoff(stores):
    run = ready_run(stores)
    store, artifacts = stores
    del artifacts.objects[f"exhibition/{run['id']}/locked_mask.png"]
    endpoint = FakeEndpoint(ack)
    row = handoff(stores, run["id"], http_client(endpoint))
    assert row["handoff_status"] == st.HANDOFF_FAILED
    assert row["handoff_error_code"] == "HANDOFF_PACKAGE_UNAVAILABLE"
    assert endpoint.calls == []  # 불완전한 패키지는 보내지 않는다


# ── 재시도 · 멱등 ────────────────────────────────────────────────────────────


def test_retry_resends_the_same_package_without_rerunning_cutout_or_maps(stores):
    run = ready_run(stores)
    store, artifacts = stores
    before = dict(artifacts.objects)
    endpoint = FakeEndpoint((502, None, "bad gateway"), ack)

    first = handoff(stores, run["id"], http_client(endpoint))
    assert first["handoff_status"] == st.HANDOFF_FAILED
    # 실패한 실행은 prep 큐로 돌아가지 않는다 → 워커가 CUTOUT/MAPS 를 다시 잡을 수 없다.
    assert store.claim_next("w2", stale_after_minutes=0) is None

    second = handoff(stores, run["id"], http_client(endpoint))
    assert second["handoff_status"] == st.HANDOFF_CONFIRMED
    assert second["handoff_attempts"] == 2 and second["handoff_error_code"] is None
    assert second["attempts"] == run["attempts"]  # prep 시도 횟수 불변
    assert second["stage"] == st.STAGE_PACKAGE and second["manifest_json"] == run["manifest_json"]
    assert artifacts.objects == before  # 패키지 파일 재작성 없음
    assert endpoint.calls[0]["payload"] == endpoint.calls[1]["payload"]
    assert {c["headers"]["Idempotency-Key"] for c in endpoint.calls} == {run["id"]}


def test_retry_does_not_call_matting_or_map_generation(stores, monkeypatch):
    run = ready_run(stores)

    def _boom(*a, **k):
        raise AssertionError("handoff retry must not rerun CUTOUT/MAPS")

    monkeypatch.setattr(svc, "build_package", _boom)
    monkeypatch.setattr(svc, "_default_matte", _boom)
    monkeypatch.setattr(svc.maps_mod, "build_breathing_maps", _boom)
    endpoint = FakeEndpoint(hc.HandoffError("HANDOFF_TIMEOUT", "t", retryable=True), ack)
    assert handoff(stores, run["id"], http_client(endpoint))["handoff_status"] == st.HANDOFF_FAILED
    assert handoff(stores, run["id"], http_client(endpoint))["handoff_status"] == st.HANDOFF_CONFIRMED


def test_confirmed_run_is_not_sent_again(stores):
    run = ready_run(stores)
    endpoint = FakeEndpoint(ack)
    handoff(stores, run["id"], http_client(endpoint))
    row = handoff(stores, run["id"], http_client(endpoint))
    assert row["handoff_status"] == st.HANDOFF_CONFIRMED and row["handoff_attempts"] == 1
    assert len(endpoint.calls) == 1


def test_in_flight_send_blocks_concurrent_send_until_stale(stores):
    run = ready_run(stores)
    store, _artifacts = stores
    store.update(run["id"], {"handoff_status": st.HANDOFF_SENDING, "handoff_attempt_id": "a1",
                             "handoff_requested_at": svc.datetime.now(svc.timezone.utc).isoformat()})
    with pytest.raises(svc.HandoffStateError) as e:
        handoff(stores, run["id"], http_client(FakeEndpoint(ack)))
    assert e.value.code == "HANDOFF_IN_PROGRESS"

    # 워커가 전송 중 죽어 SENDING 이 오래됐다 → 재전송 허용.
    store.update(run["id"], {"handoff_requested_at": "2026-01-01T00:00:00+00:00"})
    row = handoff(stores, run["id"], http_client(FakeEndpoint(ack)))
    assert row["handoff_status"] == st.HANDOFF_CONFIRMED


def test_newer_attempt_fences_out_older_result(stores):
    run = ready_run(stores)
    store, _artifacts = stores

    def _slow_then_superseded(payload):
        # 이 전송이 응답을 기다리는 사이 다른 시도가 상태를 가져갔다.
        store.update(run["id"], {"handoff_attempt_id": "newer"})
        return 200, {"run_id": payload["run_id"], "accepted": True}, ""

    row = handoff(stores, run["id"], http_client(FakeEndpoint(_slow_then_superseded)))
    assert row["handoff_status"] == st.HANDOFF_SENDING and row["handoff_attempt_id"] == "newer"


@pytest.mark.parametrize("status", [st.STATUS_NEEDS_REVIEW, st.STATUS_FAILED, st.STATUS_QUEUED, st.STATUS_RUNNING])
def test_only_ready_packages_can_be_handed_off(stores, status):
    run = ready_run(stores)
    store, _artifacts = stores
    store.update(run["id"], {"status": status})
    endpoint = FakeEndpoint(ack)
    with pytest.raises(svc.HandoffStateError) as e:
        handoff(stores, run["id"], http_client(endpoint))
    assert e.value.code == "HANDOFF_PACKAGE_NOT_READY"
    assert endpoint.calls == [] and store.get(run["id"]).get("handoff_status") is None


# ── 모드 · 설정 ──────────────────────────────────────────────────────────────


def test_mock_mode_returns_successful_ack_without_network(stores, monkeypatch):
    monkeypatch.setenv("EXHIBITION_HANDOFF_MODE", "mock")
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("mock mode must not call the network"))
    client = hc.ExhibitionHandoffClient()
    assert client.config.mode == hc.MODE_MOCK and client.config.enabled
    run = ready_run(stores)
    row = handoff(stores, run["id"], client)
    assert row["handoff_status"] == st.HANDOFF_CONFIRMED
    assert row["handoff_ack"] == {"run_id": run["id"], "accepted": True, "mock": True}
    assert set(row["handoff_request"]) == (PAYLOAD_KEYS - set(hc.PAYLOAD_FILES)) | {"files"}


def test_mode_resolution_from_env(monkeypatch):
    assert hc.HandoffConfig.from_env().mode == hc.MODE_OFF
    monkeypatch.setenv("EXHIBITION_HANDOFF_URL", URL)
    cfg = hc.HandoffConfig.from_env()
    assert cfg.mode == hc.MODE_HTTP and cfg.url == URL and cfg.timeout_sec == 10.0 and cfg.url_ttl_sec == 86400
    monkeypatch.setenv("EXHIBITION_HANDOFF_MODE", "off")
    assert not hc.HandoffConfig.from_env().enabled
    monkeypatch.setenv("EXHIBITION_HANDOFF_MODE", "mock")
    monkeypatch.setenv("EXHIBITION_HANDOFF_TIMEOUT_SEC", "3")
    cfg = hc.HandoffConfig.from_env()
    assert cfg.mode == hc.MODE_MOCK and cfg.timeout_sec == 3.0
    assert "sekret" not in repr(hc.HandoffConfig(mode=hc.MODE_HTTP, url=URL, token="sekret"))


def test_handoff_off_is_rejected_before_any_state_change(stores):
    run = ready_run(stores)
    store, _artifacts = stores
    with pytest.raises(svc.HandoffStateError) as e:
        handoff(stores, run["id"], hc.ExhibitionHandoffClient(hc.HandoffConfig(mode=hc.MODE_OFF)))
    assert e.value.code == "HANDOFF_NOT_CONFIGURED"
    assert store.get(run["id"]).get("handoff_status") is None


def test_http_mode_without_url_fails_handoff(stores):
    run = ready_run(stores)
    row = handoff(stores, run["id"], hc.ExhibitionHandoffClient(hc.HandoffConfig(mode=hc.MODE_HTTP)))
    assert row["handoff_status"] == st.HANDOFF_FAILED and row["handoff_error_code"] == "HANDOFF_NOT_CONFIGURED"


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_handoff_success_retry_and_errors(stores, monkeypatch):
    run = ready_run(stores)
    client = _app(stores, ops=True)
    path = f"/api/exhibition/prep-runs/{run['id']}/handoff"

    off = client.post(path)
    assert off.status_code == 503 and off.json()["detail"]["code"] == "HANDOFF_NOT_CONFIGURED"

    monkeypatch.setenv("EXHIBITION_HANDOFF_URL", URL)
    monkeypatch.setattr(hc, "_requests_transport", FakeEndpoint((504, None, "gateway timeout"), ack))
    failed = client.post(path)
    assert failed.status_code == 200
    assert failed.json()["handoff"]["status"] == "HANDOFF_FAILED"
    assert failed.json()["handoff"]["error_code"] == "HANDOFF_HTTP_5XX"
    assert failed.json()["status"] == "READY"

    ok = client.post(path)
    assert ok.status_code == 200
    body = ok.json()
    assert body["handoff"]["status"] == "HANDOFF_CONFIRMED" and body["handoff"]["attempts"] == 2
    assert body["handoff"]["ack"] == {"run_id": run["id"], "accepted": True}
    assert client.get(f"/api/exhibition/prep-runs/{run['id']}").json()["handoff"]["status"] == "HANDOFF_CONFIRMED"

    import uuid

    assert client.post(f"/api/exhibition/prep-runs/{uuid.uuid4()}/handoff").status_code == 404
    stores[0].update(run["id"], {"status": st.STATUS_NEEDS_REVIEW})
    assert client.post(path).json()["detail"]["code"] == "HANDOFF_PACKAGE_NOT_READY"


def test_api_handoff_requires_ops(stores, monkeypatch):
    monkeypatch.delenv("SHAKER_OPS_USER_IDS", raising=False)
    monkeypatch.delenv("OPS_USER_IDS", raising=False)
    monkeypatch.setenv("EXHIBITION_HANDOFF_MODE", "mock")
    run = ready_run(stores)
    res = _app(stores, ops=False).post(f"/api/exhibition/prep-runs/{run['id']}/handoff")
    assert res.status_code == 403
    assert stores[0].get(run["id"]).get("handoff_status") is None


# ── 격리 ─────────────────────────────────────────────────────────────────────


_HANDOFF_COLUMNS = {
    "handoff_status", "handoff_attempt_id", "handoff_attempts", "handoff_requested_at", "handoff_confirmed_at",
    "handoff_request", "handoff_ack", "handoff_error_code", "handoff_error_message",
}


def test_supabase_handoff_writes_only_handoff_columns_of_exhibition_table():
    client = _FakeSupabase()
    store, artifacts = st.SupabaseRunStore(client), st.SupabaseArtifactStore(client, bucket="b")
    row = svc.create_run(_jpeg(800, 800), created_by="ops", store=store, artifacts=artifacts)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=fake_matte(frontal_dog))
    objects_before = dict(client.objects)
    n_ops = len(client.ops)

    out = svc.hand_off_run(row["id"], store=store, artifacts=artifacts, client=http_client(FakeEndpoint(ack)))
    assert out["handoff_status"] == st.HANDOFF_CONFIRMED
    assert out["status"] == st.STATUS_READY

    handoff_ops = client.ops[n_ops:]
    assert {t for t, _op, _p in handoff_ops} == {"exhibition_prep_runs"}
    writes = [p for _t, op, p in handoff_ops if op != "select"]
    assert writes and all(set(p) <= _HANDOFF_COLUMNS for p in writes)
    assert client.objects == objects_before  # 저장소에 아무것도 다시 쓰지 않는다
    payload_urls = [v for k, v in out["handoff_request"]["files"].items()]
    assert all(u.startswith(f"exhibition/{row['id']}/") for u in payload_urls)
