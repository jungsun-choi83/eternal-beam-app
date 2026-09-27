import type { ReactNode } from "react"
import { SecondaryButton } from "@/components/ui/buttons"

interface LoadingStateProps {
  label?: ReactNode
  className?: string
}

export function LoadingState({ label = "불러오는 중…", className = "" }: LoadingStateProps) {
  return (
    <div className={`eb-feedback-state ${className}`} role="status" aria-live="polite">
      <span className="eb-spinner" aria-hidden />
      <p className="eb-feedback-state__label">{label}</p>
    </div>
  )
}

interface ErrorStateProps {
  title?: ReactNode
  description?: ReactNode
  onRetry?: () => void
  retryLabel?: string
  className?: string
}

export function ErrorState({
  title = "문제가 발생했어요",
  description,
  onRetry,
  retryLabel = "다시 시도",
  className = "",
}: ErrorStateProps) {
  return (
    <div className={`eb-feedback-state eb-feedback-state--error ${className}`} role="alert">
      <p className="eb-feedback-state__title">{title}</p>
      {description ? <p className="eb-feedback-state__description">{description}</p> : null}
      {onRetry ? (
        <SecondaryButton className="eb-feedback-state__retry" onClick={onRetry}>
          {retryLabel}
        </SecondaryButton>
      ) : null}
    </div>
  )
}
