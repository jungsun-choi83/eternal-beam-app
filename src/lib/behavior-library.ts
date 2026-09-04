/**
 * Behavior Library — 프리미엄 행동 목록의 **순수 모델**.
 *
 * 멤버가 행동을 하나씩 골라 생성한다. 여기에 네트워크도 React 도 없다.
 *
 * 판정은 새로 만들지 않는다 — premium-unlock.ts 의 behaviorState() 를 그대로
 * 쓴다. 목록도 하드코딩하지 않는다: 그룹은 **서버 레지스트리**(assets.idleEvents /
 * assets.actionEvents)에서 온다. 5번째 자발적 모션이 서버에 추가되면 이 화면에
 * 자동으로 나타나고, 코드를 고칠 필요가 없다.
 *
 * ⚠️ 재생에 관여하지 않는다. READY 는 "재생 가능"이 아니라 "생성이 끝났다"는
 * 뜻이고, 실제 재생은 예전 그대로 스케줄러와 canonical 자산이 정한다.
 * ON/OFF 선호는 아직 없다 — 다음 단계다.
 */

import type { PremiumAssets } from "./premium-assets.ts";
import { behaviorState } from "./premium-unlock.ts";
import type { SideState } from "./premium-unlock.ts";

/** 카드 한 장이 보여 주는 단 하나의 상태. */
export type BehaviorStatus = SideState; // "missing" | "generating" | "ready"

/** 자발적(스케줄러가 알아서) / 상호작용(더블탭) */
export type BehaviorGroupId = "spontaneous" | "interactive";

export interface BehaviorItem {
  /** 서버 액션 id — 그대로 ACTION:<id> 로 요청한다 */
  id: string;
  status: BehaviorStatus;
  /** READY 일 때의 재생 URL (표시·디버그용) */
  url: string | null;
  /**
   * ON/OFF 선호. **status 와 완전히 독립이다** — MISSING 인 행동에도 값이 있다.
   *
   * ⚠️ 아직 재생에 연결되지 않았다. enabled=false 라고 재생이 멈추지 않는다.
   */
  enabled: boolean;
}

export interface BehaviorGroup {
  id: BehaviorGroupId;
  items: BehaviorItem[];
}

export interface BehaviorLibraryState {
  groups: BehaviorGroup[];
  /** 생성 컨트롤을 노출해도 되는가 (= 활성 멤버) */
  canGenerate: boolean;
  /** 하나라도 생성 중인가 — 폴링 여부를 정한다 */
  anyGenerating: boolean;
  /** 아직 만들지 않은 행동 id — 목록이 비면 만들 것이 없다 */
  missingIds: string[];
  readyCount: number;
  totalCount: number;
}

/**
 * 이 행동에 ON/OFF 를 **보여 줘도** 되는가.
 *
 * READY 인 것만이다. GENERATING/MISSING 에 토글을 두면 아직 존재하지 않는 것을
 * 켜고 끄는 것처럼 보인다 — 저장은 되지만(서버는 별개 상태로 받는다) 사용자에게
 * 거짓말이 된다. 그래서 표시 규칙은 여기서 좁힌다.
 *
 * 멤버십은 조건이 아니다: 라이브러리 자체가 멤버에게만 그려지고, 선호는 결제
 * 대상이 아니라 사용자 데이터다.
 */
export function canToggleBehavior(item: BehaviorItem): boolean {
  return item.status === "ready";
}

/** 저장된 값이 없으면 켬 — 서버(DEFAULT_ENABLED)와 같은 기본값이다. */
export function preferenceOf(id: string, assets: PremiumAssets | null): boolean {
  const v = assets?.preferences?.[id];
  return v === undefined ? true : Boolean(v);
}

function itemsFrom(ids: string[], assets: PremiumAssets | null): BehaviorItem[] {
  return ids.map((id) => ({
    id,
    status: behaviorState(id, assets),
    url: assets?.ready[id] ?? null,
    enabled: preferenceOf(id, assets),
  }));
}

/**
 * 자산 상태 + 멤버십 → 화면에 그릴 행동 목록.
 *
 * entitled 는 **서버가 정한 값**을 그대로 받는다. 프론트가 구독 상태로 다시
 * 계산하지 않는다 — 서버 게이트(premium_purchase)와 어긋나면 눌러도 402 가 나는
 * 버튼을 보여 주게 된다.
 */
export function deriveBehaviorLibrary(input: {
  assets: PremiumAssets | null;
  entitled: boolean;
}): BehaviorLibraryState {
  const { assets, entitled } = input;

  const groups: BehaviorGroup[] = [
    { id: "spontaneous", items: itemsFrom(assets?.idleEvents ?? [], assets) },
    { id: "interactive", items: itemsFrom(assets?.actionEvents ?? [], assets) },
  ];

  const all = groups.flatMap((g) => g.items);
  return {
    groups,
    canGenerate: entitled,
    anyGenerating: all.some((i) => i.status === "generating"),
    missingIds: all.filter((i) => i.status === "missing").map((i) => i.id),
    readyCount: all.filter((i) => i.status === "ready").length,
    totalCount: all.length,
  };
}

/**
 * 이 행동에 [Generate] 를 눌러도 되는가.
 *
 * READY 를 다시 만들지 않는다는 규칙이 **여기 한 곳**에 있다. 서버도 같은 판정을
 * 독립적으로 하지만(asset_state 가드), UI 가 먼저 막아야 사용자가 눌렀는데
 * 아무 일도 없는 상황이 생기지 않는다.
 */
export function canGenerateBehavior(
  item: BehaviorItem,
  state: BehaviorLibraryState
): boolean {
  return state.canGenerate && item.status === "missing";
}


// ── 런타임 적격성 (Phase 6) ──────────────────────────────────────────────────

/**
 * 이 행동을 **지금 재생해도 되는가** — 최종 규칙.
 *
 *     구독 entitled  ∩  자산 READY  ∩  선호 ON
 *
 * 세 조건은 서로 다른 곳에서 오고 각자 독립적으로 변한다(구독 웹훅 / 생성 승격 /
 * 사용자 토글). 그래서 판정을 **한 함수에** 모은다 — 자발적 스케줄러와 더블탭
 * COME_CLOSER 가 같은 규칙을 쓰지 않으면, 만료된 사용자가 더블탭으로만 프리미엄을
 * 계속 쓰는 식의 구멍이 생긴다.
 *
 * ⚠️ BREATHING 은 이 판정의 **대상이 아니다**. PREMIUM_ACTIONS 밖이라 여기 물어볼
 * 일이 없고, 물어보면 항상 false 가 나온다(ready 에 없으므로). 호출부는 프리미엄
 * 행동에 대해서만 부른다 — BREATHING 은 언제나 무조건 재생된다.
 *
 * ⚠️ 재생 **타이밍**은 정하지 않는다. 언제/얼마나 자주는 예전 그대로 스케줄러가
 * 정한다. 이 함수는 후보 자격만 답한다.
 */
export function isBehaviorEligible(
  actionId: string,
  assets: PremiumAssets | null
): boolean {
  if (!assets) return false;
  // 구독 게이트는 **게이트가 켜져 있을 때만** 적용된다 (Phase 7I.2).
  //
  // 크레딧 모드(subscription_required=false)에서 entitled 는 참고값이고 구독
  // 이력이 없는 모두에게 false 다 — 그것으로 재생을 막으면 크레딧을 내고 만든
  // 자산이 영영 재생되지 않는다. 소유는 영구이고, 크레딧 모드의 재생 자격은
  // READY(=소유) 그 자체다. 구독 모드의 "만료 → 즉시 제외"는 그대로 유지된다.
  if (assets.subscriptionRequired && !assets.entitled) return false;
  if (behaviorState(actionId, assets) !== "ready") return false; // MISSING/GENERATING 제외
  return preferenceOf(actionId, assets);                 // OFF 제외
}

/**
 * 후보 목록에서 적격인 것만 남긴다.
 *
 * 스케줄러의 availableIds 를 이 결과로 좁히는 것이 자발적 행동 통합의 전부다 —
 * 스케줄러 내부(타이머·랜덤·쿨다운·연속 반복 회피)는 한 줄도 건드리지 않는다.
 */
export function eligibleBehaviorIds<T extends string>(
  candidateIds: readonly T[],
  assets: PremiumAssets | null
): T[] {
  return candidateIds.filter((id) => isBehaviorEligible(id, assets));
}

/**
 * 적격인 것만 남긴 소스 표.
 *
 * availableIds 를 좁히는 것만으로는 부족하다: 수동 트리거(개발 콘솔)나 남아 있는
 * 참조가 이벤트를 직접 발화시킬 수 있는데, 소스가 있으면 재생돼 버린다. 소스를
 * 함께 비우면 런타임이 자기 규칙(no-source 거절)으로 막는다 — 런타임을 고치지
 * 않고도 "OFF 는 절대 재생되지 않는다"가 성립한다.
 */
export function eligibleSources<T extends string>(
  sources: Partial<Record<T, string | null | undefined>>,
  assets: PremiumAssets | null
): Partial<Record<T, string>> {
  const out: Partial<Record<T, string>> = {};
  for (const [id, url] of Object.entries(sources) as [T, string | null | undefined][]) {
    if (url && isBehaviorEligible(id, assets)) out[id] = url;
  }
  return out;
}
