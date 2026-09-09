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
  markOwned,
  type ThemeOffer,
} from "@/lib/theme-ownership";
import {
  ThemeStoreError,
  fetchThemeCatalog,
  openThemePaymentWindow,
  purchaseTheme,
  startThemeCheckout,
} from "@/lib/theme-store-api";

export interface ThemeOwnershipValue {
  offers: Map<string, ThemeOffer>;
  loading: boolean;
  /** 카탈로그를 못 받았는가 (로그인 전 포함). 폴백 표시의 근거. */
  unavailable: boolean;
  /** 구매 진행 중인 theme_key. */
  buying: string | null;
  /** 마지막 구매 오류 코드 — 화면이 문구를 정한다. */
  error: string | null;
  buy: (themeKey: string) => Promise<boolean>;
  refresh: () => Promise<void>;
}

export function useThemeOwnership(): ThemeOwnershipValue {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<ThemeOffer[] | null>(null);
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
      setRows(await fetchThemeCatalog({ accessToken: token }));
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
        // 저장된 카드가 있으면 결제창 없이 끝난다. 없으면 **오류가 아니라**
        // 결제창 경로로 넘어간다 — 카드 등록이 구매의 전제가 아니다.
        try {
          await purchaseTheme({ themeKey, accessToken: token });
        } catch (e) {
          if (
            e instanceof ThemeStoreError &&
            e.code === "PAYMENT_METHOD_UNAVAILABLE"
          ) {
            const checkout = await startThemeCheckout({ themeKey, accessToken: token });
            // 페이지가 결제창으로 이동한다 — 이 아래는 실행되지 않는다.
            await openThemePaymentWindow(checkout);
            return false;
          }
          throw e;
        }
        // 낙관적 갱신 — 결제 직후 NOT OWNED 로 남아 있으면 "돈은 나갔는데
        // 안 샀다"로 보인다. 곧바로 서버 값으로 수렴시킨다.
        setRows((cur) => [...markOwned(indexOffers(cur), themeKey).values()]);
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
    buy,
    refresh,
  };
}
