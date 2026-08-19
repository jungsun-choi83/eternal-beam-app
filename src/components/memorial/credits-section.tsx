"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Coins, RefreshCw } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { getWalletBalance } from "@/app/services/videoProcessingApi";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { TEST_CREDIT_PACKS, addTestCredits, isTopUpAvailable } from "@/lib/credit-topup";

/** Memorial 의 "크레딧 받기" 가 이 섹션으로 스크롤·포커스할 때 쓰는 앵커. */
export const CREDITS_SECTION_ID = "eb-credits-section";

interface CreditsSectionProps {
  language?: string;
  /** true 면 마운트 직후 이 섹션으로 스크롤하고 잠깐 강조한다. */
  focusOnMount?: boolean;
}

/**
 * 설정 화면의 **상시 노출** 크레딧 섹션.
 *
 * 예전에는 잔액을 볼 수 있는 곳이 SubscriptionTestPanel 뿐이었고, 그것도 목록에서
 * 항목을 눌러야 열리는 숨은 패널이었다. 그래서 Memorial 이 "설정에서 크레딧을
 * 충전하세요" 라고 안내해도 실제로 충전할 수 있는 화면이 없었다.
 *
 * 충전은 **기존 IAP 경로**를 그대로 쓴다(lib/credit-topup.ts). 두 번째 지갑을
 * 만들지 않으므로, 여기서 늘어난 잔액이 곧 프리미엄 구매가 차감하는 잔액이다.
 */
export function CreditsSection({ language = "ko", focusOnMount }: CreditsSectionProps) {
  const t = memorialT(language).credits;
  const [balance, setBalance] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const w = await getWalletBalance(getEternalBeamUserId());
      setBalance(w.current_credits);
    } catch {
      setBalance(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!focusOnMount) return;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlight(true);
    const t = window.setTimeout(() => setHighlight(false), 2000);
    return () => window.clearTimeout(t);
  }, [focusOnMount]);

  const topUp = useCallback(
    async (productId: string) => {
      if (busy) return;
      setBusy(productId);
      setMessage(null);
      try {
        const r = await addTestCredits(getEternalBeamUserId(), productId);
        // 서버가 돌려준 잔액을 그대로 쓴다 — 로컬에서 더하면 실제 지갑과 어긋난다.
        setBalance(r.creditsRemaining);
        setMessage(r.replay ? t.replay : t.added(r.creditsAdded));
      } catch (e) {
        setMessage(e instanceof Error ? e.message : String(e));
        void refresh();
      } finally {
        setBusy(null);
      }
    },
    [busy, refresh, t]
  );

  if (!isTopUpAvailable()) return null;

  return (
    <motion.div
      id={CREDITS_SECTION_ID}
      ref={ref}
      animate={
        highlight
          ? { boxShadow: "0 0 0 2px rgba(201,162,39,0.55)" }
          : { boxShadow: "0 0 0 0px rgba(201,162,39,0)" }
      }
      transition={{ duration: 0.4 }}
      className="mx-2 mb-4 rounded-2xl p-4"
      style={{
        background: "rgba(201, 162, 39, 0.06)",
        border: "1px solid rgba(201, 162, 39, 0.22)",
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Coins className="w-4 h-4" style={{ color: "#d4af37" }} strokeWidth={1.5} />
          <p className="text-sm font-light" style={{ color: "#d4af37" }}>
            {t.title}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="p-1"
          aria-label={t.refresh}
        >
          <RefreshCw className="w-3.5 h-3.5" style={{ color: "#A1A1A6" }} />
        </button>
      </div>

      <div
        className="flex items-baseline gap-2 mb-3 p-3 rounded-xl"
        style={{ background: "rgba(0,0,0,0.25)" }}
      >
        <span className="text-[12px]" style={{ color: "#A1A1A6" }}>
          {t.currentBalance}
        </span>
        <span className="text-lg font-medium" style={{ color: "#F5F5F7" }}>
          {balance ?? "—"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {TEST_CREDIT_PACKS.map((pack) => (
          <button
            key={pack.productId}
            type="button"
            disabled={busy != null}
            onClick={() => void topUp(pack.productId)}
            className="py-2.5 px-2 rounded-xl text-[12px] font-light transition-opacity disabled:opacity-50"
            style={{
              background: "rgba(28, 28, 30, 0.95)",
              border: "1px solid rgba(201, 162, 39, 0.3)",
              color: "#F5F5F7",
            }}
          >
            {busy === pack.productId ? t.processing : t.addPack(pack.credits)}
          </button>
        ))}
      </div>

      <p className="mt-2 text-[10px] font-light" style={{ color: "#8a8a8a" }}>
        {t.mockHint}
      </p>

      {message ? (
        <p className="mt-1.5 text-[11px] font-light" style={{ color: "#A1A1A6" }}>
          {message}
        </p>
      ) : null}
    </motion.div>
  );
}
