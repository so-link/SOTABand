# 前端镜像 —— 多阶段构建：Node 编译 → Nginx 托管
#
# ## 为什么用 Nginx 而不是 vite preview / vite dev
#
# 生产形态下前端是**静态文件**。Nginx 同时承担两件事：
# 1. 托管 `dist/` 里的静态资源；
# 2. 把 `/api` 反代到后端 —— 这样前后端**同源**，Cookie 会话、SSE 都不需要处理跨域。
#
# 用 `vite preview` 也能跑，但它不适合长期运行，且仍要自己解决代理。

# ── 阶段 1：构建 ──────────────────────────────────────────────────────
# registry 前缀由 compose 传入（国内网络直连 Docker Hub 会超时，见根目录 Dockerfile 注释）
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/node:22-alpine AS build

WORKDIR /app

# 先只复制依赖清单 —— package.json 没变就复用 `npm ci` 这一层
COPY package.json package-lock.json ./
RUN npm ci

# 复制源码并构建（`npm run build` = `tsc -b && vite build`，类型错误会让构建失败）
COPY . .
RUN npm run build

# ── 阶段 2：运行 ──────────────────────────────────────────────────────
# 多阶段构建里每个 FROM 都要能解析 ARG，所以这里再声明一次
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
