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

# 依赖导入，若缺失则在 execute 中明确报错
try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


def _configure_chinese_font() -> bool:
    """
    配置 matplotlib 中文字体，解决中文乱码问题。
    返回是否成功找到并配置中文字体。
    """
    if plt is None:
        return False

    try:
        import matplotlib.font_manager as fm

        # 常见中文字体候选列表（按优先级排序）
        chinese_font_candidates = [
            "SimHei",              # 黑体（Windows）
            "Microsoft YaHei",     # 微软雅黑（Windows）
            "PingFang SC",         # 苹方（macOS）
            "Hiragino Sans GB",    # 冬青黑体（macOS）
            "WenQuanYi Zen Hei",   # 文泉驿正黑（Linux）
            "WenQuanYi Micro Hei", # 文泉驿微米黑（Linux）
            "Noto Sans CJK SC",    # 思源黑体（Linux）
            "Noto Sans SC",        # 思源黑体简体
            "AR PL UMing CN",      # 文鼎明体（Linux）
            "STHeiti",             # 华文黑体（macOS）
            "STSong",              # 华文宋体（macOS）
        ]

        installed_fonts = {f.name for f in fm.fontManager.ttflist}

        # 从候选字体中找出第一个已安装的
        chosen = None
        for font_name in chinese_font_candidates:
            if font_name in installed_fonts:
                chosen = font_name
                break

        if not chosen:
            # 候选字体都不可用，尝试模糊匹配系统字体名称
            for f in fm.fontManager.ttflist:
                name = f.name.lower()
                if any(kw in name for kw in ("cjk", "hei", "song", "kai", "ming", "sc", "cn", "chinese")):
                    chosen = f.name
                    break

        if chosen:
            plt.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["axes.unicode_minus"] = False
            return True
        else:
            # 没有找到中文字体，仍然设置 unicode_minus 防止负号乱码
            plt.rcParams["axes.unicode_minus"] = False
            return False
    except Exception:
        return False


# 配置字体并记录是否可用
_HAS_CHINESE_FONT = _configure_chinese_font()


def execute(**kwargs) -> dict[str, Any]:
    """电力数据可视化主函数"""
    # 1. 校验依赖
    if pd is None:
        return {"status": "failed", "message": "缺少依赖 pandas，请先安装 pandas>=1.5.0"}
    if plt is None:
        return {"status": "failed", "message": "缺少依赖 matplotlib，请先安装 matplotlib>=3.5.0"}

    # 2. 读取参数
    path = str(kwargs.get("path", "")).strip()
    if not path:
        return {"status": "failed", "message": "参数 path 不能为空"}

    try:
        n = int(kwargs.get("n"))
        start = int(kwargs.get("start"))
        end = int(kwargs.get("end"))
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 n、start、end 必须为整数"}

    # 3. 解析文件路径并检查存在性
    file_path = _resolve_path(path)
    if not Path(file_path).exists():
        return {"status": "failed", "message": f"文件不存在: {file_path}"}

    # 4. 读取 CSV
    try:
        # 尝试常见编码，提高兼容性
        try:
            df = pd.read_csv(file_path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="gbk")
    except Exception as e:
        return {"status": "failed", "message": f"读取 CSV 失败: {str(e)}"}

    if df.empty:
        return {"status": "failed", "message": "CSV 文件没有任何数据行"}

    # 5. 校验列编号
    if n < 1 or n > df.shape[1]:
        return {
            "status": "failed",
            "message": f"列编号 n={n} 超出范围，文件共有 {df.shape[1]} 列",
        }

    # 6. 校验行范围（起始/结束为数据行，1 开始，不含表头）
    total_rows = len(df)
    if start < 1:
        return {"status": "failed", "message": f"起始行 start={start} 不能小于 1"}
    if end < start:
        return {"status": "failed", "message": f"结束行 end={end} 不能小于起始行 start={start}"}
    if start > total_rows:
        return {
            "status": "failed",
            "message": f"起始行 start={start} 超出数据范围，文件共有 {total_rows} 行数据",
        }
    if end > total_rows:
        return {
            "status": "failed",
            "message": f"结束行 end={end} 超出数据范围，文件共有 {total_rows} 行数据",
        }

    # 7. 提取指定列和行范围
    try:
        selected = df.iloc[:, n - 1].iloc[start - 1 : end]
    except Exception as e:
        return {"status": "failed", "message": f"提取数据失败: {str(e)}"}

    if selected.empty:
        return {"status": "failed", "message": "所选行范围内没有数据"}

    # 8. 数据类型校验：必须为数值，且不能有缺失
    try:
        selected_numeric = pd.to_numeric(selected, errors="raise")
    except (ValueError, TypeError) as e:
        return {
            "status": "failed",
            "message": f"所选列第 {start} 至 {end} 行包含非数值数据，无法绘制时序曲线: {str(e)}",
        }

    if selected_numeric.isnull().any():
        return {
            "status": "failed",
            "message": "所选数据包含缺失值，请处理缺失值后再可视化",
        }

    # 9. 绘制时序曲线并保存为 PNG
    try:
        x_vals = list(range(1, len(selected_numeric) + 1))
        y_vals = selected_numeric.tolist()

        plt.figure(figsize=(10, 6))
        plt.plot(x_vals, y_vals, marker="o", linestyle="-", color="#1f77b4")

        # 根据中文字体可用性选择标签语言，避免乱码
        if _HAS_CHINESE_FONT:
            title = f"电力数据时序曲线（列 {n}，行 {start}-{end}）"
            xlabel = "数据点序号"
            ylabel = "数值"
        else:
            title = f"Power Data Time Series (Column {n}, Rows {start}-{end})"
            xlabel = "Data Point Index"
            ylabel = "Value"

        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        output_dir = _DATA_DIR / "visualizations"
        output_dir.mkdir(parents=True, exist_ok=True)

        img_path = output_dir / f"power_vis_{int(time.time() * 1000)}.png"
        plt.savefig(img_path, dpi=100, bbox_inches="tight")
        plt.close()

        return {
            "status": "success",
            "output_format": "image",
            "message": f"已成功生成曲线图片，共 {len(selected_numeric)} 个数据点",
            "data": {"image_path": str(img_path)},
        }
    except Exception as e:
        # 确保绘图资源被清理
        try:
            plt.close("all")
        except Exception:
            pass
        return {"status": "failed", "message": f"绘图过程中发生错误: {str(e)}"}