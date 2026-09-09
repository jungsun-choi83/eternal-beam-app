"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  PremiumApiError,
  discoverPremiumAssets,
  purchasePremium,
  type PremiumAssets,
} from "@/lib/premium-assets";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { deriveUnlockState, type UnlockState } from "@/lib/premium-unlock";
import { getWalletBalance } from "@/app/services/videoProcessingApi";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";

/** 생성 중일 때 자산 상태를 다시 물어보는 주기. */
export const UNLOCK_POLL_MS = 15_000;

export type UnlockError =
  | { code: "UNAUTHENTICATED" }
  | { code: "PET_NOT_OWNED" }
  | { code: "INSUFFICIENT_CREDITS" }
  | { code: "UNAVAILABLE"; message: string }
  | { code: "PARTIAL"; message: string };

export interface PremiumUnlock {
  state: UnlockState;
  assets: PremiumAssets | null;
  loading: boolean;
  purchasing: boolean;
  error: UnlockError | null;
  /** 직전 구매에서 **실제로** 차감된 총 크레딧 (서버 응답 기준). */
  lastCharged: number | null;
  purchase: () => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * "Unlock More Features" 상태 + 구매 실행.
 *
 * 두 가지 원칙이 이 훅의 전부다:
 *
 *   1) **발견은 자동, 구매는 수동.** 마운트/폴링/새로고침은 GET 만 한다.
 *      purchase() 는 오직 사용자 확인에서만 호출된다.
 *
 *   2) **두 구매는 독립이다.** 하나가 실패해도 성공한 쪽을 되돌리지 않는다.
 *      재시도하면 서버 원장이 성공한 쪽을 0원으로 처리하므로, 남은 쪽만 결제된다.
 */
export function usePremiumUnlock(params: {
  petId: string | null;
  petImageUrl?: string | null;
  /** false 면 아무 네트워크도 타지 않는다 (BREATH 자산이 없을 때 등). */
  enabled: boolean;
}): PremiumUnlock {
  const { petId, petImageUrl, enabled } = params;

  const [assets, setAssets] = useState<PremiumAssets | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [hasAuth, setHasAuth] = useState(false);
  const [loading, setLoading] = useState(false);
  const [purchasing, setPurchasing] = useState(false);
  const [error, setError] = useState<UnlockError | null>(null);
  const [lastCharged, setLastCharged] = useState<number | null>(null);

  // 한 번의 클릭이 두 번 제출되지 않게 하는 가드. state 가 아니라 ref 인 이유:
  // setState 는 다음 렌더에나 반영돼서, 같은 틱의 두 번째 클릭을 못 막는다.
  const inflightRef = useRef(false);
  const cancelledRef = useRef(false);

  const loadBalance = useCallback(async () => {
    try {
      const w = await getWalletBalance(getEternalBeamUserId());
      if (!cancelledRef.current) setBalance(w.current_credits);
    } catch {
      if (!cancelledRef.current) setBalance(null);
    }
  }, []);

  /** 자산 + 잔액 조회. **절대 생성하지 않는다.** */
  const refresh = useCallback(async () => {
    if (!enabled || !petId) return;
    const auth = await getPremiumAccessToken();
    if (cancelledRef.current) return;
    setHasAuth(Boolean(auth.token));
    if (!auth.token) {
      setAssets(null);
      return;
    }
    setLoading(true);
    try {
      const next = await discoverPremiumAssets({ petId, accessToken: auth.token });
      if (!cancelledRef.current) setAssets(next);
    } catch (e) {
      if (cancelledRef.current) return;
      if (e instanceof PremiumApiError && e.code === "UNAUTHENTICATED") {
        setHasAuth(false);
      } else if (e instanceof PremiumApiError && e.code === "PET_NOT_OWNED") {
        setError({ code: "PET_NOT_OWNED" });
      }
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
    void loadBalance();
  }, [enabled, petId, loadBalance]);

  useEffect(() => {
    cancelledRef.current = false;
    void refresh();
    return () => {
      cancelledRef.current = true;
    };
  }, [refresh]);

  const state = deriveUnlockState({ assets, balance, hasAuth });

  // 생성이 도는 동안에만 폴링한다. READY 가 되면 스스로 멈춘다.
  // 폴링은 GET 이므로 크레딧과 무관하다.
  const shouldPoll = state.phase === "unlocking";
  useEffect(() => {
    if (!shouldPoll || !enabled) return;
    const t = window.setInterval(() => void refresh(), UNLOCK_POLL_MS);
    return () => window.clearInterval(t);
  }, [shouldPoll, enabled, refresh]);

  /**
   * 구매 실행 — **사용자 확인에서만 호출한다.**
   *
   * 두 kind 를 각각 POST 한다. 하나가 실패해도 다른 하나는 그대로 둔다:
   * 서버 원장이 성공한 쪽을 기억하므로, 재시도는 남은 쪽만 결제한다.
   */
  const purchase = useCallback(async () => {
    if (inflightRef.current) return; // 더블클릭 방어
    if (!petId) return;

    const current = deriveUnlockState({ assets, balance, hasAuth });
    if (current.missingKinds.length === 0) return; // 살 것이 없다 — 호출하지 않는다
    if (current.phase === "insufficient-credits") {
      setError({ code: "INSUFFICIENT_CREDITS" });
      return;
    }

    const auth = await getPremiumAccessToken();
    if (!auth.token) {
      setError({ code: "UNAUTHENTICATED" });
      return;
    }

    inflightRef.current = true;
    setPurchasing(true);
    setError(null);

    const results = await Promise.allSettled(
      current.missingKinds.map((kind) =>
        purchasePremium({ kind, petId, petImageUrl, accessToken: auth.token })
      )
    );

    let charged = 0;
    const failures: PremiumApiError[] = [];
    for (const r of results) {
      if (r.status === "fulfilled") charged += r.value.creditsCharged;
      else if (r.reason instanceof PremiumApiError) failures.push(r.reason);
    }

    if (!cancelledRef.current) {
      setLastCharged(charged);
      if (failures.length) {
        const first = failures[0];
        // 부분 성공을 롤백하지 않는다. 성공한 쪽은 그대로 두고, 실패한 쪽만
        // 다음 재시도에서 결제된다(서버 원장이 중복 과금을 막는다).
        if (failures.length < results.length) {
          setError({ code: "PARTIAL", message: first.message });
        } else if (first.code === "INSUFFICIENT_CREDITS") {
          setError({ code: "INSUFFICIENT_CREDITS" });
        } else if (first.code === "UNAUTHENTICATED") {
          setError({ code: "UNAUTHENTICATED" });
        } else if (first.code === "PET_NOT_OWNED") {
          setError({ code: "PET_NOT_OWNED" });
        } else {
          setError({ code: "UNAVAILABLE", message: first.message });
        }
      }
    }

    inflightRef.current = false;
    if (!cancelledRef.current) setPurchasing(false);
    await refresh();
  }, [assets, balance, hasAuth, petId, petImageUrl, refresh]);

  return { state, assets, loading, purchasing, error, lastCharged, purchase, refresh };
}
