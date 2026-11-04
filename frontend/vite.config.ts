import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const apiPort = Number(process.env.SOTABAND_API_PORT || 8002)
const frontendPort = Number(process.env.SOTABAND_FRONTEND_PORT || 5174)

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: frontendPort,
    proxy: {
      '/api': {
        target: `http://localhost:${apiPort}`,
        changeOrigin: true,
      },
    },
    watch: {
      // 系统 inotify 上限极低(max_user_watches=8192)且无 root 权限提升，
      // 改为轮询并忽略 node_modules，避免 "ENOSPC: file watchers reached"
      usePolling: true,
      interval: 1000,
      ignored: ['**/node_modules/**', '**/.git/**', '**/dist/**', '**/output/**'],
    },
  },
})
