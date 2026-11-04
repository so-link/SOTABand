"""后端 API 权限审计（HTTP 层）

回答一个问题：**每一个后端端点，是否都有权限检查？**

多用户模式（.env 配置 SOTABAND_ROOT_PASSWORD）下的预期分层：

- 中间件：除登录/探活外，所有 ``/api/*`` 一律要求有效令牌 → 匿名 401
- 系统级（``/api/config``、``/api/llm``、``/api/users``、``/api/superuser``）
  经 ``require_superuser`` 保护 → 普通用户 403
- 其余端点：登录即可 → 普通用户 200（数据隔离靠按 uid 分目录，不靠端点鉴权）

全部隔离到 tmp_path，绝不碰真实 /root/SB-USER 与真实用户表。
"""

import pytest
from fastapi.testclient import TestClient

from core import user_paths
from core import auth as auth_mod


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """用户目录根、用户表、令牌密钥、各存储连接池全部指向临时位置。"""
    from core.chat_control import audit
    from core.cron import store as cron_store
    from core.observability import chat_store, task_history
    from core import user_prefs, user_files

    root = tmp_path / "SB-USER"
    # 只建超管目录：_next_uid 把 USER_HOME_ROOT 下已存在的数字目录也算作已占用，
    # 预建 1000 会让首个普通用户从 1001 起号。
    (root / "0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    monkeypatch.setattr(auth_mod, "USERS_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_mod, "USERS_FILE", tmp_path / "auth" / "users.json")
    monkeypatch.setattr(auth_mod, "SECRET_FILE", tmp_path / "auth" / "secret.key")
    monkeypatch.delenv("SOTABAND_AUTH_SECRET", raising=False)
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)

    # 多用户模式：必须配置超管密码，否则中间件把匿名当超管放行，一切免鉴权
    monkeypatch.setenv("SOTABAND_ROOT_USERNAME", "root")
    monkeypatch.setenv("SOTABAND_ROOT_PASSWORD", "sup3r-secret-pw")

    pools = [
        user_files._pool, chat_store._pool, task_history._pool,
        audit._pool, cron_store._pool, user_prefs._pool,
    ]
    for p in pools:
        p.reset()
    yield
    for p in pools:
        p.reset()


@pytest.fixture
def client():
    """挂载完整 app（含中间件），base_url 用回环地址以满足 Host 校验。"""
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def tokens():
    """超管与普通用户各一个有效令牌。"""
    from core.auth import authenticate, create_user, issue_token

    root = authenticate("root", "sup3r-secret-pw")
    assert root and root["id"] == "0"
    create_user("alice", "alice-password")
    alice = authenticate("alice", "alice-password")
    assert alice and alice["role"] == "user"
    return {"root": issue_token(root), "alice": issue_token(alice)}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 匿名 ──

def test_anonymous_blocked_on_all_business_endpoints(client):
    """多用户模式下，匿名访问任何业务端点都必须 401。"""
    for path in (
        "/api/tool/list", "/api/agent/list", "/api/data/list",
        "/api/model/list", "/api/apis/list", "/api/task/list",
        "/api/file/workspace/tree", "/api/chat/sessions",
    ):
        assert client.get(path).status_code == 401, path


def test_anonymous_allowed_only_for_login_and_health(client):
    """放行清单只有探活与登录相关，且 /me 不泄露身份。"""
    assert client.get("/api/health").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is False
    assert client.post("/api/auth/login",
                       json={"username": "root", "password": "wrong"}).status_code == 401


def test_invalid_or_expired_token_rejected(client):
    """无效令牌直接 401，绝不静默降级为匿名。"""
    r = client.get("/api/tool/list", headers=_bearer("not-a-real-token"))
    assert r.status_code == 401


# ── 系统级：仅超管 ──

def test_system_endpoints_reject_normal_user(client, tokens):
    """config/llm 读写 .env 与 Key，users/superuser 管账号 → 普通用户一律 403。

    系统级端点由 main.py 的 ``include_router(dependencies=[require_superuser])``
    统一拦下，路由自身不带 Depends——这里验证该挂载级约束确实生效。
    """
    h = _bearer(tokens["alice"])
    for path in (
        "/api/config/llm", "/api/llm/config", "/api/llm/test-connection",
        "/api/auth/users", "/api/auth/superuser",
    ):
        assert client.get(path, headers=h).status_code == 403, path


def test_system_endpoints_allow_superuser(client, tokens):
    h = _bearer(tokens["root"])
    for path in ("/api/config/llm", "/api/llm/config", "/api/auth/users", "/api/auth/superuser"):
        assert client.get(path, headers=h).status_code == 200, path


def test_normal_user_cannot_write_user_management(client, tokens):
    """用户管理的写操作同样受 require_superuser 约束。"""
    h = _bearer(tokens["alice"])
    assert client.post("/api/auth/users", headers=h, json={
        "username": "mallory", "password": "mallory-password",
    }).status_code == 403
    assert client.delete("/api/auth/users/1000", headers=h).status_code == 403


# ── 普通用户可达 ──

def test_normal_user_can_reach_general_endpoints(client, tokens):
    """登录即可用的端点：列表、工作空间、个人偏好、自身信息。"""
    h = _bearer(tokens["alice"])
    for path in (
        "/api/tool/list", "/api/agent/list", "/api/data/list",
        "/api/model/list", "/api/apis/list", "/api/task/list",
        "/api/file/workspace/tree", "/api/tool/workspace",
        "/api/auth/prefs", "/api/cron/options", "/api/task/recent-agents",
    ):
        assert client.get(path, headers=h).status_code == 200, path


def test_normal_user_can_use_own_profile(client, tokens):
    """改自己的显示名/密码是允许的（不动别人的）。"""
    h = _bearer(tokens["alice"])
    r = client.put("/api/auth/me", headers=h, json={"displayName": "爱丽丝"})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers=h).json()["user"]["displayName"] == "爱丽丝"


# ── 数据隔离 ──

def test_user_data_isolated_by_home_directory(client, tokens):
    """alice 上传的文件，bob 用 file_id 也读不到（resolve 限本用户目录）。"""
    up = client.post(
        "/api/file/upload", headers=_bearer(tokens["alice"]),
        files={"file": ("secret.txt", b"alice secret", "text/plain")},
    )
    assert up.status_code == 200, up.text
    fid = up.json()["fileId"]

    # alice 自己读得到
    assert client.get(f"/api/file/{fid}", headers=_bearer(tokens["alice"])).status_code == 200

    # bob 登录后拿同一个 file_id → 404（解析限本用户目录内）
    from core.auth import authenticate, create_user, issue_token
    create_user("bob", "bob-password")
    bob = issue_token(authenticate("bob", "bob-password"))
    r = client.get(f"/api/file/{fid}", headers=_bearer(bob))
    assert r.status_code == 404


# ── 目录穿越 ──

def test_scan_directory_rejects_path_traversal(client, tokens):
    """数据集扫描只允许当前用户目录内，绝对路径/.. 一律 403。"""
    h = _bearer(tokens["alice"])
    for bad in ("/etc", "../..", "/root/SB-USER/0"):
        r = client.post("/api/data/scan-directory", headers=h, json={"path": bad})
        assert r.status_code == 403, (bad, r.status_code, r.text)


# ── 执行身份（子进程落盘目录的决定因素）──

def test_chat_send_ignores_client_user_id(client, tokens, monkeypatch):
    """客户端在请求体里写 userId=0 也不能改变子进程身份。

    子进程的 SOTABAND_USER_ID 决定工具产物与文件读写落在哪个用户目录；
    若采信客户端传值，任何普通用户传 userId=0 即可写超管目录。
    """
    import app.api.routes.chat_routes as cr

    captured: dict = {}

    async def fake_execute_stream(agent_id, params=None, context=None, timeout=None):
        captured["context"] = context
        yield {"event": "done", "data": {"messageId": "m1"}}

    monkeypatch.setattr(cr.agent_factory, "is_running", lambda _aid: True)
    monkeypatch.setattr(cr.agent_factory, "execute_stream", fake_execute_stream)

    r = client.post(
        "/api/chat/send", headers=_bearer(tokens["alice"]),
        json={"content": "hi", "userId": "0", "sessionId": "s-escape"},
    )
    assert r.status_code == 200, r.text
    # alice 的 token 对应 uid=1000，客户端传的 "0" 必须被忽略
    assert captured["context"]["user_id"] == "1000"


def test_agent_execute_uses_login_identity(client, tokens, monkeypatch):
    """/api/agent/{id}/execute 同样按登录用户执行，不落 default 目录。"""
    import app.api.routes.agent_routes as ar

    captured: dict = {}

    async def fake_execute(agent_id, content, context=None):
        captured["context"] = context
        yield {"event": "done", "data": {}}

    async def fake_get(agent_id):
        return {"id": agent_id, "name": "x"}

    monkeypatch.setattr(ar.agent_factory, "is_running", lambda _aid: True)
    monkeypatch.setattr(ar.agent_factory, "execute", fake_execute)
    monkeypatch.setattr(ar.registry, "get", fake_get)

    r = client.post("/api/agent/some-agent/execute", headers=_bearer(tokens["alice"]),
                    json={"content": "hi"})
    assert r.status_code == 200, r.text
    assert captured["context"]["user_id"] == "1000"


def test_get_current_user_id_does_not_cache(monkeypatch):
    """身份不能缓存：交互 Agent 是所有用户共用一个子进程，身份每轮都变。

    缓存住第一个用户的 uid 后，第二个用户会拿到前一个人的数据集归属
    （api_data 等资源过滤都依赖它）——跨用户串号。
    """
    from core.user_context import get_current_user_id

    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    assert get_current_user_id() == "0"

    monkeypatch.setenv("SOTABAND_USER_ID", "1000")
    assert get_current_user_id() == "1000"

    # 同一进程内换用户：必须立刻生效
    monkeypatch.setenv("SOTABAND_USER_ID", "1001")
    assert get_current_user_id() == "1001"


def test_login_identity_wins_over_client_supplied_owner():
    """平台调用以登录态为准，客户端传的 owner 只是无登录态时的兜底。"""
    from core.auth.session import AuthUser, reset_current_user, set_current_user
    from core.chat_control.gateway import resolve_owner

    token = set_current_user(AuthUser(id="1000", username="alice", role="user"))
    try:
        assert resolve_owner("0") == "1000"          # 谎称超管无效
        assert resolve_owner("default") == "1000"    # 占位符仍取登录身份
    finally:
        reset_current_user(token)


def test_chat_request_drops_client_user_id():
    """ChatRequest 不再接收 userId 字段——身份只能由服务端判定。"""
    from app.api.schemas.chat_schemas import ChatRequest

    req = ChatRequest(content="hi", **{"userId": "0"})
    assert not hasattr(req, "user_id")


# ── Agent 可见性 ──

def _register_agent(client, token, agent_id):
    return client.post(
        "/api/agent/register", headers=_bearer(token),
        json={
            "specMd": f"---\nid: {agent_id}\nname: {agent_id}\nrole: task\n---\n\n# {agent_id}\n",
            "agentId": agent_id, "agentName": agent_id, "role": "task",
        },
    )


def _make_bob():
    from core.auth import authenticate, create_user, issue_token
    create_user("bob", "bob-password")
    return issue_token(authenticate("bob", "bob-password"))


def test_normal_user_agent_is_private(client, tokens):
    """普通用户创建的 Agent 仅自己可见/可用，别人一律 404。"""
    bob = _make_bob()
    r = _register_agent(client, tokens["alice"], "agent-alice")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["visibility"] == "private"

    # alice 自己正常
    assert client.get("/api/agent/agent-alice", headers=_bearer(tokens["alice"])).status_code == 200

    # bob：列表里没有、直取 404、起停删一律 404
    ids = [a["id"] for a in client.get("/api/agent/list", headers=_bearer(bob)).json()["agents"]]
    assert "agent-alice" not in ids
    assert client.get("/api/agent/agent-alice", headers=_bearer(bob)).status_code == 404
    for path in ("/api/agent/agent-alice/start", "/api/agent/agent-alice/restart"):
        assert client.post(path, headers=_bearer(bob)).status_code == 404, path
    assert client.post("/api/agent/agent-alice/execute", headers=_bearer(bob),
                       json={"content": "x"}).status_code == 404
    assert client.delete("/api/agent/agent-alice", headers=_bearer(bob)).status_code == 404


def test_superuser_agent_is_public(client, tokens):
    """超管创建的 Agent 供全员使用。"""
    bob = _make_bob()
    r = _register_agent(client, tokens["root"], "agent-root")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["visibility"] == "public"

    ids = [a["id"] for a in client.get("/api/agent/list", headers=_bearer(bob)).json()["agents"]]
    assert "agent-root" in ids
    assert client.get("/api/agent/agent-root", headers=_bearer(bob)).status_code == 200


def test_builtin_agent_visible_to_normal_user(client, tokens):
    """内置 Agent（随仓库分发）对所有人可见，不因新规则消失。"""
    entry = client.get("/api/agent/orchestrator-agent", headers=_bearer(tokens["alice"]))
    assert entry.status_code == 200, entry.text
    assert entry.json()["id"] == "orchestrator-agent"


def test_superuser_agent_written_to_shared_root(client, tokens):
    """超管发布的 Agent 落共享根——私有根是每用户独立的，不共享就没人读得到。"""
    import json as _json
    from core.resource import paths

    assert _register_agent(client, tokens["root"], "agent-shared-root").status_code == 200

    shared_reg = paths.shared_root() / "agents" / "registry.json"
    assert shared_reg.exists()
    ids = [e["id"] for e in _json.loads(shared_reg.read_text(encoding="utf-8"))]
    assert "agent-shared-root" in ids

    # 不能落在超管自己的私有根里（那样其他用户看不到）
    private_reg = paths.private_root("0") / "agents" / "registry.json"
    if private_reg.exists():
        assert "agent-shared-root" not in [
            e["id"] for e in _json.loads(private_reg.read_text(encoding="utf-8"))
        ]


def test_normal_user_cannot_modify_shared_agent(client, tokens):
    """共享 Agent 只有超管能改：普通用户保存规范 403。"""
    bob = _make_bob()
    assert _register_agent(client, tokens["root"], "agent-no-edit").status_code == 200
    r = client.post("/api/agent/agent-no-edit/save-spec", headers=_bearer(bob),
                    json={"spec_md": "# 改\n"})
    assert r.status_code == 403, (r.status_code, r.text)


# ── 数据集归属隔离 ──

def test_dataset_endpoints_enforce_ownership(client, tokens):
    """数据集是私有资源：他人的一行都读不到，也删不掉。

    注册表是随项目目录走的共享文件，registry.get() 不做归属过滤；
    跨用户访问一律 404（与"不存在"同响应），避免泄露条目是否存在。
    """
    import asyncio
    from core.auth import authenticate, create_user, issue_token
    from core.auth.session import AuthUser, reset_current_user, set_current_user
    from core.resource.registry.data_registry import DataRegistry

    reg = DataRegistry()

    def _register(uid: str, username: str, ds_id: str) -> None:
        token = set_current_user(AuthUser(id=uid, username=username, role="user"))
        try:
            asyncio.run(reg.register({"id": ds_id, "name": ds_id, "data_path": ""}))
        finally:
            reset_current_user(token)

    _register("1000", "alice", "ds-alice")
    create_user("bob", "bob-password")
    bob = issue_token(authenticate("bob", "bob-password"))
    _register("1001", "bob", "ds-bob")

    hbob = _bearer(bob)
    # bob 对 alice 的数据集：读不到（404 而非 403，不泄露存在性）
    for method, path in (
        ("get", "/api/data/ds-alice"),
        ("get", "/api/data/ds-alice/files"),
        ("get", "/api/data/ds-alice/raw?path=x.csv"),
        ("get", "/api/data/ds-alice/archive"),
        ("get", "/api/data/ds-alice/preview"),
        ("delete", "/api/data/ds-alice"),
    ):
        r = getattr(client, method)(path, headers=hbob)
        assert r.status_code == 404, (method, path, r.status_code)

    # alice 自己正常，且同样看不到 bob 的
    halice = _bearer(tokens["alice"])
    assert client.get("/api/data/ds-alice", headers=halice).status_code == 200
    assert client.get("/api/data/ds-bob", headers=halice).status_code == 404

    # 搜索同样按归属过滤：bob 搜不到 alice 的数据集
    r = client.get("/api/data/search/find?q=ds", headers=hbob)
    assert r.status_code == 200, r.text
    ids = [d["id"] for d in r.json()["datasets"]]
    assert "ds-bob" in ids and "ds-alice" not in ids

    # include_others 会下发他人的 data_path，仅超管可用
    assert client.get("/api/data/list?include_others=true", headers=hbob).status_code == 403
    assert client.get("/api/data/list?include_others=true",
                      headers=_bearer(tokens["root"])).status_code == 200
