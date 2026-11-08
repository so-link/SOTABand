# 后端镜像（FastAPI + SQLAlchemy async + Alembic）。
#
# 镜像源由 ARG REGISTRY 注入：国内直连 docker.io 会超时，compose 会覆盖成镜像源；
# 这里默认官方源，保证换台机器也能构建。
# 基础镜像用 python:3.12-slim —— bcrypt/asyncpg/uvloop 都有 manylinux wheel，
# 不需要编译器；alpine 用 musl，wheel 常缺失、要现场编译。
# 数据库迁移在容器启动时执行（见 docker-entrypoint.sh），不在构建阶段。

ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.12-slim

# unbuffered：日志实时输出；不写 .pyc、不缓存 pip 包，减小镜像。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先复制依赖声明再装依赖：改业务代码不会触发重装（利用 Docker 分层缓存）。
# 包源码必须在 `pip install -e .` 之前就位，否则 setuptools 找不到包。
COPY pyproject.toml README.md ./
COPY app ./app
COPY core ./core
COPY config ./config
COPY resources ./resources
COPY models ./models

# [postgres] extra 装 asyncpg（psycopg 的 async 与 Windows ProactorEventLoop 不兼容，
# 而 Agent 子进程在 Windows 上必须用 Proactor，所以统一选 asyncpg）。
# PIP_INDEX 理由同 REGISTRY：国内直连 PyPI 极慢，默认官方源、由 compose 覆盖。
ARG PIP_INDEX=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX}" -e ".[postgres]"

# 迁移文件放在依赖安装之后复制：改迁移不会重装依赖。
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 运行时目录 + 非 root 用户。
# resources-private 的默认路径是 <项目根>/../resources-private，容器里即 /resources-private；
# 非 root 用户无法在 / 下创建，所以这里先建好，并由 compose 用 SOTABAND_PRIVATE_RESOURCES 指过去。
RUN mkdir -p storage data output uploaded_images resources-private \
    && useradd --create-home --uid 10001 sotaband \
    && chown -R sotaband:sotaband /app

USER sotaband
EXPOSE 8001
ENTRYPOINT ["docker-entrypoint.sh"]
