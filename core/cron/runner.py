"""定时任务触发执行器。

两条执行路径：
- kind=tool  → ToolExecutor.execute()：独立子进程沙箱、无常驻状态，最适合无人值守。
- kind=agent → agent_factory：execute_stream 不冷启动（见 factory.execute_stream
  对 self._processes 的判空），所以未运行时要先 start；若进程是本任务临时
  拉起的，执行完再 stop，避免定时任务把 Agent 常驻在内存里。

身份：后台触发没有 HTTP 请求，core.auth.session 的 contextvar 为空，而
ToolRegistry / DataRegistry 的归属判断依赖 get_current_user_id()。必须按任务
owner 显式设置身份，否则无人值守写入的资源会归属到错误用户（或落到匿名）。
每次触发在独立 asyncio task 中运行，contextvar 互不污染。

库归属：任务与执行历史存在**归属用户的 platform.db** 里，所以 fire() 必须带
uid 读写（调度器跨用户遍历，不能靠 current_uid()）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.cron import store
from core.cron.cron_parse import CronError, next_run_at, validate_cron, validate_timezone


def _set_identity(owner: str):
    """按任务归属者设置请求级身份；owner 为空则保持匿名。"""
    if not owner:
        return None
    from core.auth.session import AuthUser, set_current_user
    return set_current_user(AuthUser(id=owner, username="", role="user"))


async def fire(job: dict[str, Any], trigger: str = "schedule",
               uid: str | None = None) -> dict[str, Any]:
    """执行一次任务并落库。返回 {status, message, error}。

    uid 是任务归属用户（决定读写哪个库）；job["owner"] 决定执行时的资源身份。
    正常情况下两者相同，分开传是因为 owner 可能为空（历史数据）。
    """
    job_id = job["id"]
    run_id = store.run_start(job_id, trigger, uid=uid)
    token = _set_identity(job.get("owner") or uid or "")
    timeout = float(job.get("timeout") or 600)
    status, message, error, output = "success", "", "", None
    try:
        if job["kind"] == "tool":
            status, message, output = await _run_tool(job, timeout)
        elif job["kind"] == "agent":
            status, message, output = await _run_agent(job, timeout)
        else:
            status, error = "failed", f"未知的任务类型: {job['kind']}"
    except asyncio.TimeoutError:
        status, error = "timeout", f"执行超时（>{int(timeout)}s）"
    except Exception as exc:  # 单次失败不得影响调度循环
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        if token is not None:
            from core.auth.session import reset_current_user
            reset_current_user(token)

    if status != "success":
        error = error or message
    store.run_finish(run_id, status, message=message, output=output, error=error, uid=uid)

    updates: dict[str, Any] = {"last_run_at": time.time(), "last_status": status}
    try:
        updates["next_run_at"] = next_run_at(job["cron"], job["timezone"]).timestamp()
    except CronError:
        updates["next_run_at"] = None
    store.update(job_id, updates, uid=uid)
    return {"status": status, "message": message, "error": error, "run_id": run_id}


async def _run_tool(job: dict[str, Any], timeout: float) -> tuple[str, str, Any]:
    from core.executor.tool_executor import ToolExecutor
    result = await asyncio.wait_for(
        ToolExecutor.execute(job["target_id"], job.get("params") or {}, timeout=None),
        timeout=timeout,
    )
    if not isinstance(result, dict):
        return "failed", "", str(result)
    status = "success" if result.get("status") == "success" else "failed"
    message = str(result.get("message") or "")
    if status == "failed":
        return status, message, result.get("data")
    return status, message, result.get("data")


async def _run_agent(job: dict[str, Any], timeout: float) -> tuple[str, str, Any]:
    from core.agent.factory import agent_factory
    agent_id = job["target_id"]
    started_here = False
    proc = agent_factory._processes.get(agent_id)
    if proc is None or proc.returncode is not None:
        start_result = await agent_factory.start(agent_id)
        if isinstance(start_result, dict) and start_result.get("status") not in (None, "success", "started", "running"):
            return "failed", "", f"Agent 启动失败: {start_result.get('message') or start_result}"
        started_here = True

    texts: list[str] = []
    err = ""
    try:
        # 子进程身份必须显式下发：execute_stream 默认 user_id="default"，
        # 而 SOTABAND_USER_ID 决定工具产物与文件读写落在哪个用户目录。
        # 不传的话所有用户的定时任务都写进 /root/SB-USER/default/（互相可见）。
        agen = agent_factory.execute_stream(
            agent_id, params=job.get("params") or {}, timeout=timeout,
            context={"user_id": str(job.get("owner") or "")},
        )
        deadline = time.monotonic() + timeout + 15
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    ev = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                name = ev.get("event")
                data = ev.get("data") or {}
                if name == "text":
                    texts.append(str(data.get("text") or data.get("content") or ""))
                elif name == "error":
                    err = str(data.get("message") or "Agent 执行失败")
                elif name == "card":
                    title = data.get("title")
                    if title:
                        texts.append(f"[卡片] {title}")
        finally:
            await agen.aclose()
    finally:
        if started_here:
            try:
                await agent_factory.stop(agent_id)
            except Exception:
                pass

    if err:
        return "failed", "", {"error": err, "output": "".join(texts)[:2000]}
    return "success", "".join(texts)[-1500:].strip(), None


# ── 目标资源校验（供 API 层复用） ──

async def check_target(kind: str, target_id: str,
                       owner: str | None = None) -> tuple[bool, str]:
    """校验任务目标存在且可用。返回 (ok, 错误信息)。

    ``owner`` 传入时一并校验 Agent/工具可见性：普通用户创建的私有资源
    不能挂到别人的定时任务上。
    """
    if kind == "tool":
        from core.resource.registry.tool_registry import ToolRegistry
        reg = ToolRegistry()
        entry = await reg.get(target_id)
        if not entry or (owner and not reg.is_visible(entry, owner)):
            return False, f"工具不存在: {target_id}"
        if entry.get("status") and entry["status"] != "active":
            return False, f"工具状态不可用: {entry['status']}"
        return True, ""
    if kind == "agent":
        from core.resource.registry.agent_registry import AgentRegistry
        reg = AgentRegistry()
        entry = await reg.get(target_id)
        if not entry or (owner and not reg.is_visible(entry, owner)):
            return False, f"Agent 不存在: {target_id}"
        return True, ""
    return False, f"未知的任务类型: {kind}"


def validate_job_payload(payload: dict[str, Any]) -> tuple[str, str, float | None]:
    """校验 cron 与时区，返回 (cron, timezone, next_ts)。非法抛 CronError。"""
    cron = (payload.get("cron") or "").strip()
    validate_cron(cron)
    tz = validate_timezone(payload.get("timezone") or "")
    ts = next_run_at(cron, tz).timestamp()
    return cron, tz, ts


def as_local(ts: float | None, tz_name: str = "Asia/Shanghai") -> str:
    """时间戳转本地可读串（用于日志/提示）。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""
