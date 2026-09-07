"use client";

/**
 * 크레딧 팩 결제 복귀 화면 (Phase 5).
 *
 *     Toss → /credits/success → confirm → **원래 고르던 테마로 돌아간다**
 *
 * ── 왜 홈이 아닌가 ─────────────────────────────────────────────────────────
 * 사용자는 Aurora 를 사려다 크레딧이 모자라서 여기까지 왔다. 충전을 마치고 홈으로
 * 떨어지면 그 맥락이 사라지고, 무엇을 하려던 참이었는지 다시 찾아야 한다.
 *
 * 그래서 결제창으로 떠나기 **전에** 고르던 테마를 sessionStorage 에 적어 두고
 * (theme-purchase-return-state), 돌아와서 확인이 끝나면 루트로 보낸다.
 * resolveInitialScreen 이 그 표식을 보고 테마 선택 화면 · 해당 테마로 복원한다 —
 * Toss 테마 결제 왕복이 이미 쓰던 바로 그 기전이라 새로 만들지 않았다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { creditsReturnEntry, readCreditsReturnParams } from "@/lib/app-entry";
import { CreditsError, confirmCreditPayment } from "@/lib/credits-api";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { readThemePurchaseReturnState } from "@/lib/theme-purchase-return-state";

type Phase =
  | { kind: "working" }
  | { kind: "done"; creditsAdded: number; balance: number }
  | { kind: "failed"; code: string; message: string };

const MESSAGES: Record<string, string> = {
  UNAUTHENTICATED: "로그인이 필요합니다. 다시 로그인한 뒤 확인해 주세요.",
  CREDIT_ORDER_NOT_FOUND: "주문을 찾을 수 없습니다.",
  CREDIT_ORDER_NOT_PENDING: "이미 처리된 주문입니다.",
  CREDIT_AMOUNT_MISMATCH: "주문 금액이 일치하지 않습니다.",
  CREDIT_PAYMENT_FAILED: "결제가 완료되지 않았습니다.",
  CREDIT_CONFIRM_UNAVAILABLE:
    "결제는 승인됐지만 크레딧 지급을 확정하지 못했습니다. 잠시 후 다시 확인해 주세요.",
  PAYMENT_CANCELLED: "결제가 취소되었습니다.",
  MISSING_PARAMS: "결제 정보를 확인할 수 없습니다.",
};

export function CreditPurchaseReturnScreen() {
  const outcome = creditsReturnEntry();
  const [phase, setPhase] = useState<Phase>({ kind: "working" });
  // StrictMode 이중 실행과 리렌더로 confirm 이 두 번 나가지 않게 한다.
  const startedRef = useRef(false);
  // 결제창으로 떠나기 전에 적어 둔 테마. 돌아갈 곳을 안내 문구에도 쓴다.
  const pendingTheme = readThemePurchaseReturnState()?.themeKey ?? null;

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    if (outcome === "fail") {
      const p = readCreditsReturnParams(window.location.search);
      setPhase({
        kind: "failed",
        code: p.code || "PAYMENT_CANCELLED",
        message: p.message || MESSAGES.PAYMENT_CANCELLED,
      });
      return;
    }

    const params = readCreditsReturnParams(window.location.search);
    if (!params.paymentKey || !params.orderId) {
      setPhase({ kind: "failed", code: "MISSING_PARAMS", message: MESSAGES.MISSING_PARAMS });
      return;
    }

    void (async () => {
      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setPhase({
          kind: "failed",
          code: "UNAUTHENTICATED",
          message: MESSAGES.UNAUTHENTICATED,
        });
        return;
      }
      try {
        const r = await confirmCreditPayment({
          paymentKey: params.paymentKey as string,
          orderId: params.orderId as string,
          amount: params.amount,
          accessToken: auth.token,
        });
        setPhase({
          kind: "done",
          creditsAdded: r.creditsAdded,
          balance: r.creditsRemaining,
        });
      } catch (e) {
        const code = e instanceof CreditsError ? e.code : "UNKNOWN";
        setPhase({
          kind: "failed",
          code,
          message: MESSAGES[code] || "결제를 확인하지 못했습니다.",
        });
      }
    })();
  }, [outcome]);

  /**
   * 루트로 보낸다. **홈이 아니다** — resolveInitialScreen 이 테마 복귀 표식을
   * 보고 테마 선택 화면·해당 테마로 복원한다. 표식이 없으면(설정에서 그냥
   * 충전한 경우) 평소 진입 화면으로 간다.
   */
  const goBack = useCallback(() => {
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
            {phase.creditsAdded > 0
              ? `${phase.creditsAdded} 크레딧이 충전되었습니다`
              : "이미 충전된 결제입니다"}
          </p>
          <p className="text-xs text-white/45">잔액 {phase.balance}</p>
          <button
            type="button"
            onClick={goBack}
            className="cta-gold mt-2 rounded-2xl px-6 py-3 text-sm font-medium"
          >
            {pendingTheme ? "고르던 배경으로 돌아가기" : "계속하기"}
          </button>
        </>
      )}

      {phase.kind === "failed" && (
        <>
          <p className="text-base font-medium text-white/90">
            크레딧을 충전하지 못했습니다
          </p>
          <p className="max-w-xs text-sm leading-relaxed text-white/55">{phase.message}</p>
          <button
            type="button"
            onClick={goBack}
            className="mt-2 rounded-2xl border border-white/20 px-6 py-3 text-sm text-white/70"
          >
            돌아가기
          </button>
        </>
      )}
    </div>
  );
}
