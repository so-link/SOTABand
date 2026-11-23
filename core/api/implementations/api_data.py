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
    registry = DataRegistry()
    all_entries = registry._read()
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
    """api-data-get: 根据名称或 id 精确获取数据集信息。

    只返回当前用户有权限访问的数据集。只做精确匹配——数据处理工具一旦
    模糊匹配猜错数据集，会用错数据产出错误结果还报告成功，这是不可接受的。
    歧义（重名）或未命中一律返回明确信号，由上层追问用户，绝不自行猜测。
    """

    @staticmethod
    def call(**kwargs) -> dict:
        name = kwargs.get("name", "").strip()
        if not name:
            return {"dataset": None, "error": "not_found", "message": "数据集名称不能为空"}

        # 直接同步读取 registry.json，避免 asyncio.run() 在 event loop 中冲突
        visible, all_entries, _me = _load_visible_datasets()

        # 1) name 精确匹配（唯一命中直接返回；重名返回候选，交上层追问）
        by_name = [ds for ds in visible if ds.get("name") == name]
        if len(by_name) == 1:
            return {"dataset": by_name[0]}
        if len(by_name) > 1:
            return {
                "dataset": None,
                "error": "ambiguous",
                "message": f"存在 {len(by_name)} 个名为 '{name}' 的数据集，请指定数据集 id",
                "candidates": [
                    {"id": ds.get("id"), "name": ds.get("name")}
                    for ds in by_name
                ],
            }

        # 2) name 未命中 → 按 id 精确匹配（Agent 把 name 解析成 id 后传给工具的场景）
        by_id = [ds for ds in visible if str(ds.get("id")) == name]
        if len(by_id) == 1:
            return {"dataset": by_id[0]}

        # 3) 未命中。若全库存在同名条目但属于他人，给出明确提示，
        #    避免使用者误以为是"名字写错了"。（同样只按 name/id 精确匹配）
        foreign = [
            ds for ds in all_entries
            if ds.get("name") == name or str(ds.get("id")) == name
        ]
        if foreign:
            return {
                "dataset": None,
                "error": "not_found",
                "message": (
                    f"数据集 '{name}' 存在于注册表中，但不属于当前用户，无权访问。"
                    f"（该数据集可能注册于其他机器或属于其他使用者）"
                ),
            }

        return {"dataset": None, "error": "not_found", "message": f"未找到名为 '{name}' 的数据集"}
