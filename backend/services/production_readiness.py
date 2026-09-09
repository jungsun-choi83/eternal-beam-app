"""
프로덕션 설정 감사 — **부팅 시 크게 말하고, 요청 시 닫는다**.

두 층으로 나눈 이유:

  요청 시 fail-closed 는 **이미 구현돼 있다** (여기서 다시 구현하지 않는다):
      SUPABASE_JWT_SECRET 없음        → 401/503  (auth.verify_bearer_token)
      SUBSCRIPTION_WEBHOOK_SECRET 없음 → 503      (subscription_auth)
      구독 조회 실패                   → 503      (premium_entitlement)
      소유권 조회 실패                 → 503      (premium_purchase)

  하지만 그 실패는 **사용자가 눌렀을 때** 드러난다. 배포 직후 조용히 서 있다가
  첫 결제자에게서 터지는 것이 최악이다. 그래서 부팅 시 한 번 점검해 로그에 남긴다.

**부팅을 막지는 않는다.** 프로세스를 죽이면 헬스체크가 실패해 롤백 루프에 빠지고,
설정이 하나 빠졌다는 이유로 이미 동작하던 무료 기능(BREATHING·업로드)까지 멈춘다.
대신 /health 가 상태를 보고하므로 배포 파이프라인이 확인할 수 있다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _on(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def _set(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


@dataclass
class ReadinessReport:
    #: 실 결제/실 사용자를 받을 수 있는 상태인가
    production_ready: bool
    #: 프로덕션에서 반드시 고쳐야 하는 것
    blockers: list[str] = field(default_factory=list)
    #: 켜져 있으면 위험하지만 스테이징에서는 정상인 것
    warnings: list[str] = field(default_factory=list)
    #: 확인된 안전장치
    ok: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "production_ready": self.production_ready,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "ok": self.ok,
        }


def audit() -> ReadinessReport:
    """
    지금 설정으로 실 사용자를 받아도 되는가.

    판정 기준은 하나다: **돈이나 신원이 걸린 경로가 열려 있는가.**
    """
    r = ReadinessReport(production_ready=True)

    # ── 인증 ────────────────────────────────────────────────────────────────
    # 현재 Supabase 액세스 토큰은 **ES256(비대칭)** 이라 JWKS 공개키로 검증한다.
    # 그래서 필요한 것은 SUPABASE_JWT_SECRET 이 아니라 **프로젝트 URL** 이다 —
    # 그것이 있어야 JWKS 주소와 기대 issuer 를 만들 수 있다.
    #
    # 예전에는 여기서 SUPABASE_JWT_SECRET 을 필수로 봤는데, 그 전제가 틀렸다:
    # 시크릿이 있어도 ES256 토큰은 검증되지 않았고(알고리즘 불일치), 없어도
    # ES256 경로는 정상 동작한다.
    if not _set("SUPABASE_URL") and not _set("VITE_SUPABASE_URL"):
        r.blockers.append(
            "SUPABASE_URL 미설정 — JWKS 조회와 issuer 검증이 불가해 인증이 성립하지 않는다."
        )
    else:
        r.ok.append("SUPABASE_URL 설정됨 (ES256 토큰을 JWKS 로 검증)")

    # 레거시 HS256 토큰이 아직 돌아다닌다면 필요하다. 없다고 해서 막지는 않는다 —
    # 없으면 HS256 토큰만 503 이고, 현재 발급되는 ES256 토큰은 영향받지 않는다.
    if not _set("SUPABASE_JWT_SECRET"):
        r.warnings.append(
            "SUPABASE_JWT_SECRET 미설정 — 레거시 HS256 토큰은 거절된다 "
            "(현재 발급되는 ES256 토큰에는 영향 없음)."
        )

    if _on("ALLOW_INSECURE_TEST_AUTH"):
        r.blockers.append(
            "ALLOW_INSECURE_TEST_AUTH=1 — 'Bearer test:<user_id>' 로 아무나 사칭할 수 있다."
        )

    # ── 구독 웹훅 ───────────────────────────────────────────────────────────
    sub_mock = _on("SUBSCRIPTION_MOCK")
    if sub_mock:
        r.warnings.append(
            "SUBSCRIPTION_MOCK=1 — 로그인한 사용자가 자기 구독을 임의로 활성화할 수 있다. "
            "프로덕션에서는 꺼야 한다."
        )
    if not _set("SUBSCRIPTION_WEBHOOK_SECRET"):
        # 미설정이어도 실 웹훅은 503 으로 닫힌다 — 열리는 게 아니라 **동작하지 않는다**.
        r.blockers.append(
            "SUBSCRIPTION_WEBHOOK_SECRET 미설정 — Apple/Google 웹훅이 503 으로 거절되어 "
            "구독 상태가 영원히 갱신되지 않는다."
        )
    else:
        r.ok.append("SUBSCRIPTION_WEBHOOK_SECRET 설정됨 (공유 시크릿)")

    # ── 결제 ────────────────────────────────────────────────────────────────
    if _on("PAYMENT_MOCK"):
        r.warnings.append(
            "PAYMENT_MOCK=1 — 영수증 검증 없이 크레딧이 충전된다 (레거시 4코인 경로)."
        )

    # ── 프리미엄 인가 ───────────────────────────────────────────────────────
    if not _on("PREMIUM_REQUIRES_SUBSCRIPTION", "1"):
        r.warnings.append(
            "PREMIUM_REQUIRES_SUBSCRIPTION=0 — 프리미엄 생성이 구독이 아니라 크레딧으로 "
            "인가된다 (롤백 모드)."
        )
    else:
        r.ok.append("PREMIUM_REQUIRES_SUBSCRIPTION=1 (구독이 생성 인가)")

    # ── 생성 비용 ───────────────────────────────────────────────────────────
    if _on("GENERATION_MOCK"):
        r.warnings.append(
            "GENERATION_MOCK=1 — 어떤 프로바이더로도 실제 생성이 나가지 않는다 "
            "(비용 0, 실 영상도 0)."
        )
        r.ok.append("실 프로바이더 호출 차단됨")
    else:
        r.warnings.append(
            "GENERATION_MOCK=0 — 실제 프로바이더 호출이 나간다. 비용이 발생한다."
        )

    # ── 웹 정기결제 (Toss) ──────────────────────────────────────────────────
    if _on("TOSS_MOCK"):
        r.warnings.append("TOSS_MOCK=1 — 실제 결제가 일어나지 않는다 (결제 흐름 목업).")
    elif _set("TOSS_SECRET_KEY") and _set("TOSS_CLIENT_KEY"):
        ck = (os.getenv("TOSS_CLIENT_KEY") or "").strip()
        sk = (os.getenv("TOSS_SECRET_KEY") or "").strip()
        if ck.startswith("test_") or sk.startswith("test_"):
            r.warnings.append("Toss 테스트 키 사용 중 — 실 매출이 발생하지 않는다.")
        else:
            r.ok.append("Toss 라이브 키 설정됨")
        if ck.startswith("live_") != sk.startswith("live_"):
            r.blockers.append(
                "Toss 키 짝이 맞지 않는다 (클라이언트/시크릿 중 하나만 라이브)."
            )
    else:
        r.warnings.append("Toss 키 미설정 — 멤버십 결제를 시작할 수 없다.")

    if not _set("BILLING_CRON_SECRET"):
        r.warnings.append(
            "BILLING_CRON_SECRET 미설정 — 갱신 배치가 503 이라 정기결제가 갱신되지 않는다."
        )
    else:
        r.ok.append("BILLING_CRON_SECRET 설정됨")

    # ── QR Shaker (공개 경로) ───────────────────────────────────────────────
    # 공개 엔드포인트라 설정 실수의 영향이 인증 경로보다 넓다 — 링크를 받은
    # 사람이 아니라 QR 을 본 모든 사람이 대상이 된다.
    from .shaker_policy import POLICY_DISABLED, POLICY_MEMBERSHIP, current_policy

    shaker_policy_value = current_policy()
    if shaker_policy_value == POLICY_MEMBERSHIP:
        r.ok.append(
            "SHAKER_DOUBLE_TAP_POLICY=membership (PM 확정) — 구독 ∩ READY ∩ 선호 ON 일 때만 "
            "더블탭 액션이 노출된다."
        )
    elif shaker_policy_value == POLICY_DISABLED:
        r.warnings.append(
            "SHAKER_DOUBLE_TAP_POLICY=disabled — 더블탭이 완전히 꺼져 있다 (되돌리기 모드)."
        )
    else:
        r.warnings.append(
            f"SHAKER_DOUBLE_TAP_POLICY={shaker_policy_value} — 구독 없이도 프리미엄 액션이 "
            "로그인하지 않은 방문자에게 노출된다. PM 확정값(membership)이 아니다."
        )

    if not _on("SHAKER_RATE_LIMIT_ENABLED", "1"):
        r.warnings.append(
            "SHAKER_RATE_LIMIT_ENABLED=0 — 공개 Shaker 엔드포인트에 남용 방어가 없다."
        )
    else:
        r.ok.append("Shaker 공개 엔드포인트 레이트 리밋 켜짐")

    if not _on("SHAKER_PROXY_ASSET_URLS", "1"):
        # 스토리지 객체 경로가 `{user_id}/…` 이고 user_id 는 이메일이다.
        r.warnings.append(
            "SHAKER_PROXY_ASSET_URLS=0 — 공개 응답에 스토리지 서명 URL 이 그대로 실린다. "
            "객체 경로에 고객 이메일이 포함되어 노출된다."
        )
    else:
        r.ok.append("Shaker 재생 URL 프록시 켜짐 (고객 이메일 비노출)")

    # ── 판매자/운영 QR 도구 ─────────────────────────────────────────────────
    from .shaker_ops import ops_user_ids

    if not ops_user_ids():
        # 열리는 게 아니라 닫힌다 — 차단 항목은 아니지만 QR 을 만들 수 없다.
        r.warnings.append(
            "SHAKER_OPS_USER_IDS 미설정 — 운영 QR 콘솔(/ops/shaker)을 아무도 쓸 수 없다. "
            "물리 제품용 QR 을 만들 수 없다."
        )
    else:
        r.ok.append(f"Shaker 운영자 {len(ops_user_ids())}명 등록됨")

    if not _set("PUBLIC_WEB_BASE_URL"):
        r.warnings.append(
            "PUBLIC_WEB_BASE_URL 미설정 — 운영 QR 이 API 도메인을 가리킬 수 있다. "
            "인쇄된 QR 은 회수할 수 없으므로 인쇄 전에 반드시 확인할 것."
        )
    else:
        r.ok.append("PUBLIC_WEB_BASE_URL 설정됨 (QR 이 웹앱을 가리킨다)")

    # ── 유료 테마 스토어 (Phase 11) ─────────────────────────────────────────
    # 테마 소유권은 구독과 별개 축이라 여기서도 따로 본다.
    from .theme_catalog import paid_theme_keys, price_krw

    _priced = [k for k in paid_theme_keys() if price_krw(k) is not None]
    _unpriced = [k for k in paid_theme_keys() if price_krw(k) is None]
    if _unpriced:
        # 열리는 게 아니라 닫힌다 — 가격 없는 테마는 팔리지 않는다.
        r.warnings.append(
            f"테마 가격 미설정: {', '.join(sorted(_unpriced))} — 판매되지 않고 "
            "'준비 중'으로 표시된다 (THEME_PRICE_<KEY>_KRW)."
        )
    if _priced:
        r.ok.append(f"유료 테마 {len(_priced)}종 가격 설정됨")

    # ── 물리 제품 주문 (Phase 12) ───────────────────────────────────────────
    # 실물은 되돌릴 수 없다 — 잘못된 가격이나 운영 부재는 배송된 뒤에 드러난다.
    from .physical_product import catalog as _product_catalog

    _bad_price = [p.product_type for p in _product_catalog() if p.price_krw <= 0]
    if _bad_price:
        r.blockers.append(
            f"물리 제품 가격이 0 이하: {', '.join(_bad_price)} — 무료로 배송된다."
        )
    else:
        r.ok.append(
            "물리 제품 가격 설정됨 ("
            + ", ".join(f"{p.product_type} {p.price_krw:,}원" for p in _product_catalog())
            + ")"
        )

    # ── 인쇄 생산 (Phase 13) ────────────────────────────────────────────────
    # 인쇄는 되돌릴 수 없다 — 폰트 문제는 배송된 뒤에 드러난다.
    from .print_render import font_is_embedded

    if not font_is_embedded():
        r.warnings.append(
            "PRINT_LETTER_FONT_PATH 미설정 — 편지 PDF 가 내장 CID 폰트를 쓰며 "
            "**폰트가 임베드되지 않는다**. 인쇄소 RIP 에 한글 CJK 리소스가 없으면 "
            "글자가 깨진다. 실제 인쇄 전 라이선스된 한글 TTF 를 지정할 것."
        )
    else:
        r.ok.append("인쇄용 한글 폰트 임베드됨 (PRINT_LETTER_FONT_PATH)")

    # ── 데이터 계층 ─────────────────────────────────────────────────────────
    if not _on("HYBRID_USE_SUPABASE", "1"):
        r.blockers.append(
            "HYBRID_USE_SUPABASE=0 — 구독·지갑·선호가 프로세스 메모리에만 남는다 "
            "(재시작하면 사라진다)."
        )
    elif not (_set("SUPABASE_URL") and _set("SUPABASE_SERVICE_ROLE_KEY")):
        r.blockers.append("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정.")
    else:
        r.ok.append("Supabase 설정됨")

    r.production_ready = not r.blockers
    return r


def log_audit() -> ReadinessReport:
    """부팅 시 한 번. 막지 않고 **크게 말한다**."""
    r = audit()
    for b in r.blockers:
        logger.error("PRODUCTION BLOCKER — %s", b)
    for w in r.warnings:
        logger.warning("PRODUCTION WARNING — %s", w)
    if r.production_ready:
        logger.info("프로덕션 설정 감사 통과 (%d개 항목 확인)", len(r.ok))
    else:
        logger.error(
            "프로덕션 설정 감사 실패 — 차단 항목 %d개. 실 사용자를 받기 전에 해결할 것.",
            len(r.blockers),
        )
    return r
