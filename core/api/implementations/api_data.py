"""数据集相关 API 实现"""
from core.resource.registry.data_registry import DataRegistry


class ApiDataRegister:
    """api-data-register: 注册数据集"""
    registry = DataRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        resource = {
            "id": kwargs.get("id", ""),
            "name": kwargs.get("name", ""),
            "raw_md": kwargs.get("raw_md", ""),
            "data_path": kwargs.get("data_path", ""),
            "file_count": kwargs.get("file_count", 0),
            "total_size": kwargs.get("total_size", 0),
            "formats": kwargs.get("formats", []),
        }
        dataset_id = await ApiDataRegister.registry.register(resource)
        return {"dataset_id": dataset_id}


class ApiDataDelete:
    """api-data-delete: 删除数据集"""
    registry = DataRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        resource_id = kwargs.get("resource_id", "")
        await ApiDataDelete.registry.unregister(resource_id)
        return {}


class ApiDataList:
    """api-data-list: 列出所有数据集"""
    registry = DataRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        datasets = await ApiDataList.registry.list_all()
        return {"datasets": datasets}


class ApiDataGet:
    """api-data-get: 根据名称获取数据集信息"""

    @staticmethod
    def call(**kwargs) -> dict:
        name = kwargs.get("name", "").strip()
        if not name:
            return {"dataset": None, "message": "数据集名称不能为空"}

        # 直接同步读取 registry.json，避免 asyncio.run() 在 event loop 中冲突
        import json
        from pathlib import Path as _Path
        reg_path = _Path(__file__).resolve().parent.parent.parent.parent / "resources" / "data" / "registry.json"
        if not reg_path.exists():
            return {"dataset": None, "message": "数据注册表不存在"}

        datasets = json.loads(reg_path.read_text(encoding='utf-8'))
        # 精确匹配
        for ds in datasets:
            if ds.get("name") == name:
                return {"dataset": ds}
        # 模糊匹配（包含）
        for ds in datasets:
            if name.lower() in ds.get("name", "").lower():
                return {"dataset": ds}
        # 未找到
        return {"dataset": None, "message": f"未找到名为 '{name}' 的数据集"}
