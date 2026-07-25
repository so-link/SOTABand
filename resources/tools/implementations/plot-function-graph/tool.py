
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
    """调用已注册的工具"""
    import subprocess as _sp
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_name
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' not found"}
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

import tempfile
import re
from typing import Optional

# 尝试导入必要库
try:
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端
    import matplotlib.pyplot as plt
    import sympy as sp
    DEPENDENCIES_OK = True
except ImportError as e:
    DEPENDENCIES_OK = False
    MISSING_DEP_ERROR = str(e)


def _parse_param(value, target_type):
    """
    如果 value 是字符串，尝试用 json.loads 解析为目标类型；
    解析成功且类型匹配则返回解析后的值，否则返回原值。
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, target_type):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return value


def _parse_equation(equation: str) -> Optional[sp.Expr]:
    """
    解析 y = f(x) 形式的表达式，返回关于 x 的符号表达式。
    返回 None 表示解析失败，并抛出异常或返回异常信息。
    """
    # 去除空格，统一小写
    equation = equation.strip()
    # 基本校验：必须有等号
    if '=' not in equation:
        raise ValueError("表达式缺少等号，请使用 y = f(x) 格式")
    parts = equation.split('=')
    if len(parts) != 2:
        raise ValueError("表达式包含多余等号，仅支持一个等号的 y = f(x) 格式")
    left, right = parts
    left = left.strip().lower()
    right = right.strip()
    if not left:
        raise ValueError("等号左边不能为空，请使用 y = f(x) 格式")
    # 左边通常是 y，但允许其他表示，不做严格限制（只要右边是表达式即可）
    if not right:
        raise ValueError("等号右边表达式不能为空")

    # 定义符号变量 x，并构建安全的本地命名空间（包含常用数学函数）
    x = sp.Symbol('x')
    local_dict = {
        'x': x,
        'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan,
        'exp': sp.exp, 'log': sp.log, 'ln': sp.log,
        'sqrt': sp.sqrt, 'abs': sp.Abs,
        'pi': sp.pi, 'e': sp.E,
        'asin': sp.asin, 'acos': sp.acos, 'atan': sp.atan,
        'sinh': sp.sinh, 'cosh': sp.cosh, 'tanh': sp.tanh,
    }
    try:
        # 使用 sympify 解析，关闭不安全的自动导入
        expr = sp.sympify(right, locals=local_dict)
    except (sp.SympifyError, SyntaxError) as e:
        raise ValueError(f"无法解析表达式 '{right}'：{str(e)}")
    return expr


def execute(**kwargs) -> dict[str, Any]:
    """函数图像绘制器主函数"""
    if not DEPENDENCIES_OK:
        return {
            "status": "failed",
            "message": f"缺少必要的依赖库：{MISSING_DEP_ERROR}。请安装 numpy, matplotlib, sympy"
        }

    # 1. 提取参数并进行类型转换（支持字符串化的 JSON 值）
    equation = _parse_param(kwargs.get("equation", ""), str)
    x_range_raw = kwargs.get("x_range", [-10, 10])
    x_range = _parse_param(x_range_raw, (list, tuple))
    num_points_raw = kwargs.get("num_points", 1000)
    num_points = _parse_param(num_points_raw, int)
    image_size_raw = kwargs.get("image_size", [800, 600])
    image_size = _parse_param(image_size_raw, (list, tuple))
    title = _parse_param(kwargs.get("title", "函数图像"), str)
    x_label = _parse_param(kwargs.get("x_label", "x"), str)
    y_label = _parse_param(kwargs.get("y_label", "y"), str)

    # 2. 参数校验
    # 2.1 equation 不能为空
    if not equation or not isinstance(equation, str):
        return {
            "status": "failed",
            "message": "参数 'equation' 必须是一个非空字符串，格式为 'y = f(x)'"
        }

    # 2.2 x_range 校验
    if not isinstance(x_range, (list, tuple)) or len(x_range) != 2:
        return {
            "status": "failed",
            "message": "参数 'x_range' 必须是一个长度为2的列表，如 [min, max]"
        }
    try:
        x_min, x_max = float(x_range[0]), float(x_range[1])
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "message": "参数 'x_range' 中的元素必须是数值"
        }
    if x_min >= x_max:
        return {
            "status": "failed",
            "message": f"x_range 的左端点必须小于右端点，当前值：{x_range}"
        }

    # 2.3 num_points 校验
    if not isinstance(num_points, int) or num_points <= 0:
        return {
            "status": "failed",
            "message": "参数 'num_points' 必须是一个正整数，推荐 100 到 10000 之间"
        }
    if num_points > 100000:  # 防止内存问题
        return {
            "status": "failed",
            "message": "num_points 过大，请设置在 100000 以内"
        }

    # 2.4 image_size 校验
    if not isinstance(image_size, (list, tuple)) or len(image_size) != 2:
        return {
            "status": "failed",
            "message": "参数 'image_size' 必须是一个包含宽度和高度的列表，如 [800, 600]"
        }
    try:
        width, height = int(image_size[0]), int(image_size[1])
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "message": "image_size 中的元素必须是整数"
        }
    if width <= 0 or height <= 0:
        return {
            "status": "failed",
            "message": "image_size 的值必须为正整数"
        }
    if width > 4000 or height > 4000:
        return {
            "status": "failed",
            "message": "image_size 过大，单边请不超过 4000 像素"
        }

    # 3. 解析表达式
    try:
        expr = _parse_equation(equation)
    except ValueError as e:
        return {
            "status": "failed",
            "message": f"表达式解析失败：{str(e)}"
        }

    # 4. 转换为 numpy 可计算的函数
    x_sym = sp.Symbol('x')
    try:
        # 使用 lambdify 转换为 numpy 函数，支持向量化计算
        f = sp.lambdify(x_sym, expr, modules=['numpy'])
    except Exception as e:
        return {
            "status": "failed",
            "message": f"表达式转换为计算函数失败：{str(e)}"
        }

    # 5. 生成数据点
    try:
        x = np.linspace(x_min, x_max, num_points)
        y = f(x)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"计算函数值时出错：{str(e)}"
        }

    # 处理可能的非有限值（无穷大/NaN），在绘图时会被忽略，但可给出警告信息
    if not np.all(np.isfinite(y)):
        # 记录非有限点数量
        invalid_mask = ~np.isfinite(y)
        num_invalid = np.count_nonzero(invalid_mask)
        # 我们仍然绘制，matplotlib 会跳过或断裂，但需告知用户
        msg_warning = f"（注意：定义域内有 {num_invalid} 个点无法计算，已忽略）"
    else:
        msg_warning = ""

    # 6. 绘制图像
    dpi = 100
    figsize = (width / dpi, height / dpi)
    plt.figure(figsize=figsize, dpi=dpi)
    try:
        plt.plot(x, y, linewidth=1.5, label=f"$y = {sp.latex(expr)}$")
        plt.title(title, fontsize=14)
        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()
        plt.tight_layout()

        # 保存图片到临时目录
        # 创建数据目录下的 plots 子目录，确保权限（临时目录亦可）
        plots_dir = _DATA_DIR / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        # 使用时间戳加随机数生成唯一文件名
        timestamp = int(time.time() * 1000)
        import random
        rand_suffix = random.randint(1000, 9999)
        filename = f"plot_{timestamp}_{rand_suffix}.png"
        save_path = plots_dir / filename

        plt.savefig(str(save_path), dpi=dpi, bbox_inches='tight')
        plt.close()  # 释放资源

        # 构建成功消息
        msg = f"函数图像已生成：{equation}，区间 [{x_min}, {x_max}]，采样 {num_points} 个点"
        if msg_warning:
            msg += " " + msg_warning

        return {
            "status": "success",
            "output_format": "image",
            "message": msg,
            "data": {
                "image_path": str(save_path.resolve())
            }
        }
    except Exception as e:
        plt.close()  # 确保关闭绘图
        return {
            "status": "failed",
            "message": f"生成图像时发生错误：{str(e)}"
        }
