"""任务注册表归属（8.1 多用户）回归测试。

修复前：任何登录用户都能 list 到别人的训练任务（含 ``command`` / ``cwd`` /
``log_path``），也能 cancel 别人的任务。现在：

- 提交时把 ``owner`` 写进任务记录（HTTP 用 ``ctx.user_id``，Agent 子进程用
  当前执行身份）；
- list / get / cancel 按 owner 过滤，他人的任务一律 404（不确认存在性）；
- 内部调用（worker / workflow / 脚本）传 ``owner=None``，不受过滤影响。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.task import task_manager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "RUNS_DIR", tmp_path / "tasks" / "runs")
    monkeypatch.setattr(task_manager, "REGISTRY_FILE", tmp_path / "tasks" / "registry.json")
    return task_manager.TaskManager()


@pytest.fixture
def no_real_worker(monkeypatch):
    """不真正拉起 worker 进程，也不执行 taskkill。"""
    calls: list[list[str]] = []

    class _Proc:
        pid = 4242

    def _run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(task_manager.subprocess, "run", _run)
    return calls


def _ctx(user_id: str, role: str = "member"):
    return SimpleNamespace(user_id=user_id, tenant_role=role)


# ══════════════════════════════════════════════════════════════════
# 1) TaskManager：owner 记录与过滤
# ══════════════════════════════════════════════════════════════════


def test_submit_records_owner_and_list_is_filtered(manager, no_real_worker):
    task_a = manager.submit(name="a", command=["python", "a.py"], owner="user-a")
    task_b = manager.submit(name="b", command=["python", "b.py"], owner="user-b")

    assert task_a["owner"] == "user-a"
    assert task_b["owner"] == "user-b"
    assert [task["id"] for task in manager.list_all(owner="user-a")] == [task_a["id"]]
    assert [task["id"] for task in manager.list_all(owner="user-b")] == [task_b["id"]]
    # 内部调用不过滤
    assert {task["id"] for task in manager.list_all()} == {task_a["id"], task_b["id"]}


def test_get_other_users_task_is_hidden(manager, no_real_worker):
    task_b = manager.submit(name="b", command=["python", "b.py"], owner="user-b")

    assert manager.get(task_b["id"], owner="user-a") is None
    assert manager.get(task_b["id"], owner="user-b")["id"] == task_b["id"]
    # 内部调用（worker / workflow）仍能按 id 读取
    assert manager.get(task_b["id"])["id"] == task_b["id"]


def test_cancel_other_users_task_is_denied(manager, no_real_worker):
    task_a = manager.submit(name="a", command=["python", "a.py"], owner="user-a")

    assert manager.cancel(task_a["id"], owner="user-b") is None
    assert manager.get(task_a["id"])["status"] == "queued", "他人 cancel 不得改变任务状态"

    cancelled = manager.cancel(task_a["id"], owner="user-a")
    assert cancelled["status"] == "cancelled"


def test_submit_defaults_owner_to_current_identity(manager, no_real_worker, monkeypatch):
    """Agent 子进程提交任务时不传 owner：按当前执行身份（env）归属。"""
    monkeypatch.setenv("SOTABAND_USER_ID", "uuid-of-current-user")

    task = manager.submit(name="from-agent", command=["python", "x.py"])

    assert task["owner"] == "uuid-of-current-user"


def test_legacy_tasks_without_owner_are_fail_closed(manager, no_real_worker):
    """8.1 之前的历史记录没有 owner：对 HTTP 用户不可见（不泄露 command/log）。"""
    task = manager.submit(name="legacy", command=["python", "x.py"], owner="user-a")
    record = dict(task)
    record.pop("owner", None)
    task_manager.REGISTRY_FILE.write_text(json.dumps([record]), encoding="utf-8")

    assert manager.list_all(owner="user-a") == []
    assert manager.get(task["id"], owner="user-a") is None
    # 内部调用仍可访问
    assert manager.get(task["id"])["id"] == task["id"]


# ══════════════════════════════════════════════════════════════════
# 2) HTTP 路由：list/get/cancel 只暴露自己的任务
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_task_routes_only_expose_own_tasks(monkeypatch, manager, no_real_worker):
    from app.api.routes import task_routes

    monkeypatch.setattr(task_routes, "manager", manager)
    task_a = manager.submit(name="a", command=["python", "a.py"], owner="user-a")
    task_b = manager.submit(name="b", command=["python", "b.py"], owner="user-b")

    listed = await task_routes.list_tasks(limit=100, ctx=_ctx("user-a"))
    assert [task["id"] for task in listed["tasks"]] == [task_a["id"]]

    with pytest.raises(HTTPException) as exc:
        await task_routes.get_task(task_b["id"], ctx=_ctx("user-a"))
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await task_routes.cancel_task(task_b["id"], ctx=_ctx("user-a"))
    assert exc.value.status_code == 404
    assert manager.get(task_b["id"])["status"] == "queued"

    # 本人可以读取并取消
    assert (await task_routes.get_task(task_a["id"], ctx=_ctx("user-a")))["task"]["id"] == task_a["id"]
    cancelled = await task_routes.cancel_task(task_a["id"], ctx=_ctx("user-a"))
    assert cancelled["task"]["status"] == "cancelled"
