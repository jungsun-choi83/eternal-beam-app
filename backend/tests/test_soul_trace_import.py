"""
Soul Trace → Eternal Beam 편지 가져오기 — **서버 대 서버 경계.**

핵심 계약:
  * 본문은 **브라우저를 거치지 않는다** — 우리 서버가 Soul Trace 에서 직접 받는다.
  * 공유 비밀은 **헤더로만** 나가고, 설정이 없으면 아무 요청도 보내지 않는다.
  * 모양이 틀린 traceId/토큰으로는 Soul Trace 를 때리지 않는다.
  * 1회용 토큰이라 **자동 재시도가 없다** — 소비 실패는 그대로 실패다.
  * 빈 본문을 받으면 거절한다. 기본 문구로 채우지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest

from backend.services import soul_trace_import

TRACE = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
TOKEN = "h" * 43
SECRET = "s" * 48
BODY = "안녕, 엄마 아빠. 나는 지금도 곁에 머물고 있어요."


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


class _Res:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    """httpx.AsyncClient 대역 — 나간 요청을 기록한다."""

    calls: list[dict] = []
    response = _Res(200, {"letterId": TRACE, "letterBody": BODY, "petName": "고야"})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(_FakeClient.response, Exception):
            raise _FakeClient.response
        return _FakeClient.response


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOUL_TRACE_SERVICE_TOKEN", SECRET)
    monkeypatch.setenv("SOUL_TRACE_API_BASE", "https://soultrace.example.com")
    _FakeClient.calls = []
    _FakeClient.response = _Res(200, {"letterId": TRACE, "letterBody": BODY, "petName": "고야"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    yield
    _FakeClient.calls = []


# ── 정상 경로 ────────────────────────────────────────────────────────────────


def test_fetches_the_canonical_body_server_to_server():
    got = _sync(
        soul_trace_import.fetch_source_letter,
        trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
    )
    assert got.letter_body == BODY
    assert got.letter_id == TRACE
    assert got.pet_name == "고야"

    call = _FakeClient.calls[0]
    assert call["url"] == "https://soultrace.example.com/api/internal/letter"
    assert call["json"] == {
        "traceId": TRACE, "handoff": TOKEN, "consumedBy": "me@example.com"
    }


def test_secret_travels_in_a_header_never_in_the_url_or_body():
    """
    URL 은 로그·프록시·리퍼러에 남는다. 바디는 오류 리포트에 실려 나갈 수 있다.
    공유 비밀은 헤더로만 나가야 한다.
    """
    _sync(
        soul_trace_import.fetch_source_letter,
        trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
    )
    call = _FakeClient.calls[0]
    assert call["headers"]["X-EB-Service-Token"] == SECRET
    assert SECRET not in call["url"]
    assert SECRET not in str(call["json"])


def test_consumed_by_is_recorded_for_the_claim_audit():
    _sync(
        soul_trace_import.fetch_source_letter,
        trace_id=TRACE, handoff=TOKEN, consumed_by="auditee@example.com",
    )
    assert _FakeClient.calls[0]["json"]["consumedBy"] == "auditee@example.com"


# ── 설정·모양 검사 ───────────────────────────────────────────────────────────


def test_without_the_secret_no_request_is_made_at_all(monkeypatch: pytest.MonkeyPatch):
    """설정 누락이 곧 '인증 없이 시도'가 되면 안 된다 — 나가기 전에 멈춘다."""
    monkeypatch.delenv("SOUL_TRACE_SERVICE_TOKEN", raising=False)
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "IMPORT_NOT_CONFIGURED"
    assert e.value.status == 503
    assert _FakeClient.calls == []


@pytest.mark.parametrize(
    "trace_id,handoff",
    [
        ("not-a-uuid", TOKEN),
        (TRACE, "too-short"),
        ("", TOKEN),
        (TRACE, ""),
        (TRACE, "h" * 44),
    ],
)
def test_malformed_input_never_reaches_soul_trace(trace_id: str, handoff: str):
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=trace_id, handoff=handoff, consumed_by="me@example.com",
        )
    assert e.value.code == "HANDOFF_INVALID"
    assert _FakeClient.calls == []


# ── 오류 매핑 ────────────────────────────────────────────────────────────────


def test_consumed_or_expired_token_is_reported_as_such():
    """1회용이다 — 두 번째 교환은 409 이고, 그것을 그대로 전한다."""
    _FakeClient.response = _Res(409, {"error": "already used"})
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "HANDOFF_CONSUMED"
    assert e.value.status == 409
    # **재시도하지 않는다.** 재시도가 통하면 1회용이 아니게 된다.
    assert len(_FakeClient.calls) == 1


def test_our_own_auth_failure_is_not_blamed_on_the_user():
    """
    401 은 우리 자격 증명 문제다. "링크가 잘못됐다"고 말하면 사용자는 멀쩡한
    링크를 버리고 다시 만들려 한다 — 고쳐야 할 주체는 운영이다.
    """
    _FakeClient.response = _Res(401, {"error": "Unauthorized."})
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "IMPORT_NOT_CONFIGURED"


def test_empty_body_is_refused_not_filled_in():
    """
    **핵심 계약**: Eternal Beam 은 편지를 만들지 않는다. 빈 본문에 기본 문구를
    넣으면 고객은 Soul Trace 가 쓴 적 없는 문장을 인쇄해 받는다.
    """
    _FakeClient.response = _Res(200, {"letterId": TRACE, "letterBody": "   ", "petName": "고야"})
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "SOURCE_BODY_EMPTY"


def test_a_mismatched_letter_id_is_refused():
    """요청한 편지가 아닌 것을 받으면 남의 편지를 인쇄할 수 있다."""
    other = "11111111-2222-3333-4444-555555555555"
    _FakeClient.response = _Res(200, {"letterId": other, "letterBody": BODY, "petName": "x"})
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "SOURCE_MISMATCH"


def test_network_failure_does_not_leak_internals():
    _FakeClient.response = RuntimeError("connection reset to 10.0.0.5:5432")
    with pytest.raises(soul_trace_import.ImportError_) as e:
        _sync(
            soul_trace_import.fetch_source_letter,
            trace_id=TRACE, handoff=TOKEN, consumed_by="me@example.com",
        )
    assert e.value.code == "SOURCE_UNAVAILABLE"
    assert "10.0.0.5" not in e.value.message


# ── 발췌 ─────────────────────────────────────────────────────────────────────


def test_excerpt_is_a_cut_not_a_rewrite():
    """발췌는 자른 것이어야 한다 — 요약하면 그것은 우리가 쓴 문장이 된다."""
    long_body = "가나다라마바사 " * 40
    ex = soul_trace_import.excerpt_of(long_body)
    assert len(ex) < len(long_body)
    assert long_body.startswith(ex.rstrip("…"))


def test_import_module_never_generates():
    """구조로 고정 — 가져오기 경로에 생성 모듈이 없다."""
    import ast

    forbidden = {
        "luma_service", "wan_service", "video_generation", "premium_generation",
        "generation_queue", "credit_generation_service", "prompt_factory",
        "openai", "anthropic",
    }
    tree = ast.parse(
        open("backend/services/soul_trace_import.py", encoding="utf-8").read()
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.name)
            if node.module:
                imported.add(node.module.rsplit(".", 1)[-1])
    assert not (imported & forbidden), imported & forbidden
