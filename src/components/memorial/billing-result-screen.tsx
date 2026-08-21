"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { MobileFrame } from "@/components/memorial/mobile-frame";
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

  const icon =
    phase === "confirming" ? (
      <Loader2 className="w-7 h-7 animate-spin" style={{ color: "#d4af37" }} />
    ) : phase === "done" ? (
      <Check className="w-7 h-7" style={{ color: "#d4af37" }} />
    ) : (
      <AlertCircle className="w-7 h-7" style={{ color: "#e0a0a0" }} />
    );

  const title =
    phase === "confirming" ? t.confirming : phase === "done" ? t.confirmed : t.paymentFailed;

  return (
    <MobileFrame>
      <div className="flex flex-col items-center justify-center h-full px-6 text-center">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col items-center gap-4"
        >
          {icon}
          <p className="text-base font-medium" style={{ color: "#F1E5D1" }}>
            {title}
          </p>
          {message ? (
            <p className="text-xs memorial-body max-w-[260px]">{message}</p>
          ) : phase === "done" ? (
            <p className="text-xs memorial-body max-w-[260px]">{t.confirmedHint}</p>
          ) : null}

          {phase !== "confirming" ? (
            <button
              type="button"
              onClick={onContinue}
              className="mt-2 px-6 py-2.5 rounded-xl text-[13px] font-medium tracking-wide"
              style={{
                background:
                  phase === "done"
                    ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
                    : "rgba(255,255,255,0.08)",
                color: phase === "done" ? "#0a0a0a" : "#E2E2E2",
              }}
            >
              {t.continueAfterPayment}
            </button>
          ) : null}
        </motion.div>
      </div>
    </MobileFrame>
  );
}
