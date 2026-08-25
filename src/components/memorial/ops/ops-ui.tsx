"use client";

/**
 * Ops 워크스페이스의 시각 어휘 — **한 벌만 있다.**
 *
 * 예전 Ops 화면들은 각자 검정 배경에 흰 테두리를 직접 그렸다. 세 화면이 조금씩
 * 달랐고, 무엇보다 개발자 콘솔처럼 보였다 — 스태프가 하루 종일 보는 도구인데
 * 읽기 힘들고 어디를 눌러야 할지 알기 어려웠다.
 *
 * ── 방향 ────────────────────────────────────────────────────────────────────
 * 따뜻한 밝은 회색 바탕 · 흰 카드 · 차콜 글자 · 옅은 테두리 · 절제된 금색 강조.
 * 터미널 느낌, 과한 검정, 발광, 큰 그라데이션, 본문 모노스페이스를 쓰지 않는다.
 * 식별자처럼 **정확히 옮겨 적어야 하는 값에만** 모노스페이스를 남긴다.
 */

import type { ReactNode } from "react";

/** 색 토큰 — 여기서만 정의한다. 화면이 각자 헥스를 적으면 다시 갈라진다. */
export const OPS = {
  pageBg: "#F6F4F1",
  surface: "#FFFFFF",
  border: "#E4E0DA",
  borderStrong: "#D6D1C8",
  text: "#2A2724",
  textMuted: "#6E6862",
  textFaint: "#9A938B",
  gold: "#9A7B1F",
  goldSoft: "#F3EBD6",
} as const;

export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={`rounded-xl border bg-white ${padded ? "p-5" : ""} ${className}`}
      style={{ borderColor: OPS.border }}
    >
      {children}
    </section>
  );
}

export function CardTitle({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-[13px] font-semibold tracking-wide" style={{ color: OPS.text }}>
        {children}
      </h2>
      {action}
    </div>
  );
}

/**
 * 상태 알약. 색은 **의미**로 고른다 — 진행 중(중립) / 좋음 / 주의.
 * 무지개로 칠하지 않는다: 스태프가 색을 외워야 하면 그건 실패한 표시다.
 */
export type PillTone = "neutral" | "good" | "warn" | "gold";

const PILL: Record<PillTone, { bg: string; fg: string }> = {
  neutral: { bg: "#EFEDE9", fg: "#5C5650" },
  good: { bg: "#E6F1E9", fg: "#2F6B44" },
  warn: { bg: "#FBEEE3", fg: "#8A4B16" },
  gold: { bg: OPS.goldSoft, fg: OPS.gold },
};

export function Pill({ tone = "neutral", children }: { tone?: PillTone; children: ReactNode }) {
  const c = PILL[tone];
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-[3px] text-[11px] font-medium whitespace-nowrap"
      style={{ background: c.bg, color: c.fg }}
    >
      {children}
    </span>
  );
}

/** 라벨 + 값 한 줄. 값이 없으면 조용히 — 로 둔다(빈 칸이 더 헷갈린다). */
export function Field({
  label,
  value,
  mono = false,
  children,
}: {
  label: string;
  value?: ReactNode;
  mono?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 py-2">
      <dt className="text-[11px] uppercase tracking-wider" style={{ color: OPS.textFaint }}>
        {label}
      </dt>
      <dd
        className={`text-[13px] break-words ${mono ? "font-mono text-[12px]" : ""}`}
        style={{ color: OPS.text }}
      >
        {children ?? (value === undefined || value === null || value === "" ? "—" : value)}
      </dd>
    </div>
  );
}

export function FieldGrid({ children, cols = 2 }: { children: ReactNode; cols?: number }) {
  return (
    <dl
      className="grid gap-x-6"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {children}
    </dl>
  );
}

type ButtonTone = "primary" | "default" | "quiet" | "danger";

const BUTTON: Record<ButtonTone, string> = {
  primary: "text-white",
  default: "",
  quiet: "",
  danger: "",
};

export function Button({
  children,
  onClick,
  disabled = false,
  busy = false,
  tone = "default",
  type = "button",
  size = "md",
  full = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  busy?: boolean;
  tone?: ButtonTone;
  type?: "button" | "submit";
  size?: "sm" | "md";
  full?: boolean;
}) {
  const pad = size === "sm" ? "px-2.5 py-1.5 text-[12px]" : "px-3.5 py-2 text-[13px]";
  const style =
    tone === "primary"
      ? { background: OPS.gold, borderColor: OPS.gold, color: "#fff" }
      : tone === "danger"
        ? { background: "#fff", borderColor: "#E0C9C0", color: "#8A3A22" }
        : tone === "quiet"
          ? { background: "transparent", borderColor: "transparent", color: OPS.textMuted }
          : { background: "#fff", borderColor: OPS.borderStrong, color: OPS.text };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      className={`rounded-lg border font-medium transition-opacity disabled:opacity-45 ${pad} ${
        full ? "w-full" : ""
      } ${BUTTON[tone]}`}
      style={style}
    >
      {busy ? "처리 중…" : children}
    </button>
  );
}

export function TextInput({
  value,
  onChange,
  placeholder,
  onEnter,
  mono = false,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  onEnter?: () => void;
  mono?: boolean;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && onEnter) onEnter();
      }}
      placeholder={placeholder}
      className={`w-full rounded-lg border px-3 py-2 text-[13px] outline-none ${
        mono ? "font-mono text-[12px]" : ""
      }`}
      style={{ borderColor: OPS.borderStrong, background: "#fff", color: OPS.text }}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg border px-3 py-2 text-[13px] outline-none"
      style={{ borderColor: OPS.borderStrong, background: "#fff", color: OPS.text }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="py-8 text-center text-[13px]" style={{ color: OPS.textFaint }}>
      {children}
    </p>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p
      className="mb-4 rounded-lg border px-3 py-2 text-[12px]"
      style={{ background: "#FCF3EF", borderColor: "#E8D3C9", color: "#8A3A22" }}
    >
      {children}
    </p>
  );
}

/**
 * 기술 세부(내부 id 등)를 접어 둔다.
 *
 * 스태프의 일상 화면에 내부 식별자를 크게 띄우지 않는다 — 필요할 때만 연다.
 * 지우지는 않는다: 장애를 진단할 때 그 값이 유일한 단서인 경우가 있다.
 */
export function TechnicalDetails({
  children,
  label = "기술 정보",
}: {
  children: ReactNode;
  label?: string;
}) {
  return (
    <details className="mt-3">
      <summary
        className="cursor-pointer text-[12px] select-none"
        style={{ color: OPS.textFaint }}
      >
        {label}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

/** 결제/생산/배송 상태 → 알약 톤. 세 축이 같은 규칙을 쓴다. */
export function statusTone(kind: "payment" | "production" | "shipping", value: string): PillTone {
  const v = (value || "").toLowerCase();
  if (kind === "payment") return v === "paid" ? "good" : v === "failed" ? "warn" : "neutral";
  if (kind === "production") {
    if (v === "produced") return "good";
    if (v === "pending") return "warn";
    return "neutral";
  }
  if (v === "delivered") return "good";
  if (v === "shipped") return "neutral";
  return "warn";
}

export const STATUS_LABEL: Record<string, string> = {
  paid: "결제됨",
  pending: "대기",
  failed: "실패",
  ready: "준비됨",
  in_production: "제작 중",
  produced: "제작 완료",
  shipped: "배송 중",
  delivered: "배송 완료",
};

export function statusText(value: string): string {
  return STATUS_LABEL[(value || "").toLowerCase()] ?? value ?? "—";
}
