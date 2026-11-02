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
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.cron import runner, store
from core.cron.cron_parse import CronError, next_run_at

# sleep 单段上限（秒）：即便 cancel 传播异常，也能周期性重读任务配置自愈
MAX_SLEEP_SLICE = 300.0


class CronScheduler:
    """任务调度器单例（模块级 cron_scheduler）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._running: set[str] = set()  # 正在执行中的 job_id（防重叠）
        self._started = False

    # ── 生命周期 ──

    async def start(self) -> None:
        """启动：载入所有启用任务；对过期 next_run_at 按 misfire 策略处理。"""
        if self._started:
            return
        self._started = True
        for job in store.list_jobs(include_disabled=False):
            try:
                self._spawn(job["id"])
            except Exception as exc:  # 单任务隔离：坏任务不影响其它任务与启动
                store.update(job["id"], {"last_status": f"scheduler_error: {exc}"})

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

    def upsert(self, job_id: str) -> None:
        """任务创建/更新/启停后调用：重启该任务的调度循环。"""
        if not self._started:
            return
        self._cancel(job_id)
        job = store.get(job_id)
        if job and job["enabled"]:
            self._spawn(job_id)

    def remove(self, job_id: str) -> None:
        self._cancel(job_id)

    # ── 内部 ──

    def _cancel(self, job_id: str) -> None:
        t = self._tasks.pop(job_id, None)
        if t:
            t.cancel()

    def _spawn(self, job_id: str) -> None:
        loop_task = asyncio.create_task(self._job_loop(job_id), name=f"cron-{job_id}")
        self._tasks[job_id] = loop_task

    def _compute_next(self, job: dict[str, Any]) -> float:
        ts = next_run_at(job["cron"], job["timezone"]).timestamp()
        store.update(job["id"], {"next_run_at": ts})
        return ts

    async def _job_loop(self, job_id: str) -> None:
        try:
            # 启动恢复：misfire=fire_once 且已过期 → 先补跑一次
            job = store.get(job_id)
            if not job:
                return
            nxt = job.get("next_run_at")
            if job["enabled"] and job.get("misfire") == "fire_once" and nxt and nxt < time.time():
                await self._fire_guarded(job, "schedule")
            while True:
                job = store.get(job_id)
                if not job:  # 任务被删除，循环自然退出
                    return
                if not job["enabled"]:
                    return  # 禁用：upsert() 会负责停止/重启本循环
                try:
                    nxt = self._compute_next(job)
                except CronError as exc:
                    store.update(job_id, {"last_status": f"cron_invalid: {exc}"})
                    return
                # sleep 到点（分片，避免超长 sleep 期间配置变更不能自愈）
                while True:
                    delay = nxt - time.time()
                    if delay <= 0:
                        break
                    await asyncio.sleep(min(delay, MAX_SLEEP_SLICE))
                await self._fire_guarded(job, "schedule")
        except asyncio.CancelledError:
            raise
        except Exception:
            # 循环崩溃兜底：30s 后重试，不让单个任务的意外异常静默失联
            try:
                await asyncio.sleep(30)
                if self._started and store.get(job_id):
                    # 本 task 已失效，先清掉旧引用再重启循环
                    if self._tasks.get(job_id) is asyncio.current_task():
                        self._tasks.pop(job_id, None)
                    self._spawn(job_id)
            except asyncio.CancelledError:
                raise

    async def _fire_guarded(self, job: dict[str, Any], trigger: str) -> None:
        job_id = job["id"]
        if job_id in self._running:  # max_instances=1 + coalesce
            run_id = store.run_start(job_id, trigger)
            store.run_finish(run_id, "skipped", message="上一次执行尚未结束，本次跳过")
            return
        self._running.add(job_id)
        try:
            await runner.fire(job, trigger=trigger)
        finally:
            self._running.discard(job_id)

    def trigger_now(self, job_id: str) -> dict[str, Any]:
        """手动立即执行（API 层调用）：后台执行、立即返回，前端轮询历史。"""
        job = store.get(job_id)
        if not job:
            return {"status": "failed", "error": f"任务不存在: {job_id}"}
        if job_id in self._running:
            return {"status": "failed", "error": "任务正在执行中，请稍后再试"}
        asyncio.create_task(self._fire_guarded(job, "manual"), name=f"cron-manual-{job_id}")
        return {"status": "started", "job_id": job_id}


cron_scheduler = CronScheduler()
