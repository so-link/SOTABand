"""5 字段 cron 表达式解析与下次触发时间计算（零第三方依赖）。

不使用 apscheduler 的原因：部署环境 PyPI 不可达（见实施记录），且本模块
只需「校验 + 求下次触发时间」两件事，自研可控且可单测。

支持语法：
    分 时 日 月 周        （标准 unix cron，最小粒度 1 分钟）
    *                    任意值
    5                    精确值
    1,2,3                列表
    1-5                  区间
    */15  1-30/5         步长（含区间带步长）
    周字段 0 与 7 均表示周日；其余字段不支持名称（MON/JAN 等）。

时区语义：cron 在 job.timezone 的本地墙上时间里匹配，因此夏令时切换不会
让任务时间漂移（中国无夏令时，实际等价于本地时间）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 各字段取值范围：(min, max, 是否允许 dom/dow 联动规则)
_FIELDS = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("dow", 0, 6),
]

_MAX_LOOKAHEAD_DAYS = 400


class CronError(ValueError):
    """cron 表达式非法。"""


def _parse_field(text: str, lo: int, hi: int, name: str) -> set[int]:
    text = (text or "").strip()
    if not text:
        raise CronError(f"{name} 字段为空")
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{name} 字段存在空项: {text!r}")
        step = 1
        has_step = False
        if "/" in part:
            part, _, step_s = part.partition("/")
            part = part.strip()
            has_step = True
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"{name} 字段步长非法: {step_s!r}") from None
            if step <= 0:
                raise CronError(f"{name} 字段步长必须为正整数")
        if part == "*":
            start, end = lo, hi
        elif "-" in part.lstrip("-"):
            a_s, _, b_s = part.partition("-")
            start, end = _int(a_s, name), _int(b_s, name)
        else:
            v = _int(part, name)
            # "5/10" 按 unix cron 惯例等价于 5-max/10
            start, end = (v, hi) if has_step else (v, v)
        if start > end:
            raise CronError(f"{name} 字段区间反向: {part!r}")
        for v in range(start, end + 1, step):
            if not (lo <= v <= hi):
                raise CronError(f"{name} 字段取值 {v} 超出范围 {lo}-{hi}")
            values.add(v)
    if not values:
        raise CronError(f"{name} 字段无有效取值")
    return values


def _int(s: str, name: str) -> int:
    try:
        return int(s.strip())
    except ValueError:
        raise CronError(f"{name} 字段含非数字: {s!r}") from None


class CronSpec:
    """解析后的 cron 表达式。

    dom_star / dow_star 记录「日」「周」是否写为 *：标准 cron 规定两者都
    受限时取并集（OR），任一为 * 时取交集（AND）。搞错这一点会让
    "0 9 * 1-5" 之类的表达式行为反直觉。
    """

    __slots__ = ("minute", "hour", "day", "month", "dow", "dom_star", "dow_star", "raw")

    def __init__(self, raw: str):
        parts = raw.split()
        if len(parts) != 5:
            raise CronError(f"cron 必须是 5 个字段（分 时 日 月 周），当前 {len(parts)} 个: {raw!r}")
        self.raw = raw
        self.dom_star = parts[2].strip() == "*"
        self.dow_star = parts[4].strip() == "*"
        self.minute = _parse_field(parts[0], 0, 59, "分钟")
        self.hour = _parse_field(parts[1], 0, 23, "小时")
        self.day = _parse_field(parts[2], 1, 31, "日")
        self.month = _parse_field(parts[3], 1, 12, "月")
        dow = _parse_field(parts[4], 0, 7, "星期")
        self.dow = {0 if v == 7 else v for v in dow}

    def matches(self, dt: datetime) -> bool:
        if dt.minute not in self.minute or dt.hour not in self.hour:
            return False
        return dt.month in self.month and self._day_matches(dt)

    def next_after(self, after: datetime, tz: ZoneInfo) -> datetime:
        """返回严格晚于 after 的下一个匹配时刻（tz 本地时间，带 tzinfo）。"""
        dt = (after.astimezone(tz) + timedelta(minutes=1)).replace(second=0, microsecond=0)
        hours, minutes = sorted(self.hour), sorted(self.minute)
        # 逐日粗筛（整日不匹配直接跳天），命中日内再取当天最早的匹配时分，
        # 比逐分钟推进在低频表达式上快几个数量级。
        deadline = dt + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        while dt <= deadline:
            if dt.month in self.month and self._day_matches(dt):
                for h in hours:
                    if h < dt.hour:
                        continue
                    for m in minutes:
                        if h == dt.hour and m < dt.minute:
                            continue
                        return dt.replace(hour=h, minute=m)
            dt = _start_of_next_day(dt)
        raise CronError(f"未来 {_MAX_LOOKAHEAD_DAYS} 天内没有匹配时刻: {self.raw!r}")

    def _day_matches(self, dt: datetime) -> bool:
        cron_dow = (dt.weekday() + 1) % 7
        if self.dom_star:
            return cron_dow in self.dow
        if self.dow_star:
            return dt.day in self.day
        return dt.day in self.day or cron_dow in self.dow


def _start_of_next_day(dt: datetime) -> datetime:
    return (dt + timedelta(days=1)).replace(hour=0, minute=0)


def validate_cron(expr: str) -> CronSpec:
    """校验表达式；非法抛 CronError。"""
    return CronSpec(expr or "")


def validate_timezone(tz_name: str) -> str:
    name = (tz_name or "").strip() or "Asia/Shanghai"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f"时区不存在: {name}") from exc
    return name


def next_run_at(expr: str, tz_name: str, after: datetime | None = None) -> datetime:
    """下次触发时间（aware datetime）。"""
    tz = ZoneInfo(validate_timezone(tz_name))
    spec = validate_cron(expr)
    now = after or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return spec.next_after(now, tz)
