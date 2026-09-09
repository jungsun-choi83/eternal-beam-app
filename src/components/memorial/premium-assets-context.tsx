"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  PremiumApiError,
  discoverPremiumAssets,
  type PremiumAssets,
} from "@/lib/premium-assets";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";

/** 생성이 도는 동안의 조회 주기 — 자산이 언제 READY 가 되는지 알아야 한다. */
export const PREMIUM_ASSETS_POLL_MS = 15_000;

/**
 * 생성이 없을 때의 **느린** 재확인 주기 (5분).
 *
 * 왜 필요한가: 예전에는 마운트와 생성 중에만 조회했다. 그래서 화면을 열어 둔 채
 * 구독이 만료되면(갱신 실패·환불·해지 만료) 그 세션 동안 프리미엄 행동이 계속
 * 재생됐다 — 재마운트 전까지 아무도 다시 묻지 않았기 때문이다.
 *
 * 5분인 이유: 구독 만료는 분 단위로 급한 일이 아니고(결제 주기는 월 단위),
 * 이 호출은 읽기 전용 GET 한 번이다. 더 짧게 잡으면 유휴 탭이 서버를 계속
 * 두드리고, 더 길게 잡으면 "열어 둔 화면"이 사실상 만료를 무시한다.
 */
export const ENTITLEMENT_REFRESH_MS = 5 * 60_000;

export interface PremiumAssetsValue {
  assets: PremiumAssets | null;
  hasAuth: boolean;
  loading: boolean;
  error: "PET_NOT_OWNED" | null;
  refresh: () => Promise<void>;
  /** 서버 응답으로 자산을 바로 갈아끼운다 (선호 저장 응답 등). */
  applyPreferences: (prefs: Record<string, boolean>) => void;
}

const EMPTY: PremiumAssetsValue = {
  assets: null,
  hasAuth: false,
  loading: false,
  error: null,
  refresh: async () => {},
  applyPreferences: () => {},
};

const Ctx = createContext<PremiumAssetsValue>(EMPTY);

/**
 * 프리미엄 자산 **단일 조회원**.
 *
 * Phase 4 에서 멤버십 카드와 행동 라이브러리가 같은 엔드포인트를 각자 조회하고
 * 각자 폴링했다 — 마운트마다 GET 2번, 생성 중에는 15초마다 2번. 응답이 같은
 * 엔드포인트에서 오므로 두 화면이 서로 다른 시점을 볼 수도 있었다.
 *
 * 여기서 한 번만 조회하고 둘이 나눠 쓴다. 폴링 판정도 한 곳에 있다.
 *
 * ⚠️ Provider 없이 훅을 쓰면 assets=null 로 조용히 비어 보인다. 그래서 배선을
 * 테스트로 고정한다(behavior-library.test.ts) — 런타임에 던져서 재생 화면을
 * 통째로 죽이는 것보다 낫다.
 */
export function PremiumAssetsProvider({
  petId,
  enabled,
  children,
}: {
  petId: string | null;
  enabled: boolean;
  children: ReactNode;
}) {
  const [assets, setAssets] = useState<PremiumAssets | null>(null);
  const [hasAuth, setHasAuth] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<"PET_NOT_OWNED" | null>(null);
  const cancelledRef = useRef(false);

  /** 조회만 한다. **절대 생성하지 않는다.** */
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
      if (e instanceof PremiumApiError && e.code === "UNAUTHENTICATED") setHasAuth(false);
      else if (e instanceof PremiumApiError && e.code === "PET_NOT_OWNED") {
        setError("PET_NOT_OWNED");
      }
      // 그 외는 조용히 넘긴다 — 배지가 재생을 막아서는 안 된다.
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [enabled, petId]);

  useEffect(() => {
    cancelledRef.current = false;
    void refresh();
    return () => {
      cancelledRef.current = true;
    };
  }, [refresh]);

  // 주기적 재확인. **타이머는 언제나 하나**이고, 주기만 상태에 따라 달라진다:
  //   생성 중 → 15초  (자산이 READY 가 되는 순간을 잡아야 한다)
  //   그 외   → 5분    (구독 만료를 언젠가는 알아채야 한다)
  //
  // GET 하나뿐이라 생성도 과금도 일으키지 않는다. 만료가 반영되는 순간
  // entitled=false 가 되고, 여기서 파생되는 적격성이 전부 비면서 프리미엄 행동이
  // 후보와 소스에서 함께 빠진다. BREATHING 은 이 경로 밖이라 계속 돈다.
  const generating = Boolean(assets?.generating.length);
  useEffect(() => {
    if (!enabled) return;
    const period = generating ? PREMIUM_ASSETS_POLL_MS : ENTITLEMENT_REFRESH_MS;
    const t = window.setInterval(() => void refresh(), period);
    return () => window.clearInterval(t);
  }, [generating, enabled, refresh]);

  // 탭이 다시 보이는 순간 한 번 확인한다.
  //
  // 백그라운드 탭에서는 브라우저가 타이머를 크게 늦추므로(모바일에서는 아예 멈춘다),
  // 인터벌만으로는 "한참 뒤에 돌아온 사용자"가 만료를 오래 못 볼 수 있다. 돌아오는
  // 시점은 사용자가 실제로 화면을 보기 시작하는 시점이라, 여기서 한 번 맞추는 것이
  // 가장 싸고 정확하다. 폴링을 더 조이는 것보다 낫다.
  useEffect(() => {
    if (!enabled) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [enabled, refresh]);

  /**
   * 선호만 갈아끼운다 — 토글 응답을 반영할 때 쓴다.
   *
   * 전체 재조회를 하지 않는 이유: 선호는 자산 상태(READY/GENERATING/MISSING)를
   * 바꾸지 않으므로 다시 물어볼 것이 없고, 왕복이 늘면 토글이 굼떠 보인다.
   */
  const applyPreferences = useCallback((prefs: Record<string, boolean>) => {
    setAssets((prev) => (prev ? { ...prev, preferences: prefs } : prev));
  }, []);

  const value = useMemo<PremiumAssetsValue>(
    () => ({ assets, hasAuth, loading, error, refresh, applyPreferences }),
    [assets, hasAuth, loading, error, refresh, applyPreferences]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePremiumAssetsContext(): PremiumAssetsValue {
  return useContext(Ctx);
}
