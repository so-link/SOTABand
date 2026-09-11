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

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

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
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}, encoding='utf-8').read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    # encoding/errors 必须显式：工具输出是含中文的 UTF-8 JSON，
    # Windows 下 text=True 默认按 GBK 解码会直接 UnicodeDecodeError
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
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


# ============================================================
# 数字识别工具
# 模式：train / test
# ============================================================

def _create_model(n: int):
    """创建三层卷积神经网络，每层隐含节点数为 n。

    返回 nn.Sequential 模型。
    """
    import torch.nn as nn

    return nn.Sequential(
        # Conv1: (1, 28, 28) -> (n, 28, 28)
        nn.Conv2d(1, n, kernel_size=3, padding=1),
        nn.ReLU(),
        # Conv2: (n, 28, 28) -> (n, 28, 28)
        nn.Conv2d(n, n, kernel_size=3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),  # -> (n, 14, 14)
        # Conv3: (n, 14, 14) -> (n, 14, 14)
        nn.Conv2d(n, n, kernel_size=3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),  # -> (n, 7, 7)
        nn.Flatten(),
        nn.Linear(n * 7 * 7, 10)
    )


def _get_device():
    """返回可用设备：cuda 或 cpu"""
    try:
        import torch
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    except Exception:
        return "cpu"


def _prepare_mnist_loaders(batch_size: int = 64):
    """下载/加载 MNIST 数据集，返回 train_loader 和 test_loader"""
    import torch
    import torchvision
    from torch.utils.data import DataLoader

    data_root = _DATA_DIR / "mnist"
    data_root.mkdir(parents=True, exist_ok=True)

    transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.1307,), (0.3081,))
    ])

    train_dataset = torchvision.datasets.MNIST(
        root=str(data_root), train=True, download=True, transform=transform
    )
    test_dataset = torchvision.datasets.MNIST(
        root=str(data_root), train=False, download=True, transform=transform
    )

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)
    return train_loader, test_loader


def _mode_train(kwargs: dict) -> dict:
    """训练模式：训练三层 CNN，保存模型并返回准确率"""
    import traceback
    try:
        epoch = kwargs.get("epoch", None)
        n = kwargs.get("n", None)

        # --- 参数校验与类型转换 ---
        try:
            epoch = int(epoch)
            n = int(n)
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "output_format": "text",
                "message": "参数无效：epoch 和 n 必须是正整数",
                "data": {}
            }
        if epoch <= 0 or n <= 0:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "参数无效：epoch 和 n 必须大于 0",
                "data": {}
            }

        import torch
        import torch.nn as nn
        import torch.optim as optim
        from torch.utils.data import DataLoader

        device = _get_device()
        model = _create_model(n).to(device)

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters())

        train_loader, test_loader = _prepare_mnist_loaders()

        # --- 训练 ---
        model.train()
        for ep in range(epoch):
            running_loss = 0.0
            for images, labels in train_loader:
                images, labels = images.to(device), labels.to(device)
                optimizer.zero_grad()
                outputs = model(images)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
                running_loss += loss.item()
            # 可选：打印进度，但不输出到返回值；这里静默
            # print(f"Epoch {ep+1}/{epoch} loss: {running_loss/len(train_loader):.4f}")

        # --- 计算训练集和测试集准确率 ---
        def _accuracy(loader: DataLoader) -> float:
            model.eval()
            correct = 0
            total = 0
            with torch.no_grad():
                for images, labels in loader:
                    images, labels = images.to(device), labels.to(device)
                    outputs = model(images)
                    _, predicted = torch.max(outputs, 1)
                    total += labels.size(0)
                    correct += (predicted == labels).sum().item()
            if total == 0:
                return 0.0
            return round(correct / total, 4)

        train_acc = _accuracy(train_loader)
        test_acc = _accuracy(test_loader)

        # --- 保存模型 ---
        model_dir = _DATA_DIR / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "digit_recognition.pth"
        torch.save({"n": n, "state_dict": model.state_dict()}, str(model_path))

        return {
            "status": "success",
            "output_format": "text",
            "message": f"训练完成：训练集准确率 {train_acc:.4f}，测试集准确率 {test_acc:.4f}",
            "data": {
                "train_accuracy": train_acc,
                "test_accuracy": test_acc,
                "model_path": str(model_path)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"训练模式执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }


def _mode_test(kwargs: dict) -> dict:
    """测试模式：对输入图片进行识别"""
    import traceback
    try:
        file_path = kwargs.get("file", None)
        if not file_path:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "参数无效：必须提供待识别图片文件路径 file",
                "data": {}
            }

        from pathlib import Path
        p = Path(file_path)
        if not p.is_absolute():
            p = Path(_resolve_path(str(p)))
        if not p.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"文件不存在: {p}",
                "data": {}
            }

        import torch
        import torchvision
        from PIL import Image, ImageOps

        model_path = _DATA_DIR / "models" / "digit_recognition.pth"
        if not model_path.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"模型文件不存在: {model_path}，请先执行训练模式",
                "data": {}
            }

        device = _get_device()
        checkpoint = torch.load(str(model_path), map_location=device)
        if not isinstance(checkpoint, dict) or "n" not in checkpoint or "state_dict" not in checkpoint:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "模型文件格式错误，请重新训练",
                "data": {}
            }

        n = checkpoint["n"]
        try:
            n = int(n)
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "output_format": "text",
                "message": "模型文件中的 n 参数无效",
                "data": {}
            }

        model = _create_model(n).to(device)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()

        # --- 图像预处理 ---
        img = Image.open(p)
        img = img.convert("L")  # 灰度化
        img = ImageOps.invert(img)  # 反相，使背景为黑、数字为白，匹配 MNIST 风格
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((28, 28), resample=resample)

        transform = torchvision.transforms.Compose([
            torchvision.transforms.ToTensor(),
            torchvision.transforms.Normalize((0.1307,), (0.3081,))
        ])
        input_tensor = transform(img).unsqueeze(0).to(device)

        # --- 推理 ---
        with torch.no_grad():
            outputs = model(input_tensor)
            probabilities = torch.softmax(outputs, dim=1)
            confidence, predicted = torch.max(probabilities, 1)

        digit = int(predicted.item())
        confidence = float(confidence.item())

        return {
            "status": "success",
            "output_format": "text",
            "message": f"识别结果：数字 {digit}，置信度 {confidence:.4f}",
            "data": {
                "digit": digit,
                "confidence": confidence
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"测试模式执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }


def execute(**kwargs) -> dict[str, Any]:
    """数字识别工具主入口，按 mode 分发到训练或测试模式"""
    try:
        mode = kwargs.get("mode", "train")
        if mode == "train":
            return _mode_train(kwargs)
        elif mode == "test":
            return _mode_test(kwargs)
        else:
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"未知模式: {mode}，支持的模式：train / test",
                "data": {}
            }
    except Exception as e:
        import traceback
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }