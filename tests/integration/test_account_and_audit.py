"""改密码（FR-A5）与审计查询（FR-N9）。

## 改密码要验证的不只是"能改"

1. **改完旧密码失效、新密码可用** —— 否则等于没改；
2. **全部会话被吊销**（含发起改密的那一条）—— PRD FR-A5 原文；
3. **当前密码错误返回 400 而不是 401** —— 401 会触发前端全局处理器把用户登出，
   只是打错一次密码就被踢下线是明显的体验/语义错误；
4. **失败限流** —— 改密接口同样校验当前密码，持有有效会话的攻击者可以在这里
   爆破，且**绕过登录接口的限流**。这是最容易被漏掉的一条。

## 审计查询要验证的是"授权"和"内容"

审计日志含 ``actor_id`` / ``target_id`` / ``request_id``，是最敏感的一类数据。
所以先验权限边界（owner 可读、member 403、非成员 404），再验内容正确。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

PW = "Passw0rd2026"
NEW_PW = "NewPassw0rd2026"


@asynccontextmanager
async def new_client():
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def register(client: AsyncClient, email: str, name: str) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PW, "displayName": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login(client: AsyncClient, email: str, password: str):
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


# ══════════════════════════════════════════════════════════════════════
# FR-A5 改密码
# ══════════════════════════════════════════════════════════════════════


class TestChangePassword:
    async def _setup(self, client: AsyncClient) -> str:
        email = f"pw-{uuid.uuid4().hex[:8]}@example.com"
        await register(client, email, "改密用户")
        return email

    async def test_change_then_old_password_fails_new_works(
        self, api_client: AsyncClient
    ) -> None:
        email = await self._setup(api_client)

        resp = await api_client.post(
            "/api/v1/me/password",
            json={"currentPassword": PW, "newPassword": NEW_PW},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["revokedSessions"] >= 1, "至少要吊销当前这条会话"

        # 改密后当前 Cookie 已失效 → 再调受保护接口应 401
        me = await api_client.get("/api/v1/me")
        assert me.status_code == 401

        # 旧密码不能登录
        old = await login(api_client, email, PW)
        assert old.status_code == 401

        # 新密码可以
        new = await login(api_client, email, NEW_PW)
        assert new.status_code == 200

    async def test_wrong_current_password_returns_400_not_401(
        self, api_client: AsyncClient
    ) -> None:
        """**关键语义**：401 会让前端把用户登出，打错一次密码不该被踢下线。"""
        await self._setup(api_client)

        resp = await api_client.post(
            "/api/v1/me/password",
            json={"currentPassword": "WrongPass1", "newPassword": NEW_PW},
        )
        assert resp.status_code == 400, resp.text
        assert "当前密码" in resp.json()["detail"]

        # 会话仍然有效 —— 这才是 400 的意义
        me = await api_client.get("/api/v1/me")
        assert me.status_code == 200

    async def test_new_password_must_differ_from_current(
        self, api_client: AsyncClient
    ) -> None:
        await self._setup(api_client)

        resp = await api_client.post(
            "/api/v1/me/password",
            json={"currentPassword": PW, "newPassword": PW},
        )
        assert resp.status_code == 400
        assert "不能与当前密码相同" in resp.json()["detail"]

    async def test_new_password_policy_enforced(self, api_client: AsyncClient) -> None:
        await self._setup(api_client)

        # 纯字母 → 缺数字
        resp = await api_client.post(
            "/api/v1/me/password",
            json={"currentPassword": PW, "newPassword": "abcdefgh"},
        )
        assert resp.status_code == 400
        assert "数字" in resp.json()["detail"]

    async def test_too_short_new_password_rejected(
        self, api_client: AsyncClient
    ) -> None:
        await self._setup(api_client)
        resp = await api_client.post(
            "/api/v1/me/password",
            json={"currentPassword": PW, "newPassword": "ab1"},
        )
        assert resp.status_code == 400
        assert "8-20" in resp.json()["detail"]

    async def test_failures_are_rate_limited(self, api_client: AsyncClient) -> None:
        """失败的「当前密码」尝试必须被限流 —— 这是绕过登录限流的爆破信道。"""
        await self._setup(api_client)

        statuses = []
        for _ in range(6):
            resp = await api_client.post(
                "/api/v1/me/password",
                json={"currentPassword": "WrongPass1", "newPassword": NEW_PW},
            )
            statuses.append(resp.status_code)

        assert 429 in statuses, f"连续失败应触发限流，实际状态码序列：{statuses}"
        # 前几次是 400（密码错），之后才 429
        assert statuses[0] == 400

    async def test_requires_authentication(self) -> None:
        async with new_client() as anon:
            resp = await anon.post(
                "/api/v1/me/password",
                json={"currentPassword": PW, "newPassword": NEW_PW},
            )
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════
# FR-N9 审计查询
# ══════════════════════════════════════════════════════════════════════


class TestNetworkAudit:
    async def _setup_world(self, api_client: AsyncClient) -> tuple[str, str]:
        """建网络并产生几条审计事件，返回 (network_id, tenant_id)。"""
        email = f"audit-{uuid.uuid4().hex[:8]}@example.com"
        body = await register(api_client, email, "审计用户")
        tenant_id = body["tenant"]["id"]

        net = await api_client.post("/api/v1/networks", json={"name": "审计网络"})
        assert net.status_code == 201, net.text
        network_id = net.json()["id"]

        # 再产生几条：邀请（写审计）、暂停、恢复
        await api_client.post(
            f"/api/v1/networks/{network_id}/invitations",
            json={"email": f"invitee-{uuid.uuid4().hex[:6]}@example.com"},
        )
        await api_client.patch(f"/api/v1/networks/{network_id}", json={"status": "suspended"})
        await api_client.patch(f"/api/v1/networks/{network_id}", json={"status": "active"})
        return network_id, tenant_id

    async def test_owner_can_read_audit(self, api_client: AsyncClient) -> None:
        network_id, _ = await self._setup_world(api_client)

        resp = await api_client.get(f"/api/v1/networks/{network_id}/audit")
        assert resp.status_code == 200, resp.text
        events = resp.json()
        assert events, "刚做过建网络等操作，审计不应为空"

        actions = {e["action"] for e in events}
        assert "network.create" in actions
        assert "network.status.change" in actions

        first = events[0]
        for field in ("id", "action", "result", "actor_id", "actor_kind", "request_id"):
            assert field in first, f"审计事件缺少字段 {field}"
        assert first["actor_kind"] == "user"

    async def test_newest_first(self, api_client: AsyncClient) -> None:
        network_id, _ = await self._setup_world(api_client)
        events = (
            await api_client.get(f"/api/v1/networks/{network_id}/audit")
        ).json()
        times = [e["created_at"] for e in events]
        assert times == sorted(times, reverse=True), "审计应按时间倒序"

    async def test_action_filter(self, api_client: AsyncClient) -> None:
        network_id, _ = await self._setup_world(api_client)

        resp = await api_client.get(
            f"/api/v1/networks/{network_id}/audit",
            params={"action": "network.create"},
        )
        assert resp.status_code == 200
        events = resp.json()
        assert events
        assert all(e["action"] == "network.create" for e in events)

    async def test_limit_is_capped(self, api_client: AsyncClient) -> None:
        """``limit`` 超过 200 应被 422 拒绝 —— 防止一次拉爆审计表。"""
        network_id, _ = await self._setup_world(api_client)
        resp = await api_client.get(
            f"/api/v1/networks/{network_id}/audit", params={"limit": 500}
        )
        assert resp.status_code == 422

    async def test_member_forbidden(self, api_client: AsyncClient) -> None:
        """普通成员没有 ``network.audit.read``（真值表：仅 owner/admin）。"""
        network_id, tenant_id = await self._setup_world(api_client)
        member_email = f"audit-member-{uuid.uuid4().hex[:6]}@example.com"

        async with new_client() as member:
            await register(member, member_email, "普通成员")
            # 进 Alice 的租户
            inv = await api_client.post(
                f"/api/v1/tenants/{tenant_id}/invitations", json={"email": member_email}
            )
            assert inv.status_code == 201, inv.text
            await member.post(
                f"/api/v1/tenant-invitations/{inv.json()['token']}/accept"
            )
            await member.post(
                "/api/v1/me/switch-tenant", json={"tenantId": tenant_id}
            )
            # 进网络（member 角色）
            net_inv = await api_client.post(
                f"/api/v1/networks/{network_id}/invitations",
                json={"email": member_email},
            )
            await member.post(
                f"/api/v1/network-invitations/{net_inv.json()['token']}/accept"
            )

            resp = await member.get(f"/api/v1/networks/{network_id}/audit")
            assert resp.status_code == 403, resp.text

    async def test_non_member_gets_404(self, api_client: AsyncClient) -> None:
        network_id, _ = await self._setup_world(api_client)

        async with new_client() as stranger:
            await register(
                stranger, f"audit-str-{uuid.uuid4().hex[:6]}@example.com", "陌生人"
            )
            resp = await stranger.get(f"/api/v1/networks/{network_id}/audit")
            # 跨租户 → 404（不泄露网络存在性）
            assert resp.status_code == 404

    async def test_agent_invocations_appear_in_audit(
        self, api_client: AsyncClient, release_space
    ) -> None:
        """FR-N7 的调用记录必须能在审计里查到，且主体是 workload 而非 user。"""
        network_id, _ = await self._setup_world(api_client)
        me = await api_client.get("/api/v1/me")
        owner_uid = me.json()["user_id"]

        # 发布两个 Agent，装一个、拿凭证、调一次
        # （发布接口校验资源归属：先把合成 release 注册到隔离的私有资源根）
        for release in ("audit-caller", "audit-target"):
            await release_space("agent", release, owner=owner_uid)
            resp = await api_client.post(
                f"/api/v1/agent-releases/{release}/publications",
                json={"scope": "network", "scopeId": network_id},
            )
            assert resp.status_code == 201, resp.text
        inst = await api_client.post(
            f"/api/v1/agent-releases/audit-caller/installations?networkId={network_id}"
        )
        assert inst.status_code == 201, inst.text
        installation_id = inst.json()["id"]
        cred = await api_client.post(
            f"/api/v1/installations/{installation_id}/credential"
        )
        token = cred.json()["token"]

        invoke = await api_client.post(
            f"/api/v1/installations/{installation_id}/invoke",
            json={
                "networkId": network_id,
                "targetResourceType": "agent",
                "targetReleaseId": "audit-target",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert invoke.status_code == 200, invoke.text

        events = (
            await api_client.get(
                f"/api/v1/networks/{network_id}/audit",
                params={"action": "network.agent.invoke"},
            )
        ).json()
        assert events, "Agent 调用必须出现在审计里"
        assert events[0]["actor_kind"] == "workload"
        assert events[0]["actor_id"] == installation_id
        assert events[0]["target_id"] == "audit-target"
