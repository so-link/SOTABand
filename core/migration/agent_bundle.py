"""Agent 迁移包（.sotaagent.zip）的导出与导入。

Agent 在磁盘上同样是「三元组」：
    resources*/agents/definitions/<id>.md      —— 规范文档（前端展示）
    resources*/agents/implementations/<id>/    —— 实现目录（agent.py + config.yaml + spec.md）
    resources*/agents/registry.json 里的条目    —— 注册元数据（role / tools / tags）

与工具的区别（导入后要额外体检的两件事）：
  1. **依赖的工具**：registry 条目的 `tools` 列出它要调用的工具，目标环境缺了就跑不起来；
  2. **依赖的 Python 包**：agent.py 里的第三方 import。

包结构：
    manifest.json
    registry_entry.json
    definitions/<id>.md
    implementations/<id>/**

安全策略与工具迁移一致：拒绝 zip slip、限制大小与成员数、只写私有资源根。
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

from core.deps import extract_imports
from core.migration.tool_bundle import (
    MAX_BUNDLE_MB,
    MAX_FILE_MB,
    MAX_MEMBERS,
    MAX_TOTAL_MB,
    _collect_impl_files,
    _current_user,
    _now_iso,
    _sha256,
    _stamp,
    _parse_dependencies,
)
from core.resource import paths
from core.resource.registry.agent_registry import AgentRegistry

BUNDLE_FORMAT = "sotaband-agent-bundle"
BUNDLE_VERSION = 1
BUNDLE_SUFFIX = ".sotaagent.zip"


# ── 定位 ──────────────────────────────────────────────────────

def _registry() -> AgentRegistry:
    return AgentRegistry()


def _find_entry(agent_id: str) -> tuple[dict | None, Path | None]:
    """按「私有覆盖内置」的优先级找注册条目，返回 (条目, 来源根)。"""
    reg = _registry()
    for root in reversed(paths.resource_roots()):
        for e in reg.read_from(root):
            if isinstance(e, dict) and e.get("id") == agent_id:
                return e, root
    return None, None


def _find_spec(agent_id: str) -> Path | None:
    for d in _registry().get_spec_search_dirs():
        p = d / f"{agent_id}.md"
        if p.exists():
            return p
    return None


def _find_impl(agent_id: str) -> Path | None:
    for d in _registry().get_impl_search_dirs():
        p = d / agent_id
        if (p / "agent.py").exists():
            return p
    return None


def _agent_python() -> str:
    """Agent 运行时用的解释器（与平台一致：项目 venv 优先）。"""
    for rel in ("venv/bin/python", "venv/Scripts/python.exe"):
        p = paths.PROJECT_ROOT / rel
        if p.exists():
            return str(p)
    import sys
    return sys.executable


def export_dir() -> Path:
    d = paths.PROJECT_ROOT / "data" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 导出 ──────────────────────────────────────────────────────

def export_agent(
    agent_id: str,
    out_dir: Path | str | None = None,
    include_assets: bool = True,
) -> dict:
    """把 Agent 打成迁移包，返回包信息。"""
    import re
    agent_id = (agent_id or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", agent_id):
        raise ValueError(f"非法的 Agent id: {agent_id!r}")

    entry, origin = _find_entry(agent_id)
    if entry is None:
        raise ValueError(f"Agent 不存在: {agent_id}")

    spec_file = _find_spec(agent_id)
    impl_dir = _find_impl(agent_id)
    if spec_file is None and impl_dir is None:
        raise ValueError(f"Agent {agent_id} 的规范与实现目录都不存在，无法导出")

    spec_bytes = spec_file.read_bytes() if spec_file else b""
    spec_md = spec_bytes.decode("utf-8", errors="replace")

    payload: list[tuple[str, bytes]] = []
    skipped: list[dict] = []
    total = 0
    max_file = MAX_FILE_MB * 1024 * 1024
    for rel, p in _collect_impl_files(impl_dir, include_tests=True, include_assets=include_assets):
        size = p.stat().st_size
        if size > max_file:
            skipped.append({"path": rel, "size": size, "reason": f"超过单文件上限 {MAX_FILE_MB}MB"})
            continue
        data = p.read_bytes()
        payload.append((f"implementations/{agent_id}/{rel}", data))
        total += len(data)

    if spec_bytes:
        payload.insert(0, (f"definitions/{agent_id}.md", spec_bytes))

    # Python 依赖：agent.py 的 import + 规范文档里的依赖表
    agent_py = (impl_dir / "agent.py") if impl_dir else None
    code = agent_py.read_text(encoding="utf-8-sig", errors="replace") if agent_py else ""
    deps = sorted(set(extract_imports(code)) | set(_parse_dependencies(spec_md)))

    file_manifest = [
        {"path": arc, "size": len(data), "sha256": _sha256(data)}
        for arc, data in payload
    ]

    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_VERSION,
        "exported_at": _now_iso(),
        "source": {
            "project": paths.PROJECT_ROOT.name,
            "origin": "private" if origin == paths.PRIVATE_ROOT else "builtin",
        },
        "agent": {
            "id": agent_id,
            "name": entry.get("name", agent_id),
            "version": entry.get("version", "0.1.0"),
            "role": entry.get("role", "task"),
            "tags": entry.get("tags", []),
            "tools": entry.get("tools", []),
        },
        "dependencies": deps,
        "skipped": skipped,
        "files": file_manifest,
    }

    out = Path(out_dir) if out_dir else export_dir()
    out.mkdir(parents=True, exist_ok=True)
    version = str(entry.get("version", "0.1.0")).replace("/", "_")
    file_name = f"agent-{agent_id}-{version}-{_stamp()}{BUNDLE_SUFFIX}"
    bundle_path = out / file_name

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("registry_entry.json", json.dumps(entry, ensure_ascii=False, indent=2))
        for arc, data in payload:
            info = zipfile.ZipInfo(arc, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)

    raw = buf.getvalue()
    bundle_path.write_bytes(raw)

    return {
        "agent_id": agent_id,
        "name": entry.get("name", agent_id),
        "bundle_path": str(bundle_path),
        "file_name": file_name,
        "size": len(raw),
        "sha256": _sha256(raw),
        "file_count": len(payload),
        "skipped": skipped,
        "dependencies": deps,
        "tools": entry.get("tools", []),
    }


# ── 导入 ──────────────────────────────────────────────────────

def _safe_member(name: str, agent_id: str) -> str | None:
    if not name or name.endswith("/"):
        return None
    if "\\" in name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"包内存在非法路径: {name!r}")
    if name in {"manifest.json", "registry_entry.json"}:
        return None
    prefix = f"implementations/{agent_id}/"
    if name.startswith(prefix):
        rel = name[len(prefix):]
        return rel or None
    if name == f"definitions/{agent_id}.md":
        return None
    raise ValueError(f"包内存在非预期路径: {name!r}")


def import_agent(
    bundle_path: Path | str | None = None,
    bundle_bytes: bytes | None = None,
    overwrite: bool = False,
) -> dict:
    """把 Agent 迁移包导入当前环境的私有 Agent 空间。"""
    import re

    if bundle_bytes is None:
        if bundle_path is None:
            raise ValueError("必须提供 bundle_path 或 bundle_bytes")
        p = Path(bundle_path)
        if not p.is_file():
            raise ValueError(f"迁移包不存在: {p}")
        if p.stat().st_size > MAX_BUNDLE_MB * 1024 * 1024:
            raise ValueError(f"迁移包超过 {MAX_BUNDLE_MB}MB 上限")
        bundle_bytes = p.read_bytes()
    elif len(bundle_bytes) > MAX_BUNDLE_MB * 1024 * 1024:
        raise ValueError(f"迁移包超过 {MAX_BUNDLE_MB}MB 上限")

    try:
        zf = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"不是有效的 zip 迁移包: {e}") from e

    with zf:
        names = zf.namelist()
        if len(names) > MAX_MEMBERS:
            raise ValueError(f"包内文件数超过 {MAX_MEMBERS} 上限")

        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except KeyError as e:
            raise ValueError("包内缺少 manifest.json，不是 SOTABand Agent 迁移包") from e
        except Exception as e:
            raise ValueError(f"manifest.json 解析失败: {e}") from e

        if manifest.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"不支持的包格式: {manifest.get('format')!r}")
        if int(manifest.get("format_version", 0)) > BUNDLE_VERSION:
            raise ValueError(
                f"包格式版本 {manifest.get('format_version')} 高于当前平台支持的 {BUNDLE_VERSION}"
            )

        agent_meta = manifest.get("agent") or {}
        agent_id = str(agent_meta.get("id") or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", agent_id):
            raise ValueError(f"包内 Agent id 非法: {agent_id!r}")

        private_agents = paths.ensure_private_dirs("agents")
        def_dir = private_agents / "definitions"
        impl_root = private_agents / "implementations"
        def_dir.mkdir(parents=True, exist_ok=True)
        impl_root.mkdir(parents=True, exist_ok=True)
        def_path = def_dir / f"{agent_id}.md"
        impl_dir = impl_root / agent_id

        reg_entry = None
        try:
            reg_entry = json.loads(zf.read("registry_entry.json").decode("utf-8"))
        except Exception:
            reg_entry = None

        existing_entry, existing_origin = _find_entry(agent_id)
        if existing_entry is not None and existing_origin == paths.BUILTIN_ROOT:
            raise ValueError(
                f"内置 Agent 空间已有同 id 的 {agent_id}，导入到私有根不会生效"
                f"（运行时按内置优先）。请给迁移包换一个 id，"
                f"或先从内置 resources/agents 移除该 Agent。"
            )

        existing = def_path.exists() or impl_dir.exists() or existing_entry is not None
        if existing and not overwrite:
            raise ValueError(
                f"Agent {agent_id} 已存在；如需覆盖请传 overwrite=true"
                f"（旧文件会备份为 .bak.<时间戳>）"
            )

        plan: list[tuple[str, bytes]] = []
        total = 0
        max_file = MAX_FILE_MB * 1024 * 1024
        spec_bytes = None
        for name in names:
            if name.endswith("/"):
                continue
            if name == f"definitions/{agent_id}.md":
                data = zf.read(name)
                if len(data) > max_file:
                    raise ValueError(f"{name} 超过单文件上限")
                spec_bytes = data
                total += len(data)
                continue
            rel = _safe_member(name, agent_id)
            if rel is None:
                continue
            data = zf.read(name)
            if len(data) > max_file:
                raise ValueError(f"{name} 超过单文件上限 {MAX_FILE_MB}MB")
            total += len(data)
            if total > MAX_TOTAL_MB * 1024 * 1024:
                raise ValueError(f"解压后总量超过 {MAX_TOTAL_MB}MB 上限")
            plan.append((rel, data))

        stamp = _stamp()
        backups: list[str] = []
        if impl_dir.exists():
            bak = impl_root / f"{agent_id}.bak.{stamp}"
            impl_dir.rename(bak)
            backups.append(str(bak))
        if def_path.exists():
            bak = def_dir / f"{agent_id}.md.bak.{stamp}"
            def_path.rename(bak)
            backups.append(str(bak))

        written: list[str] = []
        impl_dir.mkdir(parents=True, exist_ok=True)
        for rel, data in plan:
            target = impl_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(f"implementations/{agent_id}/{rel}")
        if spec_bytes is not None:
            def_path.write_bytes(spec_bytes)
            written.insert(0, f"definitions/{agent_id}.md")

    entry: dict = dict(reg_entry) if isinstance(reg_entry, dict) else {}
    entry.update({
        "id": agent_id,
        "name": entry.get("name") or agent_meta.get("name") or agent_id,
        "version": entry.get("version") or agent_meta.get("version") or "0.1.0",
        "role": entry.get("role") or agent_meta.get("role") or "task",
        "status": "active",
        "spec_path": f"definitions/{agent_id}.md",
        "impl_path": f"implementations/{agent_id}/",
        "tools": entry.get("tools", agent_meta.get("tools", [])),
        "tags": entry.get("tags", agent_meta.get("tags", [])),
        "health": entry.get("health", "healthy"),
        "owner": _current_user(),
        "visibility": entry.get("visibility") or "public",
        "imported_at": _now_iso(),
    })
    entry.setdefault("created_at", _now_iso())

    registry_file = private_agents / "registry.json"
    data: list[dict] = []
    if registry_file.is_file():
        try:
            loaded = json.loads(registry_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = [e for e in loaded if isinstance(e, dict)]
        except Exception:
            data = []
    data = [e for e in data if e.get("id") != agent_id]
    data.append(entry)
    registry_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 依赖工具体检：目标环境缺了它就跑不起来
    required_tools = [t for t in (entry.get("tools") or []) if isinstance(t, str)]
    have_ids = {e.get("id") for e in _registry()._read()}
    missing_tools = [t for t in required_tools if t not in have_ids]

    deps = manifest.get("dependencies") or []
    return {
        "agent_id": agent_id,
        "name": entry["name"],
        "version": entry["version"],
        "role": entry["role"],
        "tags": entry.get("tags", []),
        "files": written,
        "file_count": len(written),
        "overwritten": existing,
        "backups": backups,
        "dependencies": deps,
        "required_tools": required_tools,
        "missing_tools": missing_tools,
        "def_path": str(def_path),
        "impl_dir": str(impl_dir),
        "note": (
            f"已导入到私有 Agent 空间；依赖的工具未安装: {', '.join(missing_tools)}"
            if missing_tools else "已导入到私有 Agent 空间，所需工具齐备"
        ),
    }


# ── 依赖体检 ──────────────────────────────────────────────────

def inspect_agent_dependencies(agent_id: str) -> dict:
    """体检 Agent 的第三方依赖（agent.py 的 import + 规范里的依赖表）。"""
    from core.deps import check_dependencies

    impl = _find_impl(agent_id)
    spec = _find_spec(agent_id)
    code = ""
    if impl and (impl / "agent.py").exists():
        code = (impl / "agent.py").read_text(encoding="utf-8-sig", errors="replace")
    declared = []
    if spec:
        declared = _parse_dependencies(spec.read_text(encoding="utf-8", errors="replace"))

    py = _agent_python()
    label = "project_venv" if "/venv/" in py.replace("\\", "/") else "system"
    info = check_dependencies(code, py, label, agent_id)
    # 规范里声明、代码里动态 import 的包也纳入检查
    extra = [d for d in declared if d not in info.get("deps", [])]
    if extra:
        from core.deps import import_name_for, pip_name_for, probe_import
        for dep in extra:
            mod = import_name_for(dep)
            if mod and not probe_import(py, mod):
                info.setdefault("missing", []).append(dep)
                info.setdefault("install", []).append(pip_name_for(dep))
                info.setdefault("pairs", []).append(
                    {"import": dep, "pip": pip_name_for(dep)}
                )
        info["deps"] = sorted(set(info.get("deps", []) + extra))
    return info


# ── 包列表 ────────────────────────────────────────────────────

def list_agent_bundles(directory: Path | str | None = None) -> list[dict]:
    d = Path(directory) if directory else export_dir()
    if not d.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(d.glob(f"*{BUNDLE_SUFFIX}"), key=lambda x: x.stat().st_mtime, reverse=True):
        item = {
            "file_name": p.name,
            "bundle_path": str(p),
            "size": p.stat().st_size,
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime)),
        }
        try:
            with zipfile.ZipFile(p) as zf:
                m = json.loads(zf.read("manifest.json").decode("utf-8"))
                a = m.get("agent") or {}
                item.update({
                    "agent_id": a.get("id"),
                    "name": a.get("name"),
                    "version": a.get("version"),
                    "role": a.get("role"),
                    "tools": a.get("tools") or [],
                    "dependencies": m.get("dependencies") or [],
                })
        except Exception:
            item["error"] = "manifest 读取失败"
        out.append(item)
    return out


def resolve_agent_bundle(name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"非法的包名: {name!r}")
    p = export_dir() / name
    if not p.is_file():
        raise ValueError(f"迁移包不存在: {name}")
    return p


__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_VERSION",
    "BUNDLE_SUFFIX",
    "export_dir",
    "export_agent",
    "import_agent",
    "list_agent_bundles",
    "resolve_agent_bundle",
]
