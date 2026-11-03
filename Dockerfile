# 后端镜像 —— FastAPI + SQLAlchemy(async) + Alembic
#
# ## 为什么基础镜像是参数化的
#
# 本机（国内网络）直连 `registry-1.docker.io` 会超时，必须走镜像源。
# 用 ARG 把 registry 前缀抽出来：
#
# ```powershell
# docker compose build                      # 用 compose 里配的镜像源（默认 daocloud）
# docker build --build-arg REGISTRY=docker.io -t sotaband-backend .   # 换回官方源
# ```
#
# 硬编码镜像源会让这个 Dockerfile 在别人的机器上跑不起来，所以默认给官方源、
# 由 compose 覆盖 —— 构建脚本保持环境无关。
#
# ## 为什么用 python:3.12-slim 而不是 alpine
#
# 本项目依赖 bcrypt / asyncpg / uvloop，这些都有 manylinux wheel；slim 镜像能直接
# 用上，不需要编译器。alpine 用 musl libc，wheel 常缺失 → 现场编译 → 镜像更大更慢。
#
# ## 为什么要复制 migrations 和 alembic.ini
#
# 迁移在**容器启动时**执行（见 docker-entrypoint.sh），不是在构建时。

ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.12-slim

# PYTHONUNBUFFERED：日志立刻输出，docker logs 才看得到实时日志
# PYTHONDONTWRITEBYTECODE：不在镜像里留 .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先只复制依赖声明 —— 只要依赖没变，这一层就能命中缓存，
# 改业务代码时不用重装依赖（这是 Dockerfile 分层的基本收益）。
COPY pyproject.toml README.md ./

# 包源码必须在 `pip install -e .` 之前到位，否则 setuptools 的
# packages.find 找不到任何包，安装会失败。
COPY app ./app
COPY core ./core
COPY config ./config
COPY resources ./resources
COPY models ./models

# [postgres] extra 装的是 asyncpg。
# ⚠️ 不能用 psycopg：它的 async 模式不支持 Windows 的 ProactorEventLoop，
# 而本项目 Agent 工厂依赖 asyncio 子进程（Windows 上只在 Proactor 可用）。
# 虽然容器内是 Linux，但**开发机是 Windows**，同一个 extra 必须两边都能用。
#
# PIP_INDEX 与 REGISTRY 同理：国内网络直连 PyPI 下载极慢（实测 `pip install -e .`
# 卡在 "Installing build dependencies" 三分钟以上）。默认给官方源，由 compose 覆盖。
ARG PIP_INDEX=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX}" -e ".[postgres]"

# 迁移相关（依赖装完之后再复制，避免改迁移就重装依赖）
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 运行时会写入这些目录，先在镜像里建好并给非 root 用户权限。
#
# ⚠️ `resources-private` 必须显式指定路径：
# ``core/resource/paths.py`` 的默认值是 ``PROJECT_ROOT.parent / "resources-private"``。
# 宿主机上项目根通常位于某个可写目录，其父目录也可写；
# 但容器里项目根是 ``/app``，父目录是 **``/``** —— 非 root 用户 mkdir 直接
# `PermissionError: '/resources-private'`，应用起不来。
#
# 那个模块已经支持 ``SOTABAND_PRIVATE_RESOURCES`` 覆盖（见其模块文档），
# 所以这里只管建目录 + 在 compose 里指过去，**不改代码默认行为**
# （改默认值会动到宿主机上已有的 ``resources-private`` 布局）。
RUN mkdir -p storage data output uploaded_images resources-private \
    && useradd --create-home --uid 10001 sotaband \
    && chown -R sotaband:sotaband /app

# 以非 root 运行：容器逃逸时降低影响面
USER sotaband

EXPOSE 8001

ENTRYPOINT ["docker-entrypoint.sh"]
