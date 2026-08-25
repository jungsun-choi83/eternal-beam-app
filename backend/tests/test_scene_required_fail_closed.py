"""
고객이 고른 배경을 잃었으면 **제출하지 않는다.** (Phase 26)

── 고친 결함 ───────────────────────────────────────────────────────────────
`scene_input.resolve` 는 두 상황을 똑같이 `None` 으로 표현했다:

  (a) 클라이언트가 장면을 보내지 않았다 (레거시)
  (b) 클라이언트가 장면을 보냈는데 **우리가 그것을 가져오지 못했다**

라우터는 둘을 구분할 수 없었으므로 (b) 에서도 조용히 레거시 키프레임(단색 판)으로
떨어져 그대로 유료 생성을 돌렸다. 결과는 두 가지였고 둘 다 나빴다:

  * 고객은 자기가 고르지도 승인하지도 않은 그림의 영상을 받았다
  * 그 경로는 `if baked:` 블록 **밖**이라 멱등성 예약이 없었다 — 클라이언트
    타임아웃 한 번이 그대로 두 번째 유료 작업이 됐다

이제 (b) 는 requested=True, scene=None 으로 구분되고 프로바이더 제출 **전에**
503 SCENE_UNAVAILABLE 로 거절된다. 두 번째 문제는 자동으로 함께 닫힌다 —
예약 없이 지나가던 경로 자체가 사라지기 때문이다.

── 여기서 진짜 호출을 세는 이유 ────────────────────────────────────────────
이 파일의 핵심 주장은 "돈이 나가지 않는다"이고, 그것은 소스 문자열로는 증명되지
않는다. 그래서 프로바이더 함수를 실제로 가로채 **호출 횟수 0** 을 확인한다.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import FastAPI

from .conftest import ASGITestClient, make_rgba_png_bytes

# 이 저장소의 테스트 환경에는 OpenCV 가 없다. 라우터를 import 하려면 모듈이
# 존재하기만 하면 된다 — video_cutout_service 는 cv2 속성을 **함수 안에서만**
# 쓰고, 이 파일의 경로는 그 함수를 타지 않는다(skip_preprocessing=true).
#
# ⚠️ import 로 **새로 생긴 모듈을 전부 되돌린다.** sys.modules 는 프로세스
#    전역이다. cv2 대역만 걷어내는 것으로는 부족했다 — 대역 덕에 성공한
#    video_cutout_service 가 캐시에 남아, 나중에 그것을 import 하는
#    test_generate_gate.py 의 6건이 조용히 통과로 바뀌었다. 한 테스트 파일이
#    다른 파일의 판정을 바꾸면 그 스위트는 더 이상 무엇도 증명하지 못한다.
#
#    아래에서 잡아 두는 모듈 **객체**는 evict 후에도 그대로 살아 있고, 이
#    파일의 앱은 그 객체를 쓴다 — 다른 파일은 자기 것을 새로 import 한다.
_before = set(sys.modules)
_stubbed = "cv2" not in sys.modules
if _stubbed:  # pragma: no cover - 환경에 따라 갈린다
    try:
        import cv2  # noqa: F401

        _stubbed = False
    except ModuleNotFoundError:
        sys.modules["cv2"] = types.ModuleType("cv2")

try:
    from backend.routers import generate as generate_router  # noqa: E402
    from backend.services import scene_generation_jobs as jobs  # noqa: E402
finally:
    if _stubbed:
        _new = set(sys.modules) - _before
        for _name in _new:
            sys.modules.pop(_name, None)
        # sys.modules 를 비우는 것만으로는 **부족하다.** import 는 부모 패키지에
        # 자식 모듈을 속성으로도 붙여 놓는다. 그래서 `backend.services` 가 살아
        # 있으면 `from backend.services import dog_image_preprocessing` 이
        # 캐시를 거치지 않고 그 속성으로 성공해 버린다 — 실제로 그것 때문에
        # test_generate_gate.py 의 3건이 조용히 통과로 바뀌었다.
        for _name in _new:
            _parent, _, _child = _name.rpartition(".")
            _pmod = sys.modules.get(_parent)
            if _pmod is not None and getattr(_pmod, _child, None) is not None:
                try:
                    delattr(_pmod, _child)
                except AttributeError:  # pragma: no cover
                    pass

SCENE_URL = "https://storage.test/u/c/scene_s1.png"


class _Validation:
    passed = True
    message = ""

    def to_dict(self):
        return {"passed": True}


@pytest.fixture
def client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_VIDEO_SEAMLESS_LOOP", "0")
    jobs.__reset_for_tests()

    app = FastAPI()
    app.include_router(generate_router.router, prefix="/api")
    c = ASGITestClient(app)
    #: 프로바이더 호출 횟수 = **지출 횟수**. 이 테스트의 주장이 걸려 있다.
    c.calls = {"provider": 0, "upload": 0, "scene_fetch": 0}
    c.provider_args = []

    async def fake_upload(path, data, content_type):
        c.calls["upload"] += 1
        return f"https://storage.test/{path}"

    async def fake_provider(key_url, prompt, **kwargs):
        c.calls["provider"] += 1
        c.provider_args.append({"key_url": key_url, "prompt": prompt})
        on_submit = kwargs.get("on_submit")
        if on_submit:
            await on_submit("provider-job-1")
        return "https://provider.test/video.mp4"

    async def fake_download(url):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".mp4")
        with open(fd, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42fake")
        return path

    monkeypatch.setattr(generate_router.supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(generate_router, "create_generation_and_get_video_url", fake_provider)
    monkeypatch.setattr(generate_router, "download_video", fake_download)
    monkeypatch.setattr(generate_router, "validate_idle_video", lambda *a, **k: _Validation())
    yield c
    jobs.__reset_for_tests()


def _scene_fetch(client, *, ok: bool):
    """장면 이미지 다운로드를 가로챈다 — 성공/실패를 우리가 정한다."""
    import io

    from PIL import Image

    scene_input = generate_router.scene_input

    async def fetch(scene):
        client.calls["scene_fetch"] += 1
        if not ok:
            # 실제로 나는 모양: 서명 만료·스토리지 404·네트워크 단절.
            return scene
        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (40, 80, 40)).save(buf, format="PNG")
        return scene_input.SceneInput(
            scene_id=scene.scene_id,
            background_type=scene.background_type,
            background_id=scene.background_id,
            scene_keyframe_url=scene.scene_keyframe_url,
            scene_bytes=buf.getvalue(),
        )

    return fetch


SCENE_FORM = {
    "skip_preprocessing": "true",
    "idle_only": "true",
    "user_id": "u@example.com",
    "content_id": "c1",
    "scene_id": "s1",
    "background_type": "theme",
    "background_id": "fresh_forest",
    "scene_keyframe_url": SCENE_URL,
    "background_baked": "true",
}

LEGACY_FORM = {
    "skip_preprocessing": "true",
    "idle_only": "true",
    "user_id": "u@example.com",
    "content_id": "c1",
}


def _post(client, data):
    return client.post(
        "/api/generate-pet-video",
        files={"file": ("cutout.png", make_rgba_png_bytes(0.4), "image/png")},
        data=data,
    )


# ── 1. 장면을 요구했는데 못 얻었다 → 제출 없음 ───────────────────────────────


def test_scene_fetch_failure_refuses_before_any_provider_call(client, monkeypatch):
    """**이 파일의 핵심.** 돈이 나가기 전에 멈춘다."""
    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=False))

    res = _post(client, SCENE_FORM)

    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "SCENE_UNAVAILABLE"
    assert client.calls["scene_fetch"] == 1, "장면을 받아 보려는 시도는 했어야 한다"
    assert client.calls["provider"] == 0, "프로바이더에 제출됐다 — 돈이 나갔다"


def test_refusal_message_says_nothing_was_charged(client, monkeypatch):
    """
    "생성 실패"로 읽히면 고객은 다시 눌러 유료 제출을 반복한다. 이 경로는
    제출 전이므로 그렇게 말하면 안 된다.
    """
    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=False))
    detail = _post(client, SCENE_FORM).json()["detail"]
    assert "과금되지" in detail["message"]
    assert detail["scene_id"] == "s1"


def test_refusal_leaves_no_reservation_behind(client, monkeypatch):
    """
    예약을 잡기 **전**에 거절한다. 잡아 두고 거절하면 그 장면이 남은 시간 동안
    생성 불가가 된다(회수될 때까지).
    """
    import functools

    import anyio

    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=False))
    _post(client, SCENE_FORM)

    job = anyio.run(functools.partial(jobs.get, "u@example.com", "s1", "IDLE"))
    assert job is None, "거절했는데 예약이 남았다"


def test_malformed_scene_fields_are_not_downgraded_to_legacy(client):
    """
    background_baked=true 인데 필드가 어긋난다. 예전에는 조용히 레거시로
    떨어져 **생성에 성공**했다 — 고객이 고른 적 없는 배경으로.
    """
    bad = dict(SCENE_FORM, background_type="hologram")
    res = _post(client, bad)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "SCENE_UNAVAILABLE"
    assert client.calls["provider"] == 0


def test_repeated_retries_never_accumulate_provider_jobs(client, monkeypatch):
    """
    예전에 이 경로가 위험했던 진짜 이유. 멱등성 예약 밖이라 재시도마다 유료
    작업이 하나씩 늘었다. 이제는 제출 자체가 없다.
    """
    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=False))
    for _ in range(4):
        assert _post(client, SCENE_FORM).status_code == 503
    assert client.calls["provider"] == 0


# ── 2. 레거시 요청은 지금까지와 완전히 같다 ──────────────────────────────────


def test_legacy_request_still_succeeds(client):
    """
    background_baked 가 없으면 **아무것도 바뀌지 않는다.** 구버전 클라이언트가
    이번 변경으로 생성을 못 하게 되면 안 된다.
    """
    res = _post(client, LEGACY_FORM)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body["background_baked"] is False
    assert body["scene_id"] is None
    assert client.calls["provider"] == 1
    assert client.calls["scene_fetch"] == 0, "장면을 요구하지 않았는데 받아 오려 했다"


def test_explicit_false_flag_is_also_legacy(client):
    """background_baked=false 도 레거시다 — 거절 대상이 아니다."""
    res = _post(client, dict(LEGACY_FORM, background_baked="false", scene_id="s1"))
    assert res.status_code == 200
    assert res.json()["background_baked"] is False
    assert client.calls["provider"] == 1


# ── 3. 장면이 멀쩡하면 그대로 구워진다 ───────────────────────────────────────


def test_usable_scene_produces_a_baked_video(client, monkeypatch):
    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=True))

    res = _post(client, SCENE_FORM)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["background_baked"] is True
    assert body["scene_id"] == "s1"
    assert client.calls["provider"] == 1
    # 키프레임은 **장면**이어야 한다 — 누끼를 단색 판에 붙인 레거시 판이 아니라.
    assert "scene_s1.jpg" in client.provider_args[0]["key_url"]


def test_baked_path_records_the_reservation(client, monkeypatch):
    """구운 경로는 예약 안에서 돈다 — 그것이 재시도 보호의 전부다."""
    import functools

    import anyio

    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=True))
    _post(client, SCENE_FORM)

    job = anyio.run(functools.partial(jobs.get, "u@example.com", "s1", "IDLE"))
    assert job is not None and job.completed


# ── 4. 재사용·복구 경로는 그대로다 ───────────────────────────────────────────


def test_completed_scene_is_reused_without_a_second_provider_call(client, monkeypatch):
    """
    같은 장면의 두 번째 요청은 완료된 결과를 재사용한다 (generate.py 의
    첫 번째 하드코딩 background_baked=True 경로).
    """
    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=True))

    first = _post(client, SCENE_FORM)
    assert first.status_code == 200
    assert client.calls["provider"] == 1

    second = _post(client, SCENE_FORM)
    assert second.status_code == 200
    body = second.json()
    assert body["reused"] is True
    assert body["background_baked"] is True, "재사용 응답이 구움 표시를 잃었다"
    assert body["scene_id"] == "s1"
    assert body["idle_video_url"] == first.json()["idle_video_url"]
    assert client.calls["provider"] == 1, "재사용인데 또 제출했다"


def test_recovered_provider_job_reports_baked(client, monkeypatch):
    """
    끊긴 폴링을 provider_job_id 로 되찾는 경로 (두 번째 하드코딩
    background_baked=True). 되찾은 영상도 배경이 구워져 있다.
    """
    import functools

    import anyio

    monkeypatch.setattr(generate_router.scene_input, "fetch_bytes", _scene_fetch(client, ok=True))

    # 제출까지만 기록된 작업 — 폴링 중 워커가 죽은 모양.
    anyio.run(
        functools.partial(
            jobs.reserve, user_id="u@example.com", scene_id="s1", behavior="IDLE"
        )
    )
    anyio.run(
        functools.partial(
            jobs.mark_submitted,
            user_id="u@example.com",
            scene_id="s1",
            behavior="IDLE",
            provider="luma",
            provider_job_id="job-abc",
        )
    )

    import backend.services.generation_reconciler as _recon

    class _Outcome:
        state = "completed"
        video_url = "https://provider.test/recovered.mp4"

    async def fake_fetch(job_id, *, provider=None):
        assert job_id == "job-abc"
        return _Outcome()

    # _recover_provider_job 이 함수 안에서 import 하므로 sys.modules 를 통한다.
    # 우리 대역이 그 경로에 놓이도록 여기서 직접 심는다.
    sys.modules["backend.services.generation_reconciler"] = _recon
    monkeypatch.setattr(_recon, "fetch_outcome_by_id", fake_fetch)

    res = _post(client, SCENE_FORM)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reused"] is True
    assert body["background_baked"] is True
    assert body["idle_video_url"] == "https://provider.test/recovered.mp4"
    assert client.calls["provider"] == 0, "되찾을 수 있는데 새로 제출했다"


# ── 계약 고정 ────────────────────────────────────────────────────────────────


def test_refusal_happens_before_the_idempotency_block(client):
    """
    거절이 `if baked:` **앞**에 있어야 한다. 뒤에 두면 예약 없이 제출되는
    경로가 그대로 남는다 — 그것이 이번에 닫으려는 구멍이다.
    """
    import pathlib

    src = pathlib.Path("backend/routers/generate.py").read_text()
    refuse = src.index("raise _scene_unavailable(")
    baked_block = src.index("    if baked:\n")
    submit = src.index("create_generation_and_get_video_url(")
    assert refuse < baked_block < submit
