#!/bin/bash
# SOTABand Engine 启动脚本

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/home/jmlv/.conda/envs/python310/bin/python"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

echo "========================================"
echo "  SOTABand Engine"
echo "========================================"

# ── 后端 ──
echo ""
echo "[1/2] 启动后端 (port $BACKEND_PORT)..."

fuser -k ${BACKEND_PORT}/tcp 2>/dev/null || true

cd "$DIR"
$PYTHON -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port $BACKEND_PORT \
    --app-dir "$DIR" \
    > /tmp/maia-backend.log 2>&1 &

sleep 3

if curl -s http://localhost:${BACKEND_PORT}/api/health > /dev/null 2>&1; then
    echo "  ✅ 后端运行中: http://localhost:${BACKEND_PORT}"
else
    echo "  ❌ 后端启动失败，查看日志: tail /tmp/maia-backend.log"
    exit 1
fi

# ── 前端 ──
echo ""
echo "[2/2] 启动前端 (port $FRONTEND_PORT)..."

fuser -k ${FRONTEND_PORT}/tcp 2>/dev/null || true

cd "$DIR/frontend"
npx vite --host 0.0.0.0 --port $FRONTEND_PORT \
    > /tmp/maia-frontend.log 2>&1 &

sleep 4

if curl -s -o /dev/null -w "%{http_code}" http://localhost:${FRONTEND_PORT} | grep -q 200; then
    echo "  ✅ 前端运行中: http://localhost:${FRONTEND_PORT}"
else
    echo "  ❌ 前端启动失败，查看日志: tail /tmp/maia-frontend.log"
    exit 1
fi

echo ""
echo "========================================"
echo "  访问地址: http://localhost:${FRONTEND_PORT}"
echo "  API 地址: http://localhost:${BACKEND_PORT}"
echo "========================================"
