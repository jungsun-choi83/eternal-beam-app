/**
 * 펫 런타임 이벤트 도메인 — **액션**과 **아이들 이벤트**를 명시적으로 구분한다.
 *
 * ── 도메인 모델 ──────────────────────────────────────────────────────────────
 *   PET RUNTIME
 *   ├─ IDLE SYSTEM
 *   │   ├─ BREATHING            ← 홈/기본 상태. 이벤트가 **아니다**.
 *   │   └─ IdleEvent            ← BLINKING / EAR_TWITCHING / HEAD_TILTING /
 *   │                              TAIL_WAGGING (현재는 타입만, 미등록)
 *   └─ ACTION SYSTEM
 *       └─ PetAction            ← COME_CLOSER
 *
 * 왜 나누는가: 둘은 재생 인프라(같은 캔버스·같은 상태 기계)를 공유하지만 **성격이
 * 다르다**. 아이들 이벤트는 아무도 시키지 않아도 스스로 일어나는 미세한 생명감이고,
 * 액션은 사용자가 유발하는 사건이다. 앞으로 들어올 자발적 스케줄러는 "아이들
 * 이벤트만" 고를 수 있어야 하는데, 전부를 하나의 PremiumPetAction 으로 두면
 * 스케줄러가 매번 COME_CLOSER 를 손으로 제외해야 한다 — 빠뜨리는 순간 펫이 혼자
 * 다가온다. 그래서 분류를 데이터에 박아 둔다.
 *
 * ── "선언됨" vs "등록됨" ─────────────────────────────────────────────────────
 * 두 개념이 다르다. 이 구분이 이 파일의 핵심이다.
 *   선언(declared)  — 도메인 타입에 존재한다. BLINKING 등 4종이 여기 있다.
 *   등록(registered) — RUNTIME_EVENTS 에 정의가 있어 **실제로 재생 가능**하다.
 *                      지금은 COME_CLOSER 뿐이다.
 * 선언만 된 id 는 트리거해도 "not-registered" 로 거절된다.
 *
 * 백엔드 대응: backend/scenarios/pet_scenarios.py 의 `PREMIUM_ACTIONS` 튜플과
 * 같은 의미다(ACTION_ORDER 밖 = 4코인/NFC/device sync 계약과 무관).
 * **여기에 추가한다고 백엔드가 따라오지 않는다** — 생성·저장 경로는 별도다.
 *
 * 순수 모듈이다(DOM/React 없음) — `npm test` 가 그대로 로드한다.
 */

import {
  HOLD_AND_DISSOLVE,
  IMMEDIATE_RETURN,
  SEAM_ALIGNED,
  type ReturnProfile,
} from "./action-return-transition.ts";

/**
 * 아이들 홈 상태. 런타임 이벤트가 **아니다** — 이벤트가 없을 때 늘 돌아가는
 * 바탕이다. 재생 방식도 다르다(loop). 여기에 상수로 둔 이유는 "BREATHING 은
 * 액션이 아니다"를 코드로 못 박기 위해서다.
 */
export const IDLE_HOME_STATE = "BREATHING" as const;
export type IdleHomeState = typeof IDLE_HOME_STATE;

/** 사용자가 유발하는 사건. 늘어나면 여기에 유니온 멤버를 추가한다. */
export type PetAction = "COME_CLOSER";

/**
 * 스스로 일어나는 미세한 생명감. **현재는 타입 선언만이고 등록돼 있지 않다** —
 * 자산도 생성 경로도 아직 없다.
 */
export type IdleEvent = "BLINKING" | "EAR_TWITCHING" | "HEAD_TILTING" | "TAIL_WAGGING";

/** 재생 가능한(또는 앞으로 가능해질) 모든 것. BREATHING 은 포함되지 않는다. */
export type RuntimeEventId = PetAction | IdleEvent;

export type RuntimeEventKind = "ACTION" | "IDLE_EVENT";

/** 분류 결과 — id 와 종류를 같이 들고 다닌다. */
export type RuntimeEvent =
  | { kind: "ACTION"; id: PetAction }
  | { kind: "IDLE_EVENT"; id: IdleEvent };

/**
 * 플레이어 런타임 상태.
 *   IDLE → [EVENT_PENDING_SEAM] → EVENT_PLAYING → EVENT_RETURNING → IDLE
 *
 * EVENT_PENDING_SEAM 은 entryPolicy="wait-for-seam" 인 이벤트에서만 지나간다.
 * 이 구간에서 화면은 여전히 BREATHING 이다 — 아직 아무것도 시작되지 않았다.
 */
export type PlayerPhase =
  | "IDLE"
  | "EVENT_PENDING_SEAM"
  | "EVENT_PLAYING"
  | "EVENT_RETURNING";

export type EntryPolicy = "immediate" | "wait-for-seam";
export type ReturnPolicy = "hold-and-dissolve" | "seam-aligned" | "immediate";

export interface RuntimeEventDef {
  id: RuntimeEventId;
  /** 액션인가 아이들 이벤트인가. 스케줄러·정책이 이 값만 보고 고른다. */
  kind: RuntimeEventKind;
  /** 클수록 우선. 낮은 우선순위는 재생 중인 이벤트를 밀어내지 못한다. */
  priority: number;
  /** false 면 일단 시작된 뒤에는 무엇도 끼어들 수 없다. */
  interruptible: boolean;
  /** 재생이 끝나면 BREATHING 으로 돌아가는가. */
  returnToIdle: boolean;
  /**
   * 언제 시작할 것인가.
   *   immediate      — 트리거 즉시. 사용자가 유발한 사건은 기다리면 안 된다.
   *   wait-for-seam  — BREATHING 이 휴지 자세(루프 이음매)에 올 때까지 기다린다.
   *                    아이들 이벤트는 **아무도 기다리고 있지 않으므로** 이 지연을
   *                    감당할 수 있다. 이것이 두 범주를 가르는 진짜 기준이다.
   */
  entryPolicy: EntryPolicy;
  /**
   * 어떻게 BREATHING 으로 돌아갈 것인가.
   *   hold-and-dissolve — 도착 프레임 유지 + 긴 디졸브 + 배율 브리지 (프레이밍 불연속용)
   *   seam-aligned      — 휴지 자세끼리 잇는다. 배율 브리지 없음, 아주 짧은 교차만
   *   immediate         — 전환 없음
   */
  returnPolicy: ReturnPolicy;
  /**
   * 결과물이 배경과 무관한가 (검정 플레이트 위 펫만 생성).
   * 백엔드 is_theme_independent_action() 과 같은 의미 — 저장 키에서 place 가 빠진다.
   */
  themeIndependent: boolean;
  /**
   * 이 이벤트 <video> 의 preload 정책.
   *
   * COME_CLOSER 만 "auto" 다 — 더블탭 즉시 재생돼야 하고, 지금까지도 그렇게
   * 동작해 왔다. **새로 등록하는 것은 기본이 "none"** 이어야 한다. 이벤트가
   * 늘어날 때마다 디코더와 대역폭이 같이 늘어나기 때문이다.
   */
  preload: "none" | "metadata" | "auto";
  /**
   * 자발적 스케줄러가 이 이벤트를 고를 상대 가중치. 클수록 자주 나온다.
   *
   * 액션에는 의미가 없다 — 스케줄러는 registeredIdleEvents() 만 훑으므로
   * kind="ACTION" 은 애초에 후보에 들어오지 않는다. 생략하면 1로 본다.
   */
  spontaneousWeight?: number;
}

/**
 * 모든 아이들 이벤트가 공유하는 우선순위.
 *
 * 액션(COME_CLOSER=100)보다 한참 낮아야 한다 — 미세한 생명감이 사용자 조작을
 * 밀어내면 안 된다. 서로 같은 값인 것도 의도다: 아이들 이벤트끼리는 선점하지 않고
 * 먼저 시작한 쪽이 끝날 때까지 간다(decideTrigger 의 lower-priority 규칙).
 */
export const IDLE_EVENT_PRIORITY = 10;

/** 선언된 액션 전체. */
export const PET_ACTION_IDS: readonly PetAction[] = ["COME_CLOSER"];

/**
 * 선언된 아이들 이벤트 전체 — **도메인 목록이지 활성 목록이 아니다.**
 * 지금은 하나도 등록돼 있지 않으므로 registeredIdleEvents() 는 빈 배열이다.
 */
export const IDLE_EVENT_IDS: readonly IdleEvent[] = [
  "BLINKING",
  "EAR_TWITCHING",
  "HEAD_TILTING",
  "TAIL_WAGGING",
];

/**
 * **등록된** 런타임 이벤트 정의표 — 실제로 재생 가능한 것만.
 *
 * Partial 인 것이 의도다: 선언은 됐지만 아직 켜지지 않은 id 가 존재한다는 사실을
 * 타입으로 드러낸다. 아이들 이벤트를 켜려면 여기에 항목을 하나 추가하면 된다.
 */
export const RUNTIME_EVENTS: Readonly<Partial<Record<RuntimeEventId, RuntimeEventDef>>> = {
  COME_CLOSER: {
    id: "COME_CLOSER",
    kind: "ACTION",
    // 액션은 아이들 이벤트보다 항상 위다. 아이들 이벤트는 이 값보다 한참 낮게
    // 등록해야 한다(권장 10~50) — 미세한 생명감이 사용자 조작을 밀어내면 안 된다.
    priority: 100,
    interruptible: false,
    returnToIdle: true,
    entryPolicy: "immediate",
    returnPolicy: "hold-and-dissolve",
    themeIndependent: true,
    preload: "auto",
  },

  // Phase 1A — 첫 아이들 이벤트. 현재는 **개발용 수동 트리거 전용**이고 자발적
  // 스케줄링은 없다.
  //
  // COME_CLOSER 와 정반대의 전환 정책을 쓴다. COME_CLOSER 는 프레이밍이
  // 불연속이라(클로즈업 ↔ 전신) 긴 디졸브가 필요하지만, 눈 깜빡임은 같은 포즈·같은
  // 프레이밍이라 같은 처리를 하면 오히려 이중상이 생긴다. 대신 양쪽 휴지 자세를
  // 잇는다 — 포즈가 맞으면 하드 컷이 어떤 디졸브보다 깨끗하다.
  BLINKING: {
    id: "BLINKING",
    kind: "IDLE_EVENT",
    priority: IDLE_EVENT_PRIORITY,
    // 사용자가 유발한 액션이 언제든 밀어낼 수 있어야 한다.
    interruptible: true,
    returnToIdle: true,
    entryPolicy: "wait-for-seam",
    returnPolicy: "seam-aligned",
    themeIndependent: true,
    // 아이들 이벤트는 프리로드하지 않는다 — 늘어날수록 디코더·대역폭이 같이 는다.
    preload: "none",
    // 눈 깜빡임이 가장 흔한 생명 신호다 — 귀 움찔보다 자주 나와야 자연스럽다.
    spontaneousWeight: 3,
  },

  // Phase 2 — 두 번째 아이들 이벤트. BLINKING 과 **완전히 같은 정책**을 쓴다.
  // 값을 손으로 베끼지 않고 상수를 공유하는 이유: 아이들 이벤트가 늘어날 때
  // 하나만 정책이 어긋나면(예: entryPolicy 를 immediate 로) 그 이벤트만 이음매를
  // 무시하고 튀는데, 눈으로는 원인을 찾기 어렵다.
  EAR_TWITCHING: {
    id: "EAR_TWITCHING",
    kind: "IDLE_EVENT",
    priority: IDLE_EVENT_PRIORITY,
    interruptible: true,
    returnToIdle: true,
    entryPolicy: "wait-for-seam",
    returnPolicy: "seam-aligned",
    themeIndependent: true,
    preload: "none",
    spontaneousWeight: 1,
  },

  // Phase 4 — 남은 두 아이들 이벤트. 앞의 둘과 정책이 완전히 같다.
  // 넷이 모두 등록되면서 스케줄러의 강제 교대(둘뿐일 때는 blink→ear→blink…)가
  // 풀린다 — 연속 반복 회피가 이제 실제로 "다양성"으로 작동한다.
  HEAD_TILTING: {
    id: "HEAD_TILTING",
    kind: "IDLE_EVENT",
    priority: IDLE_EVENT_PRIORITY,
    interruptible: true,
    returnToIdle: true,
    entryPolicy: "wait-for-seam",
    returnPolicy: "seam-aligned",
    themeIndependent: true,
    preload: "none",
    spontaneousWeight: 1,
  },

  TAIL_WAGGING: {
    id: "TAIL_WAGGING",
    kind: "IDLE_EVENT",
    priority: IDLE_EVENT_PRIORITY,
    interruptible: true,
    returnToIdle: true,
    entryPolicy: "wait-for-seam",
    returnPolicy: "seam-aligned",
    themeIndependent: true,
    preload: "none",
    spontaneousWeight: 1,
  },
};

// ── 이음매(seam) 정책 — 진짜 호흡 위상 메타데이터가 없을 때의 폴백 ───────────
//
// **지금 쓰는 이음매는 "측정된 호흡 위상"이 아니라 "BREATH 클립의 루프 경계"다.**
// 그래도 근거가 있다: IDLE_COMMON_CONSTRAINT(luma_prompts.py)가 BREATH 클립에
// "동일한 휴지 자세로 시작하고 끝나며, 미세 움직임은 클립이 끝나기 전에 한 주기를
// 완료해 시작 상태로 돌아온다"를 요구한다. 즉 **t=0 이 곧 휴지 자세**다.
//
// 한계도 분명하다: 클립 안에 호흡이 여러 번 들어 있으면 중간 휴지점들이 있는데,
// 측정 없이는 그것들을 찾을 수 없어 루프 경계까지 기다리게 된다. 그래서 상한을 둔다.
// 진짜 위상 정렬은 클립당 호흡 주기/오프셋 실측이 들어와야 가능하다.

/** 루프 경계로 인정하는 오차(초). 60fps 기준 몇 프레임. */
export const SEAM_EPSILON_S = 0.08;

/**
 * 이음매를 기다리는 최대 시간. 초과하면 그냥 시작한다(품질 저하를 감수).
 *
 * 상한이 필요한 이유: 클립이 길거나 BREATH 가 아직 재생되지 않으면(자동재생 정책)
 * 이음매가 영영 오지 않을 수 있다. 이벤트가 조용히 사라지는 것보다 이음매를
 * 포기하고 재생하는 편이 낫다.
 */
export const SEAM_WAIT_MAX_MS = 3000;

/** 이벤트별 소스 URL 표. 값이 없으면 그 이벤트는 재생 불가. */
export type RuntimeEventSources = Partial<Record<RuntimeEventId, string | null>>;

/** 부모가 이벤트를 트리거할 때 쓰는 핸들. */
export type PetRuntimeTrigger = (id: RuntimeEventId) => void;

// ── 분류 ─────────────────────────────────────────────────────────────────────

export function isPetAction(value: string): value is PetAction {
  return (PET_ACTION_IDS as readonly string[]).includes(value);
}

export function isIdleEvent(value: string): value is IdleEvent {
  return (IDLE_EVENT_IDS as readonly string[]).includes(value);
}

/** 도메인에 선언돼 있는가 (등록 여부와 무관). BREATHING 은 false. */
export function isDeclaredRuntimeEvent(value: string): value is RuntimeEventId {
  return isPetAction(value) || isIdleEvent(value);
}

/** 실제로 재생 가능한가 (정의가 등록돼 있는가). */
export function isRegisteredRuntimeEvent(value: string): value is RuntimeEventId {
  return isDeclaredRuntimeEvent(value) && RUNTIME_EVENTS[value] !== undefined;
}

/**
 * id → 분류. **선언만 된 id 도 분류된다** — BLINKING 은 아직 못 틀지만
 * 도메인상 분명히 IDLE_EVENT 다.
 */
export function classifyRuntimeEvent(value: string): RuntimeEvent | null {
  if (isPetAction(value)) return { kind: "ACTION", id: value };
  if (isIdleEvent(value)) return { kind: "IDLE_EVENT", id: value };
  return null;
}

/** 등록된 정의. 미등록/미선언이면 null. */
export function getRuntimeEvent(value: string): RuntimeEventDef | null {
  return isDeclaredRuntimeEvent(value) ? RUNTIME_EVENTS[value] ?? null : null;
}

// ── 열거 ─────────────────────────────────────────────────────────────────────

function registeredDefs(): RuntimeEventDef[] {
  return (Object.keys(RUNTIME_EVENTS) as RuntimeEventId[])
    .map((id) => RUNTIME_EVENTS[id])
    .filter((d): d is RuntimeEventDef => d !== undefined);
}

/** 등록된 액션만. */
export function registeredActions(): RuntimeEventDef[] {
  return registeredDefs().filter((d) => d.kind === "ACTION");
}

/**
 * 등록된 아이들 이벤트만 — 앞으로 들어올 자발적 스케줄러의 진입점.
 *
 * COME_CLOSER 를 **손으로 제외할 필요가 없다**. kind 로 거르므로, 액션이 몇 개가
 * 되든 스케줄러 코드는 그대로다. 지금은 등록된 아이들 이벤트가 없어 빈 배열이다.
 */
export function registeredIdleEvents(): RuntimeEventDef[] {
  return registeredDefs().filter((d) => d.kind === "IDLE_EVENT");
}

// ── 트리거 정책 ──────────────────────────────────────────────────────────────

export type TriggerRejection =
  | "unknown-event"
  | "not-registered"
  | "no-source"
  | "busy-non-interruptible"
  | "lower-priority"
  | "returning";

export type TriggerDecision =
  | { accepted: true; event: RuntimeEventDef }
  | { accepted: false; reason: TriggerRejection };

export interface TriggerContext {
  phase: PlayerPhase;
  /** 현재 재생/복귀 중인 이벤트. IDLE 이면 null. */
  currentEventId: RuntimeEventId | null;
  requestedEventId: string;
  /** 요청된 이벤트의 소스 URL 이 실제로 붙어 있는가. */
  hasSource: boolean;
}

/**
 * 트리거 요청 판정 — 플레이어의 유일한 진입 규칙.
 *
 * 순수 함수로 빼 둔 이유: "재생 중 재트리거는 무시"처럼 눈으로 확인하기 어려운
 * 규칙이 플레이어 내부 상태에 흩어져 있으면, 이벤트가 늘어날 때 조용히 깨진다.
 * 정책은 **정의 데이터만** 읽는다 — id 별 특수 분기가 없다.
 *
 * 현재 정책(관측 동작은 Phase 0 과 같다):
 *   미선언 id           → unknown-event   (오타·레거시 IDLE/TOUCH/BREATHING)
 *   선언됐지만 미등록   → not-registered  (BLINKING 등 — 아직 못 튼다)
 *   IDLE                          → 수락
 *   EVENT_RETURNING               → 거절. 복귀 전환은 끝까지 간다.
 *   EVENT_PLAYING / PENDING_SEAM  → 진행 중인 것이 interruptible 이 아니면 거절,
 *                                   맞다면 **더 높은** 우선순위만 밀어낼 수 있다.
 *                                   (아직 시작도 안 한 PENDING_SEAM 도 같은 규칙을
 *                                    쓴다 — 낮은 우선순위가 대기 중인 것을 가로채
 *                                    새치기하는 일이 없어야 한다.)
 */
export function decideTrigger(ctx: TriggerContext): TriggerDecision {
  if (!isDeclaredRuntimeEvent(ctx.requestedEventId)) {
    return { accepted: false, reason: "unknown-event" };
  }
  const requested = getRuntimeEvent(ctx.requestedEventId);
  if (!requested) return { accepted: false, reason: "not-registered" };
  if (!ctx.hasSource) return { accepted: false, reason: "no-source" };

  if (ctx.phase === "IDLE") return { accepted: true, event: requested };
  if (ctx.phase === "EVENT_RETURNING") return { accepted: false, reason: "returning" };

  const current = ctx.currentEventId ? RUNTIME_EVENTS[ctx.currentEventId] : undefined;
  // 재생 중인데 무엇이 재생 중인지 모르면 보수적으로 거절한다.
  if (!current) return { accepted: false, reason: "busy-non-interruptible" };
  if (!current.interruptible) return { accepted: false, reason: "busy-non-interruptible" };
  if (requested.priority <= current.priority) {
    return { accepted: false, reason: "lower-priority" };
  }
  return { accepted: true, event: requested };
}

/**
 * returnPolicy → 실제 타이밍 프로파일.
 *
 * 정책 이름과 숫자를 분리해 둔 이유: 타이밍 튜닝(150ms 를 200ms 로)은 자주 일어나고,
 * 정책 선택(seam-aligned 인가 hold-and-dissolve 인가)은 거의 안 바뀐다.
 */
export function returnProfileFor(policy: ReturnPolicy): ReturnProfile {
  switch (policy) {
    case "hold-and-dissolve":
      return HOLD_AND_DISSOLVE;
    case "seam-aligned":
      return SEAM_ALIGNED;
    case "immediate":
      return IMMEDIATE_RETURN;
  }
}

/** 소스가 실제로 붙은 **등록된** 이벤트만. 렌더할 <video> 를 고르는 데 쓴다. */
export function mountableEvents(sources: RuntimeEventSources): RuntimeEventDef[] {
  return registeredDefs().filter((def) => {
    const src = sources[def.id];
    return typeof src === "string" && src.trim().length > 0;
  });
}
