"""
Shaker 는 **절대 생성하지 않는다** — 프로바이더 호출 0회.

왜 이 파일이 따로 있는가: 이 저장소는 이미 한 번 이 결함으로 돈을 흘렸다
(test_no_premium_cascade.py — 클릭 1회에 프로바이더 호출 3회). Shaker 는 로그인
없이 **누구나** 열 수 있는 경로라, 같은 실수가 여기서 나면 피해가 인증된 사용자
수가 아니라 QR 을 본 사람 수만큼 늘어난다.

두 층위로 고정한다:

  1. 구조   — 라우터/서비스가 생성 모듈을 **import 조차 하지 않는다**.
              경로가 없으면 실수로도 갈 수 없다.
  2. 런타임 — 생성 진입점을 전부 폭탄으로 갈아 끼우고 엔드포인트를 두드린다.
              하나라도 불리면 테스트가 죽는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_v1
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_purchase, shaker_rate_limit, shaker_share

from .conftest import ASGITestClient, follow_shaker_asset

OWNER = "owner@example.com"
PET = "pet_goya"
BREATH = "https://cdn.test/goya/idle_loop.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    # 모든 정책에서 검증한다 — 액션을 노출하는 정책이 켜져도 생성은 없어야 한다.
    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", "free")
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _mint() -> str:
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id=PET, breathing_url=BREATH, pet_name="고야",
    )
    return token


def _ready(action: str) -> None:
    key = motions_svc._motion_key(OWNER, PET, "any", action)
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=OWNER, pet_id=PET, place_id="any", action_id=action,
        video_url=f"https://cdn.test/{action.lower()}.mp4",
    )


# ── 1. 구조: 생성 모듈로 가는 import 경로가 없다 ─────────────────────────────


#: Shaker 코드가 import 해서는 안 되는 모듈들.
FORBIDDEN_IMPORTS = (
    "premium_generation",
    "generation_queue",
    "credit_generation_service",
    "luma_service",
    "luma_batch_service",
    "wan_service",
    "video_generation",
    "wallet_service",
    "iap_charge_service",
)

#: 호출해서는 안 되는 함수들 (모듈은 써도 되지만 이 이름은 안 된다).
FORBIDDEN_CALLS = (
    "premium_purchase.purchase",
    "premium_purchase.resolve_kind",
)

SHAKER_MODULES = [
    "backend/routers/shaker_v1.py",
    "backend/services/shaker_share.py",
    "backend/services/shaker_policy.py",
    "backend/services/shaker_rate_limit.py",
]


def _referenced_names(module_path: str) -> tuple[set[str], set[str]]:
    """
    (import 된 모듈 이름들, 점 표기 속성 참조들).

    AST 로 보는 이유가 두 가지다. 하나는 독스트링·주석에 적힌 **설명 문구**를 코드로
    오인하지 않기 위해서고 — 이 파일들은 "생성하지 않는다"를 길게 설명한다 —
    다른 하나는 함수 안에서 하는 **지연 import** 까지 잡기 위해서다. 모듈 그래프만
    보면 후자를 놓친다.
    """
    import ast

    tree = ast.parse(open(module_path, encoding="utf-8").read())
    imports: set[str] = set()
    attrs: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imports.add(a.name)
            if node.module:
                imports.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attrs.add(f"{node.value.id}.{node.attr}")

    return imports, attrs


@pytest.mark.parametrize("module_path", SHAKER_MODULES)
def test_shaker_modules_never_import_generation(module_path: str):
    imports, _attrs = _referenced_names(module_path)
    for bad in FORBIDDEN_IMPORTS:
        assert bad not in imports, f"{module_path} 가 {bad} 를 import 한다"


@pytest.mark.parametrize("module_path", SHAKER_MODULES)
def test_shaker_modules_never_call_charging_functions(module_path: str):
    _imports, attrs = _referenced_names(module_path)
    for bad in FORBIDDEN_CALLS:
        assert bad not in attrs, f"{module_path} 가 {bad} 를 호출한다"


def test_shaker_router_touches_only_readonly_asset_lookup():
    """
    프리미엄 자산 조회 진입점은 asset_state 하나뿐이다 (읽기 전용).

    premium_purchase 모듈 자체는 쓴다 — 소유권 검사(assert_pet_owned)와 읽기 전용
    조회가 거기 있기 때문이다. 그래서 "모듈을 안 쓴다"가 아니라 **"어떤 함수를
    쓰는가"** 로 고정한다.
    """
    _imports, attrs = _referenced_names("backend/routers/shaker_v1.py")
    used = {a for a in attrs if a.startswith("premium_purchase.")}
    assert used == {"premium_purchase.asset_state", "premium_purchase.assert_pet_owned",
                    "premium_purchase.PurchaseError"}, used


# ── 2. 런타임: 생성 진입점을 폭탄으로 갈아 끼우고 두드린다 ───────────────────


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    생성/과금으로 가는 진입점을 전부 폭탄으로 만든다.

    하나라도 불리면 그 이름이 fired 에 남고 테스트가 실패한다. 실제 프로바이더
    호출까지 가지 않아도 **의도가 있었다는 것만으로** 잡힌다.
    """
    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"Shaker 가 {name} 을 호출했다 — 생성 금지 위반")

        return _boom

    from backend.services import (
        credit_generation_service,
        generation_queue,
        premium_generation,
        video_generation,
        wallet_service,
    )

    targets = [
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
        (credit_generation_service, "generate_with_credit"),
        (video_generation, "submit_generation"),
        (wallet_service, "deduct_credits"),
    ]
    for mod, attr in targets:
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))
    return fired


def test_public_endpoint_never_generates(client: ASGITestClient, armed: list[str]):
    """READY 자산이 있는 펫 — 정상 경로에서 생성이 없다."""
    token = _mint()
    _ready("COME_CLOSER")

    for _ in range(5):  # 반복 조회(폴링)도 생성을 유발하지 않는다
        assert client.get("/api/v1/shaker/pet", params={"share": token}).status_code == 200
    assert armed == []


def test_missing_assets_do_not_trigger_generation(client: ASGITestClient, armed: list[str]):
    """
    **핵심 회귀**: 자산이 **없을 때**가 위험 구간이다.

    예전 경로(idle-event-dev-trigger / come-closer-autogen)는 "없으면 곧바로 제출"
    이었다. Shaker 는 없으면 그냥 없는 채로 BREATHING 만 준다.
    """
    token = _mint()
    # READY 를 하나도 심지 않는다 — 전부 MISSING 이다.

    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200
    assert follow_shaker_asset(client, r.json()["breathing_url"]) == BREATH
    assert r.json()["actions"] == []
    assert r.json()["double_tap_action_id"] is None
    assert armed == []


def test_invalid_and_revoked_links_never_generate(client: ASGITestClient, armed: list[str]):
    """실패 경로에서도 생성이 없다."""
    sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=BREATH
    )
    _sync(shaker_share.revoke_share, user_id=OWNER, share_id=sid)

    for params in (
        {"share": token},              # 폐기됨
        {"share": "a" * 43},           # 없는 토큰
        {"share": token, "pet_id": "pet_other"},  # petId 바꿔치기
    ):
        client.get("/api/v1/shaker/pet", params=params)
    assert armed == []


def test_share_creation_never_generates(client: ASGITestClient, armed: list[str], monkeypatch):
    """
    공유 링크 발급도 생성이 아니다 — 이미 있는 URL 을 가리킬 뿐이다.
    """
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    r = client.post(
        "/api/v1/shaker/share",
        json={"pet_id": PET, "breathing_url": BREATH, "pet_name": "고야"},
        headers={"Authorization": f"Bearer test:{OWNER}"},
    )
    assert r.status_code == 200, r.text
    assert armed == []


def test_shaker_only_ever_serves_urls_it_already_had(client: ASGITestClient):
    """
    응답의 모든 URL 은 **이미 존재하던** 자산이다.

    BREATHING 은 공유 발급 시 소유자가 넘긴 URL 그대로, 액션은 generated_motions 에
    이미 승격돼 있던 URL 그대로다. Shaker 가 만들어 낸 URL 은 하나도 없다.
    """
    token = _mint()
    _ready("COME_CLOSER")
    _ready("BLINKING")

    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()

    known = {BREATH} | {
        m.video_url for m in motions_svc._MOCK_MOTIONS.values()
    }
    assert follow_shaker_asset(client, body["breathing_url"]) in known
    for a in body["actions"]:
        assert follow_shaker_asset(client, a["url"]) in known
