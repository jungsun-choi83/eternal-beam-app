import type { ReactNode } from "react"

/**
 * Every product state has one visual (brand.css .eb-status-badge--*):
 *   loading · waiting · generating · connected · offline · success · warning ·
 *   error · locked · owned · premium, plus the generic info / neutral.
 */
export type StatusTone =
  | "neutral"
  | "info"
  | "loading"
  | "waiting"
  | "generating"
  | "connected"
  | "offline"
  | "success"
  | "warning"
  | "error"
  | "locked"
  | "owned"
  | "premium"

interface StatusBadgeProps {
  tone?: StatusTone
  children: ReactNode
  className?: string
}

export function StatusBadge({ tone = "neutral", children, className = "" }: StatusBadgeProps) {
  return (
    <span className={`eb-status-badge eb-status-badge--${tone} ${className}`}>{children}</span>
  )
}

interface StatusDotProps {
  tone?: StatusTone
  /** Live states (generating / waiting / reconnecting) pulse gently. */
  pulse?: boolean
  className?: string
}

export function StatusDot({ tone = "neutral", pulse = false, className = "" }: StatusDotProps) {
  return (
    <span
      className={`eb-status-dot eb-status-dot--${tone} ${pulse ? "eb-status-dot--pulse" : ""} ${className}`}
      aria-hidden
    />
  )
}
