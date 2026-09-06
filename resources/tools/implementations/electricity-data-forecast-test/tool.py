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
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


class LSTMModel(nn.Module):
    """简单 LSTM 时序预测模型（结构与训练工具保持一致）"""

    def __init__(self, input_size=1, hidden_size=64, num_layers=1, output_size=1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        # x: (batch, seq_len, input_size)
        out, _ = self.lstm(x)
        out = out[:, -1, :]  # 取最后一个时间步的输出
        out = self.fc(out)
        return out


def _read_csv_robust(file_path: Path) -> pd.DataFrame:
    """尝试常见编码读取 CSV，避免中文/GBK 编码导致的 UnicodeDecodeError"""
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk", "big5"]
    for enc in encodings:
        try:
            return pd.read_csv(file_path, encoding=enc)
        except UnicodeDecodeError:
            continue
    # 最后兜底：latin-1 可读取任意字节，但中文可能乱码；
    # 通常前面的 gb18030/gbk 已能覆盖中文 CSV。
    return pd.read_csv(file_path, encoding="latin-1")


def _setup_chinese_font():
    """设置 Matplotlib 中文字体，避免中文乱码"""
    # 常见中文字体候选列表（按优先级排序）
    preferred_fonts = [
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "SimHei",
        "Microsoft YaHei",
        "PingFang SC",
        "Hiragino Sans GB",
        "Arial Unicode MS",
    ]
    # 获取系统可用字体名称集合
    available_fonts = {f.name for f in font_manager.fontManager.ttflist}
    # 从候选列表中选择第一个系统中存在的字体
    selected_font = None
    for font in preferred_fonts:
        if font in available_fonts:
            selected_font = font
            break
    if selected_font:
        plt.rcParams["font.sans-serif"] = [selected_font]
        plt.rcParams["font.family"] = "sans-serif"
    else:
        # 未找到中文字体时，仍设置候选列表，系统可能回退到默认字体
        plt.rcParams["font.sans-serif"] = preferred_fonts
        plt.rcParams["font.family"] = "sans-serif"
    # 解决保存图像时负号 '-' 显示为方块的问题
    plt.rcParams["axes.unicode_minus"] = False


def execute(**kwargs) -> dict[str, Any]:
    try:
        # ── 1. 参数读取与基础校验 ──
        path = kwargs.get("path", "")
        model_name = kwargs.get("model_name", "")

        if not path:
            return {
                "status": "failed",
                "message": "缺少必填参数 path",
                "output_format": "text",
                "data": {},
            }
        if not model_name:
            return {
                "status": "failed",
                "message": "缺少必填参数 model_name",
                "output_format": "text",
                "data": {},
            }

        try:
            n = int(kwargs.get("n"))
            w = int(kwargs.get("w"))
            v = int(kwargs.get("v"))
        except Exception:
            return {
                "status": "failed",
                "message": "参数 n、w、v 必须为整数",
                "output_format": "text",
                "data": {},
            }

        if w < 1 or v < 1:
            return {
                "status": "failed",
                "message": "参数无效：窗口大小 w、预测长度 v 必须大于 0",
                "output_format": "text",
                "data": {},
            }

        resolved_path = _resolve_path(path)
        csv_file = Path(resolved_path)
        if not csv_file.exists():
            return {
                "status": "failed",
                "message": "CSV 文件不存在",
                "output_format": "text",
                "data": {},
            }

        # ── 2. 读取 CSV 并提取第 n 列 ──
        df = _read_csv_robust(csv_file)
        if n < 0 or n >= len(df.columns):
            return {
                "status": "failed",
                "message": f"参数无效：列编号 {n} 超出范围，CSV 共 {len(df.columns)} 列",
                "output_format": "text",
                "data": {},
            }

        series = pd.to_numeric(df.iloc[:, n], errors="coerce").dropna().astype(float).values
        if len(series) < w + v:
            return {
                "status": "failed",
                "message": f"数据长度不足：当前有效数据 {len(series)} 条，至少需要 {w + v} 条",
                "output_format": "text",
                "data": {},
            }

        # ── 3. 调用系统 API 获取模型信息 ──
        try:
            api_result = _call_api("api-model-get", name=model_name)
        except Exception as e:
            return {
                "status": "failed",
                "message": f"获取模型信息失败：{str(e)}",
                "output_format": "text",
                "data": {},
            }

        # 解析模型信息，兼容多种返回结构
        model_info = {}
        if isinstance(api_result, dict):
            if "model" in api_result and isinstance(api_result["model"], dict):
                model_info = api_result["model"]
            elif "data" in api_result and isinstance(api_result["data"], dict):
                data_section = api_result["data"]
                if "model" in data_section and isinstance(data_section["model"], dict):
                    model_info = data_section["model"]
                else:
                    model_info = data_section
            else:
                model_info = api_result

        # 提取模型存储路径
        model_path = None
        for key in [
            "model_path",
            "path",
            "storage_path",
            "file_path",
            "model_file",
            "model_location",
        ]:
            if key in model_info and model_info[key]:
                model_path = model_info[key]
                break

        # 如果 model_info 中未找到，尝试从 api_result 直接查找
        if not model_path and isinstance(api_result, dict):
            for key in [
                "model_path",
                "path",
                "storage_path",
                "file_path",
                "model_file",
                "model_location",
            ]:
                if key in api_result and api_result[key]:
                    model_path = api_result[key]
                    break

        if not model_path:
            return {
                "status": "failed",
                "message": f"未能从模型信息中解析出模型路径，model_name={model_name}",
                "output_format": "text",
                "data": {},
            }

        model_path_resolved = Path(_resolve_path(model_path))
        if not model_path_resolved.exists():
            return {
                "status": "failed",
                "message": f"模型文件不存在：{model_path_resolved}",
                "output_format": "text",
                "data": {},
            }

        # ── 4. 加载模型及元信息 ──
        try:
            checkpoint = torch.load(model_path_resolved, map_location="cpu")
        except Exception as e:
            return {
                "status": "failed",
                "message": f"模型加载失败：{str(e)}",
                "output_format": "text",
                "data": {},
            }

        state_dict = checkpoint.get("model_state_dict", None)
        if not state_dict:
            return {
                "status": "failed",
                "message": "模型检查点缺少 model_state_dict，无法加载",
                "output_format": "text",
                "data": {},
            }

        input_size = int(checkpoint.get("input_size", 1))
        hidden_size = int(checkpoint.get("hidden_size", 64))
        output_size_saved = int(checkpoint.get("output_size", v))
        train_min = float(checkpoint.get("train_min", 0.0))
        train_max = float(checkpoint.get("train_max", 1.0))

        # 校验预测窗口与模型输出维度是否一致
        if v != output_size_saved:
            return {
                "status": "failed",
                "message": (
                    f"参数无效：传入预测窗口 v={v} 与模型输出维度 {output_size_saved} 不匹配，"
                    "请使用与训练时相同的预测窗口大小"
                ),
                "output_format": "text",
                "data": {},
            }

        # 实例化模型并加载权重
        model = LSTMModel(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=1,
            output_size=output_size_saved,
        )
        model.load_state_dict(state_dict)
        model.eval()

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)

        # ── 5. 归一化与滑动窗口构造 ──
        scale = (train_max - train_min) if train_max > train_min else 1.0

        def normalize(x):
            return (x - train_min) / scale

        def denormalize(x):
            return x * scale + train_min

        series_norm = normalize(series)
        L = len(series_norm)
        num_windows = L - w - v + 1
        if num_windows < 1:
            return {
                "status": "failed",
                "message": "数据长度不足以生成至少一个滑动窗口",
                "output_format": "text",
                "data": {},
            }

        X_list, Y_list = [], []
        for i in range(num_windows):
            X_list.append(series_norm[i : i + w])
            Y_list.append(series_norm[i + w : i + w + v])

        X = np.array(X_list, dtype=np.float32).reshape(-1, w, 1)
        Y = np.array(Y_list, dtype=np.float32).reshape(-1, v)

        # ── 6. 执行预测并反归一化 ──
        batch_size = 128
        dataset = TensorDataset(torch.from_numpy(X), torch.from_numpy(Y))
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)

        preds_list, targets_list = [], []
        with torch.no_grad():
            for xb, yb in loader:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb)
                preds_list.append(pred.cpu().numpy())
                targets_list.append(yb.cpu().numpy())

        preds = np.concatenate(preds_list, axis=0)
        targets = np.concatenate(targets_list, axis=0)

        preds_original = denormalize(preds)
        targets_original = denormalize(targets)

        # ── 7. 计算 MAE（所有预测点平均绝对误差） ──
        mae = float(np.mean(np.abs(preds_original - targets_original)))

        # ── 8. 组装预测序列用于绘图（重叠窗口取平均） ──
        predicted_series = np.full(L, np.nan)
        # 使用字典收集每个目标时刻的所有预测值
        pred_collect = {idx: [] for idx in range(w, L)}
        for i in range(num_windows):
            for j in range(v):
                target_idx = i + w + j
                if target_idx < L:
                    pred_collect[target_idx].append(preds_original[i, j])

        for idx, values in pred_collect.items():
            if values:
                predicted_series[idx] = float(np.mean(values))

        # ── 9. 绘制原始序列与预测序列对比图 ──
        # 设置中文字体，避免乱码
        _setup_chinese_font()

        output_dir = _DATA_DIR / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        image_filename = f"electricity_forecast_test_{timestamp}.png"
        image_path = output_dir / image_filename

        plt.figure(figsize=(12, 5))
        time_axis = np.arange(L)

        # 原始序列
        plt.plot(time_axis, series, color="blue", linewidth=1.5, label="原始时序")

        # 预测序列（NaN 会被自动跳过）
        plt.plot(
            time_axis,
            predicted_series,
            color="red",
            linewidth=1.5,
            label="模型预测",
            linestyle="--",
        )

        plt.xlabel("时刻")
        plt.ylabel("数值")
        plt.title(f"电力数据预测对比 (列 {n}, 窗口={w}, 预测长度={v})")
        plt.legend()
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.tight_layout()
        plt.savefig(image_path, dpi=150)
        plt.close()

        # ── 10. 组装返回结果 ──
        data = {
            "image_path": str(image_path),
            "mae": round(mae, 6),
        }
        message = f"预测完成，MAE={mae:.6f}，对比图已保存至 {image_path}"

        return {
            "status": "success",
            "message": message,
            "output_format": "image",
            "data": data,
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"处理异常：{str(e)}",
            "output_format": "text",
            "data": {},
        }