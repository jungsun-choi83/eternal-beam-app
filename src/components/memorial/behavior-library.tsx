"use client";

import { motion } from "framer-motion";
import { AlertCircle, Check, Loader2, Sparkles } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { useBehaviorLibrary } from "@/components/memorial/use-behavior-library";
import {
  canGenerateBehavior,
  canToggleBehavior,
  type BehaviorItem,
} from "@/lib/behavior-library";

interface BehaviorLibraryProps {
  petId: string | null;
  enabled: boolean;
  language?: string;
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
}: BehaviorLibraryProps) {
  const t = memorialT(language).behaviors;
  const { state, submitting, toggling, error, generate, toggle } = useBehaviorLibrary({
    petId,
    enabled,
  });

  // 멤버가 아니면 목록 자체를 보여 주지 않는다 — 멤버십 카드가 이미 안내한다.
  // state.canGenerate 는 서버가 준 entitled 그대로다.
  if (!enabled || !state.canGenerate) return null;
  if (state.totalCount === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full max-w-[320px] rounded-2xl px-4 py-3.5 border backdrop-blur-sm text-left"
      style={{
        background: "rgba(255,255,255,0.05)",
        borderColor: "rgba(201, 162, 39, 0.22)",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 shrink-0" style={{ color: "#d4af37" }} />
          <p className="text-sm font-medium" style={{ color: "#F1E5D1" }}>
            {t.title}
          </p>
        </div>
        <span className="text-[11px]" style={{ color: "#8a8a8a" }}>
          {t.readyOf(state.readyCount, state.totalCount)}
        </span>
      </div>

      {state.groups.map((group) =>
        group.items.length === 0 ? null : (
          <div key={group.id} className="mt-3">
            <p
              className="text-[10px] tracking-wider uppercase mb-1.5"
              style={{ color: "#8a8a8a" }}
            >
              {group.id === "spontaneous" ? t.groupSpontaneous : t.groupInteractive}
            </p>
            <ul className="space-y-1.5">
              {group.items.map((item) => (
                <BehaviorRow
                  key={item.id}
                  item={item}
                  label={t.name(item.id)}
                  actionable={canGenerateBehavior(item, state)}
                  busy={submitting === item.id}
                  disabled={submitting != null}
                  toggling={toggling === item.id}
                  t={t}
                  onGenerate={() => void generate(item.id)}
                  onToggle={() => void toggle(item.id, !item.enabled)}
                />
              ))}
            </ul>
          </div>
        )
      )}

      {error ? (
        <div className="mt-2.5 flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: "#e0a0a0" }} />
          <p className="text-[11px]" style={{ color: "#e0a0a0" }}>
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
  toggling,
  t,
  onGenerate,
  onToggle,
}: {
  item: BehaviorItem;
  label: string;
  actionable: boolean;
  busy: boolean;
  disabled: boolean;
  toggling: boolean;
  t: ReturnType<typeof memorialT>["behaviors"];
  onGenerate: () => void;
  onToggle: () => void;
}) {
  return (
    <li className="flex items-center justify-between gap-2">
      <span className="text-xs memorial-body">{label}</span>

      {/* 상태 하나당 표시 하나.
          READY   → 상태 라벨 + ON/OFF (재생성 버튼은 여전히 **없다**)
          그 외   → 상태 표시 또는 [생성]. 토글은 노출하지 않는다. */}
      {item.status === "ready" ? (
        <span className="flex items-center gap-2">
          <span className="flex items-center gap-1 text-[11px]" style={{ color: "#d4af37" }}>
            <Check className="w-3.5 h-3.5 shrink-0" />
            {t.stateReady}
          </span>
          {canToggleBehavior(item) ? (
            <button
              type="button"
              role="switch"
              aria-checked={item.enabled}
              aria-label={t.toggleLabel(label)}
              onClick={onToggle}
              disabled={toggling}
              className="relative w-9 h-5 rounded-full transition-colors disabled:opacity-50"
              style={{
                background: item.enabled ? "rgba(201,162,39,0.55)" : "rgba(255,255,255,0.16)",
                border: "1px solid rgba(201,162,39,0.35)",
              }}
            >
              <span
                className="absolute top-[2px] w-3.5 h-3.5 rounded-full transition-all"
                style={{
                  left: item.enabled ? "18px" : "2px",
                  background: item.enabled ? "#F1E5D1" : "#9a9a9a",
                }}
              />
            </button>
          ) : null}
        </span>
      ) : item.status === "generating" ? (
        <span className="flex items-center gap-1 text-[11px]" style={{ color: "#A1A1A6" }}>
          <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" />
          {t.stateGenerating}
        </span>
      ) : (
        <button
          type="button"
          onClick={onGenerate}
          disabled={!actionable || disabled}
          className="px-3 py-1 rounded-lg text-[11px] font-medium disabled:opacity-45"
          style={{
            background: "rgba(201, 162, 39, 0.16)",
            border: "1px solid rgba(201, 162, 39, 0.45)",
            color: "#F1E5D1",
          }}
        >
          {busy ? t.stateSubmitting : t.generate}
        </button>
      )}
    </li>
  );
}
