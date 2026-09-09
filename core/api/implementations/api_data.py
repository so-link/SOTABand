"""数据集相关 API 实现

【归属模型】三类资源的可见性不同，这里必须严格区分：

- **数据集：私有**。数据目录在本机，天然属于当前用户。
- **工具：共享**。工具是代码资产，所有使用者看到同一批。
- **Agent：待定**。

工具是共享的，而工具通过本模块的 API 访问数据集。因此本模块**必须**做
归属过滤——否则私有数据集的隔离只在界面层生效，任何共享工具调用
API 就能绕过，看到他人环境注册的数据集。
"""
from core.resource.registry.data_registry import DataRegistry
from core.user_context import get_current_user_id


def _load_visible_datasets() -> tuple[list, list, str]:
    """同步读取数据集，并按归属划分。

    ApiDataGet.call 被设计为同步方法（避免在已运行的 event loop 中
    再调用 asyncio.run 导致冲突），因此这里也用同步读取。

    Returns:
        (visible, all_entries, current_user)
        - visible: 属于当前用户的数据集
        - all_entries: 全部条目（仅用于生成更友好的错误提示）
    """
    import json
    from pathlib import Path as _Path

    reg_path = _Path(__file__).resolve().parent.parent.parent.parent / "resources" / "data" / "registry.json"
    if not reg_path.exists():
        return [], [], get_current_user_id()

    try:
        all_entries = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], [], get_current_user_id()

    registry = DataRegistry()
    me = get_current_user_id()
    visible = [d for d in all_entries if registry.belongs_to(d, me)]
    return visible, all_entries, me


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
        # owner 由 DataRegistry.register 强制注入，不接受调用方传入，
        # 避免伪造归属把数据集挂到他人名下。
        dataset_id = await ApiDataRegister.registry.register(resource)
        return {"dataset_id": dataset_id}


class ApiDataDelete:
    """api-data-delete: 删除数据集"""
    registry = DataRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        resource_id = kwargs.get("resource_id", "")
        if not resource_id:
            return {"error": "缺少 resource_id"}

        # 归属校验：不允许删除不属于当前用户的数据集
        entry = await ApiDataDelete.registry.get(resource_id)
        if entry is not None:
            me = get_current_user_id()
            if not ApiDataDelete.registry.belongs_to(entry, me):
                return {
                    "error": f"无权删除数据集 '{resource_id}'：它不属于当前用户。"
                             f"（数据集是私有资源，只能删除自己的）"
                }

        await ApiDataDelete.registry.unregister(resource_id)
        return {}


class ApiDataList:
    """api-data-list: 列出当前用户的数据集"""
    registry = DataRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        # 只返回属于当前用户的数据集
        datasets = await ApiDataList.registry.list_all(owner=get_current_user_id())
        return {"datasets": datasets}


class ApiDataGet:
    """api-data-get: 根据名称获取数据集信息

    只返回当前用户有权限访问的数据集。若全库存在同名条目但属于他人，
    会在 message 中明确说明，帮助使用者区分"不存在"与"无权限"。
    """

    @staticmethod
    def call(**kwargs) -> dict:
        name = kwargs.get("name", "").strip()
        if not name:
            return {"dataset": None, "message": "数据集名称不能为空"}

        # 直接同步读取 registry.json，避免 asyncio.run() 在 event loop 中冲突
        visible, all_entries, _me = _load_visible_datasets()

        # 1) 在"我的"数据集中精确匹配
        for ds in visible:
            if ds.get("name") == name:
                return {"dataset": ds}

        # 2) 在"我的"数据集中模糊匹配（包含）
        for ds in visible:
            if name.lower() in ds.get("name", "").lower():
                return {"dataset": ds}

        # 3) 未找到。若他人环境存在同名条目，给出明确提示，
        #    避免使用者误以为是"名字写错了"。
        for ds in all_entries:
            if ds.get("name") == name or name.lower() in ds.get("name", "").lower():
                return {
                    "dataset": None,
                    "message": (
                        f"数据集 '{name}' 存在于注册表中，但不属于当前用户，无权访问。"
                        f"（该数据集可能注册于其他机器或属于其他使用者）"
                    ),
                }

        return {"dataset": None, "message": f"未找到名为 '{name}' 的数据集"}
