"""数据库会话配置与应用导入冒烟测试。

两件事放在一起，因为它们回答的是同一个问题：**引入数据库层之后，既有的东西
还在不在。**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.db.session import resolve_database_url


class TestResolveDatabaseUrl:
    """SQLite 相对路径必须锚定到项目根。"""

    def test_relative_path_is_anchored_to_project_root(self) -> None:
        """``./storage/x.db`` 相对的是**当前工作目录**。

        后端通常从项目根启动，但测试、脚本、Alembic 未必 —— 那样就会在别处
        悄悄建出一个空库，表现为"表明明建了却查不到"。这里统一锚定。
        """
        resolved = resolve_database_url("sqlite+aiosqlite:///./storage/sotaband.db")

        assert resolved.startswith("sqlite+aiosqlite:///")
        path = Path(resolved[len("sqlite+aiosqlite:///"):])
        assert path.is_absolute()
        assert path.name == "sotaband.db"
        assert path.parent.name == "storage"
        # 锚定到项目根：core/db/session.py → core/db → core → 项目根
        assert (path.parent.parent / "pyproject.toml").exists()

    def test_already_absolute_path_is_unchanged(self) -> None:
        url = "sqlite+aiosqlite:///E:/tmp/sotaband.db"
        assert resolve_database_url(url) == url

    def test_postgres_url_is_untouched(self) -> None:
        """非 SQLite 的连接串自带主机信息，不应被改写。"""
        url = "postgresql+psycopg://user:pw@localhost:5432/sotaband"
        assert resolve_database_url(url) == url

    def test_in_memory_sqlite_is_untouched(self) -> None:
        """``:memory:`` 不是相对路径。

        若被当成相对路径拼到项目根下（``E:/.../:memory:``），会变成一个真实的
        磁盘文件，静默丢掉"内存库"语义 —— 测试之间就会开始互相污染。
        """
        url = "sqlite+aiosqlite:///:memory:"
        assert resolve_database_url(url) == url

    def test_sqlite_file_uri_is_untouched(self) -> None:
        url = "sqlite+aiosqlite:///file:memdb1?mode=memory&cache=shared"
        assert resolve_database_url(url) == url


class TestTestIsolation:
    """确认测试确实跑在临时库上，而不是开发库。

    ``tests/conftest.py`` 里的安全阀会在 ``db_session`` 触发 ``drop_all`` 前硬失败；
    这里从外部再确认一次隔离是生效的 —— 两条独立证据好过一条。
    """

    def test_test_database_is_isolated_from_dev(self) -> None:
        """测试库绝不能是开发库 —— 两种模式下各自校验。

        默认模式（零配置）：临时目录里的 SQLite；
        显式模式（如 PostgreSQL 并发复验）：URL 由环境变量指定，要求库名含 ``test``。
        """
        import os

        from config.settings import settings
        from core.db.session import resolve_database_url

        url = resolve_database_url(settings.storage.database_url)
        normalized = url.replace("\\", "/")
        explicit = os.environ.get("SOTABAND_TEST_DATABASE_URL", "").strip()

        if explicit:
            assert "test" in url.lower(), f"显式测试库名必须含 test：{url}"
        else:
            assert "sotaband-test-" in url, f"测试库未使用临时目录：{url}"
        assert "storage/sotaband.db" not in normalized, f"测试库指向了开发库！{url}"


class TestAppStillImports:
    """数据库层的引入不得破坏既有导入链。

    ``config/settings.py`` 的 ``StorageConfig`` 在本次改造中被启用（此前是声明了
    但全库无人引用的预留字段），这条测试确认改动没有连带打断应用启动。
    """

    def test_app_main_imports(self) -> None:
        import app.main

        assert app.main.app is not None
        assert app.main.app.title

    def test_existing_routes_still_registered(self) -> None:
        """既有 9 组路由一个都不能少。

        用 ``app.openapi()`` 而不是遍历 ``app.routes``：新版 FastAPI 会把 include
        进来的 router 包成 ``_IncludedRouter``（没有 ``.path`` 属性），直接遍历
        会漏掉或报错。OpenAPI schema 是注册结果的权威视图。
        """
        import app.main

        paths = set(app.main.app.openapi()["paths"])
        for prefix in (
            "/api/chat",
            "/api/agent",
            "/api/tool",
            "/api/data",
            "/api/file",
            "/api/model",
            "/api/config",
            "/api/apis",
            "/api/llm",
        ):
            assert any(p.startswith(prefix) for p in paths), f"路由丢失：{prefix}"

    def test_storage_database_url_is_configured(self) -> None:
        from config.settings import settings

        url = settings.storage.database_url
        assert url, "database_url 未配置"
        assert url.startswith(("sqlite", "postgresql")), url

    @pytest.mark.parametrize("field", ["backend", "file_store_path", "cache_backend"])
    def test_storage_config_fields_preserved(self, field: str) -> None:
        """启用 database_url 时不得顺手删掉 StorageConfig 的其他字段。"""
        from config.settings import settings

        assert getattr(settings.storage, field) is not None
