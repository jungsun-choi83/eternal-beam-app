/**
 * "Unlock More Features" — 순수 상태/가격 모델.
 *
 * UI 는 하나의 잠금 해제로 보이지만 뒤에는 **독립된 두 구매**가 있다:
 *   IDLE_BUNDLE        1 크레딧 — 등록된 자발적 아이들 이벤트 전체
 *   ACTION:COME_CLOSER 1 크레딧 — 더블탭 액션
 *
 * 이 파일에 네트워크·React 가 없다. 가격 계산과 상태 판정은 눈으로 확인하기
 * 어려운 규칙(부분 소유, 생성 중, 잔액 부족)이라 순수 함수로 떼어 놓고 테스트한다.
 *
 * ⚠️ 여기서 계산한 금액은 **표시용 예상치**다. 실제 과금은 서버가 정한다
 * (구매 원장의 멱등성 때문에 이미 구매한 사용자는 0원이 될 수 있다). 구매 응답의
 * creditsCharged 가 언제나 최종 진실이다.
 */

import type { PremiumAssets } from "./premium-assets.ts";
import { KIND_IDLE_BUNDLE, actionKind } from "./premium-assets.ts";

/** 더블탭 액션. 서버 레지스트리(PET_ACTIONS)의 첫 항목과 같아야 한다. */
export const COME_CLOSER_ACTION = "COME_CLOSER";

/** 한 쪽(bundle 또는 action)의 소유 상태. */
export type SideState =
  /** 대상이 전부 승격됨 — 재생 가능, 추가 과금 없음 */
  | "ready"
  /** 만들 것은 없고 일부가 생성 중 — 과금 없음, 기다리면 된다 */
  | "generating"
  /** 아직 만들지 않은 대상이 있다 — 구매가 필요하다 */
  | "missing";

export interface UnlockSide {
  kind: string;
  state: SideState;
  /** 이 쪽에 필요한 크레딧 (ready/generating 이면 0) */
  credits: number;
  /** 승격된 대상 수 / 전체 대상 수 — 부분 소유 표시용 */
  readyCount: number;
  totalCount: number;
}

export type UnlockPhase =
  /** 인증 토큰이 없다 — 구매할 수 없다 */
  | "signed-out"
  /** 두 쪽 모두 준비됨 */
  | "unlocked"
  /** 살 것은 없고 생성이 도는 중 */
  | "unlocking"
  /** 구매 가능 */
  | "purchasable"
  /** 구매가 필요한데 잔액이 모자란다 */
  | "insufficient-credits";

export interface UnlockState {
  phase: UnlockPhase;
  idle: UnlockSide;
  comeCloser: UnlockSide;
  /** 지금 결제해야 하는 총액 (0 = 살 것 없음) */
  requiredCredits: number;
  /** 실제로 POST 해야 하는 kind 목록 (missing 인 쪽만) */
  missingKinds: string[];
  balance: number | null;
}

/**
 * **행동 한 건**의 상태. 판정 규칙의 단일 출처다.
 *
 * Behavior Library(행동별 카드)와 아래 sideFrom(집합 판정)이 같은 함수를 쓴다 —
 * 두 곳이 각자 판정하면 카드에는 READY 인데 집합은 missing 이라 재생성을 시도하는
 * 식으로 어긋난다. 순서도 서버(premium_purchase.asset_state)와 같다:
 * canonical 자산이 있으면 ready, 진행 중이면 generating, 그 외 missing.
 */
export function behaviorState(
  actionId: string,
  assets: PremiumAssets | null
): SideState {
  if (!assets) return "missing";
  if (assets.ready[actionId]) return "ready";
  if (assets.generating.includes(actionId)) return "generating";
  return "missing";
}

function sideFrom(kind: string, targets: string[], assets: PremiumAssets): UnlockSide {
  const states = targets.map((a) => behaviorState(a, assets));
  const ready = targets.filter((_, i) => states[i] === "ready");
  const generating = targets.filter((_, i) => states[i] === "generating");
  const missing = targets.filter((_, i) => states[i] === "missing");

  // 판정 순서가 백엔드(premium_purchase.purchase)의 가드와 **정확히 같아야** 한다:
  // 만들 것이 없으면(missing 이 비면) 서버는 과금하지 않는다. 그래서 "생성 중"은
  // 무료 상태이지 구매 대상이 아니다.
  let state: SideState;
  if (missing.length > 0) state = "missing";
  else if (generating.length > 0) state = "generating";
  else state = "ready";

  return {
    kind,
    state,
    credits: state === "missing" ? 1 : 0,
    readyCount: ready.length,
    totalCount: targets.length,
  };
}

/**
 * 자산 상태 + 잔액 → 화면에 보여줄 잠금 해제 상태.
 *
 * 대상 목록은 **서버가 준 레지스트리**(assets.idleEvents)에서 온다 — 프론트가
 * 개수를 하드코딩하지 않으므로, 5번째 아이들 모션이 추가돼도 자동으로 같은
 * 1 크레딧 번들에 들어온다.
 */
export function deriveUnlockState(input: {
  assets: PremiumAssets | null;
  balance: number | null;
  hasAuth: boolean;
}): UnlockState {
  const { assets, balance, hasAuth } = input;

  const empty: UnlockSide = {
    kind: KIND_IDLE_BUNDLE,
    state: "missing",
    credits: 1,
    readyCount: 0,
    totalCount: 0,
  };

  if (!assets) {
    return {
      phase: hasAuth ? "purchasable" : "signed-out",
      idle: empty,
      comeCloser: { ...empty, kind: actionKind(COME_CLOSER_ACTION) },
      requiredCredits: 2,
      missingKinds: [],
      balance,
    };
  }

  const idle = sideFrom(KIND_IDLE_BUNDLE, assets.idleEvents, assets);
  const comeCloser = sideFrom(
    actionKind(COME_CLOSER_ACTION),
    assets.actionEvents.includes(COME_CLOSER_ACTION) ? [COME_CLOSER_ACTION] : [],
    assets
  );

  const requiredCredits = idle.credits + comeCloser.credits;
  const missingKinds = [idle, comeCloser]
    .filter((s) => s.state === "missing")
    .map((s) => s.kind);

  let phase: UnlockPhase;
  if (!hasAuth) {
    phase = "signed-out";
  } else if (requiredCredits === 0) {
    // 살 것이 없다 — 전부 준비됐거나 생성이 도는 중이다.
    phase =
      idle.state === "generating" || comeCloser.state === "generating"
        ? "unlocking"
        : "unlocked";
  } else if (balance != null && balance < requiredCredits) {
    phase = "insufficient-credits";
  } else {
    phase = "purchasable";
  }

  return { phase, idle, comeCloser, requiredCredits, missingKinds, balance };
}

/** 이미 재생 가능한 아이들 이벤트 id 들 — 스케줄러 후보와 같은 판정. */
export function readyIdleEventIds(assets: PremiumAssets | null): string[] {
  if (!assets) return [];
  return assets.idleEvents.filter((id) => Boolean(assets.ready[id]));
}

/** 더블탭이 실제로 동작하는가 = COME_CLOSER 자산이 승격됐는가. */
export function isComeCloserReady(assets: PremiumAssets | null): boolean {
  return Boolean(assets?.ready[COME_CLOSER_ACTION]);
}
