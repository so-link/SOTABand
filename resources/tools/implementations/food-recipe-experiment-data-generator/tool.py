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

def execute(**kwargs) -> dict[str, Any]:
    """生成食品配方实验数据并注册数据集"""
    # 1. 参数提取与验证
    n = kwargs.get("n")
    output_dataset = kwargs.get("output_dataset", "")

    try:
        n = int(n)
        if n <= 0:
            return {"status": "failed", "message": "生成条目数必须为正整数"}
    except (ValueError, TypeError):
        return {"status": "failed", "message": "生成条目数必须为正整数"}

    if not output_dataset:
        return {"status": "failed", "message": "数据集注册名称不能为空"}

    # 2. 导入依赖
    try:
        import numpy as np
        import pandas as pd
        from datetime import datetime
    except ImportError as e:
        return {"status": "failed", "message": f"缺少依赖: {e}"}

    # 3. 创建带时间戳的目录
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = _DOWNLOADS_DIR / timestamp
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"status": "failed", "message": f"无法创建目标目录: {e}"}

    # 4. 模拟生成实验数据
    try:
        # 配方ID
        ids = [f"R{i+1:03d}" for i in range(n)]

        # 成分生成：总成分100%，随机分配
        other = np.round(np.random.uniform(40, 60, n), 1)  # 其他成分
        sum_ab = 100.0 - other
        a_ratio = np.round(np.random.uniform(0.3, 0.7, n), 2)
        comp_a = np.round(sum_ab * a_ratio, 1)
        comp_b = np.round(sum_ab * (1 - a_ratio), 1)

        # 工艺参数
        temp = np.random.randint(150, 220, n)
        time_min = np.random.randint(20, 90, n)
        score = np.round(np.random.uniform(5.0, 10.0, n), 1)

        # 构建DataFrame
        df = pd.DataFrame({
            "配方ID": ids,
            "成分A(%)": comp_a,
            "成分B(%)": comp_b,
            "温度(℃)": temp,
            "时间(min)": time_min,
            "感官评分": score
        })
    except Exception as e:
        return {"status": "failed", "message": f"数据生成失败: {e}"}

    # 5. 保存CSV
    csv_path = target_dir / "experiment_data.csv"
    try:
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    except Exception as e:
        return {"status": "failed", "message": f"保存 CSV 文件失败: {e}"}

    # 6. 注册数据集
    try:
        file_size = csv_path.stat().st_size
        dataset_id = f"{output_dataset}_{timestamp}"
        raw_md = f"# {output_dataset}\n\n自动生成的食品配方实验数据，共 {n} 条记录。\n\n字段说明：\n- 配方ID\n- 成分A(%)\n- 成分B(%)\n- 温度(℃)\n- 时间(min)\n- 感官评分"

        result = _call_api(
            "api-data-register",
            id=dataset_id,
            name=output_dataset,
            raw_md=raw_md,
            data_path=str(target_dir),
            file_count=1,
            total_size=file_size,
            formats=["csv"]
        )

        # 检查API返回状态
        if isinstance(result, dict) and result.get("status") == "failed":
            raise Exception(result.get("message", "数据集注册API返回失败"))
        # 若返回值不含status但抛出异常已在上面捕获，此处继续
    except Exception as e:
        return {"status": "failed", "message": f"数据集注册失败: {e}"}

    # 7. 构建表格输出（返回所有数据行，数值转为字符串确保JSON序列化）
    rows = df.values.tolist()
    rows = [[str(cell) for cell in row] for row in rows]  # 统一为字符串，避免numpy类型问题
    table_data = {
        "columns": df.columns.tolist(),
        "rows": rows
    }

    return {
        "status": "success",
        "output_format": "table",
        "message": f"成功生成 {n} 条实验数据并注册数据集 '{output_dataset}'，保存于 {target_dir}",
        "data": table_data
    }