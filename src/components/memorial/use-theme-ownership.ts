"use client";

/**
 * 테마 소유권 훅 — 카탈로그를 한 번 읽고, 구매를 실행한다.
 *
 * 판정은 **하나도 하지 않는다.** 전부 lib/theme-ownership.ts 의 순수 함수가 하고,
 * 여기서는 네트워크와 상태만 다룬다. 그래야 표시 규칙이 node --test 로 덮인다.
 *
 * ⚠️ 구매는 **사용자 조작에서만** 일어난다. 이 훅의 effect 는 카탈로그를 읽기만
 * 한다 — 화면을 열었다는 이유로 결제가 일어나면 안 된다(premium-assets.ts 가
 * 발견/구매를 나눈 것과 같은 이유).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  indexOffers,
  type ThemeOffer,
} from "@/lib/theme-ownership";
import {
  ThemeStoreError,
  fetchThemeCatalog,
  purchaseThemeWithCredits,
} from "@/lib/theme-store-api";
import { markOwned } from "@/lib/theme-ownership";

export interface ThemeOwnershipValue {
  offers: Map<string, ThemeOffer>;
  loading: boolean;
  /** 카탈로그를 못 받았는가 (로그인 전 포함). 폴백 표시의 근거. */
  unavailable: boolean;
  /** 구매 진행 중인 theme_key. */
  buying: string | null;
  /** 마지막 구매 오류 코드 — 화면이 문구를 정한다. */
  error: string | null;
  /** 지금 잔액. 아직 못 받았으면 null (0 과 구분한다). */
  balance: number | null;
  buy: (themeKey: string) => Promise<boolean>;
  refresh: () => Promise<void>;
}

export function useThemeOwnership(): ThemeOwnershipValue {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<ThemeOffer[] | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getPremiumAccessToken().then((r) => {
      if (alive) setToken(r.token);
    });
    return () => {
      alive = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const catalog = await fetchThemeCatalog({ accessToken: token });
      setRows(catalog.offers);
      setBalance(catalog.creditBalance);
      setError(null);
    } catch {
      // 카탈로그를 못 받으면 폴백 표시로 간다 — 무료 테마는 계속 쓸 수 있고,
      // 유료 테마는 잠긴 채로 남는다(관대하게 열지 않는다).
      setRows(null);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const offers = useMemo(() => indexOffers(rows), [rows]);

  const buy = useCallback(
    async (themeKey: string): Promise<boolean> => {
      if (!token) {
        setError("UNAUTHENTICATED");
        return false;
      }
      setBuying(themeKey);
      setError(null);
      try {
        // ── Beam Credit 구매 (Phase 4) ─────────────────────────────────────
        // 결제창이 없다. 페이지가 이동하지 않으므로 Toss 경로가 필요로 하던
        // 왕복 상태 저장(theme-purchase-return-state)도 없다 — 응답이 바로
        // 돌아오고 그 자리에서 OWNED 가 된다.
        //
        // 서버에서 차감·원장·소유권이 **한 트랜잭션**이다. 성공했으면 셋 다 됐고,
        // 실패했으면 셋 다 안 됐다. 그래서 여기에 보상 로직이 없다.
        const out = await purchaseThemeWithCredits({ themeKey, accessToken: token });

        // 낙관적 갱신 — 서버 재조회 전에 잠깐 NOT OWNED 로 남아 있으면
        // "크레딧은 나갔는데 안 샀다"로 보인다. 구매 직후 가장 불안한 순간이다.
        setRows((cur) => [...markOwned(indexOffers(cur), themeKey).values()]);
        if (out.creditsRemaining != null) setBalance(out.creditsRemaining);
        void refresh();
        return true;
      } catch (e) {
        setError(e instanceof ThemeStoreError ? e.code : "UNKNOWN");
        return false;
      } finally {
        setBuying(null);
      }
    },
    [token, refresh]
  );

  return {
    offers,
    loading,
    unavailable: rows === null,
    buying,
    error,
    balance,
    buy,
    refresh,
  };
}
