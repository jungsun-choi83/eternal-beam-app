import type { ReactNode } from 'react'

interface ResponsiveContainerProps {
  children: ReactNode
  className?: string
}

/** 화면 콘텐츠 폭 — 모바일/태블릿은 그대로, 데스크톱만 --eb-app-max-width 로 좁힌다. */
export function ResponsiveContainer({ children, className = '' }: ResponsiveContainerProps) {
  return <div className={`eb-container ${className}`}>{children}</div>
}
