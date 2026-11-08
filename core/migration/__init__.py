"""资源迁移（导出 / 导入）

把「工具 / Agent」这类资源打包成可跨环境搬运的单文件，并在目标环境注册可用。

约定一致（两种资源都是「定义 MD + 实现目录 + registry 条目」三元组）：
  * 工具  → .sotatool.zip   （definitions/<id>.md + implementations/<id>/ + registry）
  * Agent → .sotaagent.zip  （同上，额外记录 role / 依赖工具）

设计原则：
  * 纯标准库、同步、不依赖 FastAPI —— 既能被 HTTP 路由调用，也能被
    工具代码（子进程）直接 import；
  * 只读公共层资源，写入一律落私有资源根（家目录顶层扁平布局）；
  * 解包严格防 zip slip，限制大小与文件数。
"""

from core.migration.tool_bundle import (  # noqa: F401
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    export_tool,
    import_tool,
    list_bundles,
    export_dir,
)
from core.migration.agent_bundle import (  # noqa: F401
    export_agent,
    import_agent,
    list_agent_bundles,
)
