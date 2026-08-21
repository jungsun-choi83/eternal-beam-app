"use client";

import { motion } from "framer-motion";
import { Check, Crown, Lock, Sparkles } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { useMembership } from "@/components/memorial/use-membership";
import { keepsExistingAssets } from "@/lib/membership";

interface MembershipCardProps {
  /** BREATHING 자산이 실제로 있을 때만 켠다. */
  enabled: boolean;
  language?: string;
  /** 멤버십 화면(설정)으로 보낸다. */
  onOpenMembership?: () => void;
}

/**
 * Monthly Membership 카드 — 크레딧 UI 를 대체한다.
 *
 * 예전 UnlockFeaturesCard 는 가격·잔액·부족을 노출했다("2 크레딧으로 잠금 해제",
 * "보유 3", "크레딧이 부족합니다"). 소비자에게 지갑을 계산하게 하지 않는다:
 * 이제 상태는 **멤버인가 아닌가** 하나다.
 *
 * ⚠️ 여기서 모션을 고르거나 만들지 않는다. 행동별 선택(Behavior Library)은
 * 다음 단계다. 이 카드는 멤버십 상태만 보여 주고 가입 경로를 연다.
 *
 * ⚠️ 재생에 관여하지 않는다. 만료돼도 이미 만든 모션은 계속 재생되고
 * BREATHING 은 언제나 돈다 — 그 사실을 문구로 분명히 말해 준다.
 */
export function MembershipCard({
  enabled,
  language = "ko",
  onOpenMembership,
}: MembershipCardProps) {
  const t = memorialT(language).membership;
  const { state } = useMembership();

  if (!enabled) return null;

  const shell =
    "w-full max-w-[320px] rounded-2xl px-4 py-3.5 border backdrop-blur-sm text-left";

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

  // ── 이용 중 (active / 해지 유예) ──────────────────────────────────────────
  if (state.phase === "active" || state.phase === "grace") {
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
          <Crown className="w-4 h-4 shrink-0" style={{ color: "#d4af37" }} />
          <p className="text-sm font-medium" style={{ color: "#F1E5D1" }}>
            {t.activeTitle}
          </p>
        </div>
        <p className="mt-1.5 pl-6 text-xs memorial-body">
          {state.phase === "grace" ? t.graceHint : t.activeHint}
        </p>
        {state.readyCount > 0 ? (
          <div className="mt-2 flex items-center gap-1.5 pl-6">
            <Check className="w-3.5 h-3.5 shrink-0" style={{ color: "#d4af37" }} />
            <span className="text-[11px] memorial-body">
              {t.readyCount(state.readyCount)}
            </span>
          </div>
        ) : null}
      </motion.div>
    );
  }

  // ── 만료 / 미가입 ─────────────────────────────────────────────────────────
  const lapsed = state.phase === "lapsed";

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
          {lapsed ? t.lapsedTitle : t.joinTitle}
        </p>
      </div>

      <ul className="mt-2 space-y-1 pl-6">
        <li className="text-xs memorial-body">{t.benefitMotions}</li>
        <li className="text-xs memorial-body">{t.benefitComeCloser}</li>
      </ul>

      {/* 만료돼도 사라지지 않는다는 사실을 먼저 말한다 — 가장 큰 불안이다. */}
      <p className="mt-2 pl-6 text-[11px] memorial-body">
        {keepsExistingAssets(state) ? t.lapsedKeepsAssets(state.readyCount) : t.breathingFree}
      </p>

      {state.showJoinCta && onOpenMembership ? (
        <button
          type="button"
          onClick={onOpenMembership}
          className="mt-3 w-full py-2.5 rounded-xl text-[13px] font-medium tracking-wide"
          style={{
            background:
              "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            color: "#0a0a0a",
          }}
        >
          {lapsed ? t.resumeCta : t.joinCta}
        </button>
      ) : null}
    </motion.div>
  );
}
