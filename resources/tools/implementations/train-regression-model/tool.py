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
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score
import joblib

# 可选依赖：xgboost
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

def execute(**kwargs) -> dict[str, Any]:
    """训练回归模型核心逻辑"""
    try:
        # ── 1. 参数提取 ──
        path = kwargs.get("path", "")
        model_type = kwargs.get("model_type", "")
        # r 可能是字符串，需要转换为浮点数
        try:
            r = float(kwargs.get("r", 0.0))
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "message": f"参数 r 无效，无法转换为数字: {kwargs.get('r')}",
                "output_format": "text",
                "data": {}
            }

        # ── 2. 参数校验 ──
        # 文件是否存在
        data_path = Path(path)
        if not data_path.is_absolute():
            # 尝试相对于项目根路径
            data_path = _PROJECT_ROOT / path
        if not data_path.exists() or not data_path.is_file():
            return {
                "status": "failed",
                "message": f"文件不存在: {path}",
                "output_format": "text",
                "data": {}
            }

        # r 范围校验
        if not (0 < r < 1):
            return {
                "status": "failed",
                "message": f"参数 r 无效: {r}, 需在 (0, 1) 之间",
                "output_format": "text",
                "data": {}
            }

        # model_type 校验
        supported_models = ["random_forest", "xgboost", "linear_regression"]
        if model_type not in supported_models:
            return {
                "status": "failed",
                "message": f"不支持的模型类型: {model_type}",
                "output_format": "text",
                "data": {}
            }
        if model_type == "xgboost" and not XGB_AVAILABLE:
            return {
                "status": "failed",
                "message": "xgboost 库未安装，无法使用 xgboost 模型",
                "output_format": "text",
                "data": {}
            }

        # ── 3. 数据加载与处理 ──
        df = pd.read_csv(data_path)
        if df.shape[1] < 2:
            return {
                "status": "failed",
                "message": "CSV 文件至少需要包含两列（特征+目标）",
                "output_format": "text",
                "data": {}
            }

        # 忽略第一列（视为ID），并提取特征与目标
        if df.shape[1] == 2:
            return {
                "status": "failed",
                "message": "忽略第一列后无特征列可用，至少需要三列数据",
                "output_format": "text",
                "data": {}
            }

        # 丢弃第一列
        df = df.iloc[:, 1:]
        # 最后一列为目标
        y = df.iloc[:, -1].copy()
        X = df.iloc[:, :-1].copy()

        # 只保留数值列
        X = X.select_dtypes(include=[np.number])
        if X.shape[1] == 0:
            return {
                "status": "failed",
                "message": "特征列中无数值数据，无法进行回归",
                "output_format": "text",
                "data": {}
            }

        # 尝试将目标转为数值
        try:
            y = pd.to_numeric(y, errors='raise')
        except Exception:
            y = pd.to_numeric(y, errors='coerce')
            if y.isnull().any():
                return {
                    "status": "failed",
                    "message": "目标列包含无法转换为数值的数据",
                    "output_format": "text",
                    "data": {}
                }

        # 移除含有NaN的行
        mask = y.notnull() & X.notnull().all(axis=1)
        X = X.loc[mask]
        y = y.loc[mask]
        if len(X) == 0:
            return {
                "status": "failed",
                "message": "有效样本数为0，无法训练",
                "output_format": "text",
                "data": {}
            }

        # ── 4. 划分训练/测试集 ──
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=r, random_state=42
        )

        # ── 5. 模型选择与训练 ──
        if model_type == "random_forest":
            model = RandomForestRegressor(random_state=42)
            framework = "scikit-learn"
        elif model_type == "linear_regression":
            model = LinearRegression()
            framework = "scikit-learn"
        elif model_type == "xgboost":
            model = xgb.XGBRegressor(random_state=42)
            framework = "xgboost"
        else:
            raise ValueError(f"未处理的模型类型: {model_type}")

        model.fit(X_train, y_train)

        # ── 6. 训练集评估 ──
        y_train_pred = model.predict(X_train)
        training_error = mean_squared_error(y_train, y_train_pred)
        training_accuracy = r2_score(y_train, y_train_pred)

        # ── 7. 模型持久化 ──
        _DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        model_filename = f"regression_{model_type}_{timestamp}.pkl"
        model_path = str(_DOWNLOADS_DIR / model_filename)
        joblib.dump(model, model_path)

        # ── 8. 调用模型注册 API ──
        api_params = {
            "name": f"回归模型-{model_type}",
            "raw_md": f"自动训练的{model_type}回归模型，训练数据: {path}",
            "framework": framework,
            "model_path": model_path,
            "input_format": "csv",
            "output_format": "numerical",
            "version": "0.1.0",
            "tags": ["regression", model_type],
            "associated_tool_id": "train-regression-model"
        }
        try:
            _call_api("api-model-register", **api_params)
        except Exception:
            pass

        # ── 9. 构造返回结果 ──
        return {
            "status": "success",
            "message": (
                f"模型训练完成，保存至: {model_path}。"
                f"训练误差(MSE): {training_error:.4f}，训练R²: {training_accuracy:.4f}"
            ),
            "output_format": "text",
            "data": {
                "model_path": model_path,
                "training_error": round(training_error, 6),
                "training_accuracy": round(training_accuracy, 6)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"处理异常: {str(e)}",
            "output_format": "text",
            "data": {}
        }

# 本地快速测试（非必须，但保留以方便调试）
if __name__ == "__main__":
    # 模拟参数，需要实际CSV文件
    test_args = {
        "path": "data/test.csv",
        "model_type": "linear_regression",
        "r": "0.2"  # 测试字符串输入
    }
    result = execute(**test_args)
    print(json.dumps(result, ensure_ascii=False, indent=2))