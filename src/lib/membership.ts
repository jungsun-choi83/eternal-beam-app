/**
 * Monthly Membership — 순수 상태 모델.
 *
 * 크레딧 모델을 대체한다. 예전 UnlockFeaturesCard 는 "N 크레딧으로 잠금 해제"
 * 였고, 잔액·가격·부족 상태를 사용자에게 계산해 보여 줬다. 이제 소비자에게
 * 보이는 개념은 하나다: **멤버십이 있는가 없는가.**
 *
 * 여기에 네트워크도 React 도 없다. 판정 규칙(해지 유예, 만료 후 자산 보존)은
 * 눈으로 확인하기 어려워서 순수 함수로 떼어 놓고 테스트한다.
 *
 * ⚠️ **재생 권한을 정하지 않는다.** 이미 만들어진 READY 자산은 멤버십이 끝나도
 * 계속 재생된다(서버의 generated_motions 가 권위). 멤버십이 정하는 것은
 * **새 프리미엄 모션을 만들 수 있는가**뿐이다. BREATHING 은 무료라 언제나 돈다.
 */

/** 서버가 돌려주는 구독 상태 그대로. null = 구독 이력 없음. */
export type SubscriptionStatus = "active" | "canceled" | "expired" | null;

export type MembershipPhase =
  /** 로그인 전 — 멤버십은 계정에 묶인다 */
  | "signed-out"
  /** 한 번도 가입한 적 없다 */
  | "none"
  /** 이용 중 */
  | "active"
  /** 해지했지만 결제 기간이 남아 아직 이용 가능 */
  | "grace"
  /** 만료 — 새 생성은 막히고, 이미 만든 것은 남는다 */
  | "lapsed";

export interface MembershipState {
  phase: MembershipPhase;
  /** 새 프리미엄 모션을 만들 수 있는가 (서버 entitled 와 같은 값) */
  canGenerate: boolean;
  /** 가입 유도 CTA 를 보여야 하는가 */
  showJoinCta: boolean;
  status: SubscriptionStatus;
  /** 이미 만들어져 만료 후에도 남아 있는 프리미엄 모션 수 */
  readyCount: number;
}

export interface MembershipInput {
  status: SubscriptionStatus;
  /** 서버 권위 — 유예 규칙까지 반영된 값 */
  entitled: boolean;
  hasAuth: boolean;
  readyCount?: number;
}

/**
 * 구독 상태 → 화면에 보여 줄 멤버십 상태.
 *
 * entitled 는 **서버가 정한다**. 프론트가 status 만 보고 유예 기간을 다시 계산하면
 * 서버와 어긋난다(시계·타임존·해지 시점). status 는 문구를 고르는 데만 쓴다.
 */
export function deriveMembershipState(input: MembershipInput): MembershipState {
  const { status, entitled, hasAuth } = input;
  const readyCount = input.readyCount ?? 0;

  if (!hasAuth) {
    return {
      phase: "signed-out",
      canGenerate: false,
      showJoinCta: false,
      status: null,
      readyCount,
    };
  }

  let phase: MembershipPhase;
  if (entitled) {
    // 유예 중인지 아닌지는 status 가 알려 준다 — 둘 다 이용 가능이지만
    // "곧 끝난다"는 사실은 사용자에게 알려야 한다.
    phase = status === "canceled" ? "grace" : "active";
  } else if (status === "expired" || status === "canceled") {
    phase = "lapsed";
  } else {
    phase = "none";
  }

  return {
    phase,
    canGenerate: entitled,
    // 이용 중이면 권유하지 않는다. 만료·미가입일 때만 가입 경로를 연다.
    showJoinCta: !entitled,
    status,
    readyCount,
  };
}

/** 만료돼도 남아 있는 자산이 있는가 — "사라지지 않았다"고 안심시키는 문구용. */
export function keepsExistingAssets(state: MembershipState): boolean {
  return state.phase === "lapsed" && state.readyCount > 0;
}
