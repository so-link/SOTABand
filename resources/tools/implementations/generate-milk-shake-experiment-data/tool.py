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

import pandas as pd
import numpy as np

def execute(**kwargs) -> dict:
    try:
        # ── 1. 参数获取与校验 ──
        n_raw = kwargs.get("n")
        output_dataset = kwargs.get("output_dataset", "")

        # 校验 n
        if n_raw is None:
            return {"status": "failed", "message": "参数n必须为正整数"}
        try:
            n = int(n_raw)
        except (ValueError, TypeError):
            return {"status": "failed", "message": "参数n必须为正整数"}
        if n <= 0:
            return {"status": "failed", "message": "参数n必须为正整数"}

        # 校验 output_dataset
        if not output_dataset or not isinstance(output_dataset, str) or output_dataset.strip() == "":
            return {"status": "failed", "message": "数据集名称不能为空"}

        # ── 2. 创建带时间戳的目录 ──
        timestamp = time.strftime("%Y%m%d%H%M%S")
        target_dir = _DOWNLOADS_DIR / timestamp
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"status": "failed", "message": f"目录创建失败: {str(e)}"}

        # ── 3. 生成实验数据 ──
        np.random.seed(int(time.time()))  # 简单随机种子

        # 各列数据生成
        exp_ids = [f"EXP{i:04d}" for i in range(1, n + 1)]
        whey_protein = np.round(np.random.uniform(10.0, 30.0, n), 1)      # 分离乳清蛋白(g)
        maltodextrin = np.round(np.random.uniform(0.0, 20.0, n), 1)       # 麦芽糊精(g)
        vegetable_oil = np.round(np.random.uniform(0.0, 10.0, n), 1)      # 植物油(g)
        soy_lecithin = np.round(np.random.uniform(0.1, 2.0, n), 2)        # 大豆卵磷脂(g)
        pressure = np.round(np.random.uniform(20.0, 60.0, n), 1)          # 均质压力(MPa)
        temperature = np.random.randint(70, 101, n)                       # 杀菌温度(℃)
        storage_days = np.random.randint(1, 31, n)                        # 存放时间(天)
        precipitation = np.round(np.random.uniform(0.0, 50.0, n), 1)      # 沉淀率(%)
        preference = np.round(np.random.uniform(0.0, 10.0, n), 1)         # 整体喜好度(0~10)

        # 构建 DataFrame
        columns = [
            "实验ID",
            "分离乳清蛋白(g)",
            "麦芽糊精(g)",
            "植物油(g)",
            "大豆卵磷脂(g)",
            "均质压力(MPa)",
            "杀菌温度(℃)",
            "存放时间(天)",
            "沉淀率(%)",
            "整体喜好度（0~10）"
        ]
        data = np.column_stack([
            exp_ids,
            whey_protein,
            maltodextrin,
            vegetable_oil,
            soy_lecithin,
            pressure,
            temperature,
            storage_days,
            precipitation,
            preference
        ])
        df = pd.DataFrame(data, columns=columns)

        # 保存为 CSV
        csv_filename = "experiment_data.csv"
        csv_path = target_dir / csv_filename
        try:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        except Exception as e:
            return {"status": "failed", "message": f"文件写入失败: {str(e)}"}

        # ── 4. 注册数据集 ──
        # 数据集 ID 使用名称+时间戳，确保唯一
        dataset_id = f"{output_dataset}_{timestamp}"
        raw_md = f"奶昔配方实验数据（{n} 条记录，生成时间 {timestamp}）"
        data_path_str = str(target_dir.resolve())
        total_size = os.path.getsize(csv_path)
        file_count = 1
        formats = ["csv"]

        try:
            result = _call_api(
                "api-data-register",
                id=dataset_id,
                name=output_dataset,
                raw_md=raw_md,
                data_path=data_path_str,
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
            # 如果 API 返回的字典包含 dataset_id 字段，说明成功；否则视为失败
            if not isinstance(result, dict) or "dataset_id" not in result:
                raise ValueError(f"注册API返回异常: {result}")
        except Exception as e:
            return {"status": "failed", "message": f"数据集注册失败: {str(e)}"}

        # ── 5. 构建输出表格 ──
        # 将 DataFrame 数据转换为 Python 原生类型（避免 numpy 类型导致 JSON 序列化问题）
        rows = []
        for row in df.values:
            typed_row = []
            for val in row:
                if isinstance(val, (np.integer,)):
                    typed_row.append(int(val))
                elif isinstance(val, (np.floating,)):
                    typed_row.append(float(val))
                else:
                    typed_row.append(val)
            rows.append(typed_row)

        return {
            "status": "success",
            "message": f"成功生成 {n} 条奶昔配方实验数据，数据集已注册为 {output_dataset}（ID: {dataset_id}）",
            "output_format": "table",
            "data": {
                "columns": columns,
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": f"未知错误: {str(e)}"}