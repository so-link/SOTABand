"""工具归档包 — 导出/导入的 zip 格式约定（全仓唯一权威实现）

归档布局（``{tool_id}.zip``）::

    manifest.json                  # format/archive_version/tool_id/created_at
    registry.json                  # 精选注册表条目（丢弃 usage_count/created_at/owner/status）
    definitions/{id}.md            # 必含
    definitions/{id}-demand.md     # 可选
    definitions/{id}-reference.md  # 可选
    impl/tool.py                   # 必含
    impl/spec.md                   # 可选
    impl/requirements.txt          # 可选（uv freeze 精确 pin，环境真相来源）
    impl/tests/test_*.json         # 可选
    impl/<assets>                  # 可选（模型 XML 等；.venv/__pycache__ 永不进包）

安全约束：成员名拒绝 ``..``/绝对路径/盘符（zip-slip）；未压缩总量与成员数
有上限（zip 炸弹）；tool_id 必须匹配 ``TOOL_ID_RE``；manifest 与 registry
的 id 必须一致。非 UTF-8 标志的文件名按 cp437→utf-8 重解码（兼容 Windows
打包的中文资产名）。
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

ARCHIVE_FORMAT = "sotaband-tool"
ARCHIVE_VERSION = 1

# 与资源目录命名惯例一致：小写字母开头，仅小写字母/数字/-/_
TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_TOOL_ID_LEN = 100

# zip 炸弹防护
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 500

# registry.json 保留字段（运行期字段由 register() 重新生成）
_ENTRY_FIELDS = (
    "id", "name", "version", "type", "language",
    "spec_path", "impl_path",
    "input_schema", "output_schema", "param_meta", "modes", "tags",
    "visibility",
)

_SKIP_DIR_PARTS = {".venv", "__pycache__"}


class PackageError(ValueError):
    """归档非法（格式/校验失败），路由层映射为 400"""


def valid_tool_id(tool_id: str) -> bool:
    return bool(isinstance(tool_id, str)
                and len(tool_id) <= MAX_TOOL_ID_LEN
                and TOOL_ID_RE.match(tool_id))


def safe_member(name: str) -> str | None:
    """规范化 zip 成员名并拒绝穿越；非法返回 None"""
    n = name.replace("\\", "/")
    if not n or n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _member_bytes(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """读取成员字节；非 UTF-8 标志的文件名按 cp437→utf-8 重解码"""
    if not info.flag_bits & 0x800:
        try:
            info.filename = info.filename.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return zf.read(info)


def build_package(tool_id: str, entry: dict, def_dir: Path,
                  impl_dir: Path) -> bytes:
    """把工具打包为归档字节。def_dir=definitions 目录、impl_dir=实现目录。

    同目录下的 {id}-demand.md / {id}-reference.md 自动带入；impl 目录递归
    打包，跳过 .venv/__pycache__ 子树。
    """
    if not valid_tool_id(tool_id):
        raise PackageError(f"非法工具 ID: {tool_id!r}")
    if not def_dir.is_dir() or not impl_dir.is_dir():
        raise PackageError("工具定义或实现目录不存在")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format": ARCHIVE_FORMAT,
            "archive_version": ARCHIVE_VERSION,
            "tool_id": tool_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        zf.writestr("manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2))
        slim = {k: entry[k] for k in _ENTRY_FIELDS if k in entry}
        slim["id"] = tool_id
        zf.writestr("registry.json",
                    json.dumps(slim, ensure_ascii=False, indent=2))

        spec = def_dir / f"{tool_id}.md"
        if not spec.exists():
            raise PackageError(f"缺少定义文件 {spec.name}")
        zf.write(spec, f"definitions/{tool_id}.md")
        for suffix in ("-demand.md", "-reference.md"):
            extra = def_dir / f"{tool_id}{suffix}"
            if extra.exists():
                zf.write(extra, f"definitions/{tool_id}{suffix}")

        tool_py = impl_dir / "tool.py"
        if not tool_py.exists():
            raise PackageError(f"缺少实现文件 {tool_py}")
        for f in sorted(impl_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(impl_dir)
            if _SKIP_DIR_PARTS & set(rel.parts[:-1]):
                continue
            zf.write(f, f"impl/{rel.as_posix()}")
    return buf.getvalue()


def parse_package(data: bytes) -> dict:
    """校验并解析归档，返回:

    {
      "manifest": dict,
      "entry": dict,            # 精选 registry 条目（含 id）
      "tool_id": str,
      "def_files": {名字: str}, # definitions/ 下成员（key 为文件名）
      "impl_files": {相对路径: bytes},
      "test_data": {name: dict},# impl/tests/test_{name}.json
    }

    非法归档抛 PackageError。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("不是有效的工具包（zip 无法解析）")

    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise PackageError(f"归档成员过多（>{MAX_MEMBERS}）")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise PackageError("归档解压后体积超过上限（200MB）")

    members: dict[str, tuple[zipfile.ZipInfo, bytes]] = {}
    for info in infos:
        if info.is_dir():
            continue
        name = safe_member(info.filename)
        if name is None:
            raise PackageError(f"归档包含非法成员路径: {info.filename!r}")
        members[name] = (info, _member_bytes(zf, info))

    def _text(name: str) -> str:
        entry = members.get(name)
        return entry[1].decode("utf-8") if entry else ""

    try:
        manifest = json.loads(_text("manifest.json") or "")
    except json.JSONDecodeError:
        manifest = ""
    if not isinstance(manifest, dict) or manifest.get("format") != ARCHIVE_FORMAT:
        raise PackageError("缺少或非法 manifest.json（不是 SOTABand 工具包）")
    if manifest.get("archive_version") != ARCHIVE_VERSION:
        raise PackageError(f"不支持的归档版本: {manifest.get('archive_version')!r}")

    try:
        entry = json.loads(_text("registry.json") or "")
    except json.JSONDecodeError:
        entry = ""
    if not isinstance(entry, dict) or not entry.get("id"):
        raise PackageError("registry.json 缺失或缺少 id")

    tool_id = entry["id"]
    if not valid_tool_id(tool_id):
        raise PackageError(f"非法工具 ID: {tool_id!r}")
    if manifest.get("tool_id") != tool_id:
        raise PackageError("manifest 与 registry 的工具 ID 不一致")

    spec_name = f"definitions/{tool_id}.md"
    if spec_name not in members:
        raise PackageError(f"缺少定义文件 {spec_name}")
    impl_prefix = "impl/"
    impl_files: dict[str, bytes] = {}
    for name, (_, raw) in members.items():
        if not name.startswith(impl_prefix):
            continue
        rel = name[len(impl_prefix):]
        if not rel:
            continue
        impl_files[rel] = raw
    if "tool.py" not in impl_files or not impl_files["tool.py"].strip():
        raise PackageError("缺少实现文件 impl/tool.py")

    def_files: dict[str, str] = {}
    for name, (_, raw) in members.items():
        if name.startswith("definitions/"):
            def_files[name[len("definitions/"):]] = raw.decode("utf-8")

    test_data: dict[str, dict] = {}
    for rel, raw in impl_files.items():
        m = re.match(r"^tests/test_(.+)\.json$", rel)
        if m:
            try:
                test_data[m.group(1)] = json.loads(raw.decode("utf-8"))
            except Exception:
                continue

    slim = {k: entry[k] for k in _ENTRY_FIELDS if k in entry}
    slim["id"] = tool_id
    return {
        "manifest": manifest,
        "entry": slim,
        "tool_id": tool_id,
        "def_files": def_files,
        "impl_files": impl_files,
        "test_data": test_data,
    }
