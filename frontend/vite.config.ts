import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'fs'
import path from 'path'

// 端口等配置与后端共用仓库根目录的 .env（SOTABAND_API_PORT / SOTABAND_WEB_PORT）。
// serve.py 会通过环境变量注入同值，这里再读 .env 保证裸跑 `npm run dev` 也一致。
function loadDotEnv(): Record<string, string> {
  const out: Record<string, string> = {}
  const envFile = path.resolve(__dirname, '../.env')
  if (!fs.existsSync(envFile)) return out
  for (const line of fs.readFileSync(envFile, 'utf-8').split(/\r?\n/)) {
    const t = line.trim()
    if (!t || t.startsWith('#') || !t.includes('=')) continue
    const i = t.indexOf('=')
    const key = t.slice(0, i).trim()
    const val = t.slice(i + 1).trim().replace(/^["']|["']$/g, '')
    if (key) out[key] = val
  }
  return out
}

const dotEnv = loadDotEnv()
const env = { ...dotEnv, ...process.env }
const apiPort = env.SOTABAND_API_PORT || '8201'
const webPort = Number(env.SOTABAND_WEB_PORT || 8200)
const host = env.SOTABAND_WEB_HOST || '127.0.0.1'
const proxy = {
  '/api': {
    target: `http://127.0.0.1:${apiPort}`,
    changeOrigin: true,
    // 透传浏览器侧原始 Host（X-Forwarded-Host），供后端做来源校验：
    // 通过 IDE/预览域名访问时，Origin 与后端 Host 不同，需要该头才能判定同源。
    xfwd: true,
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host,
    port: webPort,
    strictPort: true,
    allowedHosts: true,
    proxy,
    watch: {
      // 系统 inotify 上限极低(max_user_watches=8192)且无 root 权限提升，
      // 改为轮询并忽略 node_modules，避免 "ENOSPC: file watchers reached"
      usePolling: true,
      interval: 1000,
      ignored: ['**/node_modules/**', '**/.git/**', '**/dist/**', '**/output/**'],
    },
  },
  preview: {
    host,
    port: webPort,
    strictPort: true,
    allowedHosts: true,
    proxy,
  },
})
