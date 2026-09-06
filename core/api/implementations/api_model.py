"""模型相关 API 实现"""

import json
import time
from pathlib import Path

from core.resource.registry.model_registry import ModelRegistry, MODEL_DIR


async def _auto_generate_model_tags(model_id: str, name: str, spec_md: str):
    """LLM 自动生成模型标签，更新到 registry"""
    try:
        from core.llm.client import create_llm_client
        from app.api.routes.data_routes import _extract_tags_json
        import re

        llm = create_llm_client()
        prompt = f"""根据以下模型信息，生成3-5个简短的中文标签（每个2-4字），用于模型分类和检索。直接返回 JSON 数组。

模型名称: {name}
模型描述: {spec_md[:500]}

返回格式: ["标签1", "标签2", "标签3"]"""
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200, timeout=30,
        )

        tags = _extract_tags_json(response)
        if not tags:
            matches = re.findall(r'[\u4e00-\u9fff]{2,4}', response)
            tags = list(dict.fromkeys(matches))[:5]

        if tags:
            registry = ModelRegistry()
            await registry.update(model_id, {"tags": tags})
            print(f"[_auto_generate_model_tags] 标签已更新 model_id={model_id}: {tags}")
    except Exception as e:
        print(f"[_auto_generate_model_tags] 异常 model_id={model_id}: {e}")


class ApiModelRegister:
    """api-model-register: 注册模型"""
    registry = ModelRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        base_name = kwargs.get("name", "").strip()
        # 自动在模型名称后追加时间戳（精确到毫秒），避免同名模型注册时冲突
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime()) + f"_{int(time.time() * 1000) % 1000:03d}"
        if base_name:
            model_name = f"{base_name}_{timestamp}"
        else:
            model_name = f"model_{timestamp}"

        resource = {
            "id": kwargs.get("id", f"model-{int(time.time())}"),
            "name": model_name,
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
        # 如果没有标签，同步等待 LLM 生成标签
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


class ApiModelGet:
    """api-model-get: 获取模型信息（同步方法）"""

    @staticmethod
    def call(**kwargs) -> dict:
        name = kwargs.get("name", "").strip()
        if not name:
            return {"model": None, "message": "模型名称不能为空"}

        reg_path = MODEL_DIR / "registry.json"
        if not reg_path.exists():
            return {"model": None, "message": "模型注册表不存在"}

        try:
            models = json.loads(reg_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, FileNotFoundError):
            return {"model": None, "message": "无法读取模型注册表"}

        # 精确匹配
        for m in models:
            if m.get("name") == name:
                return {"model": m}

        # 模糊匹配
        for m in models:
            if name.lower() in m.get("name", "").lower():
                return {"model": m}

        return {"model": None, "message": f"未找到名为 '{name}' 的模型"}
