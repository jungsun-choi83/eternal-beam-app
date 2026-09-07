import { defineConfig } from 'vite'
import path from 'path'
import { fileURLToPath } from 'url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    strictPort: false,
    host: true,
    proxy: {
      '/api': {
        // 8000 에 죽은 핸들이 남아 바인딩이 막힐 때가 있어 로컬은 8001 사용
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/outputs': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/output': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
