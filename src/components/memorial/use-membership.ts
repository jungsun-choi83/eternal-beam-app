"use client";

import {
  deriveMembershipState,
  type MembershipState,
  type SubscriptionStatus,
} from "@/lib/membership";
import { usePremiumAssetsContext } from "@/components/memorial/premium-assets-context";

export interface Membership {
  state: MembershipState;
  loading: boolean;
  refresh: () => Promise<void>;
}

/**
 * 멤버십 상태 — **읽기 전용**.
 *
 * 신원은 토큰이 정하고, entitled 는 서버가 정한다. 이 훅은 아무것도 구매하지
 * 않고 아무것도 생성하지 않는다 — GET 하나뿐이다. 가입/해지는 결제 화면
 * (또는 목업 패널)이 담당한다.
 *
 * 상태를 프리미엄 자산 엔드포인트에서 가져오는 이유: 멤버십 여부와 "이미 만들어진
 * 모션 수"를 **한 번의 왕복**으로 같은 신원 기준으로 읽을 수 있다. 구독 상태만
 * 따로 조회하면 두 응답이 서로 다른 시점을 볼 수 있다.
 */
export function useMembership(): Membership {
  // 조회·폴링은 **공유 컨텍스트 한 곳**이 담당한다 (Phase 4 의 중복 폴링 제거).
  const { assets, hasAuth, loading, refresh } = usePremiumAssetsContext();

  const state = deriveMembershipState({
    status: (assets?.subscriptionStatus ?? null) as SubscriptionStatus,
    entitled: Boolean(assets?.entitled),
    hasAuth,
    readyCount: assets ? Object.keys(assets.ready).length : 0,
  });

  return { state, loading, refresh };
}
