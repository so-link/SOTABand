.PHONY: help install dev-install test lint format clean run run-prod setup stop

setup:  ## 一键安装环境（venv + Python/工具/前端依赖，跨平台）
	python scripts/serve.py setup

run:  ## 一键开发模式启动（安装+热更新前后端，Ctrl+C 停止）
	python scripts/serve.py dev

run-prod:  ## 一键部署模式启动（安装+构建前端+预览模式，无热更新）
	python scripts/serve.py start

stop:  ## 停止由 serve.py 启动的服务
	python scripts/serve.py stop

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies (delegates to serve.py setup: venv + uv + tools + frontend)
	python scripts/serve.py setup

dev-install:  ## Install with dev dependencies (delegates to serve.py setup)
	python scripts/serve.py setup

test:  ## 跑 unit + api 两层（默认，快；需 8201 已启动，否则 api 自动 skip）
	./venv/bin/python -m pytest -q

test-unit:  ## 只跑 unit 层（纯逻辑，无服务依赖）
	./venv/bin/python -m pytest -q -m unit

test-api:  ## 只跑 api 层（真实 HTTP，需 8201 已启动）
	./venv/bin/python -m pytest -q -m api

test-e2e:  ## 只跑浏览器层（需前后端已启动 + SOTABAND_E2E_PASSWORD，单独一次运行）
	./venv/bin/python -m pytest tests/e2e -q --run-e2e

test-integration:  ## 兼容旧入口：agent 派发等集成用例
	./venv/bin/python -m pytest -q tests/integration

test-cov:  ## 跑测试并生成覆盖率报告（unit + api）
	./venv/bin/python -m pytest -q --cov=. --cov-report=term --cov-report=html

lint:  ## Run linter
	ruff check .

format:  ## Auto-format code
	ruff check --fix .
	ruff format .

clean:  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ 2>/dev/null || true
