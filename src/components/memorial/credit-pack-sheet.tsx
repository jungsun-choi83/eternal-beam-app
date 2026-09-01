"use client";

/**
 * 크레딧 팩 선택 시트 (Phase 5).
 *
 * ⚠️ **이 파일에 가격이 없다.** 팩 구성·금액·정렬은 전부 서버(GET /credits/packs)가
 * 정하고 여기서는 받은 목록을 그린다. 화면에 가격이 박혀 있으면 바꾸는 데 배포가
 * 필요하고, 서버와 어긋나면 눌러도 거절당하는 버튼이 생긴다.
 *
 * 결제창으로 떠나기 **전에** 돌아올 곳을 적어 둔다(returnThemeKey) — 사용자는
 * 특정 배경을 사려다 여기 온 것이고, 충전 뒤 홈으로 떨어지면 맥락을 잃는다.
 */

import { useCallback, useEffect, useState } from "react";

import {
  CreditsError,
  fetchCreditPacks,
  openCreditPaymentWindow,
  startCreditCheckout,
  type CreditPack,
} from "@/lib/credits-api";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { saveThemePurchaseReturnState } from "@/lib/theme-purchase-return-state";

interface CreditPackSheetProps {
  /** 지금 잔액. 부족분을 계산해 보여 준다. */
  balance: number | null;
  /** 사려던 상품의 가격. 없으면 부족분 안내를 생략한다. */
  needed?: number | null;
  /**
   * 결제 후 돌아갈 테마 key. 이 값이 복귀 화면의 "고르던 배경으로 돌아가기" 를
   * 만든다 — 없으면 평소 진입 화면으로 간다.
   */
  returnThemeKey?: string | null;
  onClose: () => void;
}

export function CreditPackSheet({
  balance,
  needed = null,
  returnThemeKey = null,
  onClose,
}: CreditPackSheetProps) {
  const [packs, setPacks] = useState<CreditPack[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        if (alive) setError("로그인이 필요합니다.");
        return;
      }
      try {
        const list = await fetchCreditPacks({ accessToken: auth.token });
        if (alive) setPacks(list);
      } catch (e) {
        if (alive) {
          setError(
            e instanceof CreditsError && e.code === "CREDIT_PACKS_UNAVAILABLE"
              ? "크레딧 팩을 불러오지 못했습니다."
              : "크레딧 팩을 불러오지 못했습니다."
          );
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const buy = useCallback(
    async (packKey: string) => {
      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setError("로그인이 필요합니다.");
        return;
      }
      setBusy(packKey);
      setError(null);
      try {
        // 떠나기 전에 돌아올 곳을 적는다. 결제창은 새 문서라 React state 가 사라진다.
        if (returnThemeKey) saveThemePurchaseReturnState(returnThemeKey);
        const checkout = await startCreditCheckout({ packKey, accessToken: auth.token });
        // 페이지가 결제창으로 이동한다 — 이 아래는 실행되지 않는다.
        await openCreditPaymentWindow(checkout);
      } catch (e) {
        setError(
          e instanceof CreditsError ? e.message : "결제를 시작하지 못했습니다."
        );
        setBusy(null);
      }
    },
    [returnThemeKey]
  );

  const shortfall =
    needed != null && balance != null && needed > balance ? needed - balance : null;

  return (
    <div className="fixed inset-0 z-[90] flex items-end justify-center bg-black/70">
      <div className="w-full max-w-md rounded-t-3xl bg-[#141416] px-5 pb-[max(1.5rem,env(safe-area-inset-bottom,0px))] pt-5">
        <div className="mb-4 text-center">
          <h2 className="text-base font-medium text-[#EDE3CE]">크레딧 받기</h2>
          {shortfall != null ? (
            <p className="mt-1 text-xs text-[#f5d77a]">
              {shortfall} 크레딧이 더 필요합니다
            </p>
          ) : null}
          {balance != null ? (
            <p className="mt-1 text-[11px] text-[#9a9a9a]">잔액 {balance}</p>
          ) : null}
        </div>

        {error ? (
          <p role="alert" className="mb-3 text-center text-xs text-red-300">
            {error}
          </p>
        ) : null}

        {packs == null && !error ? (
          <p className="py-6 text-center text-xs text-white/40">불러오는 중…</p>
        ) : null}

        <div className="space-y-2">
          {(packs ?? []).map((p) => (
            <button
              key={p.packKey}
              type="button"
              disabled={busy != null}
              onClick={() => void buy(p.packKey)}
              className="flex w-full items-center justify-between rounded-2xl border border-white/12 bg-white/[0.04] px-4 py-3 disabled:opacity-45"
            >
              <span className="text-sm text-[#EDE3CE]">
                {p.credits} 크레딧
                {shortfall != null && p.credits >= shortfall ? (
                  <span className="ml-2 text-[10px] text-[#a8e6a3]">충분해요</span>
                ) : null}
              </span>
              <span className="text-sm text-[#f5d77a]">
                {busy === p.packKey
                  ? "여는 중…"
                  : `₩${p.priceKrw.toLocaleString("ko-KR")}`}
              </span>
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={onClose}
          disabled={busy != null}
          className="mt-4 w-full py-3 text-center text-sm text-[#888] disabled:opacity-45"
        >
          닫기
        </button>
      </div>
    </div>
  );
}
