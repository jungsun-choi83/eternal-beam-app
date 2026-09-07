"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  PremiumApiError,
  actionKind,
  purchasePremium,
  setBehaviorPreference,
} from "@/lib/premium-assets";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  canGenerateBehavior,
  canToggleBehavior,
  deriveBehaviorLibrary,
  type BehaviorItem,
  type BehaviorLibraryState,
} from "@/lib/behavior-library";
import { usePremiumAssetsContext } from "@/components/memorial/premium-assets-context";

export type LibraryError =
  | { code: "SUBSCRIPTION_REQUIRED" }
  | { code: "UNAUTHENTICATED" }
  | { code: "PET_NOT_OWNED" }
  | { code: "UNAVAILABLE"; message: string };

export interface BehaviorLibrary {
  state: BehaviorLibraryState;
  loading: boolean;
  /** 지금 제출 중인 행동 id (버튼 하나만 비활성화하기 위해) */
  submitting: string | null;
  /** 지금 ON/OFF 저장 중인 행동 id */
  toggling: string | null;
  error: LibraryError | null;
  /** 행동 **한 건** 생성. 사용자 클릭에서만 부른다. */
  generate: (actionId: string) => Promise<void>;
  /** 행동 **한 건**의 ON/OFF. 생성을 일으키지 않는다. */
  toggle: (actionId: string, enabled: boolean) => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * Behavior Library — 행동별 조회 + **단건** 생성.
 *
 * use-premium-unlock 의 검증된 규율을 그대로 따른다:
 *   * 발견은 자동(GET), 생성은 수동(POST). effect 는 절대 POST 하지 않는다.
 *   * 생성 중일 때만 폴링한다.
 *   * inflight ref 로 같은 틱의 두 번째 클릭을 막는다(state 는 늦다).
 *
 * 다른 점 하나: 번들이 아니라 **행동 하나씩** 요청한다(ACTION:<ID>). 그래서
 * BLINKING 을 눌러도 TAIL_WAGGING 이 따라 생성되지 않는다 — 프로바이더 비용을
 * 사용자가 고른 것에만 쓴다.
 */
export function useBehaviorLibrary(params: {
  petId: string | null;
  /** false 면 아무 네트워크도 타지 않는다. */
  enabled: boolean;
}): BehaviorLibrary {
  const { petId, enabled } = params;

  // 조회·폴링은 **공유 컨텍스트 한 곳**이 담당한다 (Phase 4 의 중복 폴링 제거).
  const { assets, refresh, loading, applyPreferences } = usePremiumAssetsContext();

  const [submitting, setSubmitting] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [error, setError] = useState<LibraryError | null>(null);

  const inflightRef = useRef(false);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // entitled 는 **서버 응답에서** 온다. 프론트가 구독 상태로 다시 계산하거나
  // 부모가 내려 주면, 서버 게이트(premium_purchase)와 어긋나는 순간 눌러도 402 만
  // 나는 버튼이 생긴다. 같은 응답에서 읽으면 그럴 수 없다.
  const entitled = Boolean(assets?.entitled);
  const state = deriveBehaviorLibrary({ assets, entitled });

  /**
   * 행동 한 건 생성. **사용자 클릭에서만 호출한다.**
   *
   * READY/GENERATING 이면 호출조차 하지 않는다 — 서버도 같은 판정을 하지만
   * 왕복을 낭비할 이유가 없고, 무엇보다 재생성이 일어나지 않음을 여기서 보장한다.
   */
  const generate = useCallback(
    async (actionId: string) => {
      if (inflightRef.current || !petId) return;

      const current = deriveBehaviorLibrary({ assets, entitled: Boolean(assets?.entitled) });
      const item: BehaviorItem | undefined = current.groups
        .flatMap((g) => g.items)
        .find((i) => i.id === actionId);
      if (!item || !canGenerateBehavior(item, current)) return;

      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setError({ code: "UNAUTHENTICATED" });
        return;
      }

      inflightRef.current = true;
      setSubmitting(actionId);
      setError(null);
      try {
        // 서버 계약은 kind + pet_id 뿐이다 (Phase 7H) — 이미지는 서버가
        // pet_id 의 Phase 1 intake 에서 읽는다.
        await purchasePremium({
          kind: actionKind(actionId),
          petId,
          accessToken: auth.token,
        });
      } catch (e) {
        if (!cancelledRef.current) {
          if (e instanceof PremiumApiError && e.code === "SUBSCRIPTION_REQUIRED") {
            setError({ code: "SUBSCRIPTION_REQUIRED" });
          } else if (e instanceof PremiumApiError && e.code === "UNAUTHENTICATED") {
            setError({ code: "UNAUTHENTICATED" });
          } else if (e instanceof PremiumApiError && e.code === "PET_NOT_OWNED") {
            setError({ code: "PET_NOT_OWNED" });
          } else {
            const msg = e instanceof Error ? e.message : String(e);
            setError({ code: "UNAVAILABLE", message: msg });
          }
        }
      } finally {
        inflightRef.current = false;
        if (!cancelledRef.current) setSubmitting(null);
      }
      await refresh();
    },
    [assets, petId, refresh]
  );

  /**
   * ON/OFF 저장. **생성 경로와 완전히 분리돼 있다** — purchasePremium 을 부르지도,
   * 자산을 다시 조회하지도 않는다(선호는 READY/GENERATING/MISSING 를 바꾸지 않는다).
   *
   * READY 인 행동만 토글할 수 있다. 서버는 별개 상태로 받아 주지만, 아직 만들지
   * 않은 것을 켜고 끄는 화면은 사용자에게 거짓말이다.
   */
  const toggle = useCallback(
    async (actionId: string, enabled: boolean) => {
      if (!petId) return;

      const current = deriveBehaviorLibrary({
        assets,
        entitled: Boolean(assets?.entitled),
      });
      const item = current.groups.flatMap((g) => g.items).find((i) => i.id === actionId);
      if (!item || !canToggleBehavior(item)) return;
      if (item.enabled === enabled) return; // 값이 같으면 왕복하지 않는다

      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setError({ code: "UNAUTHENTICATED" });
        return;
      }

      setToggling(actionId);
      setError(null);
      try {
        // 서버가 갱신된 **전체** 선호를 돌려준다 — 그 값으로 수렴시킨다.
        const prefs = await setBehaviorPreference({
          petId,
          actionId,
          enabled,
          accessToken: auth.token,
        });
        if (!cancelledRef.current) applyPreferences(prefs);
      } catch (e) {
        if (cancelledRef.current) return;
        if (e instanceof PremiumApiError && e.code === "UNAUTHENTICATED") {
          setError({ code: "UNAUTHENTICATED" });
        } else if (e instanceof PremiumApiError && e.code === "PET_NOT_OWNED") {
          setError({ code: "PET_NOT_OWNED" });
        } else {
          setError({
            code: "UNAVAILABLE",
            message: e instanceof Error ? e.message : String(e),
          });
        }
      } finally {
        if (!cancelledRef.current) setToggling(null);
      }
    },
    [assets, petId, applyPreferences]
  );

  return { state, loading, submitting, toggling, error, generate, toggle, refresh };
}
