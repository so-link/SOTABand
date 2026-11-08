"""认证 API 集成测试 —— ``/api/auth/*`` 与 ``/api/v1/me``。

走完整的 HTTP 栈（ASGI），验证契约 §3.1 的接口语义与 §4 的错误码约定。

重点覆盖：

- 注册的**五条记录原子性**与自动登录；
- 登录失败的**不可区分性**（威胁模型 T3）；
- 会话的生命周期（签发 → 使用 → 吊销）；
- **IDOR**：不能吊销他人会话（隔离设计 §7.2）；
- 限流（威胁模型 T2）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
LOGOUT_ALL = "/api/auth/logout-all"
SESSION = "/api/auth/session"
ME = "/api/v1/me"

PASSWORD = "Passw0rd2026"


async def register(
    client: AsyncClient,
    *,
    email: str = "alice@example.com",
    password: str = PASSWORD,
    display_name: str = "Alice",
):
    return await client.post(
        REGISTER,
        json={"email": email, "password": password, "display_name": display_name},
    )


@asynccontextmanager
async def another_client():
    """全新 Cookie 罐的客户端（模拟另一台设备/浏览器）。

    ``app.dependency_overrides`` 是应用级的，所以 ``api_client`` 夹具设置的数据库
    覆盖对它同样生效 —— 两个客户端共享同一个测试库。
    """
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


# ── 注册 ────────────────────────────────────────────────────────────


class TestRegister:
    async def test_creates_user_and_personal_tenant(self, api_client: AsyncClient) -> None:
        resp = await register(api_client)
        assert resp.status_code == 201, resp.text

        body = resp.json()
        assert body["user"]["email"] == "alice@example.com"
        assert body["tenant"]["role"] == "owner"
        assert body["tenant"]["slug"]

    async def test_sets_session_cookie(self, api_client: AsyncClient) -> None:
        await register(api_client)
        token = api_client.cookies.get("sotaband_session")
        assert token, "注册成功后应下发会话 Cookie"

    async def test_duplicate_email_returns_409(self, api_client: AsyncClient) -> None:
        assert (await register(api_client)).status_code == 201
        resp = await register(api_client)
        assert resp.status_code == 409
        assert "已被注册" in resp.json()["detail"]

    async def test_email_is_normalized(self, api_client: AsyncClient) -> None:
        assert (await register(api_client, email="  Alice@Example.COM  ")).status_code == 201
        # 规范化后应命中同一账号
        assert (await register(api_client, email="alice@example.com")).status_code == 409

    async def test_rejects_short_password(self, api_client: AsyncClient) -> None:
        resp = await register(api_client, password="a1b2c3")  # 6 位
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "8-20 位" in detail and "6 位" in detail, detail

    async def test_rejects_over_long_password(self, api_client: AsyncClient) -> None:
        """21 位必须在 bcrypt 之前被拦住 —— 否则超长输入会让 bcrypt 抛 ValueError 变 500。"""
        resp = await register(api_client, password="a1" + "b" * 19)  # 21 位
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "8-20 位" in detail and "21 位" in detail, detail

    async def test_rejects_password_without_digit(self, api_client: AsyncClient) -> None:
        """纯字母（哪怕长度合规）也要拒 —— 用户 2026-10-27 指定的组合规则。"""
        resp = await register(api_client, password="abcdefgh")
        assert resp.status_code == 400
        assert "数字" in resp.json()["detail"]

    async def test_rejects_password_without_letter(self, api_client: AsyncClient) -> None:
        resp = await register(api_client, password="12345678")
        assert resp.status_code == 400
        assert "字母" in resp.json()["detail"]

    @pytest.mark.parametrize("password", ["abc12345", "a1" + "b" * 18])  # 8 位 / 20 位
    async def test_accepts_password_boundary_lengths(
        self, api_client: AsyncClient, password: str
    ) -> None:
        """8 位与 20 位是闭区间边界，都必须能注册成功。"""
        resp = await register(api_client, password=password)
        assert resp.status_code == 201, resp.text

    async def test_rejects_invalid_email(self, api_client: AsyncClient) -> None:
        resp = await register(api_client, email="not-an-email")
        assert resp.status_code == 422  # Pydantic 校验失败

    async def test_accepts_camel_case_display_name(self, api_client: AsyncClient) -> None:
        """冻结契约 §3.1 规定注册请求体是 ``{email, password, displayName?}``。

        ⚠️ 这是一个**静默失败**的坑：若 Pydantic 不认 ``displayName``，它会把它当
        未知字段丢弃，显示名悄悄回落成邮箱前缀 —— 请求仍返回 201，前端看不出错，
        只是用户名不对。这条测试是回归守卫。
        """
        resp = await api_client.post(
            REGISTER,
            json={
                "email": "camel@example.com",
                "password": PASSWORD,
                "displayName": "骆驼命名",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["display_name"] == "骆驼命名"

    async def test_also_accepts_snake_case_display_name(
        self, api_client: AsyncClient
    ) -> None:
        """``populate_by_name`` 让两种写法都可用，避免调用方因命名不一致踩坑。"""
        resp = await api_client.post(
            REGISTER,
            json={
                "email": "snake@example.com",
                "password": PASSWORD,
                "display_name": "蛇形命名",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["display_name"] == "蛇形命名"

    async def test_register_does_not_create_duplicate_rows(
        self, api_client: AsyncClient, db_session
    ) -> None:
        from sqlalchemy import func, select

        from core.db.models import Tenant, TenantMembership, User, UserCredential

        await register(api_client)
        for model in (User, UserCredential, Tenant, TenantMembership):
            count = await db_session.execute(select(func.count()).select_from(model))
            assert count.scalar_one() == 1, f"{model.__tablename__} 应为 1 行"


# ── 登录 ────────────────────────────────────────────────────────────


class TestLogin:
    async def test_login_success(self, api_client: AsyncClient) -> None:
        await register(api_client)
        api_client.cookies.clear()

        resp = await api_client.post(
            LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tenantRole"] == "owner"
        assert api_client.cookies.get("sotaband_session")

    async def test_wrong_password_and_unknown_user_are_indistinguishable(
        self, api_client: AsyncClient
    ) -> None:
        """威胁模型 T3：两种失败必须**完全同形**，否则可用于枚举账号。"""
        await register(api_client)

        wrong_pw = await api_client.post(
            LOGIN, json={"email": "alice@example.com", "password": "wrong-password"}
        )
        unknown = await api_client.post(
            LOGIN, json={"email": "nobody@example.com", "password": "wrong-password"}
        )

        assert wrong_pw.status_code == unknown.status_code == 401
        assert wrong_pw.json() == unknown.json()

    async def test_login_issues_new_token_not_reuse(
        self, api_client: AsyncClient
    ) -> None:
        """会话固定防护：每次登录都签发全新令牌。"""
        await register(api_client)
        first = api_client.cookies.get("sotaband_session")

        resp = await api_client.post(
            LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
        )
        assert resp.status_code == 200
        second = api_client.cookies.get("sotaband_session")
        assert first and second and first != second


# ── 会话探测 ────────────────────────────────────────────────────────


class TestSessionProbe:
    async def test_unauthenticated_returns_200_not_401(
        self, api_client: AsyncClient
    ) -> None:
        """契约 §3.1：探测接口未登录必须返回 200 + authenticated:false。

        返回 401 会让前端状态机把正常探测当成错误、也会刷控制台。
        """
        async with another_client() as fresh:
            resp = await fresh.get(SESSION)
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    async def test_authenticated_after_register(self, api_client: AsyncClient) -> None:
        await register(api_client)
        resp = await api_client.get(SESSION)
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["user"]["email"] == "alice@example.com"
        assert body["tenant_role"] == "owner"


# ── 当前用户 ────────────────────────────────────────────────────────


class TestMe:
    async def test_requires_auth(self, api_client: AsyncClient) -> None:
        async with another_client() as fresh:
            resp = await fresh.get(ME)
        assert resp.status_code == 401

    async def test_returns_user_and_tenants(self, api_client: AsyncClient) -> None:
        await register(api_client)
        resp = await api_client.get(ME)
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body["user_id"]
        assert body["email"] == "alice@example.com"
        assert body["tenant_role"] == "owner"
        assert len(body["tenants"]) == 1
        assert body["tenants"][0]["role"] == "owner"

    async def test_request_id_header_present(self, api_client: AsyncClient) -> None:
        """request_id 要能贯穿 —— 响应头回写是验证它的最简单方式。"""
        resp = await api_client.get(SESSION)
        assert resp.headers.get("X-Request-ID")

    async def test_client_supplied_request_id_is_echoed(
        self, api_client: AsyncClient
    ) -> None:
        resp = await api_client.get(SESSION, headers={"X-Request-ID": "trace-abc-123"})
        assert resp.headers.get("X-Request-ID") == "trace-abc-123"


# ── 登出与吊销 ──────────────────────────────────────────────────────


class TestLogout:
    async def test_logout_invalidates_session(self, api_client: AsyncClient) -> None:
        await register(api_client)
        assert (await api_client.get(ME)).status_code == 200

        resp = await api_client.post(LOGOUT)
        assert resp.status_code == 204

        assert (await api_client.get(ME)).status_code == 401

    async def test_logout_is_idempotent(self, api_client: AsyncClient) -> None:
        await register(api_client)
        assert (await api_client.post(LOGOUT)).status_code == 204
        # 第二次已无有效会话 → 401（未认证），而不是 500
        assert (await api_client.post(LOGOUT)).status_code == 401

    async def test_logout_all_revokes_other_devices(
        self, api_client: AsyncClient
    ) -> None:
        await register(api_client)

        async with another_client() as other:
            login = await other.post(
                LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
            )
            assert login.status_code == 200
            assert (await other.get(ME)).status_code == 200

            assert (await api_client.post(LOGOUT_ALL)).status_code == 204

            # 另一台设备也应失效
            assert (await other.get(ME)).status_code == 401
        # 当前设备同样失效
        assert (await api_client.get(ME)).status_code == 401

    async def test_cannot_delete_another_users_session(
        self, api_client: AsyncClient
    ) -> None:
        """IDOR 防护：会话 id 来自 URL，必须校验归属；不匹配返回 404。"""
        await register(api_client, email="alice@example.com")
        my_sessions = await api_client.get("/api/v1/me/sessions")
        alice_session_id = my_sessions.json()[0]["id"]

        async with another_client() as bob:
            await register(bob, email="bob@example.com", display_name="Bob")
            resp = await bob.delete(f"/api/v1/me/sessions/{alice_session_id}")
            assert resp.status_code == 404

            # Alice 的会话必须仍然有效
            assert (await api_client.get(ME)).status_code == 200

    async def test_cannot_delete_session_with_garbage_id(
        self, api_client: AsyncClient
    ) -> None:
        await register(api_client)
        resp = await api_client.delete("/api/v1/me/sessions/not-a-uuid")
        assert resp.status_code == 404


# ── 限流 ────────────────────────────────────────────────────────────


class TestRateLimit:
    async def test_login_locks_account_after_repeated_failures(
        self, api_client: AsyncClient
    ) -> None:
        """威胁模型 T2：连续失败达到阈值后锁定，返回 429 而非继续尝试。"""
        await register(api_client)

        for _ in range(5):
            resp = await api_client.post(
                LOGIN, json={"email": "alice@example.com", "password": "wrong-password"}
            )
            assert resp.status_code == 401

        locked = await api_client.post(
            LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
        )
        assert locked.status_code == 429
        assert locked.headers.get("Retry-After")

    async def test_register_limited_per_ip(self, api_client: AsyncClient) -> None:
        """注册限流：默认每小时 5 次/IP。"""
        for i in range(5):
            resp = await register(api_client, email=f"user{i}@example.com")
            assert resp.status_code == 201, resp.text

        blocked = await register(api_client, email="user9@example.com")
        assert blocked.status_code == 429


# ── 审计 ────────────────────────────────────────────────────────────


class TestAudit:
    async def test_register_and_login_are_audited(
        self, api_client: AsyncClient, db_session
    ) -> None:
        from sqlalchemy import select

        from core.db.models import AuditEvent

        await register(api_client)
        async with another_client() as other:
            await other.post(
                LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
            )
            await other.post(
                LOGIN, json={"email": "alice@example.com", "password": "wrong"}
            )

        rows = (
            await db_session.execute(select(AuditEvent).order_by(AuditEvent.gmt_create))
        ).scalars().all()
        actions = [r.action for r in rows]

        assert "auth.register" in actions
        assert "auth.login" in actions
        assert "success" in [r.result for r in rows]
        assert "failure" in [r.result for r in rows]

    async def test_failed_login_does_not_store_password(
        self, api_client: AsyncClient, db_session
    ) -> None:
        """审计与日志里**绝不能**出现密码（PRD §10）。"""
        from sqlalchemy import select

        from core.db.models import AuditEvent

        secret = "S3cret-Should-Never-Appear"
        async with another_client() as other:
            await other.post(
                LOGIN, json={"email": "ghost@example.com", "password": secret}
            )

        rows = (await db_session.execute(select(AuditEvent))).scalars().all()
        for row in rows:
            assert secret not in str(row.actor_id)
            assert secret not in str(row.target_id)
