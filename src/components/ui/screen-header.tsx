"use client"

import type { ReactNode } from "react"
import { ArrowLeft } from "lucide-react"
import { MemorialIconButton } from "@/components/memorial/memorial-chrome"

interface BackButtonProps {
  onClick: () => void
  label?: string
  className?: string
}

/** 44px 이상 터치 타깃 — mem-icon-btn(40px) 대신 eb-back-btn 크기 보정을 얹는다. */
export function BackButton({ onClick, label = "뒤로", className = "" }: BackButtonProps) {
  return (
    <MemorialIconButton
      onClick={onClick}
      aria-label={label}
      className={`eb-back-btn ${className}`}
    >
      <ArrowLeft className="h-5 w-5" />
    </MemorialIconButton>
  )
}

interface ScreenHeaderProps {
  title?: ReactNode
  onBack?: () => void
  backLabel?: string
  right?: ReactNode
  className?: string
}

/**
 * 공통 화면 헤더. 기존 화면들의 개별 <header> 마크업을 대체하지 않는다 —
 * 새 화면이나 다시 손댈 화면부터 여기로 옮겨 온다.
 */
export function ScreenHeader({
  title,
  onBack,
  backLabel,
  right,
  className = "",
}: ScreenHeaderProps) {
  return (
    <header className={`eb-screen-header ${className}`}>
      <div className="eb-screen-header__leading">
        {onBack ? <BackButton onClick={onBack} label={backLabel} /> : null}
      </div>
      {title ? <p className="eb-screen-header__title screen-title">{title}</p> : null}
      <div className="eb-screen-header__trailing">{right}</div>
    </header>
  )
}
