import { useEffect, useState, type ReactNode } from 'react'

interface FadeTransitionProps {
  children: ReactNode
  key?: string
  className?: string
}

export function FadeTransition({ children, key: keyProp, className = '' }: FadeTransitionProps) {
  const [mounted, setMounted] = useState(false)
  const [hasAnimated, setHasAnimated] = useState(false)

  useEffect(() => {
    if (hasAnimated) {
      setMounted(false)
      const t = requestAnimationFrame(() => {
        requestAnimationFrame(() => setMounted(true))
      })
      return () => cancelAnimationFrame(t)
    }
    setMounted(true)
    setHasAnimated(true)
    return () => {}
  }, [keyProp])

  return (
    <div
      className={`transition-opacity duration-300 ${className}`}
      style={{ opacity: mounted ? 1 : hasAnimated ? 0 : 1 }}
    >
      {children}
    </div>
  )
}
