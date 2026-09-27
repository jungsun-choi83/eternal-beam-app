"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { AppShell } from "@/components/layout/app-shell";
import { PrimaryButton, SecondaryButton } from "@/components/ui/buttons";
import { StatusBadge } from "@/components/ui/status-badge";
import { confirmMembership, readBillingRedirectParams } from "@/lib/toss-billing";

type Phase = "confirming" | "done" | "failed";

interface BillingResultScreenProps {
  /** Toss 가 보낸 경로 — success 면 확정을 시도하고, fail 이면 바로 실패 화면. */
  outcome: "success" | "fail";
  language?: string;
  onContinue: () => void;
}

/**
 * Toss 결제 복귀 화면 (/billing/success · /billing/fail).
 *
 * 이 화면이 있어야 하는 이유: Toss 는 결제창을 마치면 **페이지를 이동**시킨다.
 * 전용 경로가 없으면 앱이 첫 화면(QR 연결)으로 부팅되고, 사용자는 방금 낸 돈이
 * 어디로 갔는지 알 수 없다.
 *
 * 확정(confirm)은 여기서 **한 번만** 시도한다. 서버가 order_id 로 멱등 처리하므로
 * 새로고침해도 이중 청구는 없지만, 재시도 루프를 만들지 않는 편이 화면이 정직하다.
 */
export function BillingResultScreen({
  outcome,
  language = "ko",
  onContinue,
}: BillingResultScreenProps) {
  const t = memorialT(language).membership;
  const [phase, setPhase] = useState<Phase>(outcome === "fail" ? "failed" : "confirming");
  const [message, setMessage] = useState<string | null>(null);
  const startedRef = useRef(false);

  const run = useCallback(async () => {
    const p = readBillingRedirectParams(window.location.search);
    if (!p.authKey || !p.customerKey || !p.orderId) {
      setPhase("failed");
      setMessage(t.missingReturnParams);
      return;
    }
    try {
      const r = await confirmMembership({
        authKey: p.authKey,
        customerKey: p.customerKey,
        orderId: p.orderId,
        planId: p.planId ?? undefined,
      });
      setPhase(r.entitled ? "done" : "failed");
      if (!r.entitled) setMessage(t.notActivated);
    } catch (e) {
      setPhase("failed");
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      // 쿼리를 지운다 — 남겨 두면 새로고침마다 확정을 다시 부른다.
      window.history.replaceState({}, "", "/");
    }
  }, [t]);

  useEffect(() => {
    if (outcome === "fail" || startedRef.current) return;
    startedRef.current = true;
    void run();
  }, [outcome, run]);

  // Phase 10 — 상태 색은 토큰만 쓴다: 확인 중 = 블루, 완료 = 세이지, 실패 = 테라코타.
  const icon =
    phase === "confirming" ? (
      <Loader2 className="w-7 h-7 animate-spin" style={{ color: "var(--eb-blue)" }} />
    ) : phase === "done" ? (
      <Check className="w-7 h-7" style={{ color: "var(--eb-sage)" }} />
    ) : (
      <AlertCircle className="w-7 h-7" style={{ color: "var(--eb-terracotta)" }} />
    );

  const title =
    phase === "confirming" ? t.confirming : phase === "done" ? t.confirmed : t.paymentFailed;

  return (
    <AppShell>
      <div className="billing-result-screen flex flex-col items-center justify-center h-full px-6 text-center text-[var(--eb-text)]">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="eb-card flex w-full max-w-[420px] flex-col items-center gap-4 px-6 py-8"
          aria-live="polite"
        >
          {icon}
          <StatusBadge
            tone={phase === "confirming" ? "loading" : phase === "done" ? "success" : "error"}
          >
            {title}
          </StatusBadge>
          {message ? (
            <p className="eb-body-sm memorial-body max-w-[260px]">{message}</p>
          ) : phase === "done" ? (
            <p className="eb-body-sm memorial-body max-w-[260px]">{t.confirmedHint}</p>
          ) : null}

          {phase !== "confirming" ? (
            phase === "done" ? (
              <PrimaryButton block className="mt-2" onClick={onContinue}>
                {t.continueAfterPayment}
              </PrimaryButton>
            ) : (
              <SecondaryButton block className="mt-2" onClick={onContinue}>
                {t.continueAfterPayment}
              </SecondaryButton>
            )
          ) : null}
        </motion.div>
      </div>
    </AppShell>
  );
}
