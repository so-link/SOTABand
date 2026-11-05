import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// 默认与主流程 / docker-compose 保持一致（前端 5173、后端 8001）。
// 历史：hypoxemia 内置运行时为了在「主实例仍开着」时并行跑第二套后端+前端，
// 曾把默认值改成 5174/8002；结果主流程 `npm run dev`（不带环境变量）也会
// 代理到没人监听的 8002，页面所有 /api 请求 502。现在默认值改回主流程，
// 需要并行实例时按 hypoxemia 文档显式传参：
//   SOTABAND_API_PORT=8002 npm run dev -- --port 5174 --strictPort
const apiPort = Number(process.env.SOTABAND_API_PORT || 8001)
const frontendPort = Number(process.env.SOTABAND_FRONTEND_PORT || 5173)

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
