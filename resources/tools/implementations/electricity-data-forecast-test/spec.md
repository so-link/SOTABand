---
id: electricity-data-forecast-test
name: 电力数据预测测试
version: 0.1.0
type: script
language: python
status: active
created: 2026-08-13
---

# 电力数据预测测试

## 1. 功能概述

本工具用于对电力传感器时序数据进行 LSTM 模型的滑动窗口预测测试。  
输入包含 CSV 数据文件路径、待测试列编号、已注册模型名称、历史窗口大小和预测窗口大小。工具首先调用系统 API【获取模型信息】获取模型存储路径，并加载该路径下的 LSTM 模型；然后使用指定列的时序数据构造连续滑动窗口样本，对每个历史窗口使用模型预测未来指定长度的数据，并计算预测值与真实值之间的平均绝对误差（MAE）；最后生成时序对比图，用两条不同颜色曲线分别展示原始时间序列和模型预测序列，横坐标为时刻，纵坐标为数值。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| path | string | 是 | 无 | CSV 文件路径，表格数据为时序数据，第一行为表头，每行代表一个时刻，每列代表一个传感器 |
| n | int | 是 | 无 | 待测试的列编号（从 0 开始），对应 CSV 中某一列传感器数据 |
| model_name | string | 是 | 无 | 已注册的 LSTM 模型名称，工具将通过【获取模型信息】获取其存储路径 |
| w | int | 是 | 无 | 历史时间窗口大小，即用于预测的连续历史时刻数量 |
| v | int | 是 | 无 | 预测时间窗口大小，即需要预测的未来时刻数量 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明，包含预测 MAE 等关键信息 |
| output_format | string | image |
| data | dict | 输出数据，包含 `image_path`（时序对比图路径）和 `mae`（平均绝对误差） |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"..."}` | 纯文本 |
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |
| `table` | `{"columns":[...], "rows":[[...]]}` | 渲染表格 |
| `file` | `{"file_path":"/path/to/result.csv"}` | 下载链接 |

> 当 `output_format` 为 `image` 时，`data` 中除 `image_path` 外还会包含 `mae` 字段，用于展示预测误差。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| pandas | >=1.5.0 | 读取 CSV 文件、处理时序数据 |
| numpy | >=1.24.0 | 数值计算与数组操作 |
| torch | >=2.0.0 | 加载和运行 LSTM 模型 |
| matplotlib | >=3.7.0 | 绘制时序对比图 |
| pathlib | 标准库 | 处理文件路径 |

## 5. 运行机制

本工具的核心执行逻辑参考实现如下（完整代码）：

```python
原LSTM模型的代码如下：

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset


class LSTMModel(nn.Module):
    """简单 LSTM 时序预测模型"""
    def __init__(self, input_size=1, hidden_size=64, num_layers=1, output_size=1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        # x: (batch, seq_len, input_size)
        out, _ = self.lstm(x)
        # 取最后一个时间步的输出
        out = out[:, -1, :]
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


def execute(**kwargs) -> dict[str, Any]:
    try:
        # ── 1. 参数读取与基础校验 ──
        path = kwargs.get("path", "")
        if not path:
            return {"status": "failed", "message": "缺少必填参数 path", "output_format": "text", "data": {}}

        try:
            n = int(kwargs.get("n"))
            w = int(kwargs.get("w"))
            v = int(kwargs.get("v"))
            epoch = int(kwargs.get("epoch"))
        except Exception:
            return {"status": "failed", "message": "参数 n、w、v、epoch 必须为整数", "output_format": "text", "data": {}}

        if w < 1 or v < 1 or epoch < 1:
            return {
                "status": "failed",
                "message": "参数无效：窗口大小 w、预测长度 v 和训练轮次 epoch 必须大于 0",
                "output_format": "text",
                "data": {},
            }

        resolved_path = _resolve_path(path)
        csv_file = Path(resolved_path)
        if not csv_file.exists():
            return {"status": "failed", "message": "CSV 文件不存在", "output_format": "text", "data": {}}

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

        # ── 3. 构建监督学习样本 ──
        # 使用训练集前，先按 80/20 划分用于 MinMax 缩放与样本切分
        # 为了保留时序顺序，采用顺序划分
        test_samples = max(1, int(len(series) * 0.2))
        train_len = len(series) - test_samples
        if train_len < 1:
            return {
                "status": "failed",
                "message": "数据量过少，无法划分训练集和测试集",
                "output_format": "text",
                "data": {},
            }

        train_series = series[:train_len]
        train_min = float(train_series.min())
        train_max = float(train_series.max())
        scale = (train_max - train_min) if train_max > train_min else 1.0

        def normalize(x):
            return (x - train_min) / scale

        def denormalize(x):
            return x * scale + train_min

        series_norm = normalize(series)

        X, Y = [], []
        for i in range(len(series_norm) - w - v + 1):
            X.append(series_norm[i : i + w])
            Y.append(series_norm[i + w : i + w + v])

        X = np.array(X, dtype=np.float32).reshape(-1, w, 1)
        Y = np.array(Y, dtype=np.float32).reshape(-1, v)

        if len(X) < 2:
            return {
                "status": "failed",
                "message": "样本数量不足，无法进行模型训练与测试集评估",
                "output_format": "text",
                "data": {},
            }

        split = len(X) - max(1, int(len(X) * 0.2))
        X_train, X_test = X[:split], X[split:]
        Y_train, Y_test = Y[:split], Y[split:]

        if len(X_train) == 0 or len(X_test) == 0:
            return {
                "status": "failed",
                "message": "训练集或测试集为空，请增加数据量",
                "output_format": "text",
                "data": {},
            }

        # ── 4. 构建 PyTorch DataLoader ──
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        torch.manual_seed(42)
        np.random.seed(42)

        batch_size = min(32, len(X_train))
        train_dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(Y_train))
        test_dataset = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(Y_test))
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=False)
        test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

        # ── 5. 训练 LSTM 模型 ──
        model = LSTMModel(input_size=1, hidden_size=64, num_layers=1, output_size=v).to(device)
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

        for _ in range(epoch):
            model.train()
            for xb, yb in train_loader:
                xb, yb = xb.to(device), yb.to(device)
                optimizer.zero_grad()
                pred = model(xb)
                loss = criterion(pred, yb)
                loss.backward()
                optimizer.step()

        # ── 6. 计算 MAE（还原到原始量纲） ──
        def compute_mae(loader):
            model.eval()
            preds, targets = [], []
            with torch.no_grad():
                for xb, yb in loader:
                    xb, yb = xb.to(device), yb.to(device)
                    pred = model(xb)
                    preds.append(pred.cpu().numpy())
                    targets.append(yb.cpu().numpy())
            preds = np.concatenate(preds, axis=0)
            targets = np.concatenate(targets, axis=0)
            preds_original = denormalize(preds)
            targets_original = denormalize(targets)
            return float(np.mean(np.abs(preds_original - targets_original)))

        train_mae = compute_mae(train_loader)
        test_mae = compute_mae(test_loader)

        # ── 7. 保存模型 ──
        model_dir = _DATA_DIR / "models"
        model_dir.mkdir(parents=True, exist_ok=True)

        # 允许通过额外参数 model 自定义保存路径，否则使用默认路径
        custom_model_path = kwargs.get("model", "")
        if custom_model_path:
            model_path = str(Path(_resolve_path(custom_model_path)))
        else:
            model_path = str(model_dir / "electricity_lstm_model.pt")

        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        model_name = Path(model_path).name

        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "input_size": 1,
                "hidden_size": 64,
                "output_size": v,
                "window_size": w,
                "train_min": train_min,
                "train_max": train_max,
                "column_index": n,
                "data_length": len(series),
            },
            model_path,
        )

        # ── 8. 调用模型注册 API ──
        raw_md = (
            f"# 电力数据预测模型\n\n"
            f"- 模型类型：LSTM 时序预测\n"
            f"- 输入：过去 {w} 个时刻的传感器数据\n"
            f"- 输出：未来 {v} 个时刻的预测数据\n"
            f"- 训练轮次：{epoch}\n"
            f"- 数据列：第 {n} 列\n"
            f"- 框架：PyTorch\n"
        )

        try:
            register_result = _call_api(
                "api-model-register",
                name=model_name,
                raw_md=raw_md,
                framework="PyTorch",
                model_path=model_path,
                input_format="csv",
                output_format="table",
                version="0.1.0",
                tags=["electricity", "lstm", "time-series"],
                associated_tool_id="electricity-data-forecast-model-training",
            )
            model_id = register_result.get("model_id", "")
            register_msg = "；模型注册成功"
        except Exception as e:
            model_id = ""
            register_msg = f"；模型注册失败：{e}"

        # ── 9. 组装返回结果 ──
        data = {
            "columns": ["train_mae", "test_mae", "model_name", "model_path"],
            "rows": [[round(train_mae, 6), round(test_mae, 6), model_name, model_path]],
            "train_mae": train_mae,
            "test_mae": test_mae,
            "model_name": model_name,
            "model_path": model_path,
        }

        message = (
            f"模型训练完成，训练集 MAE={train_mae:.6f}，"
            f"测试集 MAE={test_mae:.6f}{register_msg}"
        )

        return {
            "status": "success",
            "message": message,
            "output_format": "table",
            "data": data,
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"处理异常：{str(e)}",
            "output_format": "text",
            "data": {},
        }
```

### 5.1 执行流程

1. **读取输入数据**：读取 `path` 指定的 CSV 文件，解析表头并提取第 `n` 列时序数据。
2. **获取模型路径**：调用系统 API【获取模型信息】，根据 `model_name` 获取对应 LSTM 模型的存储路径。
3. **加载模型**：从获取的路径加载 LSTM 模型及其元信息（如归一化参数、窗口大小等）。
4. **构造滑动窗口**：对历史数据序列，按历史窗口大小 `w` 和预测窗口大小 `v` 生成连续的输入‑输出样本对。
5. **执行预测**：对每个滑动窗口，使用 LSTM 模型预测未来 `v` 个时刻的值，并进行反归一化还原到原始量纲。
6. **计算误差**：计算预测值与真实值之间的平均绝对误差（MAE）。
7. **绘制对比图**：使用 matplotlib 绘制原始时间序列与预测时间序列的对比曲线，横坐标为时刻，纵坐标为数值，两条曲线使用不同颜色。
8. **返回结果**：返回包含对比图路径和 MAE 值的结构化结果。

### 5.2 错误处理

- **文件不存在** → 返回错误信息：`CSV 文件不存在`
- **参数无效**：
  - `path` 为空 → 返回 `缺少必填参数 path`
  - `n`、`w`、`v` 非整数 → 返回 `参数必须为整数`
  - 窗口大小或预测长度小于 1 → 返回 `参数无效：窗口大小 w、预测长度 v 必须大于 0`
  - 列编号 `n` 超出范围 → 返回 `列编号超出范围`
  - 数据长度不足 → 返回 `数据长度不足，至少需要 w + v 条`
- **模型加载失败** → 捕获异常并返回详细错误信息
- **处理异常** → 捕获所有未预期异常并返回 `处理异常：{错误详情}`

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-13 | 初始版本 |