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
  * 目标目录固定为私有资源根，永不写内置资源（内置随包分发、只读）。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

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


def export_dir() -> Path:
    """导出包默认目录（随项目分发之外，git 已忽略）。"""
    d = paths.PROJECT_ROOT / "data" / "exports"
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


def _find_entry(tool_id: str) -> tuple[dict | None, Path | None]:
    """按「私有覆盖内置」的优先级找注册条目，并返回它来自哪个根。"""
    reg = _registry()
    for root in reversed(paths.resource_roots()):
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

def export_tool(
    tool_id: str,
    out_dir: Path | str | None = None,
    include_tests: bool = True,
    include_assets: bool = True,
    *,
    exported_by: str | None = None,
    allow_other: bool = False,
) -> dict:
    """把工具打成迁移包，返回包信息。

    Args:
        tool_id: 工具 id
        out_dir: 输出目录，默认 data/exports
        include_tests: 是否带上 implementations/<id>/tests
        include_assets: 是否带上常见二进制资产（.xml/.onnx/.pt …）

    Returns:
        {"bundle_path", "file_name", "size", "sha256", "tool_id", "file_count", "skipped"}
    """
    tool_id = (tool_id or "").strip()
    if not _ID_RE.match(tool_id):
        raise ValueError(f"非法的工具 id: {tool_id!r}")

    entry, origin = _find_entry(tool_id)
    if entry is None:
        raise ValueError(f"工具不存在: {tool_id}")

    # 归属校验（与写权限同口径）：不能把别人的私有工具打包带走。
    # 内置共享资产（无 owner）允许导出；租户管理员通过 allow_other 兜底。
    owner = str(entry.get("owner") or "")
    if owner and not allow_other:
        exporter = str(exported_by or _current_user())
        if owner != exporter:
            raise ValueError("该工具属于其他用户，无权导出")

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
        # 导出者身份（UUID）：仅用于「迁移包可见性」与溯源，不承载权限。
        # 导入到目标环境时，registry 的 owner 一律取**导入者**（见 import_tool）。
        "exported_by": exported_by or _current_user(),
        "source": {
            "project": paths.PROJECT_ROOT.name,
            "origin": "private" if origin == paths.PRIVATE_ROOT else "builtin",
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

    out = Path(out_dir) if out_dir else export_dir()
    out.mkdir(parents=True, exist_ok=True)
    version = str(entry.get("version", "0.1.0")).replace("/", "_")
    file_name = f"tool-{tool_id}-{version}-{_stamp()}{BUNDLE_SUFFIX}"
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
        "tool_id": tool_id,
        "name": entry.get("name", tool_id),
        "bundle_path": str(bundle_path),
        "file_name": file_name,
        "size": len(raw),
        "sha256": _sha256(raw),
        "file_count": len(payload),
        "skipped": skipped,
        "dependencies": manifest["dependencies"],
    }


# ── 导入 ──────────────────────────────────────────────────────

def _validate_member_rel(rel: str, original: str) -> str:
    """校验包内成员相对路径（zip slip，含 Windows 盘符绝对路径）。

    ⚠️ 只挡 ``..`` / ``\\`` / 前导 ``/`` 是不够的：``E:/Learn/x.py`` 在
    ``PurePosixPath`` 里是普通相对路径，但 ``pathlib.Path`` 在 Windows 上会把
    ``E:`` 识别成驱动器并**重置根目录** —— 写 ``impl_dir / "E:/..."`` 会落到
    ``E:\\...``。因此必须用 ``PureWindowsPath`` 额外拒绝 drive，并在真正写盘前
    用 ``resolve().relative_to(base)`` 做第二次校验（双保险）。
    """
    if not rel:
        raise ValueError(f"包内存在非法路径: {original!r}")
    if "\x00" in rel or "\\" in rel or rel.startswith("/"):
        raise ValueError(f"包内存在非法路径: {original!r}")

    posix = PurePosixPath(rel)
    if posix.is_absolute() or posix.drive:
        raise ValueError(f"包内存在非法路径: {original!r}")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"包内存在非法路径: {original!r}")

    # Windows 盘符（``E:`` / ``C:``）与 UNC/设备路径前缀
    win = PureWindowsPath(rel)
    if win.drive or win.root:
        raise ValueError(f"包内存在非法路径: {original!r}")
    return rel


def _safe_member_path(name: str, tool_id: str) -> str | None:
    """校验 zip 成员名，返回允许的相对路径（在 implementations/<id>/ 下）或 None。"""
    if not name or name.endswith("/"):
        return None
    if "\\" in name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"包内存在非法路径: {name!r}")
    if name in {"manifest.json", "registry_entry.json"}:
        return None
    if PureWindowsPath(name).drive:
        raise ValueError(f"包内存在非法路径: {name!r}")
    prefix = f"implementations/{tool_id}/"
    if name.startswith(prefix):
        return _validate_member_rel(name[len(prefix):], name)
    if name == f"definitions/{tool_id}.md":
        return None  # 单独处理
    raise ValueError(f"包内存在非预期路径: {name!r}")


def _safe_write_target(base: Path, rel: str, original: str) -> Path:
    """把相对路径拼到 base 下，并二次校验解析后仍在 base 内。"""
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"包内存在非法路径: {original!r}") from None
    if target == base.resolve():
        raise ValueError(f"包内存在非法路径: {original!r}")
    return target


def import_tool(
    bundle_path: Path | str | None = None,
    bundle_bytes: bytes | None = None,
    overwrite: bool = False,
    *,
    owner_id: str | None = None,
    allow_overwrite: bool = False,
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
        # 内置同 id 不能覆盖：ToolExecutor 定位实现时「内置优先」，
        # 写入私有根会被内置版本遮蔽 —— 看起来导入成功，跑的却是内置代码。
        if existing_entry is not None and existing_origin == paths.BUILTIN_ROOT:
            raise ValueError(
                f"内置工具空间已有同 id 工具 {tool_id}，导入到私有根不会生效"
                f"（执行时内置实现优先）。请给迁移包换一个 id，"
                f"或先从内置 resources/tools 移除该工具。"
            )

        existing = def_path.exists() or impl_dir.exists() or existing_entry is not None
        if existing and not overwrite:
            raise ValueError(
                f"工具 {tool_id} 已存在；如需覆盖请传 overwrite=true"
                f"（旧文件会备份为 .bak.<时间戳>）"
            )
        if existing and overwrite and not allow_overwrite:
            # 覆盖 = 改别人的代码（可植入后门）。只允许覆盖自己的工具；
            # 租户 Owner/Admin 的兜底由路由层传 allow_overwrite=True。
            existing_owner = str((existing_entry or {}).get("owner") or "")
            importer = str(owner_id or _current_user())
            if existing_owner and existing_owner != importer:
                raise ValueError("该工具已存在且属于其他用户，无权覆盖")

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
        # 写盘前再解析一次目标路径，确保不会因盘符 / 符号链接跳出私有资源根
        impl_dir_resolved = impl_dir.resolve()
        for rel, data in plan:
            target = _safe_write_target(
                impl_dir_resolved, rel, f"implementations/{tool_id}/{rel}"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(f"implementations/{tool_id}/{rel}")
        if spec_bytes is not None:
            def_path.write_bytes(spec_bytes)
            written.insert(0, f"definitions/{tool_id}.md")

    # ── registry：合并进私有 registry.json ──
    entry: dict = dict(reg_entry) if isinstance(reg_entry, dict) else {}
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
        "owner": owner_id or _current_user(),
        "visibility": entry.get("visibility") or "public",
        "imported_at": _now_iso(),
    })
    entry.setdefault("created_at", _now_iso())
    entry.setdefault("usage_count", 0)

    registry_file = private_tools / "registry.json"
    data: list[dict] = []
    if registry_file.is_file():
        try:
            loaded = json.loads(registry_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = [e for e in loaded if isinstance(e, dict)]
        except Exception:
            data = []
    data = [e for e in data if e.get("id") != tool_id]
    data.append(entry)
    registry_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    deps = manifest.get("dependencies") or []
    return {
        "tool_id": tool_id,
        "name": entry["name"],
        "version": entry["version"],
        "tags": entry.get("tags", []),
        "files": written,
        "file_count": len(written),
        "overwritten": existing,
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

def list_bundles(
    directory: Path | str | None = None,
    *,
    user_id: str | None = None,
    include_all: bool = False,
) -> list[dict]:
    """列出导出目录下的迁移包（读 manifest 摘要，损坏的包跳过）。

    **可见性**（管理员评审意见 2 同类问题：包内含工具源码）：
    - 本人导出的包；
    - 租户 Owner/Admin（``include_all=True``）可见全部；
    - 旧包没有 ``exported_by`` 字段 → fail-closed，仅 ``include_all`` 可见。
    """
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
                    "exported_by": m.get("exported_by"),
                })
        except Exception:
            item["error"] = "manifest 读取失败"

        if not include_all:
            owner = item.get("exported_by")
            if not user_id or owner != user_id:
                continue
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
    "export_tool",
    "import_tool",
    "list_bundles",
    "resolve_bundle",
]
