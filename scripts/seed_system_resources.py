"""种子导入：内置资源（git 种子包）→ 系统用户目录 + 私有根布局扁平化

背景（2026-11 确认）：内置资源改为「运行期安装」，公共资源归属系统用户
（uid=100）。resources/ 降级为出厂种子包，运行期解析根收敛为
[私有根, 系统根]（见规划切面③）。同时目录布局扁平化：取消
resources-private/ 中间层，空间目录直接落用户目录顶层
（SB-USER/<uid>/{tools,agents,data,models}/）。

本脚本两步（均幂等，可重复执行；不覆盖已存在项）：

  1) 扁平化：SB-USER/<uid>/resources-private/<space>/ → SB-USER/<uid>/<space>/
  2) 种子导入：resources/<space>/ → SB-USER/100/<space>/
     - registry 条目：owner→"100"、visibility→"public"；
     - 死条目跳过：绝对路径 data_path/model_path 指向他机目录
       （如 /Users/leo/...）且本机不存在的幽灵条目不入库；
     - definitions/、implementations/ 按条目引用复制（git 原件保留）；
     - 物理数据集目录（resources/data/datasets/）无注册条目的补建条目，
       data_path 写导入后的绝对路径。

  _shared 不迁移：现内容只有删除掩码（models 全 deleted）与空 registry，
  幽灵条目被清后掩码失去意义；退役清理在切面③统一做。

用法：
  ./venv/bin/python scripts/seed_system_resources.py --dry-run   # 预览
  ./venv/bin/python scripts/seed_system_resources.py             # 执行
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.user_paths import SYSTEM_UID, USER_HOME_ROOT

SPACES = ("tools", "agents", "data", "models")
SEED_ROOT = ROOT / "resources"
DRY = False
done: list[str] = []


def log(msg: str) -> None:
    done.append(msg)
    print(("  [dry] " if DRY else "  ") + msg)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _save(path: Path, items: list[dict]) -> None:
    if DRY:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_into(src: Path, dst: Path) -> bool:
    """目录/文件复制到 dst；dst 已存在则逐子项补缺（不覆盖）。返回是否动过。

    .venv 不复制：它是可再生的构建产物（git 种子本就不含，平台经
    create_tool_venv/check_tool_env 从 uv 缓存硬链接重建）；copytree
    会打断硬链接变成真实 25G 占用。
    """
    if not src.exists() or src.name in (".venv", "__pycache__"):
        return False
    if not dst.exists():
        if not DRY:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst,
                                ignore=shutil.ignore_patterns(".venv", "__pycache__"))
            else:
                shutil.copy2(src, dst)
        log(f"复制 {src} → {dst}")
        return True
    touched = False
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.name in (".venv", "__pycache__"):
                continue
            touched |= _copy_into(child, dst / child.name)
    return touched


def _entry_alive(entry: dict, space: str) -> tuple[bool, str]:
    """条目可否导入：绝对 data_path/model_path 指向他机（本机不存在）则跳过。"""
    if not entry.get("id"):
        return False, "空 id"
    if entry.get("status") == "deleted":
        return False, "已删除"
    for key in ("data_path", "model_path"):
        p = entry.get(key)
        if isinstance(p, str) and p.startswith("/") and not Path(p).exists():
            return False, f"{key} 指向他机死路径 {p[:50]}"
    if space == "data" and not str(entry.get("data_path") or "").strip():
        return False, "data_path 为空"
    return True, ""


def _entry_files(entry: dict, space: str) -> list[Path]:
    """条目引用的种子侧物理路径（spec / impl / 数据集目录）。"""
    out: list[Path] = []
    sp = entry.get("spec_path")
    if isinstance(sp, str) and sp:
        out.append(SEED_ROOT / space / sp)
    ip = entry.get("impl_path")
    if isinstance(ip, str) and ip:
        out.append(SEED_ROOT / space / ip)
    dp = entry.get("data_path")
    if space == "data" and isinstance(dp, str) and dp and not dp.startswith("/"):
        out.append(SEED_ROOT / space / dp)
    return out


def flatten_private() -> None:
    """步骤1：resources-private/<space> → 家目录顶层 <space>（不覆盖）。"""
    print("步骤1 布局扁平化")
    if not USER_HOME_ROOT.is_dir():
        print(f"  {USER_HOME_ROOT} 不存在，跳过")
        return
    for d in sorted(USER_HOME_ROOT.iterdir()):
        if not d.is_dir() or not d.name.isdigit():
            continue
        old = d / "resources-private"
        if not old.is_dir():
            continue
        for space in SPACES:
            src = old / space
            if src.is_dir():
                _move_merge(src, d / space)
                if src.exists() and not any(src.iterdir()) and not DRY:
                    src.rmdir()
        rest = [p.name for p in old.iterdir()
                if p.name != "__pycache__" and (p.is_file() or any(p.iterdir()))]
        if not rest:
            if not DRY:
                shutil.rmtree(old)
            log(f"删除空的 {old}")
        else:
            print(f"  ⚠️ {old} 仍有非空间内容 {rest}，保留待手工处理")


def _move_merge(src: Path, dst: Path) -> None:
    """src → dst：dst 不存在整搬；存在则逐子项补缺（不覆盖）。"""
    if not dst.exists():
        if not DRY:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        log(f"上提 {src} → {dst}")
        return
    if not src.is_dir():
        log(f"跳过（目标已存在）{src}")
        return
    for child in sorted(src.iterdir()):
        if child.name == "__pycache__":
            continue
        _move_merge(child, dst / child.name)


def _backfill_dataset_entries(space: str, seed_entries: list[dict], dst_dir: Path,
                              existing_ids: set) -> list[dict]:
    """种子物理数据集无注册条目的补建条目（data_path 指导入后位置）。"""
    ds_root = SEED_ROOT / "data" / "datasets"
    if space != "data" or not ds_root.is_dir():
        return []
    known = {str(e.get("data_path", "")).rstrip("/") for e in seed_entries}
    added: list[dict] = []
    for d in sorted(ds_root.iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        if d.name in existing_ids or any(k.endswith(d.name) for k in known):
            continue
        target = dst_dir / "datasets" / d.name
        _copy_into(d, target)
        n = sum(1 for f in d.rglob("*") if f.is_file())
        added.append({
            "id": d.name,
            "name": d.name,
            "version": "0.1.0",
            "type": "generic",
            "status": "active",
            "spec_path": "",
            "data_path": str(USER_HOME_ROOT / SYSTEM_UID / "data" / "datasets" / d.name),
            "file_count": n,
            "formats": sorted({f.suffix.lstrip(".") for f in d.rglob("*") if f.is_file() and f.suffix})[:8],
            "tags": [],
            "quality_score": None,
            "created_at": "1970-01-01T00:00:00Z",
            "visibility": "public",
            "storage": "local",
            "owner": SYSTEM_UID,
            "_seed_backfilled": True,
        })
        log(f"补建数据集条目 {d.name}（{n} 个文件）")
    return added


def import_seed() -> None:
    """步骤2：resources/<space> → SB-USER/<uid>/<space>（uid=SYSTEM_UID）。"""
    print(f"步骤2 种子导入 → {USER_HOME_ROOT / SYSTEM_UID}")
    for space in SPACES:
        seed_dir = SEED_ROOT / space
        dst_dir = USER_HOME_ROOT / SYSTEM_UID / space
        seed_entries = _load(seed_dir / "registry.json")
        kept: list[dict] = []
        for e in seed_entries:
            ok, why = _entry_alive(e, space)
            if not ok:
                print(f"  跳过死条目 {space}/{e.get('id')}: {why}")
                continue
            e["owner"] = SYSTEM_UID
            e["visibility"] = "public"
            kept.append(e)

        existing = {e.get("id") for e in _load(dst_dir / "registry.json")}
        kept.extend(_backfill_dataset_entries(space, seed_entries, dst_dir, existing))

        # 物理文件按条目引用复制
        for e in kept:
            for src in _entry_files(e, space):
                if not src.exists():
                    continue
                rel = src.relative_to(seed_dir)
                _copy_into(src, dst_dir / rel)

        # registry 合并写回（同 id 保留目标已有——运行期改动优先于出厂值）
        dst_entries = _load(dst_dir / "registry.json")
        have = {e.get("id") for e in dst_entries}
        new = [e for e in kept if e.get("id") not in have]
        if new:
            _save(dst_dir / "registry.json", dst_entries + new)
            log(f"{space}: 导入 {len(new)} 条（跳过死条目 {len(seed_entries) - len([e for e in seed_entries if e in kept])}）")
        else:
            print(f"  {space}: 无新条目（种子 {len(seed_entries)} 条，目标已有 {len(dst_entries)} 条）")


def main() -> None:
    global DRY
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的动作")
    ap.add_argument("--flatten-only", action="store_true",
                    help="只做步骤1（旧根上提）：须与 private_root 代码改造同批执行")
    ap.add_argument("--seed-only", action="store_true",
                    help="只做步骤2（种子导入）：目标为新目录，不影响运行期旧路径")
    args = ap.parse_args()
    DRY = args.dry_run
    if not args.seed_only:
        flatten_private()
    if not args.flatten_only:
        import_seed()
    print(f"\n完成，共 {len(done)} 项动作{'（dry-run 未落盘）' if DRY else ''}")


if __name__ == "__main__":
    main()
