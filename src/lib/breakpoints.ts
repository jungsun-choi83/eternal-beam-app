import { useEffect, useState } from 'react'

/**
 * 320–767 모바일, 768–1023 태블릿, 1024+ 데스크톱.
 * CSS 쪽 미디어쿼리(foundation.css)와 반드시 같은 값을 쓴다 — 어긋나면 JS 가
 * "데스크톱"이라 그린 화면을 CSS 는 아직 태블릿으로 본다.
 */
export const BREAKPOINTS = {
  tablet: 768,
  desktop: 1024,
} as const

export type BreakpointName = 'mobile' | 'tablet' | 'desktop'

function resolveBreakpoint(width: number): BreakpointName {
  if (width >= BREAKPOINTS.desktop) return 'desktop'
  if (width >= BREAKPOINTS.tablet) return 'tablet'
  return 'mobile'
}

export function useBreakpoint(): BreakpointName {
  const [breakpoint, setBreakpoint] = useState<BreakpointName>(() =>
    typeof window === 'undefined' ? 'mobile' : resolveBreakpoint(window.innerWidth),
  )

  useEffect(() => {
    if (typeof window === 'undefined') return
    const tabletQuery = window.matchMedia(`(min-width: ${BREAKPOINTS.tablet}px)`)
    const desktopQuery = window.matchMedia(`(min-width: ${BREAKPOINTS.desktop}px)`)
    const update = () => setBreakpoint(resolveBreakpoint(window.innerWidth))
    update()
    tabletQuery.addEventListener('change', update)
    desktopQuery.addEventListener('change', update)
    return () => {
      tabletQuery.removeEventListener('change', update)
      desktopQuery.removeEventListener('change', update)
    }
  }, [])

  return breakpoint
}

export function useIsDesktop(): boolean {
  return useBreakpoint() === 'desktop'
}
