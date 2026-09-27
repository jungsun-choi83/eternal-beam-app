import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import App from './app/App'
import { ErrorBoundary } from './app/ErrorBoundary'
import './styles/index.css'
import { applyUILiteClass } from './lib/ui-performance'

applyUILiteClass()

const rootEl = document.getElementById('root')
if (!rootEl) {
  document.body.innerHTML =
    '<div style="padding: 2rem; font-family: sans-serif; color: #963f2b;">#root 요소를 찾을 수 없습니다. index.html을 확인하세요.</div>'
} else {
  createRoot(rootEl).render(
    <StrictMode>
      {/* Phase 10 — framer-motion honours the OS reduced-motion preference. */}
      <MotionConfig reducedMotion="user">
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </MotionConfig>
    </StrictMode>,
  )
}
