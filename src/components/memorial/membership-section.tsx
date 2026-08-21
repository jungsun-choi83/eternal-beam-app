"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Crown, RefreshCw } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { fetchSubscriptionStatus } from "@/lib/subscription-mock";
import {
  BillingError,
  cancelMembership,
  confirmMembership,
  fetchBillingConfig,
  fetchBillingStatus,
  readBillingRedirectParams,
  resumeMembership,
  startMembershipCheckout,
  type BillingStatus,
} from "@/lib/toss-billing";
import type { SubscriptionStatusResult } from "@/app/services/videoProcessingApi";

/** Memorial 의 "멤버십" 진입이 이 섹션으로 스크롤·포커스할 때 쓰는 앵커. */
export const MEMBERSHIP_SECTION_ID = "eb-membership-section";

interface MembershipSectionProps {
  language?: string;
  /** true 면 마운트 직후 이 섹션으로 스크롤하고 잠깐 강조한다. */
  focusOnMount?: boolean;
}

/**
 * 설정 화면의 **상시 노출** 멤버십 섹션 — 크레딧 섹션을 대체한다.
 *
 * 예전에는 여기에 지갑 잔액과 "테스트 크레딧 추가" 버튼이 있었다. 소비자에게
 * 크레딧은 더 이상 제품 개념이 아니므로 노출하지 않는다. 남은 크레딧은 레거시
 * 기기 팩(IDLE/TOUCH/VOICE/NFC)이 계속 쓰지만, 그 재원은 이제 멤버십 갱신이
 * 자동으로 채운다 — 사용자가 직접 관리할 것이 없다.
 *
 * 실제 가입/해지는 스토어 결제(IAP)가 담당한다. 이 섹션은 **상태를 보여 준다**.
 * 목업 환경에서의 상태 전환은 구독 테스트 패널이 따로 담당한다.
 */
export function MembershipSection({ language = "ko", focusOnMount }: MembershipSectionProps) {
  const t = memorialT(language).membership;
  const [status, setStatus] = useState<SubscriptionStatusResult | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [testMode, setTestMode] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [action, setAction] = useState<"start" | "cancel" | "resume" | "confirm" | null>(null);
  const [busy, setBusy] = useState(false);
  const [highlight, setHighlight] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await fetchSubscriptionStatus());
    } catch (e) {
      setStatus(null);
      setError(e instanceof Error ? e.message : String(e));
    }
    // 청구 상태는 별개로 읽는다 — 자격(구독)과 청구(결제 수단·해지 예약)는
    // 서로 다른 계층이고, 한쪽이 실패해도 다른 쪽은 보여 줄 수 있어야 한다.
    try {
      const cfg = await fetchBillingConfig();
      setConfigured(cfg.configured);
      setTestMode(cfg.testMode);
      if (cfg.configured) setBilling(await fetchBillingStatus());
    } catch {
      setConfigured(false);
    }
    setBusy(false);
  }, []);

  /**
   * Toss 리다이렉트 복귀 처리.
   *
   * 결제창에서 돌아오면 URL 에 authKey 가 실려 있다. 여기서 confirm 을 부르고
   * 쿼리를 지운다 — 남겨 두면 새로고침마다 다시 부르게 된다(서버가 멱등이라
   * 이중 청구는 없지만, 화면이 계속 "확인 중"으로 깜빡인다).
   */
  useEffect(() => {
    const p = readBillingRedirectParams(window.location.search);
    if (!p.authKey || !p.customerKey || !p.orderId) return;

    let cancelled = false;
    setAction("confirm");
    void (async () => {
      try {
        const r = await confirmMembership({
          authKey: p.authKey!, customerKey: p.customerKey!,
          orderId: p.orderId!, planId: p.planId ?? undefined,
        });
        if (cancelled) return;
        setNotice(r.entitled ? t.confirmed : null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) {
          window.history.replaceState({}, "", window.location.pathname);
          setAction(null);
          void refresh();
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 마운트 시 한 번만 — 쿼리는 위에서 지운다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = useCallback(
    async (kind: "start" | "cancel" | "resume", fn: () => Promise<void>) => {
      setAction(kind);
      setError(null);
      setNotice(null);
      try {
        await fn();
        if (kind !== "start") await refresh();
      } catch (e) {
        setError(
          e instanceof BillingError ? e.message : e instanceof Error ? e.message : String(e)
        );
      } finally {
        setAction(null);
      }
    },
    [refresh]
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!focusOnMount) return;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlight(true);
    const timer = window.setTimeout(() => setHighlight(false), 2000);
    return () => window.clearTimeout(timer);
  }, [focusOnMount]);

  const entitled = Boolean(status?.entitled);
  const label = !status
    ? t.stateUnknown
    : entitled
      ? status.status === "canceled"
        ? t.stateGrace
        : t.stateActive
      : status.status === "expired" || status.status === "canceled"
        ? t.stateLapsed
        : t.stateNone;

  return (
    <motion.div
      id={MEMBERSHIP_SECTION_ID}
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
          <Crown className="w-4 h-4" style={{ color: "#d4af37" }} strokeWidth={1.5} />
          <p className="text-sm font-light" style={{ color: "#d4af37" }}>
            {t.sectionTitle}
          </p>
        </div>
        <button type="button" onClick={() => void refresh()} className="p-1" aria-label={t.refresh}>
          <RefreshCw
            className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`}
            style={{ color: "#A1A1A6" }}
          />
        </button>
      </div>

      <div className="p-3 rounded-xl space-y-1.5" style={{ background: "rgba(0,0,0,0.25)" }}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[12px]" style={{ color: "#A1A1A6" }}>
            {t.stateLabel}
          </span>
          <span
            className="text-sm font-medium"
            style={{ color: entitled ? "#d4af37" : "#F5F5F7" }}
          >
            {label}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[12px]" style={{ color: "#A1A1A6" }}>
            {t.planLabel}
          </span>
          <span className="text-[12px]" style={{ color: "#F5F5F7" }}>
            {status?.display_name ?? t.planStandard}
            {status?.price_krw_monthly
              ? ` · ${t.perMonth(status.price_krw_monthly)}`
              : ""}
          </span>
        </div>
        {status?.next_billing_date ? (
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[12px]" style={{ color: "#A1A1A6" }}>
              {status.status === "canceled" ? t.endsOn : t.nextBilling}
            </span>
            <span className="text-[12px]" style={{ color: "#F5F5F7" }}>
              {status.next_billing_date.slice(0, 10)}
            </span>
          </div>
        ) : null}
      </div>

      {/* ── 결제 액션 (Toss) ─────────────────────────────────────────────── */}
      {configured ? (
        <div className="mt-3 space-y-2">
          {!entitled ? (
            <button
              type="button"
              disabled={action != null}
              onClick={() => void run("start", startMembershipCheckout)}
              className="w-full py-2.5 rounded-xl text-[13px] font-medium tracking-wide disabled:opacity-50"
              style={{
                background:
                  "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
                color: "#0a0a0a",
              }}
            >
              {action === "start" ? t.starting : action === "confirm" ? t.confirming : t.startCta}
            </button>
          ) : billing?.billing?.cancel_at_period_end ? (
            <>
              <p className="text-[11px]" style={{ color: "#A1A1A6" }}>
                {t.cancelScheduled(
                  (billing.billing.current_period_end ?? "").slice(0, 10) || "—"
                )}
              </p>
              <button
                type="button"
                disabled={action != null}
                onClick={() => void run("resume", resumeMembership)}
                className="w-full py-2.5 rounded-xl text-[13px] font-medium disabled:opacity-50"
                style={{
                  background: "rgba(201, 162, 39, 0.16)",
                  border: "1px solid rgba(201, 162, 39, 0.45)",
                  color: "#F1E5D1",
                }}
              >
                {t.resumeCta2}
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={action != null}
              onClick={() => void run("cancel", cancelMembership)}
              className="w-full py-2 rounded-xl text-[12px] font-light disabled:opacity-50"
              style={{
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.14)",
                color: "#A1A1A6",
              }}
            >
              {action === "cancel" ? t.canceling : t.cancelCta}
            </button>
          )}

          {testMode ? (
            <p className="text-[10px] font-light" style={{ color: "#8a8a8a" }}>
              {t.testModeHint}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-3 text-[11px] font-light" style={{ color: "#8a8a8a" }}>
          {t.notConfigured}
        </p>
      )}

      {notice ? (
        <p className="mt-2 text-[11px]" style={{ color: "#d4af37" }}>
          {notice}
        </p>
      ) : null}

      {/* 만료 불안을 여기서도 한 번 더 눌러 준다. */}
      <p className="mt-2 text-[10px] font-light" style={{ color: "#8a8a8a" }}>
        {t.assetsKeptHint}
      </p>

      {error ? (
        <p className="mt-1.5 text-[11px] font-light" style={{ color: "#A1A1A6" }}>
          {error}
        </p>
      ) : null}
    </motion.div>
  );
}
