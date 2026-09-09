"""
QR 재다운로드 + 인쇄 안전 (Phase 13.1).

── 무엇을 푸는가 ───────────────────────────────────────────────────────────
Phase 10 은 공유 토큰을 해시로만 저장한다. 옳지만 운영에 실질적 문제를 만들었다:
발급 탭을 닫으면 **같은 QR 을 다시 뽑을 수 없어** 재발급뿐이었고, 그건 새 토큰 →
이미 인쇄된 QR 무효화 → 재인쇄를 뜻했다.

여기서 고정하는 것:
  * 같은 QR 이 **바이트 단위로 동일하게** 다시 나온다.
  * 원문 토큰은 **여전히 어디에도 저장되지 않는다.**
  * 재다운로드가 새 공유를 만들지 않고, **이미 인쇄된 QR 도 계속 유효하다.**
  * 인쇄용 QR 은 PUBLIC_WEB_BASE_URL 이 없으면 **만들지 않는다** (fail closed).
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_ops_v1, shaker_v1
from backend.services import (
    generated_motions_service as motions_svc,
)
from backend.services import (
    premium_purchase,
    qr_service,
    shaker_qr_artifact,
    shaker_rate_limit,
    shaker_share,
)

from .conftest import ASGITestClient

OPS = "ops@eternalbeam.com"
CUSTOMER = "customer@example.com"
CONTENT = "abc123"
PET = f"pet_{CONTENT}"
BUCKET = "user-assets"
OBJ = f"{CUSTOMER}/{CONTENT}/idle_loop.mp4"
WEB = "https://eternalbeam.com"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", WEB)
    shaker_share.__reset_for_tests()
    shaker_qr_artifact.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_qr_artifact.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_ops_v1.router, prefix="/api")
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


class FakeStorage:
    def __init__(self, bucket: str, existing: set[str]):
        self.bucket = bucket
        self.existing = existing

    def create_signed_url(self, path: str, seconds: int):
        if path not in self.existing:
            raise RuntimeError("object not found")
        return {
            "signedURL": f"https://proj.supabase.co/storage/v1/object/sign/"
                         f"{self.bucket}/{path}?token=FRESH"
        }


class FakeClient:
    def __init__(self, existing: set[str]):
        self.existing = existing
        self.storage = self

    def from_(self, bucket: str) -> FakeStorage:
        return FakeStorage(bucket, self.existing)


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch):
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient({OBJ}))


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(u: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _seed_pet() -> None:
    key = motions_svc._motion_key(CUSTOMER, PET, "any", "COME_CLOSER")
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=CUSTOMER, pet_id=PET, place_id="any", action_id="COME_CLOSER",
        video_url="https://cdn.test/cc.mp4",
    )


def _mint(client: ASGITestClient, purpose: str = "LETTER"):
    return client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "pet_name": "고야", "purpose": purpose},
        headers=_auth(OPS),
    )


# ── 접근 제어 ────────────────────────────────────────────────────────────────


def test_qr_redownload_is_ops_only(client: ASGITestClient):
    _seed_pet()
    sid = _mint(client).json()["share_id"]

    assert client.get(f"/api/v1/shaker/ops/share/{sid}/qr").status_code == 401
    # 펫의 **주인**이라도 운영자가 아니면 접근할 수 없다 — 생산 도구는 판매자 소유다.
    assert client.get(
        f"/api/v1/shaker/ops/share/{sid}/qr", headers=_auth(CUSTOMER)
    ).status_code == 403
    assert client.get(
        f"/api/v1/shaker/ops/share/{sid}/qr", headers=_auth(OPS)
    ).status_code == 200


# ── 재다운로드: 같은 QR ──────────────────────────────────────────────────────


def test_redownload_returns_the_exact_same_qr(client: ASGITestClient):
    """
    **핵심 회귀**: 다시 받은 QR 이 처음 것과 **바이트 단위로 같다.**

    같지 않으면 이미 인쇄된 카드와 새로 뽑은 카드가 다른 것을 가리킬 수 있다.
    """
    _seed_pet()
    created = _mint(client).json()
    sid, url = created["share_id"], created["share_url"]

    original = client.get(
        "/api/v1/shaker/ops/qr", params={"share_url": url, "kind": "svg"}, headers=_auth(OPS)
    ).content

    for _ in range(3):
        again = client.get(
            f"/api/v1/shaker/ops/share/{sid}/qr", params={"kind": "svg"}, headers=_auth(OPS)
        )
        assert again.status_code == 200
        assert again.content == original


def test_redownload_works_after_losing_the_token(client: ASGITestClient):
    """
    발급 응답을 잃어버려도 QR 을 다시 받을 수 있다 — 이것이 이 기능의 이유다.

    share_id 만 알면 된다(비밀이 아니다). 토큰은 여전히 복원되지 않는다.
    """
    _seed_pet()
    sid = _mint(client).json()["share_id"]  # 토큰은 버린다

    r = client.get(
        f"/api/v1/shaker/ops/share/{sid}/qr", params={"kind": "svg"}, headers=_auth(OPS)
    )
    assert r.status_code == 200
    assert r.content.startswith(b"<?xml") or r.content.lstrip().startswith(b"<svg")
    assert r.headers["cache-control"] == "no-store"


def test_redownload_supports_png(client: ASGITestClient):
    _seed_pet()
    sid = _mint(client).json()["share_id"]
    r = client.get(
        f"/api/v1/shaker/ops/share/{sid}/qr", params={"kind": "png"}, headers=_auth(OPS)
    )
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_redownload_creates_no_new_share(client: ASGITestClient, monkeypatch):
    """**핵심 회귀**: 재다운로드가 새 공유를 만들지 않는다 (펫 경험 중복 금지)."""
    _seed_pet()
    sid = _mint(client).json()["share_id"]
    before = dict(shaker_share._MOCK_SHARES)

    async def _boom(**_kw):
        raise AssertionError("재다운로드가 새 공유를 발급했다")

    monkeypatch.setattr(shaker_share, "create_share", _boom)

    for _ in range(3):
        assert client.get(
            f"/api/v1/shaker/ops/share/{sid}/qr", headers=_auth(OPS)
        ).status_code == 200

    assert shaker_share._MOCK_SHARES == before
    assert len(shaker_qr_artifact._MOCK_ARTIFACTS) == 1


def test_already_printed_share_still_opens_after_redownload(client: ASGITestClient):
    """
    **핵심 회귀**: 재다운로드가 이미 인쇄된 QR 을 무효화하지 않는다.

    예전 경로(재발급)는 새 토큰을 만들어 인쇄물을 죽였다.
    """
    _seed_pet()
    created = _mint(client).json()
    token = created["token"]

    assert client.get("/api/v1/shaker/pet", params={"share": token}).status_code == 200

    for _ in range(3):
        client.get(f"/api/v1/shaker/ops/share/{created['share_id']}/qr", headers=_auth(OPS))

    # 인쇄된 카드가 들고 있는 토큰이 여전히 열린다.
    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200
    assert r.json()["pet_id"] == PET


def test_unknown_share_has_no_artifact(client: ASGITestClient):
    r = client.get("/api/v1/shaker/ops/share/shr_nope/qr", headers=_auth(OPS))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "QR_ARTIFACT_NOT_FOUND"


# ── 토큰 원문은 여전히 저장되지 않는다 ──────────────────────────────────────


def test_plaintext_token_is_never_stored(client: ASGITestClient):
    """
    **핵심 회귀**: 산출물을 보관해도 원문 토큰은 어디에도 문자열로 남지 않는다.

    ⚠️ QR 은 디코딩 가능하다 — 산출물을 읽을 수 있는 사람은 스캔해서 URL 을 얻을
    수 있고, 그건 인쇄된 카드를 가진 사람과 같은 수준이다. 보호는 접근 제어다.
    여기서 고정하는 것은 "덤프에서 문자열 검색으로 전량 긁히지 않는다"이다.
    """
    _seed_pet()
    token = _mint(client).json()["token"]

    shares_blob = repr(shaker_share._MOCK_SHARES)
    artifacts_blob = repr(shaker_qr_artifact._MOCK_ARTIFACTS)

    assert token not in shares_blob
    assert token not in artifacts_blob, "QR 산출물 테이블에 원문 토큰이 남았다"
    # 해시는 여전히 조회 키다.
    assert shaker_share.hash_token(token) in shares_blob


def test_artifact_stores_hash_not_url(client: ASGITestClient):
    """산출물 행에 share_url 컬럼이 없다 — 저장하는 것은 이미지와 호스트뿐이다."""
    _seed_pet()
    created = _mint(client).json()
    art = _sync(shaker_qr_artifact.get, created["share_id"])

    assert art is not None
    assert art.token_hash == shaker_share.hash_token(created["token"])
    assert art.target_host == "eternalbeam.com"
    assert not hasattr(art, "share_url")
    assert created["token"] not in art.qr_svg


# ── 인쇄 안전: fail closed ───────────────────────────────────────────────────


@pytest.mark.parametrize("purpose", ["LETTER", "MEMORY_BOX"])
def test_print_purpose_requires_configured_base(
    client: ASGITestClient, monkeypatch, purpose: str
):
    """
    **핵심 회귀**: PUBLIC_WEB_BASE_URL 이 없으면 인쇄용 QR 을 만들지 않는다.

    요청 호스트로 유도하면 API 도메인이나 localhost 를 가리킨 카드가 인쇄되고,
    인쇄물은 회수할 수 없다.
    """
    monkeypatch.delenv("PUBLIC_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("VITE_PUBLIC_WEB_URL", raising=False)
    _seed_pet()

    r = _mint(client, purpose)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PRINT_BASE_URL_MISSING"
    # 공유도 산출물도 만들어지지 않았다.
    assert shaker_share._MOCK_SHARES == {}
    assert shaker_qr_artifact._MOCK_ARTIFACTS == {}


@pytest.mark.parametrize(
    "base,code",
    [
        ("http://eternalbeam.com", "PRINT_BASE_URL_INSECURE"),
        ("https://localhost:5174", "PRINT_BASE_URL_UNSAFE"),
        ("https://127.0.0.1:8000", "PRINT_BASE_URL_UNSAFE"),
        ("https://testserver", "PRINT_BASE_URL_UNSAFE"),
    ],
)
def test_print_base_rejects_unsafe_targets(
    client: ASGITestClient, monkeypatch, base: str, code: str
):
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", base)
    _seed_pet()
    r = _mint(client, "LETTER")
    assert r.status_code == 409, base
    assert r.json()["detail"]["code"] == code, base


def test_non_print_purpose_still_works_without_base(client: ASGITestClient, monkeypatch):
    """
    화면용(OPS/CUSTOMER)은 예전처럼 폴백한다 — 인쇄물이 아니라 되돌릴 수 있다.

    막는 것은 **인쇄**이지 개발 편의가 아니다.
    """
    monkeypatch.delenv("PUBLIC_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("VITE_PUBLIC_WEB_URL", raising=False)
    _seed_pet()

    r = _mint(client, "OPS")
    assert r.status_code == 200
    assert r.json()["share_url"].startswith("http://testserver/shaker?")


def test_print_safety_helpers_are_pure(monkeypatch):
    monkeypatch.delenv("PUBLIC_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("VITE_PUBLIC_WEB_URL", raising=False)
    assert qr_service.configured_web_base() == ""
    with pytest.raises(qr_service.QrError):
        qr_service.assert_printable_base()

    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", WEB)
    assert qr_service.assert_printable_base() == WEB
    assert qr_service.is_print_purpose("MEMORY_BOX") is True
    assert qr_service.is_print_purpose("CUSTOMER") is False
    assert qr_service.target_host(f"{WEB}/shaker?share=x") == "eternalbeam.com"


# ── 생성 금지 ────────────────────────────────────────────────────────────────


def test_qr_recovery_never_generates(client: ASGITestClient, monkeypatch):
    from backend.services import generation_queue, premium_generation

    _seed_pet()
    sid = _mint(client).json()["share_id"]

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    for kind in ("svg", "png"):
        client.get(
            f"/api/v1/shaker/ops/share/{sid}/qr", params={"kind": kind}, headers=_auth(OPS)
        )
    assert fired == []


def test_artifact_module_is_independent():
    """구조로 고정 — 산출물 모듈이 생성·구독·결제 모듈을 import 하지 않는다."""
    import ast

    forbidden = {
        "premium_entitlement", "subscription_store_service", "premium_generation",
        "generation_queue", "credit_generation_service", "wallet_service",
        "premium_purchase", "luma_service", "wan_service", "video_generation",
        "toss_billing", "theme_purchase",
    }
    tree = ast.parse(open("backend/services/shaker_qr_artifact.py", encoding="utf-8").read())
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


# ── 생산이 산출물로 준비된다 ────────────────────────────────────────────────


def test_production_can_prepare_from_artifact_without_the_url(client: ASGITestClient):
    """
    **핵심 회귀**: 토큰을 잃어버려도 생산 준비가 막히지 않는다.

    Phase 13 에서는 URL 이 필수라 발급 탭을 닫으면 생산이 불가능했고, 남는 경로는
    재발급(= 인쇄물 무효화)뿐이었다.
    """
    from backend.services import physical_order, production_package, soul_trace_letter

    _seed_pet()
    created = _mint(client).json()

    _sync(
        soul_trace_letter.link_letter,
        user_id=CUSTOMER, source_letter_id="st_1", pet_id=PET,
        letter_body="안녕, 엄마 아빠.", child_name="고야",
    )
    letter_id = _sync(soul_trace_letter.list_letters, CUSTOMER)[0].letter_id

    _sync(
        physical_order.create,
        order_id="eb_order_x", user_id=CUSTOMER, pet_id=PET,
        product_type="MEMORY_BOX", amount=49000, soul_trace_letter_id=letter_id,
        recipient_name="김보호", recipient_phone="010", postal_code="06236",
        address_line1="서울시", shaker_share_id=created["share_id"],
    )
    _sync(physical_order.mark_paid, order_id="eb_order_x", payment_key="pk", amount=49000)

    # URL 을 주지 않는다 — 산출물로 준비돼야 한다.
    pkg = _sync(production_package.prepare, order_id="eb_order_x")
    assert pkg.qr_source == "artifact"
    assert pkg.qr_share_url is None
    assert pkg.shaker_share_id == created["share_id"]

    # 그리고 QR 카드는 **인쇄된 것과 같은** QR 바이트를 쓴다.
    art = _sync(shaker_qr_artifact.get, created["share_id"])
    card = _sync(production_package.render_file, pkg, "qr_card")
    assert card.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert art.qr_png is not None

    production_package.__reset_for_tests()
    physical_order.__reset_for_tests()
    soul_trace_letter.__reset_for_tests()


def test_artifact_is_immutable_once_written(client: ASGITestClient):
    """
    **핵심 회귀**: 산출물은 한 번 쓰이면 바뀌지 않는다.

    덮어쓰면 재다운로드가 **이미 인쇄된 QR 과 달라진다** — 이 기능이 막으려던
    바로 그 상황이다. store() 를 다시 불러도(다른 URL 로도) 원본이 유지돼야 한다.

    ⚠️ 돌연변이 검사에서 드러난 구멍이다: 실제 흐름은 store() 를 한 번만 부르므로
       덮어쓰기 방지가 테스트되지 않고 있었다.
    """
    _seed_pet()
    created = _mint(client).json()
    sid = created["share_id"]
    original = _sync(shaker_qr_artifact.get, sid)

    # 완전히 다른 공유 URL 로 다시 저장을 시도한다.
    other = f"{WEB}/shaker?petId={PET}&share=" + "z" * 43
    returned = _sync(
        shaker_qr_artifact.store,
        share_id=sid, token_hash="different_hash", pet_id=PET,
        share_url=other, purpose="LETTER",
    )

    assert returned.qr_svg == original.qr_svg, "산출물이 덮어써졌다"
    assert returned.token_hash == original.token_hash
    assert _sync(shaker_qr_artifact.get, sid).qr_svg == original.qr_svg

    # 엔드포인트로 받은 것도 그대로다.
    served = client.get(
        f"/api/v1/shaker/ops/share/{sid}/qr", params={"kind": "svg"}, headers=_auth(OPS)
    ).content
    assert served == original.qr_svg.encode("utf-8")
