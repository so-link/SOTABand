"""core/cron/cron_parse.py 单元测试。"""

from datetime import datetime

import pytest

pytestmark = pytest.mark.unit

from zoneinfo import ZoneInfo

from core.cron.cron_parse import CronError, next_run_at, validate_cron, validate_timezone

TZ = ZoneInfo("Asia/Shanghai")


def dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


class TestValidate:
    def test_valid_expressions(self):
        for expr in [
            "0 17 * * 1-5", "*/15 * * * *", "30 9 * * 7", "0 0 1 * *",
            "5,35 8-12 * * *", "0-30/10 * * * *", "5/10 * * * *",
            "0 12 * * 0",  # 0=周日
        ]:
            validate_cron(expr)

    @pytest.mark.parametrize("expr", [
        "* * * *",            # 4 字段
        "* * * * * *",        # 6 字段
        "60 * * * *",         # 越界
        "* 24 * * *",
        "5-1 * * * *",        # 区间反向
        "*/0 * * * *",        # 步长为 0
        "a * * * *",          # 非数字
        "1,,2 * * * *",       # 空项
        "0 12 * * 8",         # dow 越界
    ])
    def test_invalid(self, expr):
        with pytest.raises(CronError):
            validate_cron(expr)

    def test_timezone(self):
        assert validate_timezone("") == "Asia/Shanghai"
        assert validate_timezone("UTC") == "UTC"
        with pytest.raises(CronError):
            validate_timezone("Mars/Olympus")


class TestNextRun:
    def test_weekday_17(self):
        # 周一 12:00 → 同一周一 17:00
        assert next_run_at("0 17 * * 1-5", "Asia/Shanghai", dt(2026, 11, 2, 12, 0)) == dt(2026, 11, 2, 17, 0)
        # 周一 18:00 → 周二 17:00
        assert next_run_at("0 17 * * 1-5", "Asia/Shanghai", dt(2026, 11, 2, 18, 0)) == dt(2026, 11, 3, 17, 0)
        # 周五 18:00 → 下周一 17:00
        assert next_run_at("0 17 * * 1-5", "Asia/Shanghai", dt(2026, 11, 6, 18, 0)) == dt(2026, 11, 9, 17, 0)

    def test_step(self):
        assert next_run_at("*/15 * * * *", "Asia/Shanghai", dt(2026, 11, 2, 10, 1)) == dt(2026, 11, 2, 10, 15)
        assert next_run_at("*/15 * * * *", "Asia/Shanghai", dt(2026, 11, 2, 10, 46)) == dt(2026, 11, 2, 11, 0)

    def test_strictly_after(self):
        # 恰好命中当刻也要严格晚于（下一分钟粒度内无匹配则顺延）
        assert next_run_at("30 9 * * *", "Asia/Shanghai", dt(2026, 11, 2, 9, 30)) == dt(2026, 11, 3, 9, 30)

    def test_dow_zero_and_seven_are_sunday(self):
        # 2026-11-08 是周日
        assert next_run_at("0 9 * * 0", "Asia/Shanghai", dt(2026, 11, 2, 0, 0)) == dt(2026, 11, 8, 9, 0)
        assert next_run_at("0 9 * * 7", "Asia/Shanghai", dt(2026, 11, 2, 0, 0)) == dt(2026, 11, 8, 9, 0)

    def test_dom_dow_or_semantics(self):
        # 日=1 或 周三 都触发（两者都受限时取并集）
        # 2026-11-04 是周三，2026-12-01 是周二（日=1）
        assert next_run_at("0 8 1 * 3", "Asia/Shanghai", dt(2026, 11, 2, 9, 0)) == dt(2026, 11, 4, 8, 0)
        assert next_run_at("0 8 1 * 3", "Asia/Shanghai", dt(2026, 11, 27, 9, 0)) == dt(2026, 12, 1, 8, 0)

    def test_dom_star_dow_and_semantics(self):
        # dom=* 时仅看 dow：周三 9:00
        assert next_run_at("0 9 * * 3", "Asia/Shanghai", dt(2026, 11, 2, 0, 0)) == dt(2026, 11, 4, 9, 0)

    def test_month_rollover(self):
        assert next_run_at("0 0 1 * *", "Asia/Shanghai", dt(2026, 11, 15, 8, 0)) == dt(2026, 12, 1, 0, 0)

    def test_year_rollover(self):
        assert next_run_at("0 6 1 1 *", "Asia/Shanghai", dt(2026, 6, 1, 0, 0)) == dt(2027, 1, 1, 6, 0)

    def test_v5_step_equiv_range(self):
        # 5/10 ≡ 5-59/10
        assert next_run_at("5/10 * * * *", "Asia/Shanghai", dt(2026, 11, 2, 10, 0)) == dt(2026, 11, 2, 10, 5)
        assert next_run_at("5/10 * * * *", "Asia/Shanghai", dt(2026, 11, 2, 10, 55)) == dt(2026, 11, 2, 11, 5)


class TestSpecAttrs:
    def test_star_flags(self):
        s = validate_cron("0 8 1 * 3")
        assert s.dom_star is False and s.dow_star is False
        s = validate_cron("0 8 * * 3")
        assert s.dom_star is True and s.dow_star is False

    def test_dow_normalization(self):
        assert validate_cron("0 0 * * 7").dow == {0}
