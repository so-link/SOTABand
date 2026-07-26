#!/bin/bash
# SOTABand Engine 一键启动脚本
# 用法:
#   ./run.sh              # 后台启动后端+前端
#   ./run.sh --foreground # 前台启动（终端显示日志）
#   ./run.sh --stop       # 停止所有服务
#   ./run.sh --restart    # 重启所有服务
#   ./run.sh --backend    # 仅启动后端
#   ./run.sh --frontend   # 仅启动前端
#   ./run.sh --check      # 检查工具运行环境

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
VENV_DIR="$DIR/venv"
BACKEND_LOG="/tmp/sotaband-backend.log"
FRONTEND_LOG="/tmp/sotaband-frontend.log"
MODE="${1:-start}"

# ── 颜色输出 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  SOTABand Engine（优智联邦）${NC}"
echo -e "${CYAN}========================================${NC}"

# ── 检测 Python 环境 ──
detect_python() {
    # 优先使用项目虚拟环境
    if [ -f "$VENV_DIR/bin/python" ]; then
        echo "$VENV_DIR/bin/python"
        return
    fi
    # 尝试系统 Python
    for py in python3.12 python3.11 python3.10 python3; do
        if command -v $py &>/dev/null; then
            echo "$(command -v $py)"
            return
        fi
    done
    echo ""
}

PYTHON=$(detect_python)
if [ -z "$PYTHON" ]; then
    echo -e "${RED}❌ 未找到 Python 3.10+，请先安装 Python${NC}"
    exit 1
fi

# ── 检测 Node.js ──
detect_node() {
    for node in node npm npx; do
        if ! command -v $node &>/dev/null; then
            echo ""
            return
        fi
    done
    echo "ok"
}

NODE_OK=$(detect_node)
if [ -z "$NODE_OK" ]; then
    echo -e "${YELLOW}⚠️  未检测到 Node.js/npm，前端无法启动${NC}"
    echo -e "${YELLOW}   安装: brew install node${NC}"
fi

# ── 检查 .env ──
if [ ! -f "$DIR/.env" ]; then
    echo -e "${YELLOW}⚠️  .env 文件不存在，正在创建...${NC}"
    if [ -f "$DIR/.env.example" ]; then
        cp "$DIR/.env.example" "$DIR/.env"
        echo -e "${YELLOW}   已从 .env.example 创建 .env，请编辑填入 DEEPSEEK_API_KEY${NC}"
    else
        echo "DEEPSEEK_API_KEY=sk-your-key-here" > "$DIR/.env"
        echo -e "${YELLOW}   已创建 .env，请编辑填入 DEEPSEEK_API_KEY${NC}"
    fi
fi

# ── 创建虚拟环境（如果不存在）──
setup_venv() {
    if [ ! -f "$VENV_DIR/bin/python" ]; then
        echo -e "${YELLOW}📦 创建虚拟环境...${NC}"
        $PYTHON -m venv "$VENV_DIR"
        echo -e "${GREEN}✅ 虚拟环境已创建${NC}"
    fi

    # 激活虚拟环境
    source "$VENV_DIR/bin/activate"

    # 检查关键依赖
    if ! python -c "import fastapi" 2>/dev/null; then
        echo -e "${YELLOW}📦 安装 Python 依赖...${NC}"
        pip install -e ".[dev]" -q
        echo -e "${GREEN}✅ Python 依赖已安装${NC}"
    fi
}

# ── 检查前端依赖 ──
setup_frontend() {
    if [ -z "$NODE_OK" ]; then
        return 1
    fi
    if [ ! -d "$DIR/frontend/node_modules" ]; then
        echo -e "${YELLOW}📦 安装前端依赖...${NC}"
        cd "$DIR/frontend"
        npm install --silent
        cd "$DIR"
        echo -e "${GREEN}✅ 前端依赖已安装${NC}"
    fi
    return 0
}

# ── 检查工具环境 ──
check_tools() {
    echo ""
    echo -e "${CYAN}[检查工具环境]${NC}"
    if [ -f "$DIR/scripts/check_tool_env.py" ]; then
        source "$VENV_DIR/bin/activate"
        python "$DIR/scripts/check_tool_env.py" --auto
    else
        echo -e "${YELLOW}⚠️  check_tool_env.py 不存在，跳过工具环境检查${NC}"
    fi
}

# ── 停止服务 ──
stop_services() {
    echo -e "${YELLOW}🛑 停止服务...${NC}"
    lsof -ti :$BACKEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
    lsof -ti :$FRONTEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 1
    echo -e "${GREEN}✅ 服务已停止${NC}"
}

# ── 启动后端 ──
start_backend() {
    echo ""
    echo -e "${CYAN}[1/3] 启动后端 (port $BACKEND_PORT)...${NC}"

    lsof -ti :${BACKEND_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 1

    source "$VENV_DIR/bin/activate"
    cd "$DIR"

    if [ "$FOREGROUND" = true ]; then
        echo -e "${GREEN}  后端前台运行中... (Ctrl+C 停止)${NC}"
        python -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT --app-dir "$DIR"
    else
        nohup python -m uvicorn app.main:app \
            --host 0.0.0.0 \
            --port $BACKEND_PORT \
            --app-dir "$DIR" \
            > "$BACKEND_LOG" 2>&1 &
        BACKEND_PID=$!
        sleep 3

        if curl -s http://localhost:${BACKEND_PORT}/api/health > /dev/null 2>&1; then
            echo -e "${GREEN}  ✅ 后端运行中: http://localhost:${BACKEND_PORT}${NC}"
        else
            echo -e "${RED}  ❌ 后端启动失败，查看日志: tail $BACKEND_LOG${NC}"
            tail -20 "$BACKEND_LOG"
            return 1
        fi
    fi
}

# ── 启动前端 ──
start_frontend() {
    if [ -z "$NODE_OK" ]; then
        echo -e "${YELLOW}  ⏭️  跳过前端（Node.js 未安装）${NC}"
        return 0
    fi

    echo ""
    echo -e "${CYAN}[2/3] 启动前端 (port $FRONTEND_PORT)...${NC}"

    lsof -ti :${FRONTEND_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 1

    cd "$DIR/frontend"

    if [ "$FOREGROUND" = true ]; then
        echo -e "${GREEN}  前端前台运行中... (Ctrl+C 停止)${NC}"
        npx vite --host 0.0.0.0 --port $FRONTEND_PORT
    else
        nohup npx vite --host 0.0.0.0 --port $FRONTEND_PORT \
            > "$FRONTEND_LOG" 2>&1 &
        FRONTEND_PID=$!
        sleep 4

        if curl -s -o /dev/null -w "%{http_code}" http://localhost:${FRONTEND_PORT} 2>/dev/null | grep -q 200; then
            echo -e "${GREEN}  ✅ 前端运行中: http://localhost:${FRONTEND_PORT}${NC}"
        else
            echo -e "${RED}  ❌ 前端启动失败，查看日志: tail $FRONTEND_LOG${NC}"
            tail -20 "$FRONTEND_LOG"
            return 1
        fi
    fi
}

# ── 显示信息 ──
show_info() {
    echo ""
    echo -e "${CYAN}[3/3] 启动完成${NC}"
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "  🌐 访问地址: ${GREEN}http://localhost:${FRONTEND_PORT}${NC}"
    echo -e "  📡 API 地址: ${GREEN}http://localhost:${BACKEND_PORT}${NC}"
    echo -e "  📋 API 文档: ${GREEN}http://localhost:${BACKEND_PORT}/docs${NC}"
    echo ""
    echo -e "  📄 后端日志: tail -f $BACKEND_LOG"
    echo -e "  📄 前端日志: tail -f $FRONTEND_LOG"
    echo ""
    echo -e "  停止服务:   ${YELLOW}./run.sh --stop${NC}"
    echo -e "  重启服务:   ${YELLOW}./run.sh --restart${NC}"
    echo -e "  检查环境:   ${YELLOW}./run.sh --check${NC}"
    echo -e "${CYAN}========================================${NC}"
}

# ── 主流程 ──
FOREGROUND=false

case "$MODE" in
    --stop)
        stop_services
        exit 0
        ;;
    --restart)
        stop_services
        ;;
    --foreground|-f)
        FOREGROUND=true
        ;;
    --backend)
        setup_venv
        start_backend
        exit $?
        ;;
    --frontend)
        setup_frontend
        start_frontend
        exit $?
        ;;
    --check)
        setup_venv
        check_tools
        exit $?
        ;;
    --help|-h)
        echo "用法: ./run.sh [选项]"
        echo ""
        echo "选项:"
        echo "  (无)           后台启动后端+前端"
        echo "  --foreground   前台启动（终端显示日志）"
        echo "  --stop         停止所有服务"
        echo "  --restart      重启所有服务"
        echo "  --backend      仅启动后端"
        echo "  --frontend     仅启动前端"
        echo "  --check        检查并修复工具运行环境"
        echo "  --help         显示帮助"
        exit 0
        ;;
esac

# 执行启动流程
setup_venv
setup_frontend
start_backend
if [ "$FOREGROUND" = true ]; then
    # 前台模式：后端已在后台，前端在前台
    wait
else
    start_frontend
    show_info
fi
