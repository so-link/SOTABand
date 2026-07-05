import os
import json
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional, Union

from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.svm import SVR
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import r2_score, mean_squared_error
import joblib

try:
    from xgboost import XGBRegressor
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False


# 支持的模型注册表
MODEL_DICT = {
    "random_forest": RandomForestRegressor,
    "gradient_boosting": GradientBoostingRegressor,
    "svr": SVR,
    "linear_regression": LinearRegression,
    "ridge": Ridge,
}

if XGB_AVAILABLE:
    MODEL_DICT["xgboost"] = XGBRegressor


def execute(**kwargs) -> Dict[str, Any]:
    """
    奶昔数据分析工具：训练或预测沉淀率与整体喜好度。

    Args:
        mode (str): 'train' 或 'predict'
        csv_file (str): CSV数据文件路径
        model_type (str): 模型名称，可选 'random_forest', 'xgboost', 'gradient_boosting',
                          'svr', 'linear_regression', 'ridge'

    Returns:
        dict: 包含 status, message, output_format, data 字段的字典
    """
    # ---------- 参数提取 ----------
    mode = kwargs.get("mode", None)
    csv_file = kwargs.get("csv_file", None)
    model_type = kwargs.get("model_type", None)

    # ---------- 参数校验 ----------
    if mode is None or not isinstance(mode, str):
        return _error("参数 'mode' 是必填项，且必须为字符串。")
    if csv_file is None or not isinstance(csv_file, str):
        return _error("参数 'csv_file' 是必填项，且必须为字符串。")
    if model_type is None or not isinstance(model_type, str):
        return _error("参数 'model_type' 是必填项，且必须为字符串。")

    mode = mode.strip()
    csv_file = csv_file.strip()
    model_type = model_type.strip().lower()

    if mode not in ("train", "predict"):
        return _error("参数 'mode' 取值必须为 'train' 或 'predict'。")

    if model_type not in MODEL_DICT:
        return _error(
            f"不支持的模型类型 '{model_type}'。"
            f"支持的模型: {list(MODEL_DICT.keys())}"
        )

    # ---------- 文件检查 ----------
    if not os.path.isfile(csv_file):
        return _error(f"CSV文件不存在或路径无效: {csv_file}")

    try:
        # ---------- 数据加载 ----------
        df = pd.read_csv(csv_file, dtype=str)  # 先按字符串读入，便于检查列数
        if mode == "train":
            if df.shape[1] != 10:
                return _error(
                    f"训练模式要求CSV包含10列，当前文件包含 {df.shape[1]} 列。"
                )
            # 将数值列转换为浮点数
            try:
                # 列顺序按规范：实验编号(0), 7个特征(1-7), 沉淀率(8), 整体喜好度(9)
                numeric_cols = df.columns[1:]  # 从第2列开始
                df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            except Exception:
                return _error("CSV文件中的数值列无法转换为数字，请检查数据。")

            # 检查缺失值
            if df.isnull().any().any():
                return _error("训练数据包含缺失值，请清理后再试。")

            X = df.iloc[:, 1:8].values.astype(float)
            y = df.iloc[:, 8:10].values.astype(float)

            # ---------- 训练 ----------
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )

            # 实例化模型
            base_estimator = MODEL_DICT[model_type]()
            model = MultiOutputRegressor(base_estimator)
            model.fit(X_train, y_train)

            # 评估
            y_train_pred = model.predict(X_train)
            y_test_pred = model.predict(X_test)

            # 整体R²和RMSE（针对两目标整体）
            train_r2 = r2_score(y_train, y_train_pred)
            test_r2 = r2_score(y_test, y_test_pred)
            train_rmse = float(np.sqrt(mean_squared_error(y_train, y_train_pred)))
            test_rmse = float(np.sqrt(mean_squared_error(y_test, y_test_pred)))

            # 保存模型
            model_dir = "./models"
            os.makedirs(model_dir, exist_ok=True)
            model_path = os.path.join(model_dir, f"model_{model_type}.pkl")
            joblib.dump(model, model_path)

            # 组装输出
            data = {
                "columns": ["数据集", "R²", "RMSE"],
                "rows": [
                    ["Training", round(train_r2, 4), round(train_rmse, 4)],
                    ["Test", round(test_r2, 4), round(test_rmse, 4)],
                ],
                "model_path": model_path,
            }

            return {
                "status": "success",
                "message": f"模型训练完成，已保存至 {model_path}",
                "output_format": "table",
                "data": data,
            }

        elif mode == "predict":
            if df.shape[1] != 8:
                return _error(
                    f"预测模式要求CSV包含8列，当前文件包含 {df.shape[1]} 列。"
                )
            # 列顺序：实验编号(0), 7个特征(1-7)
            try:
                numeric_cols = df.columns[1:]
                df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            except Exception:
                return _error("CSV文件中的特征列无法转换为数字，请检查数据。")

            if df.isnull().any().any():
                return _error("预测数据包含缺失值，请清理后再试。")

            exp_ids = df.iloc[:, 0].tolist()
            X = df.iloc[:, 1:8].values.astype(float)

            # 加载模型
            model_path = f"./models/model_{model_type}.pkl"
            if not os.path.isfile(model_path):
                return _error(
                    f"模型文件不存在: {model_path}，请先使用训练模式训练模型。"
                )
            model = joblib.load(model_path)
            predictions = model.predict(X)  # shape (n_samples, 2)

            rows = []
            for exp_id, (pred1, pred2) in zip(exp_ids, predictions):
                rows.append([
                    exp_id,
                    round(float(pred1), 2),   # 沉淀率保留两位
                    round(float(pred2), 2),   # 喜好度保留两位
                ])

            data = {
                "columns": ["实验编号", "预测沉淀率(%)", "整体喜好度(1-9)"],
                "rows": rows,
            }

            return {
                "status": "success",
                "message": "预测完成",
                "output_format": "table",
                "data": data,
            }

    except Exception as e:
        # 捕获所有未预料的异常
        return _error(f"执行过程中发生异常: {str(e)}")


def _error(message: str) -> Dict[str, Any]:
    """构造统一错误返回格式"""
    return {
        "status": "failed",
        "message": message,
        "output_format": "text",
        "data": {},
    }