import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('App Error:', error, errorInfo)
  }

  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div
          className="flex min-h-screen flex-col items-center justify-center p-8"
          style={{
            background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)',
            color: '#fff',
          }}
        >
          <h1 className="mb-4 text-2xl font-bold text-red-400">오류가 발생했습니다</h1>
          <pre className="mb-6 max-w-full overflow-auto rounded-lg bg-black/40 p-4 text-sm">
            {this.state.error.toString()}
          </pre>
          <pre className="mb-6 max-h-48 overflow-auto text-xs text-gray-400">
            {this.state.error.stack}
          </pre>
          <button
            onClick={() => window.location.reload()}
            className="rounded-lg bg-purple-600 px-6 py-2 font-semibold hover:bg-purple-500"
          >
            새로고침
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
