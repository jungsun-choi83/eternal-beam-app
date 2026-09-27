"use client"

import type { ReactNode } from "react"
import { motion, type HTMLMotionProps } from "framer-motion"

export type EbButtonState = "default" | "loading" | "success" | "error"
export type EbButtonVariant = "primary" | "secondary" | "ghost" | "destructive"

interface EbButtonProps extends HTMLMotionProps<"button"> {
  children: ReactNode
  state?: EbButtonState
  /** Full-width block button (default for primary/secondary in screen footers). */
  block?: boolean
}

const VARIANT_CLASS: Record<EbButtonVariant, string> = {
  // Legacy class names stay on the element so older CSS hooks (tests, layout
  // rules such as .pet-intake__footer-inner .mem-btn-primary) keep matching.
  primary: "eb-btn eb-btn--primary mem-btn-primary",
  secondary: "eb-btn eb-btn--secondary mem-btn-secondary",
  ghost: "eb-btn eb-btn--ghost",
  destructive: "eb-btn eb-btn--destructive",
}

function stateClass(state: EbButtonState): string {
  if (state === "error") return "eb-btn--error"
  if (state === "success") return "eb-btn--success"
  return ""
}

/**
 * One button system (Phase 10): primary / secondary / ghost / destructive,
 * each with loading · success · error · disabled states. Visuals live in
 * brand.css; this component only names the variant and wires the state.
 */
export function EbButton({
  children,
  className = "",
  disabled,
  state = "default",
  variant = "primary",
  block = false,
  ...props
}: EbButtonProps & { variant?: EbButtonVariant }) {
  const isDisabled = disabled || state === "loading"
  return (
    <motion.button
      type="button"
      disabled={isDisabled}
      aria-busy={state === "loading" || undefined}
      data-state={state !== "default" ? state : undefined}
      className={`${VARIANT_CLASS[variant]} ${stateClass(state)} ${block ? "eb-btn--block" : ""} ${className}`}
      whileTap={isDisabled ? undefined : { scale: 0.98 }}
      {...props}
    >
      {state === "loading" ? <span className="eb-btn__spinner" aria-hidden /> : null}
      <span>{children}</span>
    </motion.button>
  )
}

/** 기존 mem-btn-primary 텍스처를 그대로 쓴다 — 새 버튼을 만드는 게 아니라 이름을 준다. */
export function PrimaryButton(props: EbButtonProps) {
  return <EbButton variant="primary" {...props} />
}

export function SecondaryButton(props: EbButtonProps) {
  return <EbButton variant="secondary" {...props} />
}

export function GhostButton(props: EbButtonProps) {
  return <EbButton variant="ghost" {...props} />
}

export function DestructiveButton(props: EbButtonProps) {
  return <EbButton variant="destructive" {...props} />
}
