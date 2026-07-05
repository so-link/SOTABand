import os
import pandas as pd
import numpy as np
from typing import Any, Dict

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.svm import SVR
from sklearn.neural_network import MLPRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import r2_score, mean_squared_error
import joblib


def execute(**kwargs) -> Dict[str, Any]:
    """
    代餐奶昔形状预测模型训练

    Args:
        data_path: str, CSV数据集文件路径，需包含7个特征列和2个目标列 (必填)
        model_type: str, 模型类型，支持: random_forest, gradient_boosting, linear_regression, svr, mlp (默认: random_forest)
        random_state: int, 随机种子 (默认: 42)
        test_size: float, 测试集比例 (默认: 0.2)
        output_dir: str, 模型保存目录 (默认: ./models)

    Returns:
        dict: 包含 status, message, output_format, data 的标准输出字典
    """
    # 提取参数
    data_path = kwargs.get('data_path')
    model_type = kwargs.get('model_type', 'random_forest')
    random_state = kwargs.get('random_state', 42)
    test_size = kwargs.get('test_size', 0.2)
    output_dir = kwargs.get('output_dir', './models')

    # 1. 校验必填参数
    if data_path is None:
        return {
            "status": "failed",
            "message": "缺少必填参数 data_path",
            "output_format": "file",
            "data": {}
        }

    # 2. 文件存在性检查
    if not os.path.isfile(data_path):
        return {
            "status": "failed",
            "message": f"文件路径无效: {data_path}",
            "output_format": "file",
            "data": {}
        }

    # 3. 读取数据
    try:
        df = pd.read_csv(data_path)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"读取CSV文件失败: {str(e)}",
            "output_format": "file",
            "data": {}
        }

    # 4. 提取数值列，按规则获取特征与目标
    numeric_cols = df.select_dtypes(include='number').columns.tolist()
    if len(numeric_cols) < 9:
        return {
            "status": "failed",
            "message": "数据集至少需要9个数值列（7个特征+2个目标），当前数值列数量不足",
            "output_format": "file",
            "data": {}
        }

    X = df[numeric_cols[:7]]
    y = df[numeric_cols[-2:]]

    # 5. 缺失值检查
    if X.isnull().any().any() or y.isnull().any().any():
        return {
            "status": "failed",
            "message": "数据存在空值，需先清洗",
            "output_format": "file",
            "data": {}
        }

    n_samples = X.shape[0]
    if n_samples < 10:
        return {
            "status": "failed",
            "message": "数据集样本数不足（少于10），请提供更多数据",
            "output_format": "file",
            "data": {}
        }

    # 6. test_size 范围检查
    if test_size <= 0 or test_size >= 1:
        return {
            "status": "failed",
            "message": "test_size 比例不合理，应在(0, 1)之间",
            "output_format": "file",
            "data": {}
        }

    # 7. 模型类型校验
    supported_models = ['random_forest', 'gradient_boosting', 'linear_regression', 'svr', 'mlp']
    if model_type not in supported_models:
        return {
            "status": "failed",
            "message": f"不支持的模型类型: {model_type}，支持: {', '.join(supported_models)}",
            "output_format": "file",
            "data": {}
        }

    # 8. 实例化模型
    if model_type == 'random_forest':
        model = RandomForestRegressor(random_state=random_state)
    elif model_type == 'gradient_boosting':
        model = GradientBoostingRegressor(random_state=random_state)
    elif model_type == 'linear_regression':
        model = LinearRegression()
    elif model_type == 'svr':
        model = MultiOutputRegressor(SVR())
    elif model_type == 'mlp':
        model = MLPRegressor(random_state=random_state, max_iter=1000)
    else:
        return {
            "status": "failed",
            "message": f"不支持的模型类型: {model_type}",
            "output_format": "file",
            "data": {}
        }

    # 9. 数据划分
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    # 10. 训练
    try:
        model.fit(X_train, y_train)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"模型训练失败: {str(e)}",
            "output_format": "file",
            "data": {}
        }

    # 11. 预测与评估 (全局 RMSE)
    y_pred_train = model.predict(X_train)
    y_pred_test = model.predict(X_test)

    train_rmse = np.sqrt(np.mean((y_train.values - y_pred_train) ** 2))
    test_rmse = np.sqrt(np.mean((y_test.values - y_pred_test) ** 2))

    train_r2 = r2_score(y_train, y_pred_train, multioutput='uniform_average')
    test_r2 = r2_score(y_test, y_pred_test, multioutput='uniform_average')

    # 12. 保存模型
    try:
        os.makedirs(output_dir, exist_ok=True)
        model_path = os.path.join(output_dir, 'model.pkl')
        joblib.dump(model, model_path)
        abs_path = os.path.abspath(model_path)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"模型保存失败: {str(e)}",
            "output_format": "file",
            "data": {}
        }

    # 13. 构建返回信息
    message = (
        f"Training completed. "
        f"Train R²={train_r2:.3f}, RMSE={train_rmse:.2f}; "
        f"Test R²={test_r2:.3f}, RMSE={test_rmse:.2f}. "
        f"Model type: {model_type}"
    )

    return {
        "status": "success",
        "message": message,
        "output_format": "file",
        "data": {
            "file_path": abs_path,
            "model_type": model_type,
            "train_r2": round(train_r2, 3),
            "train_rmse": round(train_rmse, 2),
            "test_r2": round(test_r2, 3),
            "test_rmse": round(test_rmse, 2)
        }
    }