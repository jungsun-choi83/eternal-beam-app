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

import { PrimaryButton, SecondaryButton } from "@/components/ui/buttons";
import { StatusBadge } from "@/components/ui/status-badge";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { readThemeReturnParams, themeReturnEntry } from "@/lib/app-entry";
import { ThemeStoreError, confirmThemePayment } from "@/lib/theme-store-api";
import { confirmThemePurchaseReturn } from "@/lib/theme-purchase-return-state";

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
        // 루트 앱이 이 key 로 선택 테마와 pending 누끼를 되살린다. 확인 성공 전에는
        // confirmed 로 쓰지 않으므로 승인되지 않은 테마를 보유로 가장하지 않는다.
        confirmThemePurchaseReturn(r.themeKey);
        setPhase({ kind: "done", themeKey: r.themeKey, alreadyOwned: r.alreadyOwned });
        window.setTimeout(() => window.location.replace("/"), 350);
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
    // 결제 전 저장한 theme key 가 남아 있어 테마 선택 화면으로 복원된다. 성공이면
    // 서버 카탈로그가 OWNED 를, 실패면 다시 구매 가능한 상태를 보여 준다.
    window.location.replace("/");
  }, []);

  // Phase 10 — 앱 셸 바깥에서 그려지므로 아이보리 페이지를 스스로 깐다.
  // 결과는 가운데 .eb-card 패널 하나. fixed 오버레이가 아니다.
  return (
    <div className="theme-purchase-return-screen flex min-h-[100dvh] w-full flex-col items-center justify-center gap-4 bg-[var(--eb-bg)] px-6 py-12 text-center text-[var(--eb-text)]">
      <section
        className="eb-card eb-fade-up flex w-full max-w-[420px] flex-col items-center gap-4 px-6 py-8"
        aria-live="polite"
      >
        {phase.kind === "working" && (
          <>
            <StatusBadge tone="loading">확인 중</StatusBadge>
            <p className="eb-body-sm">결제를 확인하는 중…</p>
          </>
        )}

        {phase.kind === "done" && (
          <>
            <StatusBadge tone="success">구매 완료</StatusBadge>
            <p className="eb-title text-base font-medium">
              {phase.alreadyOwned ? "이미 보유한 테마입니다" : "테마를 구매했습니다"}
            </p>
            <p className="eb-caption font-mono">{phase.themeKey}</p>
            <p className="eb-caption">테마 선택 화면으로 돌아가는 중…</p>
          </>
        )}

        {phase.kind === "failed" && (
          <>
            <StatusBadge tone="error">결제 실패</StatusBadge>
            <p className="eb-title text-base font-medium">결제를 완료하지 못했습니다</p>
            <p className="eb-body-sm max-w-xs leading-relaxed">{phase.message}</p>
          </>
        )}

        {phase.kind !== "working" && (
          phase.kind === "done" ? (
            <PrimaryButton block className="mt-2" onClick={goBack}>
              테마 선택으로
            </PrimaryButton>
          ) : (
            <SecondaryButton block className="mt-2" onClick={goBack}>
              테마 선택으로 돌아가기
            </SecondaryButton>
          )
        )}
      </section>
    </div>
  );
}
