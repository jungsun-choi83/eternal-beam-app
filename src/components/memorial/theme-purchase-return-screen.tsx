"use client";

/**
 * 테마 결제 복귀 화면 — Toss 결제창에서 돌아온 직후.
 *
 *   /themes/success?paymentKey=…&orderId=…&amount=…  → 서버 확인 → OWNED
 *   /themes/fail?code=…&message=…                    → 안내만
 *
 * ── EternalBeamApp **바깥**에서 분기하는 이유 ────────────────────────────────
 * 메인 앱의 화면 열거형을 건드리지 않기 위해서다. Shaker 와 같은 방식이다.
 * 결제 복귀는 앱 상태 복원과 무관하고, 여기서 끝나면 사용자를 앱으로 돌려보낸다.
 *
 * ⚠️ 확인(confirm)은 **한 번만** 부른다. 새로고침해도 서버가 멱등이라 재승인되지
 * 않지만, 화면이 반복 호출할 이유는 없다.
 *
 * 실물 주문(/orders/*)은 **다른 화면**을 쓴다(order-confirmation-screen). 보여 줘야
 * 하는 것이 다르기 때문이다 — 주문번호·아이·제품·수령인·결제 상태.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { readThemeReturnParams, themeReturnEntry } from "@/lib/app-entry";
import { ThemeStoreError, confirmThemePayment } from "@/lib/theme-store-api";

type Phase =
  | { kind: "working" }
  | { kind: "done"; themeKey: string; alreadyOwned: boolean }
  | { kind: "failed"; code: string; message: string };

const MESSAGES: Record<string, string> = {
  THEME_AMOUNT_MISMATCH: "주문 금액이 맞지 않습니다. 다시 시도해 주세요.",
  THEME_ORDER_NOT_FOUND: "주문을 찾을 수 없습니다.",
  THEME_ORDER_NOT_PENDING: "이미 종료된 주문입니다. 다시 구매해 주세요.",
  THEME_PAYMENT_FAILED: "결제가 완료되지 않았습니다.",
  UNAUTHENTICATED: "로그인이 필요합니다.",
};

export function ThemePurchaseReturnScreen() {
  const outcome = themeReturnEntry();
  const [phase, setPhase] = useState<Phase>({ kind: "working" });
  // StrictMode 이중 실행과 리렌더로 confirm 이 두 번 나가지 않게 한다.
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    if (outcome === "fail") {
      const p = readThemeReturnParams(window.location.search);
      setPhase({
        kind: "failed",
        code: p.code || "PAYMENT_CANCELLED",
        message: p.message || "결제가 취소되었습니다.",
      });
      return;
    }

    const params = readThemeReturnParams(window.location.search);
    if (!params.paymentKey || !params.orderId) {
      setPhase({
        kind: "failed",
        code: "MISSING_PARAMS",
        message: "결제 정보를 확인할 수 없습니다.",
      });
      return;
    }

    void (async () => {
      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setPhase({ kind: "failed", code: "UNAUTHENTICATED", message: MESSAGES.UNAUTHENTICATED });
        return;
      }
      try {
        const r = await confirmThemePayment({
          paymentKey: params.paymentKey as string,
          orderId: params.orderId as string,
          amount: params.amount,
          accessToken: auth.token,
        });
        setPhase({ kind: "done", themeKey: r.themeKey, alreadyOwned: r.alreadyOwned });
      } catch (e) {
        const code = e instanceof ThemeStoreError ? e.code : "UNKNOWN";
        setPhase({
          kind: "failed",
          code,
          message: MESSAGES[code] || "결제를 확인하지 못했습니다.",
        });
      }
    })();
  }, [outcome]);

  const goBack = useCallback(() => {
    // 앱 루트로. 테마 선택 화면이 카탈로그를 새로 읽어 OWNED 를 반영한다.
    window.location.replace("/");
  }, []);

  return (
    <div className="fixed inset-0 flex flex-col items-center justify-center gap-4 bg-[#0a0a0a] px-8 text-center">
      {phase.kind === "working" && (
        <p className="text-sm text-white/50">결제를 확인하는 중…</p>
      )}

      {phase.kind === "done" && (
        <>
          <p className="text-base font-medium text-[#EDE3CE]">
            {phase.alreadyOwned ? "이미 보유한 테마입니다" : "테마를 구매했습니다"}
          </p>
          <p className="text-xs text-white/45">{phase.themeKey}</p>
        </>
      )}

      {phase.kind === "failed" && (
        <>
          <p className="text-base font-medium text-white/90">결제를 완료하지 못했습니다</p>
          <p className="max-w-xs text-sm leading-relaxed text-white/55">{phase.message}</p>
        </>
      )}

      {phase.kind !== "working" && (
        <button
          type="button"
          onClick={goBack}
          className="mt-2 rounded-full border border-white/20 px-5 py-2 text-sm text-white/80 active:bg-white/10"
        >
          돌아가기
        </button>
      )}
    </div>
  );
}
