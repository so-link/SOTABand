
# 模型注册与获取 API 设计文档

> 参照「数据集注册 API」和「获取数据集 API」的设计模式，为模型空间新增「注册模型」和「获取模型」两个 API。

---

## 一、设计背景

### 1.1 模型空间在架构中的位置

根据 architecture.md，SOTABand 资源层包含六大空间，模型空间（`resources/models/`）是其中之一：

```
资源层 (resources/)
├── 数据空间 (data/)
├── 工具空间 (tools/)
├── 模型空间 (models/)     ← 本次设计
├── Agent空间 (agents/)
├── 用户空间 (users/)
└── 任务空间 (tasks/)
```

模型空间的管理内容包括：
- 多模态模型（LLM / ViT / 3D-CNN / 时序模型等）
- 模型名称、框架类型、输入输出格式、权重文件路径、版本、部署状态、关联工具ID

每个 AI 模型对应工具空间中的一个调用工具，形成「模型空间版本管理 + 工具空间调用接口」的双重管理模式。

### 1.2 当前状态

模型空间目录 `resources/models/` 存在但仅有 `__init__.py`，没有：
- ModelRegistry 注册中心
- registry.json 注册表
- definitions/ 定义文档目录
- API 实现

工具代码无法通过 API 注册模型或查询模型路径。

### 1.3 对标参照

| 数据集侧（已实现） | 模型侧（本次设计） |
|-------------------|-------------------|
| DataRegistry | **ModelRegistry** |
| `resources/data/registry.json` | `resources/models/registry.json` |
| `core/api/implementations/api_data.py` | `core/api/implementations/api_model.py` |
| `api-data-register` | **`api-model-register`** |
| `api-data-get` | **`api-model-get`** |

---

## 二、API 设计

### 2.1 api-model-register（注册模型）

**功能**：将一个 AI 模型注册到模型空间，保存模型的元信息（名称、框架、权重路径、输入输出格式等）。

#### 输入参数

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| id | string | 否 | 自动生成 `model-{timestamp}` | 模型唯一标识 |
| name | string | 是 | — | 模型名称，如 "YOLOv8-nano" |
| raw_md | string | 是 | — | MD 规范文档（模型的能力描述） |
| framework | string | 是 | — | 模型框架，如 "PyTorch", "ONNX", "TensorFlow" |
| model_path | string | 是 | — | 模型权重文件路径 |
| input_format | string | 否 | — | 输入格式，如 "image/PNG 640x640" |
| output_format | string | 否 | — | 输出格式，如 "json/bbox" |
| version | string | 否 | "0.1.0" | 模型版本号 |
| tags | list | 否 | [] | 标签列表 |
| associated_tool_id | string | 否 | — | 关联的调用工具 ID（模型对应工具空间中的工具） |

#### 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| model_id | string | 注册的模型 ID |
| name | string | 模型名称 |
| tags | list | 标签列表 |
| _action | string | `"register_model"`（前端识别标记） |

#### registry.json 条目结构

```json
{
  "id": "model-1723123456",
  "name": "YOLOv8-nano",
  "version": "0.1.0",
  "type": "model",
  "status": "registered",
  "spec_path": "definitions/model-1723123456.md",
  "framework": "PyTorch",
  "model_path": "models/yolov8-nano/weights/best.pt",
  "input_format": "image/PNG 640x640",
  "output_format": "json/bbox",
  "tags": ["目标检测", "YOLO", "轻量"],
  "associated_tool_id": "tool-yolov8-inference",
  "created_at": "2026-08-04T00:00:00Z"
}
```

---

### 2.2 api-model-get（获取模型）

**功能**：根据模型名称，从已注册的模型中查找并返回该模型的详细信息（特别是 `model_path`，供工具代码加载模型权重）。

#### 输入参数

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 是 | — | 模型名称（精确匹配优先，回退模糊匹配） |

#### 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| model | dict | 模型详细信息（id, name, framework, model_path, input_format, output_format, version, associated_tool_id 等），未找到时返回 null |
| message | string | 未找到时的提示信息 |

#### 查找逻辑（与 api-data-get 一致）

1. 精确匹配 `entry.name == name`
2. 模糊匹配 `name.lower() in entry.name.lower()`
3. 未找到返回 `{"model": null, "message": "未找到名为 'xxx' 的模型"}`

---

## 三、文件清单

### 3.1 新建文件

| 文件 | 说明 |
|------|------|
| `core/resource/registry/model_registry.py` | 模型注册中心，继承 BaseRegistry |
| `core/api/implementations/api_model.py` | 模型 API 实现（ApiModelRegister + ApiModelGet） |
| `resources/models/registry.json` | 模型注册表（初始为空数组 `[]`） |
| `resources/models/definitions/` | 模型 MD 规范文档目录 |
| `resources/models/models/` | 模型权重文件存储目录 |
| `core/api/definitions/api-model-register.md` | 注册模型 API 的 MD 规范文档 |
| `core/api/definitions/api-model-get.md` | 获取模型 API 的 MD 规范文档 |

### 3.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `core/api/registry.json` | 新增 `api-model-register` 和 `api-model-get` 两条目 |
| `frontend/src/stores/chat-store.ts` | 识别 `_action: "register_model"`，自动加入模型空间 |

---

## 四、实现细节

### 4.1 ModelRegistry

参照 `DataRegistry` 实现，继承 `BaseRegistry`：

```python
# core/resource/registry/model_registry.py

MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "models"
REGISTRY_FILE = MODEL_DIR / "registry.json"

class ModelRegistry(BaseRegistry):
    def __init__(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "definitions").mkdir(parents=True, exist_ok=True)
        (MODEL_DIR / "models").mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            self._write([])

    # _read, _write, _get_def_dir, register, unregister, get, list_all, update
    # 与 DataRegistry 完全相同的实现模式
```

`register` 方法中构建的 entry 结构：
```python
entry = {
    "id": model_id,
    "name": resource.get("name", model_id),
    "version": resource.get("version", "0.1.0"),
    "type": "model",
    "status": "registered",
    "spec_path": f"definitions/{model_id}.md",
    "framework": resource.get("framework", ""),
    "model_path": resource.get("model_path", f"models/{model_id}/"),
    "input_format": resource.get("input_format", ""),
    "output_format": resource.get("output_format", ""),
    "tags": resource.get("tags", []),
    "associated_tool_id": resource.get("associated_tool_id", ""),
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
```

### 4.2 ApiModelRegister

参照 `ApiDataRegister` 实现：

```python
class ApiModelRegister:
    registry = ModelRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        resource = {
            "id": kwargs.get("id", f"model-{int(time.time())}"),
            "name": kwargs.get("name", ""),
            "raw_md": kwargs.get("raw_md", ""),
            "framework": kwargs.get("framework", ""),
            "model_path": kwargs.get("model_path", ""),
            "input_format": kwargs.get("input_format", ""),
            "output_format": kwargs.get("output_format", ""),
            "version": kwargs.get("version", "0.1.0"),
            "tags": kwargs.get("tags", []),
            "associated_tool_id": kwargs.get("associated_tool_id", ""),
        }
        model_id = await ApiModelRegister.registry.register(resource)
        # 如果没有标签，同步等待 LLM 生成标签（与数据集注册一致）
        if not resource["tags"]:
            try:
                await _auto_generate_model_tags(model_id, resource["name"], resource.get("raw_md", ""))
            except Exception:
                pass
        return {
            "model_id": model_id,
            "name": resource["name"],
            "tags": resource["tags"],
            "_action": "register_model",
        }
```

### 4.3 ApiModelGet

参照 `ApiDataGet` 实现（**同步方法**，直接读 registry.json 避免 event loop 冲突）：

```python
class ApiModelGet:
    @staticmethod
    def call(**kwargs) -> dict:
        name = kwargs.get("name", "").strip()
        if not name:
            return {"model": None, "message": "模型名称不能为空"}
        # 同步读取 registry.json
        reg_path = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "models" / "registry.json"
        if not reg_path.exists():
            return {"model": None, "message": "模型注册表不存在"}
        models = json.loads(reg_path.read_text(encoding='utf-8'))
        # 精确匹配
        for m in models:
            if m.get("name") == name:
                return {"model": m}
        # 模糊匹配
        for m in models:
            if name.lower() in m.get("name", "").lower():
                return {"model": m}
        return {"model": None, "message": f"未找到名为 '{name}' 的模型"}
```

### 4.4 标签自动生成

参照数据集的 `_auto_generate_dataset_tags`，为模型注册增加 `_auto_generate_model_tags`：

```python
async def _auto_generate_model_tags(model_id: str, name: str, spec_md: str):
    """LLM 自动生成模型标签"""
    from core.llm.client import create_llm_client
    llm = create_llm_client()
    prompt = f"""根据以下模型信息，生成3-5个简短的中文标签（每个2-4字），用于模型分类和检索。直接返回 JSON 数组。

模型名称: {name}
模型描述: {spec_md[:500]}

返回格式: ["标签1", "标签2", "标签3"]"""
    response = await llm.chat(...)
    tags = _extract_tags_json(response)
    # 回退正则提取
    if tags:
        await registry.update(model_id, {"tags": tags})
```

### 4.5 registry.json 条目

在 `core/api/registry.json` 中新增两条：

```json
{
  "id": "api-model-register",
  "name": "模型注册API",
  "version": "1.0.0",
  "category": "resource",
  "status": "active",
  "spec_path": "definitions/api-model-register.md",
  "impl_module": "core.api.implementations.api_model",
  "impl_class": "ApiModelRegister",
  "impl_method": "call",
  "is_async": true,
  "input_schema": {
    "name": "string",
    "raw_md": "string",
    "framework": "string",
    "model_path": "string",
    "input_format": "string",
    "output_format": "string",
    "version": "string",
    "tags": "list",
    "associated_tool_id": "string"
  },
  "output_schema": {
    "model_id": "string"
  },
  "tags": ["模型", "注册", "管理"],
  "created_at": "2026-08-04T00:00:00Z"
}
```

```json
{
  "id": "api-model-get",
  "name": "获取模型信息",
  "version": "1.0.0",
  "category": "resource",
  "status": "active",
  "spec_path": "definitions/api-model-get.md",
  "impl_module": "core.api.implementations.api_model",
  "impl_class": "ApiModelGet",
  "impl_method": "call",
  "is_async": false,
  "input_schema": {
    "name": "string"
  },
  "output_schema": {
    "model": "dict"
  },
  "tags": ["模型", "检索", "查询"],
  "created_at": "2026-08-04T00:00:00Z"
}
```

---

## 五、工具调用示例

注册模型后，工具代码可以通过以下方式使用：

```python
# 注册模型
result = _call_api("api-model-register",
    name="YOLOv8-nano",
    raw_md=spec_md,
    framework="PyTorch",
    model_path="/path/to/best.pt",
    input_format="image/PNG 640x640",
    output_format="json/bbox",
    tags=["目标检测", "YOLO"],
    associated_tool_id="tool-yolov8-inference"
)
# result = {"model_id": "model-xxx", "name": "YOLOv8-nano", ...}

# 获取模型路径
result = _call_api("api-model-get", name="YOLOv8-nano")
# result = {"model": {"id": "model-xxx", "model_path": "/path/to/best.pt", ...}}

model_path = result["model"]["model_path"]
# 加载模型权重
model = torch.load(model_path)
```

---

## 六、与数据集 API 的对比

| 维度 | 数据集 API | 模型 API |
|------|-----------|---------|
| 注册 API ID | `api-data-register` | `api-model-register` |
| 获取 API ID | `api-data-get` | `api-model-get` |
| Registry 类 | `DataRegistry` | `ModelRegistry` |
| 注册表文件 | `resources/data/registry.json` | `resources/models/registry.json` |
| 定义文档目录 | `resources/data/definitions/` | `resources/models/definitions/` |
| 资源存储目录 | `resources/data/datasets/` | `resources/models/models/` |
| 实现文件 | `core/api/implementations/api_data.py` | `core/api/implementations/api_model.py` |
| 特有字段 | data_path, file_count, total_size, formats | framework, model_path, input_format, output_format, associated_tool_id |
| 标签自动生成 | 是（`_auto_generate_dataset_tags`） | 是（`_auto_generate_model_tags`） |
| 前端自动加入空间 | 是（`_action: "register_dataset"`） | 是（`_action: "register_model"`） |

---

## 七、实现步骤

### Phase 1：后端基础设施
1. 新建 `core/resource/registry/model_registry.py`（继承 BaseRegistry）
2. 新建 `resources/models/registry.json`（初始空数组）
3. 新建 `resources/models/definitions/` 和 `resources/models/models/` 目录

### Phase 2：API 实现
4. 新建 `core/api/implementations/api_model.py`（ApiModelRegister + ApiModelGet + 标签生成）
5. 新建 `core/api/definitions/api-model-register.md`
6. 新建 `core/api/definitions/api-model-get.md`
7. 在 `core/api/registry.json` 中新增两条 API 条目

### Phase 3：前端联动
8. 更新 `frontend/src/stores/chat-store.ts`，识别 `_action: "register_model"`，自动加入模型空间
