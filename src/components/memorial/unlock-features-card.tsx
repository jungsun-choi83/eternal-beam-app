"use client";

import { motion } from "framer-motion";
import { Lock, Sparkles, Check, Loader2, AlertCircle } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { usePremiumUnlock } from "@/components/memorial/use-premium-unlock";

interface UnlockFeaturesCardProps {
  petId: string | null;
  petImageUrl?: string | null;
  /** BREATH 자산이 실제로 있을 때만 켠다 — 없으면 보여 줄 이유가 없다. */
  enabled: boolean;
  language?: string;
  /** 잔액 부족일 때 노출되는 "크레딧 받기" — 설정의 크레딧 섹션으로 보낸다. */
  onGetCredits?: () => void;
}

/**
 * "Unlock More Features" — 하나의 잠금 해제로 보이는 UI.
 *
 * 뒤에는 독립된 두 구매(IDLE_BUNDLE / ACTION:COME_CLOSER)가 있지만 사용자에게는
 * 기술적 분할을 노출하지 않는다. 다만 **가격은 실제로 필요한 만큼만** 보여 준다:
 * 한쪽을 이미 갖고 있으면 1 크레딧, 둘 다 있으면 CTA 자체가 사라진다.
 *
 * ⚠️ 이 컴포넌트는 재생에 관여하지 않는다. 재생 가능 여부는 언제나 READY 자산이
 * 정하고(스케줄러·decideTrigger), 여기서 지갑을 보는 일은 **구매 시점뿐**이다.
 */
export function UnlockFeaturesCard({
  petId,
  petImageUrl,
  enabled,
  language = "ko",
  onGetCredits,
}: UnlockFeaturesCardProps) {
  const t = memorialT(language).unlock;
  const { state, purchasing, error, purchase } = usePremiumUnlock({
    petId,
    petImageUrl,
    enabled,
  });

  if (!enabled) return null;

  const shell =
    "w-full max-w-[320px] rounded-2xl px-4 py-3.5 border backdrop-blur-sm text-left";

  // ── 이미 전부 준비됨 — CTA 없음 ────────────────────────────────────────────
  if (state.phase === "unlocked") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className={shell}
        style={{
          background: "rgba(201, 162, 39, 0.08)",
          borderColor: "rgba(201, 162, 39, 0.28)",
        }}
      >
        <div className="flex items-center gap-2">
          <Check className="w-4 h-4 shrink-0" style={{ color: "#d4af37" }} />
          <p className="text-sm font-medium" style={{ color: "#F1E5D1" }}>
            {t.unlockedTitle}
          </p>
        </div>
        <ul className="mt-2 space-y-1 pl-6">
          <li className="text-xs memorial-body">{t.unlockedIdle}</li>
          <li className="text-xs memorial-body">{t.unlockedComeCloser}</li>
        </ul>
      </motion.div>
    );
  }

  // ── 생성 중 — BREATHING 은 계속 재생된다 ──────────────────────────────────
  if (state.phase === "unlocking") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className={shell}
        style={{
          background: "rgba(255,255,255,0.05)",
          borderColor: "rgba(255,255,255,0.14)",
        }}
      >
        <div className="flex items-center gap-2">
          <Loader2 className="w-4 h-4 shrink-0 animate-spin" style={{ color: "#d4af37" }} />
          <p className="text-sm font-medium" style={{ color: "#F1E5D1" }}>
            {t.unlockingTitle}
          </p>
        </div>
        <p className="mt-1.5 pl-6 text-xs memorial-body">{t.unlockingHint}</p>
      </motion.div>
    );
  }

  // ── 로그인 필요 ───────────────────────────────────────────────────────────
  if (state.phase === "signed-out") {
    return (
      <div
        className={shell}
        style={{
          background: "rgba(255,255,255,0.04)",
          borderColor: "rgba(255,255,255,0.12)",
        }}
      >
        <div className="flex items-center gap-2">
          <Lock className="w-4 h-4 shrink-0" style={{ color: "#9a9a9a" }} />
          <p className="text-sm" style={{ color: "#E2E2E2" }}>
            {t.signInRequired}
          </p>
        </div>
      </div>
    );
  }

  const insufficient = state.phase === "insufficient-credits";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={shell}
      style={{
        background: "rgba(255,255,255,0.05)",
        borderColor: "rgba(201, 162, 39, 0.22)",
      }}
    >
      <div className="flex items-center gap-2">
        <Sparkles className="w-4 h-4 shrink-0" style={{ color: "#d4af37" }} />
        <p className="text-sm font-medium" style={{ color: "#F1E5D1" }}>
          {t.title}
        </p>
      </div>

      <ul className="mt-2 space-y-1 pl-6">
        <li className="text-xs memorial-body">{t.benefitIdle}</li>
        <li className="text-xs memorial-body">{t.benefitComeCloser}</li>
      </ul>

      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs" style={{ color: "#c9a227" }}>
          {t.cost(state.requiredCredits)}
        </span>
        {state.balance != null ? (
          <span className="text-[11px]" style={{ color: "#8a8a8a" }}>
            {t.balance(state.balance)}
          </span>
        ) : null}
      </div>

      <button
        type="button"
        onClick={() => void purchase()}
        disabled={purchasing || insufficient}
        className="mt-3 w-full py-2.5 rounded-xl text-[13px] font-medium tracking-wide disabled:opacity-45"
        style={{
          background: insufficient
            ? "rgba(255,255,255,0.08)"
            : "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
          color: insufficient ? "#c9c9c9" : "#0a0a0a",
        }}
      >
        {purchasing
          ? t.purchasing
          : insufficient
            ? t.notEnough
            : t.cta(state.requiredCredits)}
      </button>

      {/* 잔액이 모자라면 잠금 해제는 **비활성으로 남기고** 충전 경로를 연다.
          예전에는 "설정에서 충전하세요" 라는 안내만 있고 실제 충전 UI 가 없었다. */}
      {insufficient && onGetCredits ? (
        <button
          type="button"
          onClick={onGetCredits}
          className="mt-2 w-full py-2.5 rounded-xl text-[13px] font-medium tracking-wide"
          style={{
            background: "rgba(201, 162, 39, 0.16)",
            border: "1px solid rgba(201, 162, 39, 0.45)",
            color: "#F1E5D1",
          }}
        >
          {t.getCredits}
        </button>
      ) : null}

      {insufficient ? (
        <p className="mt-2 text-[11px] memorial-body">{t.topUpHint}</p>
      ) : null}

      {error && error.code !== "INSUFFICIENT_CREDITS" ? (
        <div className="mt-2 flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: "#e0a0a0" }} />
          <p className="text-[11px]" style={{ color: "#e0a0a0" }}>
            {error.code === "PARTIAL"
              ? t.partialFailure
              : error.code === "UNAUTHENTICATED"
                ? t.signInRequired
                : error.code === "PET_NOT_OWNED"
                  ? t.notYourPet
                  : t.unavailable}
          </p>
        </div>
      ) : null}
    </motion.div>
  );
}
