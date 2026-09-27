"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { X, RefreshCw } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  fetchSubscriptionStatus,
  sendSubscriptionMockWebhook,
  type SubscriptionMockEvent,
} from "@/lib/subscription-mock";
import type { SubscriptionStatusResult } from "@/app/services/videoProcessingApi";

interface SubscriptionTestPanelProps {
  userId: string;
  language?: string;
  onClose: () => void;
  onCreditsChanged?: (remaining: number) => void;
}

export function SubscriptionTestPanel({
  userId,
  language = "ko",
  onClose,
  onCreditsChanged,
}: SubscriptionTestPanelProps) {
  const t = memorialT(language).subscriptionTest;
  const [status, setStatus] = useState<SubscriptionStatusResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastMsg, setLastMsg] = useState<string | null>(null);

  // 신원은 토큰이 정한다 — userId prop 은 더 이상 서버로 가지 않는다.
  const refresh = useCallback(async () => {
    setBusy("refresh");
    try {
      const s = await fetchSubscriptionStatus();
      setStatus(s);
      if (s.credits_remaining != null) onCreditsChanged?.(s.credits_remaining);
    } catch (e) {
      setLastMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [onCreditsChanged]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (event: SubscriptionMockEvent) => {
    setBusy(event);
    setLastMsg(null);
    try {
      const r = await sendSubscriptionMockWebhook(event);
      setLastMsg(r.message);
      if (r.credits_remaining != null) onCreditsChanged?.(r.credits_remaining);
      await refresh();
    } catch (e) {
      setLastMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const buttons: { event: SubscriptionMockEvent; label: string }[] = [
    { event: "INITIAL_BUY", label: t.initialBuy },
    { event: "RENEWAL", label: t.renewal },
    { event: "CANCEL", label: t.cancel },
    { event: "EXPIRATION", label: t.expiration },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="eb-card eb-card--muted mx-2 mb-4 p-4"
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="eb-section-title truncate">{t.title}</p>
        <button type="button" onClick={onClose} className="mem-icon-btn -mr-2 shrink-0" aria-label="Close">
          <X className="w-4 h-4 text-[var(--eb-text-2)]" />
        </button>
      </div>

      <p className="eb-caption mb-3">{t.hint}</p>

      {status ? (
        <div className="text-[12px] space-y-1 mb-3 p-3 rounded-[var(--eb-radius-sm)] bg-[var(--eb-surface)] border border-[var(--eb-hairline)]">
          <p className="text-[var(--eb-text)]">
            {t.status}:{" "}
            <span
              className={`font-semibold ${
                status.entitled ? "text-[var(--eb-sage-text)]" : "text-[var(--eb-terracotta-text)]"
              }`}
            >
              {status.status ?? "—"} ({status.entitled ? t.entitledYes : t.entitledNo})
            </span>
          </p>
          <p className="text-[var(--eb-text-2)]">
            {t.credits}: {status.credits_remaining ?? "—"}
          </p>
          <p className="text-[var(--eb-text-2)]">
            {t.nextBilling}: {status.next_billing_date?.slice(0, 10) ?? "—"}
          </p>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2">
        {buttons.map(({ event, label }) => (
          <button
            key={event}
            type="button"
            disabled={!!busy}
            onClick={() => void run(event)}
            aria-busy={busy === event || undefined}
            className="eb-btn eb-btn--secondary mem-btn-secondary text-xs px-2"
          >
            {busy === event ? t.processing : label}
          </button>
        ))}
      </div>

      <button
        type="button"
        disabled={!!busy}
        onClick={() => void refresh()}
        className="eb-btn eb-btn--ghost eb-btn--block mt-2 text-xs"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${busy === "refresh" ? "animate-spin" : ""}`} />
        {t.refresh}
      </button>

      {lastMsg ? <p className="mt-2 eb-caption">{lastMsg}</p> : null}
    </motion.div>
  );
}
