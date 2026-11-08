"""工具迁移包（.sotatool.zip）的导出与导入。

一个工具在磁盘上是「三元组」：
    resources*/tools/definitions/<id>.md        —— 规范文档
    resources*/tools/implementations/<id>/      —— 实现目录（tool.py + spec.md + tests/ …）
    resources*/tools/registry.json 里的条目      —— 注册元数据

导出把它们打成一个自包含的 zip；导入时解到**私有资源根**并合并 registry，
因此目标环境无需重启即可在 `/api/tool/list` 看到、并可直接执行。

包结构（固定）：
    manifest.json                     # 格式版本、工具元数据、依赖、文件校验和
    registry_entry.json               # 原注册条目（导入时以此为基础重建）
    definitions/<id>.md
    implementations/<id>/**

安全：
  * 拒绝 `..`、绝对路径、反斜杠、符号链接（zip slip）；
  * 限制包大小 / 解压总量 / 单文件 / 成员数；
  * 目标目录固定为私有资源根，永不写内置资源（内置随包分发、只读）；
  * 落盘前过静态安全审查（与 /api/tool/import 同一闸门），被阻断的代码不落盘；
  * 导出目录按用户隔离（<USER_HOME>/<uid>/exports），包是用户资产，
    list / 下载 / bundle_path 一律只能在当前用户自己的目录内解析。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

from core.resource import paths
from core.resource.registry.tool_registry import ToolRegistry

BUNDLE_FORMAT = "sotaband-tool-bundle"
BUNDLE_VERSION = 1
BUNDLE_SUFFIX = ".sotatool.zip"

MAX_BUNDLE_MB = 64          # 单个包上限
MAX_TOTAL_MB = 256          # 解压后总量上限
MAX_FILE_MB = 32            # 解压后单文件上限
MAX_MEMBERS = 2000          # 成员数上限

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 实现目录里不打进包的噪声
_SKIP_DIRS = {
    "__pycache__", ".venv", "venv", ".git", ".idea", ".vscode",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
_SKIP_SUFFIX = {".pyc", ".pyo", ".pyd", ".log", ".tmp", ".orig", ".rej"}


# ── 通用小工具 ────────────────────────────────────────────────

def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _registry() -> ToolRegistry:
    return ToolRegistry()


def export_dir(uid: str | None = None) -> Path:
    """当前用户的导出包目录：<USER_HOME>/<uid>/exports。

    迁移包里含实现代码与注册元数据，属于用户资产；放在项目共享目录会让
    任何登录用户都能 list/下载到别人的包。按用户目录隔离后，与私有资源根
    同一套身份解析（core.user_paths），单用户模式下仍固定在 0 号目录。
    """
    from core.user_paths import user_home

    d = user_home(uid) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _current_user() -> str:
    try:
        from core.user_context import get_current_user_id
        return get_current_user_id()
    except Exception:
        return "unknown"


def _import_visibility(declared: object) -> str:
    """导入条目的可见性：仅系统身份可保留 public，其余一律 private。

    与 agent_routes 注册路径（is_system_identity() 判定）保持一致：导入不是
    发布通道，普通用户不应经由迁移包把资源标成全员可见。
    """
    if str(declared or "") == "public":
        try:
            from app.api.deps import is_system_identity
            if is_system_identity():
                return "public"
        except Exception:
            pass
    return "private"


def _find_entry(tool_id: str) -> tuple[dict | None, Path | None]:
    """按「私有覆盖内置」的优先级找注册条目，并返回它来自哪个根。"""
    reg = _registry()
    # resource_roots() 已是「私有 > 系统（公共）」的优先序，直接按序命中即可，
    # 不能再 reversed()——那会翻回公共层优先，导出时混版。
    for root in paths.resource_roots():
        for e in reg.read_from(root):
            if isinstance(e, dict) and e.get("id") == tool_id:
                return e, root
    return None, None


def _collect_impl_files(impl_dir: Path, include_tests: bool, include_assets: bool):
    """收集实现目录下要打进包的文件，返回 [(相对路径 posix, 绝对路径)]。"""
    out: list[tuple[str, Path]] = []
    if not impl_dir or not impl_dir.is_dir():
        return out
    for p in sorted(impl_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(impl_dir).parts
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if not include_tests and "tests" in rel_parts[:-1]:
            continue
        if p.suffix.lower() in _SKIP_SUFFIX:
            continue
        if not include_assets and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".pt", ".onnx", ".pkl", ".h5", ".xml"}:
            continue
        out.append((p.relative_to(impl_dir).as_posix(), p))
    return out


def _parse_dependencies(spec_md: str) -> list[str]:
    """从规范文档「## 4. 依赖环境」表格里提取第三方依赖名（best-effort）。"""
    deps: list[str] = []
    if not spec_md:
        return deps
    lines = spec_md.splitlines()
    inside = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("## "):
            inside = ("依赖" in line)
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        name = cells[0].strip("`").strip()
        if not name or name in {"依赖", "------", "---"} or set(name) <= {"-", ":", " "}:
            continue
        low = name.lower()
        if low in {"无", "none", "内建", "内置", "标准库", "stdlib", "python", "python3"}:
            continue
        if low.startswith("python"):
            continue
        # 第二列写「内建 / 标准库 / stdlib」的属于标准库，不算第三方依赖
        kind = cells[1].strip("`").strip().lower() if len(cells) > 1 else ""
        if any(k in kind for k in ("内建", "内置", "标准库", "stdlib", "builtin", "built-in")):
            continue
        deps.append(name)
    # 去重保序
    seen: set[str] = set()
    uniq = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


# ── 导出 ──────────────────────────────────────────────────────

def build_bundle_bytes(
    tool_id: str,
    include_tests: bool = True,
    include_assets: bool = True,
) -> tuple[bytes, str, dict]:
    """把工具打成迁移包并直接返回内存字节（不落盘），供浏览器直接下载。

    Returns:
        (zip 字节, 建议文件名, export_tool 同构的摘要 dict)
    """
    raw, file_name, info = _build_bundle(tool_id, include_tests, include_assets)
    return raw, file_name, info


def _build_bundle(
    tool_id: str,
    include_tests: bool,
    include_assets: bool,
    out_path: Path | None = None,
) -> tuple[bytes, str, dict]:
    """构建迁移包字节；out_path 非空时同时写盘。返回 (字节, 文件名, 摘要)。"""
    tool_id = (tool_id or "").strip()
    if not _ID_RE.match(tool_id):
        raise ValueError(f"非法的工具 id: {tool_id!r}")

    entry, origin = _find_entry(tool_id)
    if entry is None:
        raise ValueError(f"工具不存在: {tool_id}")

    reg = _registry()
    def_file = reg.find_def_file(tool_id)
    impl_dir = reg.find_impl_dir(tool_id)
    if def_file is None and impl_dir is None:
        raise ValueError(f"工具 {tool_id} 的定义文件与实现目录都不存在，无法导出")

    spec_bytes = def_file.read_bytes() if def_file else b""
    spec_md = spec_bytes.decode("utf-8", errors="replace")

    payload: list[tuple[str, bytes]] = []
    skipped: list[dict] = []
    total = 0
    max_file = MAX_FILE_MB * 1024 * 1024
    for rel, p in _collect_impl_files(impl_dir, include_tests, include_assets):
        size = p.stat().st_size
        if size > max_file:
            skipped.append({"path": rel, "size": size, "reason": f"超过单文件上限 {MAX_FILE_MB}MB"})
            continue
        data = p.read_bytes()
        payload.append((f"implementations/{tool_id}/{rel}", data))
        total += len(data)

    if spec_bytes:
        payload.insert(0, (f"definitions/{tool_id}.md", spec_bytes))

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
            "origin": paths.source_of(origin) if origin is not None else "builtin",
        },
        "tool": {
            "id": tool_id,
            "name": entry.get("name", tool_id),
            "version": entry.get("version", "0.1.0"),
            "type": entry.get("type", "function"),
            "language": entry.get("language", "python"),
            "tags": entry.get("tags", []),
            "modes": entry.get("modes", []),
            "param_meta": entry.get("param_meta", []),
            "input_schema": entry.get("input_schema", {}),
            "output_schema": entry.get("output_schema", {}),
        },
        "dependencies": _parse_dependencies(spec_md),
        "has_definition": bool(spec_bytes),
        "has_implementation": impl_dir is not None,
        "skipped": skipped,
        "files": file_manifest,
    }

    version = str(entry.get("version", "0.1.0")).replace("/", "_")
    file_name = f"tool-{tool_id}-{version}-{_stamp()}{BUNDLE_SUFFIX}"

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
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)

    info = {
        "tool_id": tool_id,
        "name": entry.get("name", tool_id),
        "file_name": file_name,
        "size": len(raw),
        "sha256": _sha256(raw),
        "file_count": len(payload),
        "skipped": skipped,
        "dependencies": manifest["dependencies"],
    }
    return raw, file_name, info


def export_tool(
    tool_id: str,
    out_dir: Path | str | None = None,
    include_tests: bool = True,
    include_assets: bool = True,
) -> dict:
    """把工具打成迁移包，返回包信息。

    Args:
        tool_id: 工具 id
        out_dir: 输出目录，默认本人导出目录 <USER_HOME>/<uid>/exports
        include_tests: 是否带上 implementations/<id>/tests
        include_assets: 是否带上常见二进制资产（.xml/.onnx/.pt …）

    Returns:
        {"bundle_path", "file_name", "size", "sha256", "tool_id", "file_count", "skipped"}
    """
    out = Path(out_dir) if out_dir else export_dir()
    raw, file_name, info = _build_bundle(
        tool_id, include_tests, include_assets, out_path=None)
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / file_name
    bundle_path.write_bytes(raw)
    info["bundle_path"] = str(bundle_path)
    return info


# ── 导入 ──────────────────────────────────────────────────────

def _safe_member_path(name: str, tool_id: str) -> str | None:
    """校验 zip 成员名，返回允许的相对路径（在 implementations/<id>/ 下）或 None。"""
    if not name or name.endswith("/"):
        return None
    if "\\" in name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"包内存在非法路径: {name!r}")
    if name in {"manifest.json", "registry_entry.json"}:
        return None
    prefix = f"implementations/{tool_id}/"
    if name.startswith(prefix):
        rel = name[len(prefix):]
        return rel or None
    if name == f"definitions/{tool_id}.md":
        return None  # 单独处理
    raise ValueError(f"包内存在非预期路径: {name!r}")


def import_tool(
    bundle_path: Path | str | None = None,
    bundle_bytes: bytes | None = None,
    overwrite: bool = False,
) -> dict:
    """把迁移包导入当前环境的私有工具空间。

    Args:
        bundle_path: 包文件路径（与 bundle_bytes 二选一）
        bundle_bytes: 包内容
        overwrite: 同 id 工具已存在时是否覆盖（会备份旧定义与实现）

    Returns:
        {"tool_id", "name", "files", "overwritten", "dependencies", "def_path", "impl_dir"}
    """
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
            raise ValueError("包内缺少 manifest.json，不是 SOTABand 工具迁移包") from e
        except Exception as e:
            raise ValueError(f"manifest.json 解析失败: {e}") from e

        if manifest.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"不支持的包格式: {manifest.get('format')!r}")
        if int(manifest.get("format_version", 0)) > BUNDLE_VERSION:
            raise ValueError(
                f"包格式版本 {manifest.get('format_version')} 高于当前平台支持的 {BUNDLE_VERSION}"
            )

        tool_meta = manifest.get("tool") or {}
        tool_id = str(tool_meta.get("id") or "").strip()
        if not _ID_RE.match(tool_id):
            raise ValueError(f"包内工具 id 非法: {tool_id!r}")

        # 目标：一律私有资源根
        private_tools = paths.ensure_private_dirs("tools")
        def_dir = private_tools / "definitions"
        impl_root = private_tools / "implementations"
        def_dir.mkdir(parents=True, exist_ok=True)
        impl_root.mkdir(parents=True, exist_ok=True)
        def_path = def_dir / f"{tool_id}.md"
        impl_dir = impl_root / tool_id

        reg_entry = None
        try:
            reg_entry = json.loads(zf.read("registry_entry.json").decode("utf-8"))
        except Exception:
            reg_entry = None

        existing_entry, existing_origin = _find_entry(tool_id)
        # 公共层同 id 允许导入：resource_roots() 私有优先，私有副本遮蔽公共
        # 版本，与「复制为我的副本」同一覆盖层语义（见 tool_routes /import 的
        # overrode_builtin 先例）。响应里带 overrode_builtin=true 由 UI 警示。
        # 字段名保留 overrode_builtin：前端已按此消费，语义 = 遮蔽了公共层版本。
        overrode_builtin = (existing_origin is not None
                            and paths.source_of(existing_origin) == "system")

        # 冲突只看「私有层是否已有同名」：内置同 id 不构成冲突（私有副本会
        # 遮蔽它，属于正常导入），否则内置工具将永远无法导入，用户也拿不到
        # 可编辑的副本。-- 此处曾把 existing_entry（含内置命中）一并算作冲突。
        existing = def_path.exists() or impl_dir.exists() or (
            existing_entry is not None and not overrode_builtin)
        if existing and not overwrite:
            raise ValueError(
                f"工具 {tool_id} 已存在；如需覆盖请传 overwrite=true"
                f"（旧文件会备份为 .bak.<时间戳>）"
            )

        # 先整体校验成员再落盘，避免半途失败留下残缺目录
        plan: list[tuple[str, bytes]] = []
        total = 0
        max_file = MAX_FILE_MB * 1024 * 1024
        spec_bytes = None
        for name in names:
            if name.endswith("/"):
                continue
            rel = _safe_member_path(name, tool_id)
            if name == f"definitions/{tool_id}.md":
                data = zf.read(name)
                if len(data) > max_file:
                    raise ValueError(f"{name} 超过单文件上限")
                spec_bytes = data
                total += len(data)
                continue
            if rel is None:
                continue
            data = zf.read(name)
            if len(data) > max_file:
                raise ValueError(f"{name} 超过单文件上限 {MAX_FILE_MB}MB")
            total += len(data)
            if total > MAX_TOTAL_MB * 1024 * 1024:
                raise ValueError(f"解压后总量超过 {MAX_TOTAL_MB}MB 上限")
            plan.append((rel, data))

        # 落盘前静态安全审查：与 /api/tool/import 同一闸门，被阻断的代码不落盘。
        from core.security.code_scan import check_undeclared_imports, scan_code

        _req_text = "\n".join(str(d) for d in (manifest.get("dependencies") or []))
        import_warnings: list[str] = []
        for rel, data in plan:
            if not rel.endswith(".py"):
                continue
            _code = data.decode("utf-8", errors="replace")
            _scan = scan_code(_code)
            if not _scan.ok:
                raise ValueError(
                    f"迁移包 {rel} 静态安全审查未通过：\n" + "\n".join(_scan.errors)
                )
            for _mod in check_undeclared_imports(_code, _req_text or None):
                import_warnings.append(f"{rel}: import {_mod} 未在依赖清单声明")

        stamp = _stamp()
        backups: list[str] = []
        if impl_dir.exists():
            bak = impl_root / f"{tool_id}.bak.{stamp}"
            impl_dir.rename(bak)
            backups.append(str(bak))
        if def_path.exists():
            bak = def_dir / f"{tool_id}.md.bak.{stamp}"
            def_path.rename(bak)
            backups.append(str(bak))

        written: list[str] = []
        impl_dir.mkdir(parents=True, exist_ok=True)
        for rel, data in plan:
            target = impl_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(f"implementations/{tool_id}/{rel}")
        if spec_bytes is not None:
            def_path.write_bytes(spec_bytes)
            written.insert(0, f"definitions/{tool_id}.md")

    # ── registry：合并进私有 registry.json ──
    entry: dict = dict(reg_entry) if isinstance(reg_entry, dict) else {}
    owner = _current_user()
    entry.update({
        "id": tool_id,
        "name": entry.get("name") or tool_meta.get("name") or tool_id,
        "version": entry.get("version") or tool_meta.get("version") or "0.1.0",
        "type": entry.get("type") or tool_meta.get("type") or "function",
        "language": entry.get("language") or tool_meta.get("language") or "python",
        "status": "active",
        "spec_path": f"definitions/{tool_id}.md",
        "impl_path": f"implementations/{tool_id}/",
        "tags": entry.get("tags", tool_meta.get("tags", [])),
        "modes": entry.get("modes", tool_meta.get("modes", [])),
        "param_meta": entry.get("param_meta", tool_meta.get("param_meta", [])),
        "input_schema": entry.get("input_schema", tool_meta.get("input_schema", {})),
        "output_schema": entry.get("output_schema", tool_meta.get("output_schema", {})),
        "owner": owner,
        # 与 agent_routes 注册路径同规则：仅超管可发布 public，普通用户导入即私有
        "visibility": _import_visibility(entry.get("visibility")),
        "imported_at": _now_iso(),
    })
    entry.setdefault("created_at", _now_iso())
    entry.setdefault("usage_count", 0)

    # 读-改-写走 ToolRegistry 的写锁 + 原子写：裸 write_text 会与
    # register/update 的并发读-改-写互相覆盖丢条目（tool_registry._WRITE_LOCK）
    from core.resource.registry.tool_registry import _WRITE_LOCK
    reg = _registry()
    with _WRITE_LOCK:
        data = [e for e in reg._read_private() if e.get("id") != tool_id]
        data.append(entry)
        reg._write(data)

    deps = manifest.get("dependencies") or []
    return {
        "tool_id": tool_id,
        "name": entry["name"],
        "version": entry["version"],
        "tags": entry.get("tags", []),
        "files": written,
        "file_count": len(written),
        "overwritten": existing,
        "overrode_builtin": overrode_builtin,
        "import_warnings": import_warnings,
        "backups": backups,
        "dependencies": deps,
        "def_path": str(def_path),
        "impl_dir": str(impl_dir),
        "note": (
            f"已导入到私有工具空间；第三方依赖需自行安装: {', '.join(deps)}"
            if deps else "已导入到私有工具空间，可直接使用"
        ),
    }


# ── 包列表 / 查找 ─────────────────────────────────────────────

def list_bundles(directory: Path | str | None = None) -> list[dict]:
    """列出导出目录下的迁移包（读 manifest 摘要，损坏的包跳过）。"""
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
                t = m.get("tool") or {}
                item.update({
                    "tool_id": t.get("id"),
                    "name": t.get("name"),
                    "version": t.get("version"),
                    "dependencies": m.get("dependencies") or [],
                })
        except Exception:
            item["error"] = "manifest 读取失败"
        out.append(item)
    return out


def resolve_bundle(name: str) -> Path:
    """把包名解析为导出目录下的文件路径（拒绝路径穿越）。"""
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
    "build_bundle_bytes",
    "export_tool",
    "import_tool",
    "list_bundles",
    "resolve_bundle",
]
