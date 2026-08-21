/**
 * 소유자 QR 카드의 **순수 상태 모델**.
 *
 * 여기에 React 도 네트워크도 없다. 카드가 언제 보이고, 무엇을 눌러도 되고,
 * 어떤 링크를 보여 줄지를 전부 순수 함수로 정해 node --test 로 덮는다.
 * behavior-library.ts 가 Behavior Library 에 대해 하는 일과 같은 역할이다.
 *
 * ⚠️ 펫 데이터를 만들지 않는다. 이 모듈이 다루는 pet_id 는 **이미 존재하는**
 * content_id 에서 파생된 값이고(pet-identity.ts), 공유는 그것을 가리키기만 한다.
 */

import type { ShareSummary } from "./shaker-share.ts";

/** 카드가 그릴 수 있는 상태. */
export type SharePanelPhase =
  /** 아직 BREATHING 이 없다 — 공유할 것이 없다. */
  | "no-asset"
  /** 로그인이 필요하다 (발급은 인증 경로다). */
  | "signed-out"
  | "loading"
  /** 활성 링크가 없다 — [QR 만들기] 를 보여 준다. */
  | "empty"
  /** 활성 링크가 있다 — 링크와 [해제] 를 보여 준다. */
  | "active"
  | "error";

export interface SharePanelInput {
  /** 실제 BREATHING 자산이 있는가. 없으면 공유할 대상이 없다. */
  hasBreathingAsset: boolean;
  /** 유효한 액세스 토큰이 있는가. */
  hasAuth: boolean;
  loading: boolean;
  shares: ShareSummary[] | null;
  error: string | null;
  /** 이번 세션에서 방금 발급한 링크 (원문 토큰은 이때만 존재한다). */
  justCreatedUrl: string | null;
}

export interface SharePanelState {
  phase: SharePanelPhase;
  /** 지금 화면에 보여 줄 링크. 목록에는 토큰이 없으므로 방금 만든 것만 보일 수 있다. */
  shareUrl: string | null;
  /** 활성 링크 개수 — "QR 3개 사용 중" 같은 표시에 쓴다. */
  activeCount: number;
  /** 폐기 대상이 될 링크 id 들. */
  activeShareIds: string[];
  canCreate: boolean;
  canRevoke: boolean;
  /** 링크가 있는데 원문을 모른다 — 다시 볼 수 없다는 것을 설명해야 한다. */
  hasUnviewableLink: boolean;
}

/**
 * 카드 상태 판정. **한 곳에 모아 두는 것이 요점이다.**
 *
 * 조건이 다섯 개(자산·인증·로딩·목록·오류)라 컴포넌트 안에서 즉석으로 조합하면
 * 반드시 어긋난다 — 특히 "링크는 있는데 원문을 모른다"는 상태는 잊기 쉽고,
 * 잊으면 사용자에게 빈 칸을 보여 주게 된다.
 */
export function deriveSharePanel(input: SharePanelInput): SharePanelState {
  const active = (input.shares ?? []).filter((s) => s.active);
  const base = {
    shareUrl: input.justCreatedUrl,
    activeCount: active.length,
    activeShareIds: active.map((s) => s.shareId),
    canCreate: false,
    canRevoke: false,
    hasUnviewableLink: false,
  };

  // 자산이 먼저다. BREATHING 이 없으면 로그인 여부와 무관하게 공유할 것이 없다.
  if (!input.hasBreathingAsset) return { ...base, phase: "no-asset" };
  if (!input.hasAuth) return { ...base, phase: "signed-out" };
  if (input.loading) return { ...base, phase: "loading" };
  if (input.error) return { ...base, phase: "error" };

  if (active.length === 0) {
    return { ...base, phase: "empty", canCreate: true };
  }

  return {
    ...base,
    phase: "active",
    canCreate: true, // 편지용·박스용을 따로 만들 수 있어야 한다
    canRevoke: true,
    // 서버가 원문 토큰을 저장하지 않으므로, 방금 만든 것이 아니면 링크를 보여 줄 수 없다.
    hasUnviewableLink: !input.justCreatedUrl,
  };
}

/**
 * 공유에 쓸 포스터 후보.
 *
 * 누끼 이미지를 우선한다 — 배경이 없어 어떤 화면에서도 어색하지 않다.
 * data: URL 은 제외한다: 서버가 원격 URL 만 받고(공유 링크에 담기지 않는다),
 * 넘기면 400 으로 거절당한다.
 */
export function pickSharePoster(
  candidates: Array<string | null | undefined>
): string | null {
  for (const c of candidates) {
    const v = (c || "").trim();
    if (!v) continue;
    if (!v.startsWith("http://") && !v.startsWith("https://")) continue;
    return v;
  }
  return null;
}

/**
 * 클립보드 복사. 실패하면 false — 호출부가 "직접 복사하세요"로 폴백한다.
 *
 * navigator.clipboard 는 보안 컨텍스트(https)에서만 있고, iOS 에서는 사용자
 * 제스처 밖이면 거부된다. 둘 다 흔한 상황이라 실패를 정상 경로로 다룬다.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  const value = (text || "").trim();
  if (!value) return false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* 권한 거부 / 비보안 컨텍스트 — 아래 폴백으로 */
  }
  return false;
}
