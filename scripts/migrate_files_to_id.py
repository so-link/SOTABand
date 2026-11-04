"""一次性迁移：存量用户目录文件登记进 user_files 表 + 工作区树改造

背景：文件接口从"路径直传"硬切为"file_id"（2026-11）。改造后新上传/新工具
产物会自动登记，但**存量**文件没有登记行、存量 workspace_tree.json 的节点还
引用绝对路径——前端已不再按路径取文件，这些节点必须换成 fileId，指向用户
目录外（历史"打开目录"功能遗留）的节点只能丢弃。

对每个用户目录（<USER_HOME_ROOT>/<uid>/）：

  1. uploads/ 与 plots/ 下现存文件 → 登记（kind 分别为 upload/plot）；
  2. workspace_tree.json：文件节点 path → fileId（能对应上登记的），
     目录节点保留（虚拟分组），path 字段删除；用户目录外/已不存在的
     文件节点移除。

幂等：登记本身幂等（同 rel_path 复用 id）；树重写只在存在变化时写回。

用法：停后端 → ./venv/bin/python scripts/migrate_files_to_id.py → 起后端
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.user_paths import USER_HOME_ROOT  # noqa: E402


def migrate_user(uid: str) -> tuple[int, int]:
    """迁移一个用户。返回 (登记文件数, 树中移除的节点数)。"""
    from core import user_files

    home = USER_HOME_ROOT / uid
    if not home.is_dir():
        return 0, 0

    # 让 to_rel/register 以该用户身份解析（脚本无请求上下文）
    import os
    os.environ["SOTABAND_USER_ID"] = uid

    registered = 0
    by_path: dict[str, str] = {}  # 绝对路径(str) → file_id
    for sub, kind in (("uploads", "upload"), ("plots", "plot")):
        d = home / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            try:
                rec = user_files.register(p, kind=kind)
            except (ValueError, OSError) as e:
                print(f"  跳过 {p}: {e}")
                continue
            registered += 1
            by_path[str(p.resolve())] = rec["file_id"]

    removed = _rewrite_tree(home, by_path)
    return registered, removed


def _rewrite_tree(home: Path, by_path: dict[str, str]) -> int:
    """工作区树：文件节点 path→fileId；目录外/缺失的文件节点删除。"""
    tree_file = home / "workspace_tree.json"
    if not tree_file.exists():
        return 0
    try:
        data = json.loads(tree_file.read_text(encoding="utf-8"))
        root = data.get("root")
        if not isinstance(root, dict):
            return 0
    except (OSError, json.JSONDecodeError):
        print(f"  跳过损坏的树: {tree_file}")
        return 0

    removed = 0
    changed = False
    by_fid = set(by_path.values())

    def walk(n: dict) -> dict | None:
        """文件节点 path→fileId（无对应登记则删除）；目录节点递归过滤。"""
        nonlocal removed, changed
        if n.get("type") == "file":
            # 已迁移过的节点（无 path、fileId 有效）原样保留——重跑幂等
            if n.get("fileId") and n.get("fileId") in by_fid:
                return n
            path = n.get("path", "")
            fid = by_path.get(str(Path(path).resolve())) if path else None
            if fid is None:
                removed += 1
                changed = True
                return None
            if n.get("fileId") != fid or "path" in n:
                changed = True
                return {k: v for k, v in n.items() if k != "path"} | {"fileId": fid}
            return n
        out = dict(n)
        kids = []
        for c in out.get("children") or []:
            nc = walk(c)
            if nc is not None:
                kids.append(nc)
        if out.get("children") != kids:
            changed = True
            out["children"] = kids
        if str(out.get("path") or "").startswith("/"):
            out.pop("path")
            changed = True
        return out

    new_root = walk(root)
    if changed and new_root is not None:
        tree_file.write_text(
            json.dumps({"root": new_root}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return removed


def main() -> None:
    if not USER_HOME_ROOT.is_dir():
        print(f"用户目录根不存在: {USER_HOME_ROOT}")
        return
    for d in sorted(USER_HOME_ROOT.iterdir()):
        if not d.is_dir():
            continue
        reg, rem = migrate_user(d.name)
        print(f"用户 {d.name}: 登记 {reg} 个文件，工作区树移除 {rem} 个失效节点")
    print("完成。")


if __name__ == "__main__":
    main()
