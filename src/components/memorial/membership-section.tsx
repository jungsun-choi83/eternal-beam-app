"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Coins, Crown, RefreshCw } from "lucide-react";

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
import { fetchWallet } from "@/lib/credits-api";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";

/** Memorial 의 "멤버십" 진입이 이 섹션으로 스크롤·포커스할 때 쓰는 앵커. */
export const MEMBERSHIP_SECTION_ID = "eb-membership-section";

interface MembershipSectionProps {
  language?: string;
  /** true 면 마운트 직후 이 섹션으로 스크롤하고 잠깐 강조한다. */
  focusOnMount?: boolean;
}

/**
 * 설정 화면의 **상시 노출** 멤버십 섹션.
 *
 * 소비자에게 보이는 상태는 **멤버인가 아닌가** 하나다. 레거시 기기 팩의 재원은
 * 멤버십 갱신이 자동으로 채우므로 사용자가 직접 관리할 것이 없다.
 *
 * 실제 가입/해지는 스토어 결제(IAP)가 담당한다. 이 섹션은 **상태를 보여 준다**.
 * 목업 환경에서의 상태 전환은 구독 테스트 패널이 따로 담당한다.
 *
 * Phase 10: 프리미엄 악센트는 gold-line 테두리 + gold-text 만 — 카드 전체를
 * 금색으로 물들이지 않는다. 강조(focusOnMount)는 .eb-card--selected 링으로.
 */
export function MembershipSection({ language = "ko", focusOnMount }: MembershipSectionProps) {
  const t = memorialT(language).membership;
  const [status, setStatus] = useState<SubscriptionStatusResult | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [walletBalance, setWalletBalance] = useState<number | null>(null);
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
    try {
      const auth = await getPremiumAccessToken();
      if (auth.token) {
        const wallet = await fetchWallet({ accessToken: auth.token });
        setWalletBalance(wallet.balance);
      }
    } catch {
      // 크레딧 조회 실패가 멤버십 상태/관리를 막지 않는다.
      setWalletBalance(null);
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
      className={`eb-card mx-2 mb-4 p-4${highlight ? " eb-card--selected" : ""}`}
      style={{ borderColor: highlight ? undefined : "var(--eb-gold-line)" }}
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <Crown className="w-4 h-4 shrink-0 text-[var(--eb-gold-text)]" strokeWidth={1.5} />
          <p className="eb-section-title truncate">{t.sectionTitle}</p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="mem-icon-btn -mr-2 shrink-0"
          aria-label={t.refresh}
          aria-busy={busy || undefined}
        >
          <RefreshCw className={`w-4 h-4 text-[var(--eb-text-2)] ${busy ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="p-3 rounded-[var(--eb-radius-sm)] space-y-1.5 bg-[var(--eb-surface-2)]">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[12px] text-[var(--eb-text-2)]">{t.stateLabel}</span>
          <span
            className={`text-sm font-semibold text-right ${
              entitled ? "text-[var(--eb-gold-text)]" : "text-[var(--eb-text)]"
            }`}
          >
            {label}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[12px] text-[var(--eb-text-2)]">{t.planLabel}</span>
          <span className="text-[12px] text-[var(--eb-text)] text-right">
            {status?.display_name ?? t.planStandard}
            {status?.price_krw_monthly
              ? ` · ${t.perMonth(status.price_krw_monthly)}`
              : ""}
          </span>
        </div>
        {status?.next_billing_date ? (
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[12px] text-[var(--eb-text-2)]">
              {status.status === "canceled" ? t.endsOn : t.nextBilling}
            </span>
            <span className="text-[12px] text-[var(--eb-text)] tabular-nums">
              {status.next_billing_date.slice(0, 10)}
            </span>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-2 pt-1 border-t border-[var(--eb-hairline)]">
          <span className="flex items-center gap-1.5 text-[12px] text-[var(--eb-text-2)]">
            <Coins className="w-3.5 h-3.5 text-[var(--eb-gold-text)]" aria-hidden />
            {t.beamCredits}
          </span>
          <span className="text-[12px] font-semibold text-[var(--eb-text)] tabular-nums">
            {walletBalance ?? t.stateUnknown}
          </span>
        </div>
      </div>

      {/* ── 결제 액션 (Toss) ─────────────────────────────────────────────── */}
      {configured ? (
        <div className="mt-3 space-y-2">
          {!entitled ? (
            <button
              type="button"
              disabled={action != null}
              onClick={() => void run("start", startMembershipCheckout)}
              aria-busy={action === "start" || action === "confirm" || undefined}
              className="eb-btn eb-btn--primary mem-btn-primary eb-btn--block text-sm"
            >
              {action === "start" ? t.starting : action === "confirm" ? t.confirming : t.startCta}
            </button>
          ) : billing?.billing?.cancel_at_period_end ? (
            <>
              <p className="eb-caption">
                {t.cancelScheduled(
                  (billing.billing.current_period_end ?? "").slice(0, 10) || "—"
                )}
              </p>
              <button
                type="button"
                disabled={action != null}
                onClick={() => void run("resume", resumeMembership)}
                aria-busy={action === "resume" || undefined}
                className="eb-btn eb-btn--secondary mem-btn-secondary eb-btn--block text-sm"
              >
                {t.resumeCta2}
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={action != null}
              onClick={() => void run("cancel", cancelMembership)}
              aria-busy={action === "cancel" || undefined}
              className="eb-btn eb-btn--ghost eb-btn--block text-sm"
            >
              {action === "cancel" ? t.canceling : t.cancelCta}
            </button>
          )}

          {testMode ? <p className="eb-caption">{t.testModeHint}</p> : null}
        </div>
      ) : (
        <p className="mt-3 eb-caption">{t.notConfigured}</p>
      )}

      {notice ? (
        <p className="mt-2 text-[12px] text-[var(--eb-sage-text)]">{notice}</p>
      ) : null}

      {/* 만료 불안을 여기서도 한 번 더 눌러 준다. */}
      <p className="mt-2 eb-caption">{t.assetsKeptHint}</p>

      {error ? (
        <p className="mt-1.5 eb-field-error" role="alert">
          {error}
        </p>
      ) : null}
    </motion.div>
  );
}
