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

  const refresh = useCallback(async () => {
    setBusy("refresh");
    try {
      const s = await fetchSubscriptionStatus(userId);
      setStatus(s);
      if (s.credits_remaining != null) onCreditsChanged?.(s.credits_remaining);
    } catch (e) {
      setLastMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [userId, onCreditsChanged]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (event: SubscriptionMockEvent) => {
    setBusy(event);
    setLastMsg(null);
    try {
      const r = await sendSubscriptionMockWebhook(userId, event);
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
      className="mx-2 mb-4 rounded-2xl p-4"
      style={{
        background: "rgba(201, 162, 39, 0.08)",
        border: "1px solid rgba(201, 162, 39, 0.25)",
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-light" style={{ color: "#d4af37" }}>
          {t.title}
        </p>
        <button type="button" onClick={onClose} className="p-1">
          <X className="w-4 h-4" style={{ color: "#A1A1A6" }} />
        </button>
      </div>

      <p className="text-[11px] mb-3 font-light" style={{ color: "#A1A1A6" }}>
        {t.hint}
      </p>

      {status ? (
        <div
          className="text-[12px] space-y-1 mb-3 p-3 rounded-xl"
          style={{ background: "rgba(0,0,0,0.25)" }}
        >
          <p style={{ color: "#F5F5F7" }}>
            {t.status}:{" "}
            <span style={{ color: status.entitled ? "#4ade80" : "#f87171" }}>
              {status.status ?? "—"} ({status.entitled ? t.entitledYes : t.entitledNo})
            </span>
          </p>
          <p style={{ color: "#A1A1A6" }}>
            {t.credits}: {status.credits_remaining ?? "—"}
          </p>
          <p style={{ color: "#A1A1A6" }}>
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
            className="py-2.5 px-2 rounded-xl text-[11px] font-light transition-opacity"
            style={{
              background: "rgba(28, 28, 30, 0.95)",
              border: "1px solid rgba(201, 162, 39, 0.3)",
              color: "#F5F5F7",
              opacity: busy && busy !== event ? 0.5 : 1,
            }}
          >
            {busy === event ? t.processing : label}
          </button>
        ))}
      </div>

      <button
        type="button"
        disabled={!!busy}
        onClick={() => void refresh()}
        className="w-full mt-2 py-2 flex items-center justify-center gap-2 text-[11px]"
        style={{ color: "#A1A1A6" }}
      >
        <RefreshCw className={`w-3 h-3 ${busy === "refresh" ? "animate-spin" : ""}`} />
        {t.refresh}
      </button>

      {lastMsg ? (
        <p className="mt-2 text-[10px] font-light" style={{ color: "#A1A1A6" }}>
          {lastMsg}
        </p>
      ) : null}
    </motion.div>
  );
}
