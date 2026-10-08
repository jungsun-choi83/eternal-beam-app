"use client";

import { motion } from "framer-motion";
import { AlertCircle, Coins, Crown, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { useBehaviorLibrary } from "@/components/memorial/use-behavior-library";
import { canGenerateBehavior, type BehaviorItem } from "@/lib/behavior-library";

interface BehaviorLibraryProps {
  petId: string | null;
  enabled: boolean;
  language?: string;
  onOpenMembership?: () => void;
}

/**
 * Behavior Library — 활성 멤버가 프리미엄 행동을 **하나씩** 만드는 화면.
 *
 * 카드 한 장은 언제나 상태 하나만 보여 준다:
 *   MISSING    → [생성] 버튼
 *   GENERATING → 진행 표시 (버튼 없음)
 *   READY      → 완료 표시 (버튼 없음 — 재생성 경로가 존재하지 않는다)
 *
 * ⚠️ ON/OFF 선호는 아직 없다. READY 는 "만들어졌다"는 뜻이고, 실제 재생 여부는
 * 예전 그대로 스케줄러가 정한다 — 이 화면은 재생에 관여하지 않는다.
 */
export function BehaviorLibrary({
  petId,
  enabled,
  language = "ko",
  onOpenMembership,
}: BehaviorLibraryProps) {
  const t = memorialT(language).behaviors;
  const { state, loading, submitting, error, generate, refresh } = useBehaviorLibrary({
    petId,
    enabled,
  });

  if (!enabled) return null;
  const offerGroups = state.groups
    .map((group) => ({ ...group, items: group.items.filter((item) => item.status !== "ready") }))
    .filter((group) => group.items.length > 0);
  if (!loading && offerGroups.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="my-library__available"
    >
      <div className="my-library__available-head">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 shrink-0 text-[var(--eb-gold-text)]" />
            <h3 className="my-library__available-title">{t.availableTitle}</h3>
          </div>
          <p className="my-library__available-hint">{t.availableHint}</p>
        </div>
        <button type="button" className="my-library__pet-retry" onClick={() => void refresh()} aria-label={t.refresh}>
          <RefreshCw className={`w-4 h-4${loading ? " my-library__spin" : ""}`} />
        </button>
      </div>

      {offerGroups.map((group) =>
        group.items.length === 0 ? null : (
          <div key={group.id} className="my-library__available-group">
            <p className="my-library__available-group-label">
              {group.id === "spontaneous" ? t.groupSpontaneous : t.groupInteractive}
            </p>
            <ul className="my-library__available-list">
              {group.items.map((item) => (
                <BehaviorRow
                  key={item.id}
                  item={item}
                  label={t.name(item.id)}
                  actionable={canGenerateBehavior(item, state)}
                  busy={submitting === item.id}
                  disabled={submitting != null}
                  t={t}
                  onGenerate={() => void generate(item.id)}
                  onOpenMembership={onOpenMembership}
                />
              ))}
            </ul>
          </div>
        )
      )}

      {error ? (
        <div className="my-library__available-error">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <p>
            {error.code === "SUBSCRIPTION_REQUIRED"
              ? t.membershipRequired
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

function BehaviorRow({
  item,
  label,
  actionable,
  busy,
  disabled,
  t,
  onGenerate,
  onOpenMembership,
}: {
  item: BehaviorItem;
  label: string;
  actionable: boolean;
  busy: boolean;
  disabled: boolean;
  t: ReturnType<typeof memorialT>["behaviors"];
  onGenerate: () => void;
  onOpenMembership?: () => void;
}) {
  const isLocked = item.offerAccess === "locked";
  const accessLabel = item.offerAccess === "included"
    ? t.accessIncluded
    : item.offerAccess === "member"
      ? t.accessMember
      : item.offerAccess === "credit"
        ? item.priceCredits == null ? t.accessCredit : t.accessCredits(item.priceCredits)
        : t.accessLocked;

  return (
    <li className="my-library__available-row">
      <div className="my-library__available-copy">
        <span className="my-library__available-name">{label}</span>
        <span className={`my-library__access my-library__access--${item.offerAccess ?? "locked"}`}>
          {item.offerAccess === "credit" ? <Coins className="w-3 h-3" /> : item.offerAccess === "locked" ? <Crown className="w-3 h-3" /> : null}
          {accessLabel}
        </span>
      </div>

      {item.status === "generating" ? (
        <span className="my-library__available-generating">
          <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" />
          <span>{t.stateGenerating}<small>{t.generationKeepsGoing}</small></span>
        </span>
      ) : isLocked ? (
        <button type="button" onClick={onOpenMembership} className="eb-btn eb-btn--secondary my-library__available-cta">
          {t.manageMembership}
        </button>
      ) : (
        <button
          type="button"
          onClick={onGenerate}
          disabled={!actionable || disabled}
          className="eb-btn eb-btn--secondary my-library__available-cta"
        >
          {busy ? t.stateSubmitting : t.generate}
        </button>
      )}
    </li>
  );
}
