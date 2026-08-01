# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录 ──
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具（通过 registry.json 查找工具 ID 对应的实现目录）"""
    import subprocess as _sp
    # 从 registry.json 中查找工具 ID（目录名）
    reg_path = _PROJECT_ROOT / "resources" / "tools" / "registry.json"
    tool_id = tool_name  # 默认用名称作为 ID
    if reg_path.exists():
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
            # 先精确匹配 id，再模糊匹配 name
            for t in tools:
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            pass
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_id
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' (id={tool_id}) not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}).read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sympy import symbols, sympify, lambdify

def execute(**kwargs) -> dict[str, Any]:
    """
    接收一元函数表达式，绘制函数图像并返回图片路径
    """
    equation = kwargs.get("equation", "")
    if not equation or not isinstance(equation, str):
        return {
            "status": "failed",
            "message": "参数 'equation' 缺失或非字符串"
        }

    # 准备输出目录
    plot_dir = _DATA_DIR / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    # 解析表达式并生成计算函数
    try:
        x = symbols('x')
        expr = sympify(equation)
        f = lambdify(x, expr, "numpy")
    except Exception as e:
        return {
            "status": "failed",
            "message": f"表达式解析错误: {str(e)}"
        }

    # 生成采样点
    x_min, x_max = -10.0, 10.0
    num_points = 2000
    xs = np.linspace(x_min, x_max, num_points)

    # 计算 y 值，捕获可能的数学错误
    try:
        ys = f(xs)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"数值计算错误: {str(e)}"
        }

    # 绘图并保存
    try:
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.plot(xs, ys, linewidth=2, color='#1f77b4')
        ax.grid(True, alpha=0.3)
        ax.axhline(0, color='black', linewidth=0.5)
        ax.axvline(0, color='black', linewidth=0.5)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_title(f"y = {equation}")

        image_name = f"function_plot_{int(time.time())}.png"
        image_path = plot_dir / image_name
        fig.savefig(str(image_path), dpi=150, bbox_inches='tight')
        plt.close(fig)

        return {
            "status": "success",
            "output_format": "image",
            "message": "函数图像已生成",
            "data": {"image_path": str(image_path)}
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"绘图或保存出错: {str(e)}"
        }