"""
히어로 배경의 **내구성** — 며칠 뒤에 claim 해도 배경이 남아 있는가. (Phase 24)

── 고친 결함 ───────────────────────────────────────────────────────────────
Soul Trace 는 DALL·E 가 돌려준 임시 주소를 그대로 들고 있었다. 한두 시간이면
죽는다. 고객이 편지를 보고 곧바로 넘어오면 우리가 제때 복사하지만, 이틀 뒤에
넘어오면 원본은 이미 없다 — 편지 본문은 멀쩡한데 실물 편지에서 배경만 사라진다.

이제 Soul Trace 가 **생성 직후** 바이트를 자기 비공개 버킷에 넣고, claim 시점에
짧은 서명을 새로 발급해 준다. 우리 쪽에서 달라지는 것은 하나다: 받는 주소의
호스트가 DALL·E 가 아니라 Supabase 스토리지다. 그것을 허용하지 않으면 고친
효과가 **우리 쪽 허용 목록에서 그대로 막힌다.**

여기서 확인하는 것:
  * 서명된 Soul Trace 객체를 받아들이는가 (막히면 배경이 계속 사라진다)
  * 그렇다고 아무 주소나 받아들이지는 않는가 (오픈 프록시가 되지 않는다)
  * 레거시 DALL·E 주소가 여전히 동작하는가
  * 몇 번을 claim 해도 객체가 하나인가
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services import letter_background

SIGNED = (
    "https://pjoyuvqykggcuvbsnxio.supabase.co/storage/v1/object/sign/"
    "hero-images/letters/3f2504e0-4f89-11d3-9a0c-0305e82c3301/hero.png?token=abc.def"
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("SOUL_TRACE_SUPABASE_URL", raising=False)
    monkeypatch.delenv("SOUL_TRACE_API_BASE", raising=False)


# ── 1·2·3. 보관된 히어로가 실제로 우리에게 도달하는가 ────────────────────────


def test_signed_soul_trace_object_is_accepted():
    """**이 한 줄이 막히면 이번 단계 전체가 무의미해진다.**"""
    assert letter_background.is_allowed_source(SIGNED)


def test_configured_storage_host_narrows_the_allowance(monkeypatch):
    monkeypatch.setenv("SOUL_TRACE_SUPABASE_URL", "https://pjoyuvqykggcuvbsnxio.supabase.co")
    assert letter_background.is_allowed_source(SIGNED)

    other = SIGNED.replace("pjoyuvqykggcuvbsnxio", "someoneelse")
    assert not letter_background.is_allowed_source(other), "다른 프로젝트가 통과한다"


def test_legacy_dalle_url_still_works():
    """보관 이전에 만들어진 편지들이다. 원본이 살아 있는 동안은 그대로 동작한다."""
    assert letter_background.is_allowed_source(
        "https://oaidalleapiprod.blob.core.windows.net/private/img.png"
    )


# ── 보안: 넓혀도 오픈 프록시는 되지 않는다 ───────────────────────────────────


def test_supabase_allowance_is_limited_to_signed_objects():
    """
    호스트만 보지 않고 **경로까지** 본다. 서명된 객체 하나에만 열려 있어야지
    프로젝트 API 전체가 열리면 안 된다 — 그쪽에는 DB 도 붙어 있다.
    """
    host = "https://pjoyuvqykggcuvbsnxio.supabase.co"
    for bad in (
        f"{host}/rest/v1/soul_trace_profiles?select=*",
        f"{host}/auth/v1/admin/users",
        f"{host}/storage/v1/object/public/hero-images/x.png",
        f"{host}/",
    ):
        assert not letter_background.is_allowed_source(bad), bad


def test_still_rejects_everything_else():
    for bad in (
        "http://pjoyuvqykggcuvbsnxio.supabase.co/storage/v1/object/sign/x",  # https 아님
        "https://evil.example.com/storage/v1/object/sign/x",
        "https://evil.blob.core.windows.net/x.png",
        "https://supabase.co.evil.com/storage/v1/object/sign/x",
        "http://169.254.169.254/latest/meta-data/",
        "",
        "not a url",
    ):
        assert not letter_background.is_allowed_source(bad), bad


# ── 6. 반복 claim 이 객체를 늘리지 않는다 ────────────────────────────────────


def test_repeated_claim_converges_on_one_object():
    """
    경로는 (user_id, letter_id) 로 결정적이다. 그래서 몇 번을 가져와도 같은 자리에
    덮어쓴다 — claim 마다 새 객체를 만들면 스토리지에 고아가 쌓이고, 어느 것이
    인쇄될지 알 수 없게 된다.
    """
    paths = {
        letter_background.object_path_for("u@x.com", "stl_abc") for _ in range(5)
    }
    assert len(paths) == 1
    assert paths.pop() == "u@x.com/letters/stl_abc/background.jpg"


def test_upload_is_upsert_not_a_second_object():
    """멱등의 나머지 절반 — 경로가 같아도 업로드가 거절되면 사본이 갱신되지 않는다."""
    import pathlib

    src = pathlib.Path("backend/services/supabase_assets.py").read_text()
    i = src.index("async def upload_asset_to_storage")
    assert '"upsert": "true"' in src[i : i + 900]


def test_stored_ref_is_a_path_not_a_signed_url():
    ref = letter_background.object_path_for("u@x.com", "stl_abc")
    assert "http" not in ref and "token" not in ref and "?" not in ref


# ── 5. 없거나 깨진 히어로는 치명적이지 않다 ─────────────────────────────────


def test_missing_source_is_not_fatal():
    for empty in (None, "", "   "):
        assert asyncio.run(
            letter_background.import_from_source(
                source_url=empty, user_id="u@x.com", letter_id="stl_abc"
            )
        ) is None


def test_disallowed_source_returns_none_without_fetching(monkeypatch):
    """거절은 예외가 아니다 — 배경이 없을 뿐 편지 가져오기는 계속된다."""
    called = False

    async def _never(url):  # noqa: ANN001
        nonlocal called
        called = True
        return b"x"

    monkeypatch.setattr(letter_background, "_fetch", _never)
    got = asyncio.run(
        letter_background.import_from_source(
            source_url="https://evil.example.com/x.png",
            user_id="u@x.com",
            letter_id="stl_abc",
        )
    )
    assert got is None
    assert not called, "허용되지 않은 호스트로 요청이 나갔다"


def _capture_uploads(monkeypatch) -> list[tuple[str, bytes, str]]:
    """업로드를 가로챈다. 실제 Supabase 없이 '무엇이 올라갔는가'를 본다."""
    from backend.services import supabase_assets

    seen: list[tuple[str, bytes, str]] = []

    async def _upload(path, data, content_type):  # noqa: ANN001
        seen.append((path, data, content_type))
        return "https://eb.example/signed"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", _upload)
    return seen


def test_corrupt_bytes_do_not_produce_a_ref(monkeypatch):
    """
    이미지가 아닌 바이트를 배경이라고 저장하면 인쇄 시점에야 알게 된다.
    여기서 None 을 돌려주면 인쇄는 기존 스크림으로 떨어진다.
    """
    uploaded = _capture_uploads(monkeypatch)

    async def _html(url):  # noqa: ANN001
        return b"<html>expired</html>"

    monkeypatch.setattr(letter_background, "_fetch", _html)
    got = asyncio.run(
        letter_background.import_from_source(
            source_url=SIGNED, user_id="u@x.com", letter_id="stl_abc"
        )
    )
    assert got is None
    assert uploaded == [], "해석하지 못한 바이트가 스토리지로 올라갔다"


def test_claim_days_later_still_imports_the_background(monkeypatch):
    """
    **이번 단계가 사려는 결과다.**

    편지 생성 시각과 claim 사이에 며칠이 놓여도, Soul Trace 가 방금 발급한 서명을
    통해 히어로가 우리 스토리지로 들어온다. DALL·E 원본이 살아 있는지 여부는
    이 경로 어디에도 등장하지 않는다.
    """
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 90, 60)).save(buf, format="PNG")
    png = buf.getvalue()

    uploaded = _capture_uploads(monkeypatch)
    fetched: list[str] = []

    async def _fetch(url):  # noqa: ANN001
        fetched.append(url)
        return png

    monkeypatch.setattr(letter_background, "_fetch", _fetch)
    ref = asyncio.run(
        letter_background.import_from_source(
            source_url=SIGNED, user_id="u@x.com", letter_id="stl_abc"
        )
    )

    assert ref == "u@x.com/letters/stl_abc/background.jpg"
    assert fetched == [SIGNED], "Soul Trace 가 준 서명 주소로 받지 않았다"
    assert len(uploaded) == 1
    path, data, content_type = uploaded[0]
    assert path == ref
    assert content_type == "image/jpeg"
    # 인쇄 입력으로 평탄화된 JPEG 이다 — 원본 PNG 를 그대로 넣지 않는다.
    assert data[:3] == b"\xff\xd8\xff"


def test_repeated_claim_writes_the_same_path(monkeypatch):
    """반복 claim 이 두 번째 객체를 만들지 않는다 — 경로가 같으면 덮어쓴다."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="PNG")
    png = buf.getvalue()

    uploaded = _capture_uploads(monkeypatch)

    async def _fetch(url):  # noqa: ANN001
        return png

    monkeypatch.setattr(letter_background, "_fetch", _fetch)
    for _ in range(3):
        asyncio.run(
            letter_background.import_from_source(
                source_url=SIGNED, user_id="u@x.com", letter_id="stl_abc"
            )
        )

    assert len({p for p, _d, _c in uploaded}) == 1, "claim 마다 새 객체가 생긴다"


def test_claim_does_not_break_when_background_import_raises():
    """
    가져오기 라우트가 배경 실패를 삼키는지 — 소스로 확인한다. 여기서 예외가
    새어 나가면 **결제로 이어질 편지 자체**를 배경 한 장 때문에 잃는다.
    """
    import pathlib

    src = pathlib.Path("backend/routers/orders_v1.py").read_text()
    i = src.index("letter_background.import_from_source")
    block = src[max(0, i - 900) : i + 400]
    assert "try:" in block
    assert "except Exception:" in block
    assert "background_ref = None" in block


# ── 계약: 우리는 받은 주소를 저장하지 않는다 ────────────────────────────────


def test_we_never_store_the_url_we_were_given():
    """
    Soul Trace 가 준 주소는 서명이든 DALL·E 든 **수명이 짧다.** 그것을 저장해 두고
    인쇄 시점에 다시 받는 설계는 어느 쪽이든 성립하지 않는다 — 그래서 받는 즉시
    바이트를 복사하고, 저장하는 것은 우리 경로뿐이다.
    """
    import inspect

    src = inspect.getsource(letter_background.import_from_source)
    assert "object_path_for" in src
    assert "return path" in src
    # source_url 을 그대로 돌려주는 경로가 없어야 한다.
    assert "return url" not in src and "return source_url" not in src
