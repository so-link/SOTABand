"""``.env.example`` 完整性（P0-3）测试。

管理员评审意见 3：用户模块新增了配置项，但 ``.env.example`` 没更新，新部署
容易漏配（尤其 ``SOTABAND_WORKLOAD_SECRET`` —— 不配则重启后 Agent 凭证全失效、
多 worker 互不认）。

这里从 ``config/settings.py`` 源码解析所有读取的环境变量，断言示例文件都提到。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_env_example_covers_settings_env_vars():
    sources = [
        ROOT / "config" / "settings.py",
        ROOT / "core" / "resource" / "paths.py",
        ROOT / "core" / "runtime" / "paths.py",
        ROOT / "core" / "agent" / "invoke_bus.py",
    ]
    names: set[str] = set()
    for src in sources:
        text = src.read_text(encoding="utf-8")
        names |= set(re.findall(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"', text))
        names |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]+)"', text))

    # 只跳过测试专用/第三方内部变量
    skip = {"SOTABAND_TEST_DATABASE_URL", "SOTABAND_LOAD_DOTENV"}
    required = {
        n
        for n in names
        if n not in skip
        and (
            n.startswith("SOTABAND_")
            or n.startswith("LLM_")
            or n in ("DATABASE_URL", "STORAGE_BACKEND")
        )
    }
    assert required, "应当从 settings.py 解析出配置项"

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = sorted(n for n in required if n not in example)
    assert not missing, f".env.example 缺少配置项: {missing}"


def test_env_example_flags_workload_secret_as_required():
    """生产必填项要显式标注，否则新部署者会忽略。"""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "SOTABAND_WORKLOAD_SECRET" in example
    idx = example.index("SOTABAND_WORKLOAD_SECRET")
    context = example[max(0, idx - 200) : idx + 120]
    assert ("必填" in context) or ("必须" in context)
