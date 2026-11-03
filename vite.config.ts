import { defineConfig, loadEnv } from 'vite'
import path from 'path'
import { fileURLToPath } from 'url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Preserve the existing dev backend by default. Passport/local-preview can
  // opt in to another backend without changing other developers' setup.
  const devProxyTarget = (env.VITE_DEV_API_PROXY_TARGET || 'http://127.0.0.1:8001').replace(/\/+$/, '')

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5174,
      strictPort: false,
      host: true,
      proxy: {
        '/api': {
          target: devProxyTarget,
          changeOrigin: true,
        },
        '/outputs': {
          target: devProxyTarget,
          changeOrigin: true,
        },
        '/output': {
          target: devProxyTarget,
          changeOrigin: true,
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  }
})
