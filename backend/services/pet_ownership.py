"""
"이 펫이 이 사용자의 것인가" — **한 곳에서만** 답한다.

── 왜 별도 모듈인가 ────────────────────────────────────────────────────────
두 출처를 합쳐야 답이 나온다:

    pet_registry            public.pets 의 인증된 소유자 (정답이 있을 때)
    premium_purchase        TOFU — 다른 사용자 아래에 같은 pet_id 의 자산·작업·
                            구매가 이미 있는가 (레지스트리에 없을 때)

그런데 pet_registry 는 **구조적으로 독립**이어야 한다. 결제·생성·구독 모듈을
import 하지 않는다는 규칙이 테스트로 고정돼 있고(test_registry_module_is_independent),
그 규칙에는 이유가 있다 — 레지스트리는 "무료 BREATHING 만 있는 펫"까지 담는
가장 아래 계층이라, 위쪽 모듈을 끌어들이면 순환과 결합이 생긴다.

그래서 합성은 여기서 한다. 레지스트리는 순수한 채로 두고, 이 모듈이 둘을 엮는다.

── 왜 호출부마다 따로 구현하지 않는가 ──────────────────────────────────────
같은 방어를 두 번 다르게 구현하면 한쪽이 반드시 약해진다(Phase 11·12 에서
이미 겪었다). 주문 체크아웃과 편지-펫 연결은 **같은 질문**을 하므로 같은 답을
써야 한다.
"""

from __future__ import annotations

import logging

from . import pet_registry

logger = logging.getLogger(__name__)


class PetOwnershipError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 403):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def assert_owned(user_id: str, pet_id: str) -> None:
    """
    이 펫이 이 사용자의 것이 아니면 **거절한다.**

    레지스트리에 있으면 그것이 정답이다 — public.pets.user_id 는 인증된 신원이다.
    아직 등록되지 않았다면(예전 신원으로 올라간 펫) TOFU 검사로 넘긴다.

    ⚠️ 미등록을 **통과로 해석하지 않는다.** 그러면 레지스트리에 없는 pet_id 를
    적어 넣는 것만으로 검사를 건너뛸 수 있고, 검사가 있으나 마나가 된다.

    왜 주문 경로에 필요한가: 주문은 pet_id 로 생산 패키지를 만들고, 그 패키지가
    그 펫의 Shaker 공유로 QR 을 찍는다. 검사가 없으면 **남의 펫 QR 이 인쇄된**
    실물이 내 주소로 배송된다 — 종이라서 되돌릴 수 없다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetOwnershipError("PET_REQUIRED", "user_id 와 pet_id 가 필요합니다.", status=400)

    try:
        owner = await pet_registry.owner_of(pid)
    except pet_registry.PetRegistryError as e:
        # 조회 실패를 "소유자 없음"으로 해석하지 않는다 — 그러면 레지스트리가
        # 잠깐 흔들리는 동안 소유권 검사가 통째로 열린다.
        raise PetOwnershipError(
            getattr(e, "code", "PET_REGISTRY_UNAVAILABLE"),
            getattr(e, "message", "펫 정보를 확인하지 못했습니다."),
            status=getattr(e, "status", 503),
        ) from e

    if owner is not None:
        if owner != uid:
            # 남의 펫이라는 사실 자체를 알려 주지 않는다.
            raise PetOwnershipError(
                "PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403
            )
        return

    # 미등록 — TOFU 로 넘긴다.
    from . import premium_purchase

    try:
        await premium_purchase.assert_pet_owned(uid, pid)
    except premium_purchase.PurchaseError as e:
        raise PetOwnershipError(
            getattr(e, "code", "PET_NOT_OWNED"),
            getattr(e, "message", "이 펫에 접근할 권한이 없습니다."),
            status=getattr(e, "status", 403),
        ) from e
