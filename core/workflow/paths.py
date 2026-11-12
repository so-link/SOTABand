"""工作流运行期路径（适配 wxbit 的用户目录布局）。

main 版本通过 ``core.runtime.paths`` 把任务/时序产物写到全局的
``.runtime/refactor/``；wxbit 采用 ``USER_HOME/<uid>/`` 的用户隔离布局，
因此这里重新实现同名入口，语义对应关系：

===================  ==========================================
main 符号            wxbit 落点
===================  ==========================================
TASK_REGISTRY_FILE   ``<user_home>/tasks/registry.json``
TASK_RUNS_DIR        ``<user_home>/tasks/runs``
TIMESERIES_RUNS_DIR  ``<user_home>/timeseries-runs``
MODEL_REGISTRY_FILE  ``<user_home>/models/registry.json``
===================  ==========================================

所有函数在调用时按**当前身份**解析（``user_home()`` 内部读 ContextVar /
环境变量），因此不能在模块级缓存成常量——这一点与 main 的全局单根不同。
"""

from __future__ import annotations

from pathlib import Path

from core.user_paths import user_home


def _ensure(path: Path) -> Path:
    """确保目录存在并返回。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def tasks_dir() -> Path:
    """当前用户的任务根目录。"""
    return _ensure(user_home() / "tasks")


def task_registry_file() -> Path:
    """任务注册表（首次访问时初始化为空数组）。"""
    target = tasks_dir() / "registry.json"
    if not target.exists():
        target.write_text("[]", encoding="utf-8")
    return target


def task_runs_dir() -> Path:
    """单次任务的工作目录根（每个任务一个子目录）。"""
    return _ensure(tasks_dir() / "runs")


def timeseries_runs_dir() -> Path:
    """时序工作流的产物根目录。"""
    return _ensure(user_home() / "timeseries-runs")


def model_registry_path() -> Path:
    """当前用户的模型注册表文件。

    与 ``core.resource.registry.model_registry.ModelRegistry`` 的私有根保持
    一致（``private_space_dir("models")`` → ``<user_home>/models``）。
    """
    return _ensure(user_home() / "models") / "registry.json"
