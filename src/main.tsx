import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './app/App'
import { ErrorBoundary } from './app/ErrorBoundary'
import './styles/index.css'

const rootEl = document.getElementById('root')
if (!rootEl) {
  document.body.innerHTML =
    '<div style="padding: 2rem; font-family: sans-serif; color: red;">#root 요소를 찾을 수 없습니다. index.html을 확인하세요.</div>'
} else {
  createRoot(rootEl).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  )
}
