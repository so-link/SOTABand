"""进程内 cron 调度循环（零第三方依赖）。

不使用 apscheduler 的原因同 cron_parse：部署环境 PyPI 不可达。实现上每个
启用任务一个 asyncio 循环——算下次时间 → sleep 到点 → 防重叠检查 → 交给
runner.fire 执行 → 重算下一轮。

关键语义（与 docs/cron-scheduler-design.md 一致）：
- max_instances=1：同一任务上一次还没跑完就到点，本次记 skipped 不并发。
- coalesce：sleep 醒来若已错过多个周期，只补一次。
- misfire 策略在启动恢复时生效：next_run_at 已过期 → skip 直接重算未来，
  fire_once 先补跑一次再进入正常循环。
- 启动恢复失败隔离：单个任务载入异常只标记该任务，不拖垮整个调度器。

前提：后端单 worker 运行（uvicorn 默认），进程内调度不会重复触发；若将来
多 worker，需要把调度器收敛到独立进程或加文件锁。

多用户（2026-11）：任务存在各用户自己的 platform.db 里，调度器是系统级循环，
因此内部一律以 ``(uid, job_id)`` 为键；路由层调用带 uid（= 当前登录用户），
启动恢复用 ``store.iter_jobs()`` 跨用户遍历。job_id 只在单个用户的库内唯一，
所以必须带 uid 才能定位——顺带避免了不同用户撞同一个 job_id 时互相顶掉循环。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.cron import runner, store
from core.cron.cron_parse import CronError, next_run_at
from core.user_paths import current_uid


def _uid_or_current(uid: str | None) -> str:
    """路由层调用总是带 uid（当前登录用户）；缺省时回退当前身份。"""
    return uid if uid is not None else current_uid()


def _alive_uids() -> set[str]:
    """用户表中存在的 uid——删号用户（目录残留）的任务不再触发。

    超管行恒在表中，单用户模式下匿名超管（uid=0）的任务不受影响。
    """
    from core.auth import known_uids

    return known_uids()

# sleep 单段上限（秒）：即便 cancel 传播异常，也能周期性重读任务配置自愈
MAX_SLEEP_SLICE = 300.0


def _key(uid: str, job_id: str) -> str:
    return f"{uid}:{job_id}"


class CronScheduler:
    """任务调度器单例（模块级 cron_scheduler）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._running: set[str] = set()  # 正在执行中的 (uid, job_id)（防重叠）
        self._started = False

    # ── 生命周期 ──

    async def start(self) -> None:
        """启动：载入所有用户的所有启用任务；对过期 next_run_at 按 misfire 策略处理。"""
        if self._started:
            return
        self._started = True
        alive = _alive_uids()
        for uid, job in store.iter_jobs(include_disabled=False):
            if uid not in alive:  # 用户已删号（目录残留但不在用户表）：不再调度
                continue
            try:
                self._spawn(uid, job["id"])
            except Exception as exc:  # 单任务隔离：坏任务不影响其它任务与启动
                store.update(job["id"], {"last_status": f"scheduler_error: {exc}"}, uid=uid)

    async def shutdown(self) -> None:
        self._started = False
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    # ── 动态变更（供 API 层调用） ──

    def upsert(self, job_id: str, uid: str | None = None) -> None:
        """任务创建/更新/启停后调用：重启该任务的调度循环。"""
        if not self._started:
            return
        uid = _uid_or_current(uid)
        self._cancel(uid, job_id)
        job = store.get(job_id, uid)
        if job and job["enabled"]:
            self._spawn(uid, job_id)

    def remove(self, job_id: str, uid: str | None = None) -> None:
        self._cancel(_uid_or_current(uid), job_id)

    # ── 内部 ──

    def _cancel(self, uid: str, job_id: str) -> None:
        t = self._tasks.pop(_key(uid, job_id), None)
        if t:
            t.cancel()

    def _spawn(self, uid: str, job_id: str) -> None:
        loop_task = asyncio.create_task(
            self._job_loop(uid, job_id), name=f"cron-{uid}-{job_id}"
        )
        self._tasks[_key(uid, job_id)] = loop_task

    def _compute_next(self, job: dict[str, Any], uid: str) -> float:
        ts = next_run_at(job["cron"], job["timezone"]).timestamp()
        store.update(job["id"], {"next_run_at": ts}, uid=uid)
        return ts

    async def _job_loop(self, uid: str, job_id: str) -> None:
        try:
            # 启动恢复：misfire=fire_once 且已过期 → 先补跑一次
            job = store.get(job_id, uid)
            if not job:
                return
            nxt = job.get("next_run_at")
            if job["enabled"] and job.get("misfire") == "fire_once" and nxt and nxt < time.time():
                await self._fire_guarded(uid, job, "schedule")
            while True:
                job = store.get(job_id, uid)
                if not job:  # 任务被删除，循环自然退出
                    return
                if not job["enabled"]:
                    return  # 禁用：upsert() 会负责停止/重启本循环
                try:
                    nxt = self._compute_next(job, uid)
                except CronError as exc:
                    store.update(job_id, {"last_status": f"cron_invalid: {exc}"}, uid=uid)
                    return
                # sleep 到点（分片，避免超长 sleep 期间配置变更不能自愈）
                while True:
                    delay = nxt - time.time()
                    if delay <= 0:
                        break
                    await asyncio.sleep(min(delay, MAX_SLEEP_SLICE))
                await self._fire_guarded(uid, job, "schedule")
        except asyncio.CancelledError:
            raise
        except Exception:
            # 循环崩溃兜底：30s 后重试，不让单个任务的意外异常静默失联
            try:
                await asyncio.sleep(30)
                if self._started and store.get(job_id, uid):
                    # 本 task 已失效，先清掉旧引用再重启循环
                    key = _key(uid, job_id)
                    if self._tasks.get(key) is asyncio.current_task():
                        self._tasks.pop(key, None)
                    self._spawn(uid, job_id)
            except asyncio.CancelledError:
                raise

    async def _fire_guarded(self, uid: str, job: dict[str, Any], trigger: str) -> None:
        if uid not in _alive_uids():  # 删号用户在循环睡眠期间被删：不再触发
            return
        key = _key(uid, job["id"])
        if key in self._running:  # max_instances=1 + coalesce
            run_id = store.run_start(job["id"], trigger, uid=uid)
            store.run_finish(run_id, "skipped", message="上一次执行尚未结束，本次跳过", uid=uid)
            return
        self._running.add(key)
        try:
            await runner.fire(job, trigger=trigger, uid=uid)
        finally:
            self._running.discard(key)

    def trigger_now(self, job_id: str, uid: str | None = None) -> dict[str, Any]:
        """手动立即执行（API 层调用）：后台执行、立即返回，前端轮询历史。"""
        uid = _uid_or_current(uid)
        job = store.get(job_id, uid)
        if not job:
            return {"status": "failed", "error": f"任务不存在: {job_id}"}
        key = _key(uid, job_id)
        if key in self._running:
            return {"status": "failed", "error": "任务正在执行中，请稍后再试"}
        asyncio.create_task(self._fire_guarded(uid, job, "manual"), name=f"cron-manual-{key}")
        return {"status": "started", "job_id": job_id}


cron_scheduler = CronScheduler()
