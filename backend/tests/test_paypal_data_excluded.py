"""
PayPal 데이터는 크레딧·소유권 시스템에 **들어오지 않는다** (분류: legacy/dev-only).

── 왜 테스트로 고정하는가 ────────────────────────────────────────────────────
"옮기지 않기로 했다"는 결정은 문서에만 있으면 잊힌다. 몇 달 뒤 누군가
`purchased_slots` 에 행이 남아 있는 것을 보고 "고객이 산 건데 안 보이네" 하며
이관 코드를 쓰는 것이 자연스러운 반응이다. 그 판단의 근거였던 사실
(라우터가 마운트된 적이 없어 실 결제가 불가능했다)은 그때 눈에 보이지 않는다.

그래서 **경로의 부재**를 테스트로 만든다. 이관 코드를 추가하면 테스트가 깨지고,
깨진 테스트가 docs/PAYPAL_LEGACY.md 를 가리킨다.

근거 요약은 docs/PAYPAL_LEGACY.md 참고.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

#: 이 파일들은 레거시 표를 **다루는 것이 일**이므로 검사에서 뺀다.
#:
#: ⚠️ Phase 11 에서 짧아졌다 — PayPal 라우터·서비스·모델·theme_prices 는 삭제됐다.
#: 목록에 없는 파일이 purchased_slots 를 만지기 시작하면 검사에 걸린다.
_ALLOWED = {
    "backend/services/supabase_assets.py",   # 레거시 조회 헬퍼가 여기 산다
    "backend/routers/assets.py",             # GET /purchased-slots (레거시 조회)
    "backend/scripts/verify_paypal_dev_only.py",
    "backend/scripts/audit_financial_records.py",
}


def _py_files():
    """
    **프로덕션 모듈만** 훑는다.

    테스트는 제외한다: 이 분류를 설명하거나 회귀를 고정하려면 테스트가
    purchased_slots 를 언급하면서 동시에 소유권을 부여하는 것이 정상이기 때문이다
    (예: "크레딧 구매가 Toss 소유권을 덮어쓰지 않는다"를 확인하려면 grant() 로
    Toss 소유권을 먼저 만들어야 한다). 실제로 그 조합이 오탐을 냈다.

    이관 코드가 숨을 수 있는 곳은 실행 경로뿐이고, 그것은 전부 여기에 있다.
    """
    for p in BACKEND.rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts or "tests" in p.parts:
            continue
        yield p, str(p.relative_to(REPO)).replace("\\", "/")


def test_the_paypal_router_does_not_come_back():
    """
    **이 분류의 근거가 되는 사실.**

    purchased_slots 에 쓰는 유일한 함수(record_theme_purchase)는 PayPal 라우터에서만
    호출됐고, 그 라우터는 backend/main.py 에 **한 번도 마운트된 적이 없다**.
    즉 배포된 API 는 그 표에 한 줄도 쓸 수 없었다 — 실 고객 결제가 코드 배치상
    불가능했다.

    Phase 11 에서 라우터 자체가 삭제됐다. 되살아나면 분류의 근거가 사라지므로
    여기서 잡는다: 파일의 부재와 마운트의 부재를 함께 본다.
    """
    assert not (BACKEND / "routers" / "paypal.py").exists(), (
        "paypal 라우터가 돌아왔다 — docs/PAYPAL_LEGACY.md 를 다시 검토할 것."
    )
    main_py = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert not re.search(r"include_router\(\s*paypal", main_py), (
        "paypal 라우터가 마운트됐다. 그러면 '개발 전용' 분류의 근거가 사라진다 — "
        "docs/PAYPAL_LEGACY.md 를 다시 검토할 것."
    )


def test_purchased_slots_has_no_writers_left():
    """
    **표는 남기고 쓰기만 없앤다** (Phase 11).

    지우지 않는 이유: 과거 구매 증거는 새 아키텍처가 생겼다는 이유로 버리는 것이
    아니다. 쓰기를 없애는 이유: "이제 안 쓴다"는 주석은 시간이 지나면 지켜지지
    않는다. 쓰던 코드가 사라진 지금이 막기에 가장 안전한 시점이다.

    조회(get_purchased_themes)는 그대로 살아 있다 — 그것이 증거 보존의 실체다.
    """
    writers = [
        rel for p, rel in _py_files()
        # _ALLOWED 는 레거시를 **설명하는 것이 일**인 파일들이다 (삭제 사유를 적어 둔
        # 주석, 재검증 스크립트). 그 밖의 파일이 이 이름을 언급하면 잡는다.
        if "record_theme_purchase" in p.read_text(encoding="utf-8", errors="ignore")
        and rel not in _ALLOWED
    ]
    assert writers == [], f"purchased_slots 에 쓰는 코드가 생겼다: {writers}"

    from backend.services import supabase_assets

    assert not hasattr(supabase_assets, "record_theme_purchase")
    assert hasattr(supabase_assets, "get_purchased_themes"), (
        "조회까지 사라지면 고객 문의에 답할 근거가 없다"
    )


def test_the_table_is_frozen_at_the_database_level():
    """
    애플리케이션 코드만으로는 부족하다 — 이 서비스는 service-role 키로 접속하므로
    권한으로도 막히지 않는다. 트리거는 접속 주체와 무관하게 걸린다.
    """
    sql = (
        REPO / "supabase" / "migrations"
        / "20261009000000_freeze_legacy_purchase_tables.sql"
    ).read_text(encoding="utf-8")
    assert "create trigger purchased_slots_frozen" in sql
    assert "drop table" not in sql.lower(), "증거를 지우고 있다"


def test_no_module_migrates_purchased_slots_into_entitlements():
    """
    **이관 경로가 존재하지 않는다.**

    purchased_slots 를 읽으면서 동시에 소유권을 만드는 모듈이 있으면 안 된다.
    두 조건을 함께 보는 이유: 각각은 정당할 수 있어도(레거시 조회 / 정상 구매),
    **한 파일에 같이 있으면** 그것이 곧 이관 코드다.
    """
    offenders = []
    for p, rel in _py_files():
        if rel in _ALLOWED:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        reads_legacy = "purchased_slots" in text or "get_purchased_themes" in text
        writes_ownership = "theme_entitlement.grant" in text or "entitlement.grant(" in text
        if reads_legacy and writes_ownership:
            offenders.append(rel)

    assert not offenders, (
        f"PayPal 소유권 이관으로 보이는 모듈이 있다: {offenders}\n"
        "purchased_slots 는 legacy/dev-only 로 분류됐고 이관하지 않기로 했다 — "
        "docs/PAYPAL_LEGACY.md 참고."
    )


def test_no_paypal_provider_is_written_to_entitlements():
    """
    user_theme_entitlements 에 provider='paypal' 행을 만들지 않는다.

    문자열로 검사하는 이유: 이관은 결국 grant(provider="paypal", ...) 로 나타나고,
    그 리터럴이 저장소에 등장하는 순간이 곧 규칙을 깨는 순간이다.
    """
    offenders = []
    for p, rel in _py_files():
        if rel in _ALLOWED:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r'provider\s*=\s*["\']paypal["\']', text):
            offenders.append(rel)

    assert not offenders, (
        f"provider='paypal' 소유권을 만드는 코드가 있다: {offenders}\n"
        "PayPal 구매는 이관 대상이 아니다 — docs/PAYPAL_LEGACY.md 참고."
    )


def test_the_theme_catalog_does_not_read_the_legacy_store():
    """
    카탈로그의 소유 판정은 user_theme_entitlements 하나만 본다.

    레거시 표를 함께 읽기 시작하면 개발용 행이 실 소유권처럼 보이고, 그것은
    이관과 실질적으로 같은 효과다(코드만 없을 뿐).
    """
    src = (BACKEND / "routers" / "theme_store_v1.py").read_text(encoding="utf-8")

    # 문서 문자열과 주석에서 레거시를 **설명하는 것은 허용한다** — 오히려 있어야
    # 다음 사람이 왜 안 읽는지 안다. 금지하는 것은 실행되는 코드다. 그래서
    # 문자열을 잘라 내는 대신 AST 로 docstring 을 걷어 내고 주석을 지운 뒤 본다
    # (예전에는 특정 문구를 replace 로 지웠는데, 문구를 한 글자만 고쳐도 깨졌다).
    tree = ast.parse(src)
    body = tree.body[1:] if ast.get_docstring(tree) else tree.body
    code_lines = src.splitlines()
    start = min((n.lineno for n in body), default=len(code_lines) + 1)
    code = "\n".join(
        line.split("#", 1)[0] for line in code_lines[start - 1 :]
    )

    assert "get_purchased_themes" not in code, (
        "카탈로그가 레거시 PayPal 소유권을 읽는다 — docs/PAYPAL_LEGACY.md 참고."
    )
    assert "purchased_slots" not in code, (
        "카탈로그가 purchased_slots 를 참조한다 — 개발용 행이 실 소유권처럼 보이게 된다."
    )


def test_the_legacy_classification_is_documented():
    """분류 근거 문서가 사라지면 다음 사람이 판단할 자료가 없다."""
    doc = REPO / "docs" / "PAYPAL_LEGACY.md"
    assert doc.is_file(), "docs/PAYPAL_LEGACY.md 가 없다"
    text = doc.read_text(encoding="utf-8")
    assert "LEGACY / DEV-ONLY" in text
    assert "verify_paypal_dev_only" in text, "재검증 방법이 문서에 있어야 한다"
