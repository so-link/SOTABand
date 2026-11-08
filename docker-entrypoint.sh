#!/usr/bin/env sh
# 后端容器入口 —— 等库就绪 → 跑迁移 → 起服务。
#
# 为什么迁移放在容器启动时而不是构建时：
# 构建镜像时数据库还不存在（compose 里 db 是另一个容器），而且同一镜像应该能
# 连不同的库。所以迁移必须在**运行时**做。`alembic upgrade head` 是幂等的，
# 重复启动不会重复建表。
#
# 为什么还要自己等库：
# compose 的 `depends_on: condition: service_healthy` 已经挡了大部分情况，
# 但 PG 的 healthcheck 通过后仍有一小段不可连接窗口，迁移可能抢跑失败。
# 加一层 socket 探测是廉价的保险。

set -eu

echo "[entrypoint] 等待数据库就绪…"

python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlsplit

url = os.environ.get("DATABASE_URL", "")
if not url.startswith(("postgresql", "postgres")):
    print(f"[entrypoint] DATABASE_URL 非 PostgreSQL（{url[:32] or '未设置'}），跳过等待")
    raise SystemExit(0)

parts = urlsplit(url)
host = parts.hostname or "localhost"
port = parts.port or 5432

for attempt in range(1, 61):
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        print(f"[entrypoint] 数据库可达 {host}:{port}（第 {attempt} 次尝试）")
        raise SystemExit(0)
    except OSError:
        time.sleep(1)

print(f"[entrypoint] 数据库 {host}:{port} 60 秒内不可达", file=sys.stderr)
raise SystemExit(1)
PY

echo "[entrypoint] 执行 Alembic 迁移…"
python -m alembic upgrade head

echo "[entrypoint] 启动 API（端口 ${PORT:-8001}）…"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8001}"
