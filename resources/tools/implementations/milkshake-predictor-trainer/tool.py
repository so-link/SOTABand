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
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.metrics import mean_squared_error, r2_score
import joblib

try:
    from xgboost import XGBRegressor
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False


def execute(**kwargs) -> dict[str, Any]:
    try:
        path = kwargs.get("path", "")
        model_type = kwargs.get("model_type", "")
        r = kwargs.get("r", 0.2)

        # 参数校验
        if not path:
            return {"status": "failed", "message": "缺少参数: path"}
        if not model_type:
            return {"status": "failed", "message": "缺少参数: model_type"}
        try:
            r = float(r)
            if not (0.0 < r < 1.0):
                raise ValueError
        except (TypeError, ValueError):
            return {"status": "failed", "message": "r 必须在 (0,1) 之间"}

        # 读取 CSV
        try:
            df = pd.read_csv(path)
        except Exception as e:
            return {"status": "failed", "message": f"无法读取CSV文件: {str(e)}"}

        if df.shape[1] < 2:
            return {"status": "failed", "message": "CSV文件列数不足，至少需要2列（标识列+特征列+目标列）"}

        # 第一列忽略，中间列为特征，最后一列为目标
        X = df.iloc[:, 1:-1]
        y = df.iloc[:, -1]

        if X.shape[1] == 0:
            return {"status": "failed", "message": "没有可用的特征列"}

        # 划分训练集与测试集
        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=r, random_state=42
            )
        except Exception as e:
            return {"status": "failed", "message": f"数据划分失败: {str(e)}"}

        # 选择模型
        model_map = {
            "random_forest": RandomForestRegressor(n_estimators=100, random_state=42),
            "linear": LinearRegression(),
            "ridge": Ridge(),
            "lasso": Lasso(),
        }
        if XGB_AVAILABLE:
            model_map["xgboost"] = XGBRegressor(random_state=42)

        if model_type.lower() not in model_map:
            supported = list(model_map.keys())
            return {"status": "failed", "message": f"不支持的模型类型: {model_type}，支持: {supported}"}

        model = model_map[model_type.lower()]

        # 训练模型
        try:
            model.fit(X_train, y_train)
        except Exception as e:
            return {"status": "failed", "message": f"模型训练失败: {str(e)}"}

        # 评估训练集性能（使用 RMSE 和 R²）
        try:
            y_pred_train = model.predict(X_train)
            mse = mean_squared_error(y_train, y_pred_train)
            train_rmse = mse ** 0.5
            train_r2 = r2_score(y_train, y_pred_train)
        except Exception as e:
            return {"status": "failed", "message": f"模型评估失败: {str(e)}"}

        # 保存模型
        timestamp = time.strftime("%Y%m%d%H%M%S")
        _DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        model_filename = f"model_{timestamp}.pkl"
        model_path = _DOWNLOADS_DIR / model_filename
        try:
            joblib.dump(model, model_path)
        except Exception as e:
            return {"status": "failed", "message": f"模型保存失败: {str(e)}"}

        # 模型注册
        model_name = f"奶昔预测模型{timestamp}"
        framework = "xgboost" if model_type.lower() == "xgboost" else "scikit-learn"
        raw_md = (
            f"## {model_name}\n"
            f"使用 {model_type} 回归模型训练。\n"
            f"训练数据来源: {path}，测试集比例: {r}。"
        )

        registration_failed = False
        registration_msg = ""
        try:
            reg_result = _call_api(
                "api-model-register",
                name=model_name,
                raw_md=raw_md,
                framework=framework,
                model_path=str(model_path),
                input_format="tabular",
                output_format="scalar",
                version="0.1.0",
                tags=["regression", "milkshake", "prediction"],
                associated_tool_id="milkshake-predictor-trainer",
            )
            if reg_result.get("status") == "failed":
                registration_failed = True
                registration_msg = reg_result.get("message", "未知错误")
        except Exception as e:
            registration_failed = True
            registration_msg = str(e)

        # 构建输出信息
        base_text = (
            f"模型路径: {model_path}, 训练误差: {train_rmse:.6f}, 训练准确率: {train_r2:.6f}"
        )
        if registration_failed:
            message = f"模型训练成功，但模型注册失败: {registration_msg}"
            text = base_text + " (模型注册失败)"
        else:
            message = "模型训练完成并成功注册"
            text = base_text

        return {
            "status": "success",
            "message": message,
            "output_format": "text",
            "data": {
                "text": text,
                "model_path": str(model_path),
                "train_error": round(train_rmse, 6),
                "train_accuracy": round(train_r2, 6),
            },
        }

    except Exception as e:
        return {"status": "failed", "message": f"执行过程中发生未预期错误: {str(e)}"}