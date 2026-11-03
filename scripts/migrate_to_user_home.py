"""一次性迁移：存量用户数据 → USER_HOME（/root/SB-USER/0/）

多用户路径规划（2026-11）落地脚本。幂等：目标已存在的项跳过不覆盖。
迁移内容：

  storage/platform.db(+wal/shm)   → /root/SB-USER/0/platform.db
  /root/resources-private/*       → /root/SB-USER/0/resources-private/*
  data/uploads/*                  → /root/SB-USER/0/uploads/*
  storage/workspace_tools.json    → /root/SB-USER/0/workspace_tools.json
  storage/workspace_tree.json     → /root/SB-USER/0/workspace_tree.json
  <私有根>/users/{users.json,secret.key} → SYSTEM_ROOT/storage/auth/（系统级例外）

迁移后把用户侧 registry 条目与数据集 data_path 中的旧私有根前缀改写，
并把历史 owner（local-xxx / 登录 id）统一改写为 "0"（默认用户）。

用法：停后端 → ./venv/bin/python scripts/migrate_to_user_home.py → 起后端
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.user_paths import USER_HOME_ROOT, SYSTEM_ROOT, DEFAULT_UID  # noqa: E402

OLD_PRIVATE = Path("/root/resources-private")
NEW_PRIVATE = USER_HOME_ROOT / DEFAULT_UID / "resources-private"
NEW_HOME = USER_HOME_ROOT / DEFAULT_UID
LEGACY_PRIVATE = ROOT / "resources-private"  # 僵尸目录（历史产物）

moved: list[str] = []


def move_data(src: Path, dst: Path) -> None:
    """src → dst：dst 不存在则整搬；dst 已存在则逐子项补缺（不覆盖）。"""
    if not src.exists():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(f"{src} → {dst}")
        return
    if src.is_dir():
        for child in src.iterdir():
            target = dst / child.name
            if not target.exists():
                shutil.move(str(child), str(target))
                moved.append(f"{child} → {target}")
            else:
                print(f"  跳过（目标已存在）: {child}")
    elif not dst.exists():
        shutil.move(str(src), str(dst))
        moved.append(f"{src} → {dst}")


def main() -> None:
    NEW_HOME.mkdir(parents=True, exist_ok=True)

    # 1) SQLite（chat/task/cron/audit）
    for name in ("platform.db", "platform.db-wal", "platform.db-shm"):
        move_data(ROOT / "storage" / name, NEW_HOME / name)

    # 2) 认证（系统级例外：SYSTEM_ROOT/storage/auth）
    auth_dir = SYSTEM_ROOT / "storage" / "auth"
    for name in ("users.json", "secret.key"):
        for old_root in (OLD_PRIVATE, LEGACY_PRIVATE):
            src = old_root / "users" / name
            if src.exists() and not (auth_dir / name).exists():
                auth_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(auth_dir / name))
                moved.append(f"{src} → {auth_dir / name}")

    # 3) 私有资源根（含 4 个 registry + implementations + usage.json + 数据集文件）
    move_data(OLD_PRIVATE, NEW_PRIVATE)
    # 僵尸目录内容合并（etf-quote-analysis-agent 等历史产物）
    if LEGACY_PRIVATE.exists() and LEGACY_PRIVATE.resolve() != NEW_PRIVATE.resolve():
        for space in ("tools", "agents", "data", "models"):
            src_reg = LEGACY_PRIVATE / space / "registry.json"
            dst_reg = NEW_PRIVATE / space / "registry.json"
            if src_reg.exists():
                try:
                    src_items = json.loads(src_reg.read_text(encoding="utf-8"))
                    dst_items = json.loads(dst_reg.read_text(encoding="utf-8")) if dst_reg.exists() else []
                    have = {e.get("id") for e in dst_items if isinstance(e, dict)}
                    add = [e for e in src_items if isinstance(e, dict) and e.get("id") not in have]
                    if add:
                        dst_items.extend(add)
                        dst_reg.parent.mkdir(parents=True, exist_ok=True)
                        dst_reg.write_text(json.dumps(dst_items, ensure_ascii=False, indent=2), encoding="utf-8")
                        moved.append(f"registry 合并 {space}: +{len(add)} 条")
                    # 实现目录/定义文件补缺
                    for sub in ("implementations", "definitions"):
                        s = LEGACY_PRIVATE / space / sub
                        if s.is_dir():
                            for child in s.iterdir():
                                t = NEW_PRIVATE / space / sub / child.name
                                if not t.exists():
                                    t.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.move(str(child), str(t))
                                    moved.append(f"{child} → {t}")
                except Exception as e:  # noqa: BLE001
                    print(f"  ⚠️ registry 合并失败 {space}: {e}")

    # 4) 附件上传
    move_data(ROOT / "data" / "uploads", NEW_HOME / "uploads")

    # 5) 工作区状态文件
    move_data(ROOT / "storage" / "workspace_tools.json", NEW_HOME / "workspace_tools.json")
    move_data(ROOT / "storage" / "workspace_tree.json", NEW_HOME / "workspace_tree.json")

    # 5.1) 旧持久化用户文件（user_context 已不再读取，归档到 USER_HOME 备查）
    move_data(ROOT / "storage" / "user.json", NEW_HOME / "user.json.legacy")

    # 6) registry 条目的 owner 统一改写为默认用户；data_path 旧前缀改写
    for space in ("tools", "agents", "data", "models"):
        reg = NEW_PRIVATE / space / "registry.json"
        if not reg.exists():
            continue
        try:
            items = json.loads(reg.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for e in items:
            if not isinstance(e, dict):
                continue
            owner = e.get("owner")
            if owner and owner != DEFAULT_UID:
                e["owner"] = DEFAULT_UID
                changed = True
            dp = e.get("data_path")
            if isinstance(dp, str) and dp.startswith(str(OLD_PRIVATE)):
                e["data_path"] = dp.replace(str(OLD_PRIVATE), str(NEW_PRIVATE), 1)
                changed = True
            if isinstance(dp, str) and dp.startswith(str(LEGACY_PRIVATE)):
                e["data_path"] = dp.replace(str(LEGACY_PRIVATE), str(NEW_PRIVATE), 1)
                changed = True
        if changed:
            reg.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            moved.append(f"{space}/registry.json: owner→{DEFAULT_UID}, data_path 前缀改写")

    print(f"\n完成，共迁移 {len(moved)} 项:")
    for m in moved:
        print(f"  {m}")
    print(f"\nUSER_HOME = {NEW_HOME}")


if __name__ == "__main__":
    main()
