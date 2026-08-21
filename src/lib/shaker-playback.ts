/**
 * Shaker 재생 배선 — 공개 API 응답을 **기존 런타임**에 연결한다.
 *
 * 재생 자체는 한 줄도 새로 만들지 않는다. BREATHING 루프, 1회 재생, 자동 복귀,
 * 실패 시 복구는 전부 IdleLoopVideo 와 pet-runtime-events 가 이미 하고 있다.
 * 이 모듈이 하는 일은 번역뿐이다:
 *
 *     서버가 허락한 액션  →  런타임이 재생할 수 있는 이벤트 소스
 *
 * ── 두 번 거르는 이유 ────────────────────────────────────────────────────────
 * 서버는 **상업 정책**으로 거른다(shaker_policy). 런타임은 **등록 여부**로 거른다
 * (RUNTIME_EVENTS). 둘은 다른 질문이고 서로를 대신할 수 없다.
 *
 * 지금은 다섯 행동이 모두 등록돼 있어 대개 통과하지만, 이 검사가 지키는 것은
 * **버전 스큐**다: 서버가 새 행동을 먼저 알게 되면(배포 순서상 흔하다) 프론트가
 * 모르는 id 를 지목받는다. 그대로 믿으면 UI 는 "더블탭해 보세요"를 띄우고 런타임은
 * "not-registered" 로 조용히 거절한다 — 탭했는데 아무 일도 없는 화면이 된다.
 *
 * ── 소스를 하나만 붙이는 이유 ────────────────────────────────────────────────
 * 서버가 여러 액션을 허락해도 Shaker 가 마운트하는 것은 **더블탭이 실제로 재생할
 * 하나뿐**이다. IdleLoopVideo 는 소스가 붙은 이벤트마다 <video> 를 만드는데,
 * Shaker 에는 자발적 스케줄러가 없어 나머지는 영원히 재생되지 않는다. 모바일
 * 데이터와 디코더를 쓰면서 아무 일도 하지 않는 요소를 남기지 않는다.
 */

import {
  isRegisteredRuntimeEvent,
  type RuntimeEventId,
  type RuntimeEventSources,
} from "./pet-runtime-events.ts";
import type { ShakerPet } from "./shaker-api.ts";

/**
 * IdleLoopVideo 에 넘길 이벤트 소스 표 — **더블탭이 재생할 하나만** 담는다.
 *
 * 이 표가 비면 더블탭은 구조적으로 아무 일도 하지 않는다. IdleLoopVideo 는 소스가
 * 없는 이벤트에 <video> 를 만들지 않고, trigger 도 "no-source" 로 거절한다 —
 * 화면 쪽에 별도의 방어가 필요 없다.
 */
export function shakerEventSources(pet: ShakerPet | null): RuntimeEventSources {
  const decision = resolveShakerDoubleTap(pet);
  if (!decision.available) return {};
  const url = (pet?.actions ?? []).find((a) => a.id === decision.actionId)?.url;
  if (!url) return {};
  return { [decision.actionId]: url } as RuntimeEventSources;
}

/** 더블탭이 왜 아무 일도 하지 않는가 — 진단·UI 힌트용. */
export type DoubleTapUnavailableReason =
  /** 아직 펫을 못 불러왔다. */
  | "not-loaded"
  /** 서버가 액션을 하나도 허락하지 않았다 (정책 미결이 기본값이다). */
  | "no-permitted-action"
  /** 서버가 지목했지만 런타임에 등록되지 않은 id 다 — 재생 수단이 없다. */
  | "not-registered";

export type ShakerDoubleTap =
  | { available: true; actionId: RuntimeEventId }
  | { available: false; reason: DoubleTapUnavailableReason };

/**
 * 더블탭이 재생할 이벤트.
 *
 * 서버의 doubleTapActionId 를 **존중하되 신뢰하지는 않는다** — 등록 여부를 다시
 * 확인한다. 서버가 지목한 것이 재생 불가면, 남은 소스 중 하나로 대체하지 않고
 * 그대로 불가로 답한다. 정책이 지목한 것과 다른 것을 재생하는 편이 더 나쁘다.
 */
export function resolveShakerDoubleTap(pet: ShakerPet | null): ShakerDoubleTap {
  if (!pet) return { available: false, reason: "not-loaded" };

  const id = pet.doubleTapActionId;
  if (!id) return { available: false, reason: "no-permitted-action" };

  if (!isRegisteredRuntimeEvent(id)) {
    return { available: false, reason: "not-registered" };
  }
  // 지목된 id 에 실제 URL 이 있어야 한다. parseShakerPet 이 이미 맞춰 주지만,
  // 여기서 한 번 더 보는 비용이 0 이고 놓쳤을 때의 증상(먹통 탭)이 나쁘다.
  const hasSource = (pet.actions ?? []).some((a) => a.id === id && !!a.url);
  if (!hasSource) return { available: false, reason: "no-permitted-action" };

  return { available: true, actionId: id as RuntimeEventId };
}

/**
 * 이 펫에게 더블탭 힌트를 보여 줘도 되는가.
 *
 * 힌트를 조건 없이 띄우면 정책이 꺼져 있는 기본 상태에서 "더블탭해 보세요"라고
 * 안내한 뒤 아무 일도 일어나지 않는다. 그건 버그처럼 보인다.
 */
export function shouldShowDoubleTapHint(pet: ShakerPet | null): boolean {
  return resolveShakerDoubleTap(pet).available;
}

/**
 * 화면이 그릴 수 있는 상태 — 로딩/오류를 화면 밖에서 정리한다.
 *
 * BREATHING 은 **언제나 재생 가능한 상태로 취급한다.** 액션이 하나도 없어도,
 * 정책이 꺼져 있어도, 구독이 만료됐어도 마찬가지다 — 무료이기 때문이다.
 */
export interface ShakerViewModel {
  petName: string | null;
  breathingUrl: string;
  posterUrl: string | null;
  eventSources: RuntimeEventSources;
  doubleTap: ShakerDoubleTap;
}

export function buildShakerViewModel(pet: ShakerPet): ShakerViewModel {
  return {
    petName: pet.petName,
    breathingUrl: pet.breathingUrl,
    posterUrl: pet.posterUrl,
    eventSources: shakerEventSources(pet),
    doubleTap: resolveShakerDoubleTap(pet),
  };
}
