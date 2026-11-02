"""定时任务模块（cron 周期执行工具 / Agent）。

与 config/settings.py 的 SchedulerConfig（算力调度）无关：本模块负责
「时间维度」的周期触发——按 cron 表达式定时调用已注册的工具或 Agent，
并把每次执行结果落到 storage/platform.db 供前端查询。

设计文档见 docs/cron-scheduler-design.md。
"""

from core.cron.scheduler import cron_scheduler

__all__ = ["cron_scheduler"]
