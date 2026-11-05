"""Tool 代码生成器 v2 — LLM 生成完整文件 + 沙箱执行 + 自动调试"""

import ast as _ast
import json
import re
import subprocess
import tempfile
import os as _os
import threading
from pathlib import Path
from datetime import datetime

from core.resource.builder.builder_base import BaseBuilder
from config.settings import get_debug_exec_timeout, get_debug_max_rounds


def _debug_exec_timeout() -> float:
    """自动调试单轮沙箱执行超时（秒），可通过 .env 的 DEBUG_EXEC_TIMEOUT 调整。

    取值统一走 config.settings，与 Agent 调试、modify-and-debug 共用同一份
    配置与默认值，避免「系统设置」里改大了超时却被本处小值掐断。
    """
    return get_debug_exec_timeout()


def _debug_max_rounds() -> int:
    """自动调试最大轮数，可通过 .env 的 DEBUG_MAX_ROUNDS 调整。"""
    return get_debug_max_rounds()

# ── 全局调试状态：线程安全的停止标志 ──
# key=tool_id, value=True 表示正在运行
_debug_states: dict[str, bool] = {}
_debug_lock = threading.Lock()

def set_debug_running(tool_id: str):
    """设置指定工具的调试为运行状态"""
    with _debug_lock:
        _debug_states[tool_id] = True

def set_debug_stopped(tool_id: str):
    """设置指定工具的调试为停止状态"""
    with _debug_lock:
        _debug_states.pop(tool_id, None)

def is_debug_running(tool_id: str) -> bool:
    """检查指定工具的调试是否在运行"""
    with _debug_lock:
        return _debug_states.get(tool_id, False)

def stop_debug(tool_id: str):
    """请求停止指定工具的调试"""
    with _debug_lock:
        if tool_id in _debug_states:
            _debug_states[tool_id] = False
from core.llm.client import create_gen_llm_client


# ================================================================
#   工具代码模板 — LLM 以此为基础生成完整文件
# ================================================================

TOOL_TEMPLATE = r'''# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None

_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

# ── 资源根（私有 + 内置，私有优先）──
def _resource_roots():
    """当前用户私有根 + 内置根，私有在前（覆盖层语义：用户副本覆盖内置）。

    优先向 core.resource.paths 询问（平台权威实现）；不可用时按 env 还原。
    """
    try:
        from core.resource import paths as _p
        return list(_p.resource_roots())
    except Exception:
        return [_USER_HOME / "resources-private", _PROJECT_ROOT / "resources"]

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具（通过 registry.json 查找工具 ID 对应的实现目录）"""
    import subprocess as _sp
    roots = list(_resource_roots())  # 私有根在前，优先级更高
    # 从「私有 + 内置」资源根的 registry.json 中查找工具 ID（目录名）
    tool_id = tool_name  # 默认用名称作为 ID
    for _root in roots:
        reg_path = _root / "tools" / "registry.json"
        if not reg_path.exists():
            continue
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 先精确匹配 id，再匹配 name
        for t in tools:
            if t.get("id") == tool_name or t.get("name") == tool_name:
                tool_id = t["id"]
        if tool_id != tool_name:
            break
    # 在「私有 + 内置」资源根中定位工具实现
    tool_dir = None
    for _root in roots:
        _cand = _root / "tools" / "implementations" / tool_id
        if (_cand / "tool.py").exists():
            tool_dir = _cand
            break
    if tool_dir is None:
        return {"status": "failed", "message": f"Tool '{tool_name}' (id={tool_id}) not found"}
    tool_file = tool_dir / "tool.py"
    py_exe = sys.executable
    for _rel in (("bin", "python"), ("Scripts", "python.exe")):
        _cand = tool_dir.joinpath(".venv", *_rel)
        if _cand.exists():
            py_exe = str(_cand)
            break
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}, encoding='utf-8').read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    # encoding/errors 必须显式：工具输出是含中文的 UTF-8 JSON，
    # Windows 下 text=True 默认按 GBK 解码会直接 UnicodeDecodeError
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 资源空间访问辅助（模型空间 / 数据空间） ──
def _run_async(coro):
    """在同步上下文中执行异步注册中心调用。

    execute() 是同步函数（不能 async/await），而 ModelRegistry / DataRegistry /
    ToolRegistry 的 get / register / list_all 都是 async 方法。直接调用只会拿到
    coroutine 对象，运行时报 'coroutine' object has no attribute 'get'。
    所有注册中心调用必须经过本函数。
    """
    import asyncio, threading
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}
    def _worker():
        try:
            box["value"] = asyncio.run(coro)
        except Exception as exc:
            box["error"] = exc
    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")

def _model_entry(model_id: str) -> dict:
    """从模型空间读取模型登记项（同步封装，永不返回 coroutine）。"""
    from core.resource.registry.model_registry import ModelRegistry
    return _run_async(ModelRegistry().get(model_id)) or {}

def _model_data(model_id: str) -> dict:
    """读取模型空间中的模型产物内容（model.json 的 dict）。

    model_path 是相对「模型资源根」的路径（形如 models/<id>/model.json），
    必须按资源根解析；按项目根解析会命中项目根下残留的同名文件，出现读旧模型的问题。
    """
    entry = _model_entry(model_id)
    rel = (entry or {}).get("model_path") or f"models/{model_id}/model.json"
    candidates = []
    p = Path(rel)
    if p.is_absolute():
        candidates.append(p)
    else:
        roots = []
        try:
            from core.resource import paths as _paths
            roots = list(_paths.resource_roots())
        except Exception:
            roots = []
        # resource_roots() 已私有在前（新建模型只写私有根）
        candidates.extend(Path(str(r)) / "models" / rel for r in roots)
    candidates.append(Path(_resolve_path(f"models/{model_id}/model.json")))
    for cand in candidates:
        for c in (cand, cand / "model.json"):
            try:
                if c.is_file():
                    with open(c, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                continue
    return {}

def _dataset_dir(dataset_id: str) -> str:
    """从数据空间取数据集的本地目录（同步封装，永不返回 coroutine）。"""
    from core.resource.registry.data_registry import DataRegistry
    entry = _run_async(DataRegistry().get(dataset_id)) or {}
    return entry.get("data_path", "") or ""

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）。

    跨平台边界统一在此处理：展开 ~ 用户目录、Windows 反斜杠分隔符
    一律归一为 /（posix 与 nt 的 pathlib 均可解析）、消除 . 与 .. 跳转、
    剥离首尾空白与包裹引号。相对路径一律相对 _PROJECT_ROOT 解析。
    """
    s = str(path).strip().strip('"').strip("'").replace("\\", "/")
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return str(Path(os.path.normpath(str(p))))

# === 头部结束，以下由 LLM 生成 ===
'''


class ToolCodeBuilder(BaseBuilder):
    """v2: LLM 生成完整文件 + 仅沙箱执行 + 自动调试"""

    def __init__(self, llm_client=None):
        # 代码生成走「生成模型」（LLM_GEN_*，未配置时回退全局）；
        # 客户端每次调用时才解析配置，设置页改动即时生效
        self.llm = llm_client or create_gen_llm_client()

    async def dry_run(self, code: str) -> dict:
        """兼容 BaseBuilder 抽象方法"""
        return await self.sandbox_execute(code, {})

    # ── 参数解析 ──

    @staticmethod
    def _parse_spec_inputs(spec_md: str) -> list[dict]:
        """从 MD 的输入规范表格解析参数（支持多模式：解析各模式下的输入规范，参数带 mode 字段）"""
        inputs = []
        # 先解析模式列表，用于给参数打 mode 标签
        modes = ToolCodeBuilder.parse_modes(spec_md)
        current_mode = None  # 当前所在模式（单模式为 None）

        in_section = False
        for line in spec_md.split("\n"):
            stripped = line.strip()
            # 检测模式标题，切换 current_mode
            m = re.match(r"^#{2,3}\s*模式\s*\d*\s*[:：]\s*(.+)$", stripped)
            if m:
                title = m.group(1).strip()
                pm = re.match(r"^([A-Za-z_][\w-]*)\s*[（(]", title)
                pm2 = re.match(r"^(.+?)\s*[（(]\s*([A-Za-z_][\w-]*)\s*[)）]$", title)
                if pm:
                    current_mode = pm.group(1)
                elif pm2:
                    current_mode = pm2.group(2)
                else:
                    # 无法从标题提取 id，按顺序匹配 modes 列表
                    pass
                continue

            if "输入规范" in stripped:
                in_section = True
                continue
            # 遇到新的二级/三级标题且不是输入规范时，结束当前输入表格解析
            if in_section and stripped.startswith("#") and "输入" not in stripped:
                in_section = False
                # 若是模式外的章节标题，退出当前模式上下文
                if stripped.startswith("## ") and "模式" not in stripped:
                    current_mode = None
                continue
            if in_section and stripped.startswith("|") and "参数名" not in stripped and "---" not in stripped:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 2 and parts[0]:
                    required_raw = parts[2] if len(parts) > 2 else ""
                    default_raw = parts[3] if len(parts) > 3 else ""
                    # 表格里默认值常写成 "overwrite" / 'x' 形式，包裹引号是
                    # 书写习惯不是值本身；不剥离会把引号带进 registry，
                    # 执行时值比对（如 mode in ("overwrite","append")）失配。
                    default_val = default_raw.strip()
                    if len(default_val) >= 2 and default_val[0] == default_val[-1] and default_val[0] in "\"'“”‘’`":
                        default_val = default_val[1:-1].strip()
                    item = {
                        "name": parts[0], "type": parts[1] if len(parts) > 1 else "string",
                        "required": "是" in required_raw,
                        "default": default_val if default_val not in ("-", "—", "", "无", "无默认值") else None,
                        "desc": parts[4] if len(parts) > 4 else "",
                    }
                    # 条件必填：如「条件（模式2）」「仅模式2」→ 视为在该模式下必填
                    cond_mode = None
                    cm = re.search(r"条件\s*[（(]?\s*(?:模式\s*)?([0-9A-Za-z_\-]+)\s*[)）]?", required_raw)
                    if cm and "条件" in required_raw:
                        cond_mode = cm.group(1)
                    elif "仅" in required_raw or "only" in required_raw.lower():
                        cm2 = re.search(r"([0-9A-Za-z_\-]+)", required_raw)
                        cond_mode = cm2.group(1) if cm2 else None
                    if cond_mode:
                        item["required"] = True
                        item["mode"] = cond_mode
                    elif current_mode:
                        item["mode"] = current_mode
                    inputs.append(item)
        return inputs

    @staticmethod
    def parse_modes(spec_md: str) -> list[dict]:
        """从 MD 规范文档中解析多模式定义。

        仅当 MD 含「模式定义」章节时返回非空列表；否则返回空列表（单模式工具）。
        每个模式: {id, name, desc}
        """
        modes = []
        # 检测是否存在「模式定义」章节
        if "模式定义" not in spec_md and "模式：" not in spec_md:
            return modes

        # 逐行解析「### 模式 N：xxx（模式标识）」或「### 模式 N：xxx」
        # 模式标识可能出现在标题末尾括号、或紧跟的「- 模式标识：」行
        current = None
        for line in spec_md.split("\n"):
            stripped = line.strip()
            # 匹配 "### 模式 N：名称" 或 "## 模式 N：名称"
            m = re.match(r"^#{2,3}\s*模式\s*\d*\s*[:：]\s*(.+)$", stripped)
            if m:
                title = m.group(1).strip()
                # 尝试从括号中提取模式标识，如 "train（训练模式）" 或 "训练模式（train）"
                ident = None
                name = title
                pm = re.match(r"^([A-Za-z_][\w-]*)\s*[（(]\s*(.+?)\s*[)）]$", title)
                if pm:
                    ident, name = pm.group(1), pm.group(2)
                else:
                    pm2 = re.match(r"^(.+?)\s*[（(]\s*([A-Za-z_][\w-]*)\s*[)）]$", title)
                    if pm2:
                        name, ident = pm2.group(1), pm2.group(2)
                current = {"id": ident or "", "name": name, "desc": ""}
                modes.append(current)
                continue
            # 匹配 "- 模式标识：train" 补充 id
            if current and not current["id"]:
                im = re.match(r"^[-*]\s*模式标识\s*[:：]\s*([A-Za-z_][\w-]*)$", stripped)
                if im:
                    current["id"] = im.group(1)
                    continue
            # 匹配 "- 描述：xxx" 补充 desc
            if current:
                dm = re.match(r"^[-*]\s*描述\s*[:：]\s*(.+)$", stripped)
                if dm:
                    current["desc"] = dm.group(1).strip()
                    continue
            # 遇到新的非模式章节标题则结束
            if current and re.match(r"^#{1,2}\s+", stripped) and "模式" not in stripped:
                current = None

        # 清理：丢弃无 id 的模式（视为解析失败），并生成兜底 id
        result = []
        for i, m in enumerate(modes, 1):
            mid = m["id"] or f"mode{i}"
            result.append({"id": mid, "name": m["name"] or mid, "desc": m["desc"]})
        return result

    async def extract_param_metadata(self, spec_md: str) -> list[dict]:
        """LLM 提取参数元数据（支持多模式：每项含可选 mode 字段）"""
        modes = self.parse_modes(spec_md)
        mode_hint = ""
        if modes:
            mode_list = "、".join(f"{m['id']}({m['name']})" for m in modes)
            mode_hint = (
                f"\n该工具包含以下模式: {mode_list}。"
                f"每个参数对象必须额外包含 \"mode\" 字段，值为该参数所属的模式 id"
                f"（如 \"train\" 或 \"test\"）。"
                f"重要：某模式真正需要的参数，其 required 必须为 true；"
                f"不要因为参数在其它模式不需要就将其设为 false。"
            )

        prompt = f"""从以下工具 MD 规范文档中提取输入参数列表，返回 JSON 数组。
每个参数: name, type, required(true/false), default(null或值), desc, hints(示例输入数组){mode_hint}

注意：若文档中某参数的「必填」列写的是「条件（模式X）」「仅模式X」等模糊表述，
应理解为「该参数在模式X下必填」，将 required 设为 true，并给该参数附加 mode 字段值为 X。

MD 文档:
{spec_md[:20000]}

仅返回 JSON 数组:"""
        response = await self.llm.chat(messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=100000)
        try:
            clean = response.strip()
            if clean.startswith("```"): clean = clean.split("\n",1)[1].rsplit("\n",1)[0]
            params = json.loads(clean)
        except:
            return []
        # 确定性后处理：修正「条件必填」语义（不依赖 LLM 是否遵守）
        return self._normalize_conditional_params(params, spec_md)

    @staticmethod
    def _normalize_conditional_params(params: list[dict], spec_md: str) -> list[dict]:
        """将「条件必填」参数修正为 required=true 并打上 mode 标签。

        兜底处理 LLM 提取时未正确标记的情况：
        - desc 含「条件必填」「仅模式X」「mode=X 时必填」等 → required=true
        - 尝试从 desc / MD 中提取关联的模式标识
        """
        # 从 MD 提取 mode 参数的可选值（如 square/file_save），用于匹配条件
        mode_values = []
        for line in spec_md.split("\n"):
            m = re.search(r"mode.*?(?:可选值|取值|支持)[：:]\s*([^\n]+)", line)
            if m:
                mode_values = re.findall(r"[A-Za-z_][\w-]*", m.group(1))
                break

        normalized = []
        for p in params:
            desc = (p.get("desc", "") or "")
            name = (p.get("name", "") or "")
            is_conditional = (
                ("条件必填" in desc)
                or ("条件" in desc and "必填" in desc)
                or ("仅" in desc and "必填" in desc)
                or re.search(r"mode\s*=\s*([A-Za-z_][\w-]*)\s*时", desc)
            )
            if is_conditional and not p.get("required"):
                p["required"] = True
                # 尝试提取关联模式
                if not p.get("mode"):
                    cm = re.search(r"mode\s*=\s*([A-Za-z_][\w-]*)", desc)
                    if cm:
                        p["mode"] = cm.group(1)
                    elif "模式1" in desc:
                        p["mode"] = mode_values[0] if mode_values else "1"
                    elif "模式2" in desc:
                        p["mode"] = mode_values[1] if len(mode_values) > 1 else "2"
            normalized.append(p)
        return normalized

    async def validate_spec(self, spec: dict) -> bool:
        md = spec.get("raw_md", "")
        return all(s in md for s in ["功能概述","输入规范","输出规范","依赖环境","运行机制"])

    # ── 代码生成 — LLM 生成完整文件 ──

    async def build(self, spec: dict) -> str:
        """LLM 根据模板 + MD 规范生成完整 Python 文件，不做任何后处理"""
        raw_md = spec.get("raw_md", "")
        return await self._llm_generate(raw_md)

    async def build_stream(self, spec: dict):
        """流式版本：yield (kind, token)，kind ∈ {reasoning, content}"""
        raw_md = spec.get("raw_md", "")
        async for kind, token in self._llm_generate_stream(raw_md):
            yield kind, token

    def _build_code_prompt(self, spec_md: str) -> str:
        """构建代码生成的 prompt（不含 LLM 调用，供流式/非流式复用）"""
        # 解析标记
        api_calls = re.findall(r'(?<!【)【(?!【)(.+?)(?<!】)】(?!】)', spec_md)
        tool_calls = re.findall(r'【【(.+?)】】', spec_md)

        api_info = ""
        tool_info = ""

        api_lines = []

        if api_calls:
            from core.api.registry import ApiRegistry
            reg = ApiRegistry()
            all_apis = reg._read()

            # API MD 定义文件所在目录
            api_def_dir = Path(__file__).resolve().parent.parent.parent / "core" / "api" / "definitions"

            for name in api_calls:
                for api in all_apis:
                    if api.get("name") == name:
                        api_id = api['id']
                        input_schema = api.get("input_schema", {})
                        output_schema = api.get("output_schema", {})

                        # 1. 读取 API 的 MD 定义文件，提取输入/输出参数表
                        spec_path = api.get("spec_path", f"definitions/{api_id}.md")
                        md_file = api_def_dir / (spec_path.split("/")[-1] if "/" in spec_path else spec_path)
                        param_descriptions = {}
                        output_descriptions = {}
                        if md_file.exists():
                            md_content = md_file.read_text(encoding='utf-8')
                            # 解析输入参数
                            in_input = False
                            in_output = False
                            for line in md_content.split("\n"):
                                if "输入规范" in line:
                                    in_input = True; in_output = False
                                    continue
                                if "输出规范" in line:
                                    in_output = True; in_input = False
                                    continue
                                if (in_input or in_output) and line.startswith("##"):
                                    in_input = False; in_output = False
                                    continue
                                if line.startswith("|") and "参数名" not in line and "字段" not in line and "---" not in line:
                                    parts = [p.strip() for p in line.split("|")[1:-1]]
                                    if len(parts) >= 2 and parts[0]:
                                        name = parts[0]
                                        typ = parts[1] if len(parts) > 1 else "string"
                                        if in_input:
                                            # 输入规范表格：| 参数名 | 类型 | 必填 | 默认值 | 说明 |
                                            required = parts[2] if len(parts) > 2 else ""
                                            desc = parts[4] if len(parts) > 4 else (parts[3] if len(parts) > 3 else "")
                                            param_descriptions[name] = {"type": typ, "required": "是" in required, "description": desc}
                                        elif in_output:
                                            # 输出规范表格：| 字段 | 类型 | 说明 |
                                            desc = parts[2] if len(parts) > 2 else ""
                                            output_descriptions[name] = {"type": typ, "description": desc}

                        # 2. 构造详细的输入参数信息
                        api_lines.append(f"  API: {name} (ID: {api_id})")

                        if input_schema:
                            params_detail = []
                            for k, v in input_schema.items():
                                md_info = param_descriptions.get(k, {})
                                desc = md_info.get("description", v)
                                req = "必填" if md_info.get("required", True) else "可选"
                                params_detail.append(f"      {k} ({v}, {req}): {desc}")
                            params_example = ", ".join(f'{k}=<{k}>' for k in input_schema.keys())
                            api_lines.append(f"    调用: _call_api(\"{api_id}\", {params_example})")
                            api_lines.append(f"    输入参数:")
                            api_lines.extend(params_detail)
                        else:
                            api_lines.append(f"    调用: _call_api(\"{api_id}\")")
                            api_lines.append(f"    输入参数: 无")

                        # 3. 构造输出格式说明
                        if output_schema:
                            output_detail = []
                            for k, v in output_schema.items():
                                md_info = output_descriptions.get(k, {})
                                desc = md_info.get("description", v)
                                output_detail.append(f"      {k} ({v}): {desc}")
                            api_lines.append(f"    返回值 (dict):")
                            api_lines.extend(output_detail)
                        else:
                            api_lines.append(f"    返回值: 无特定格式")

                        api_lines.append(f"    使用方式: result = _call_api(\"{api_id}\", ...); # 然后从 result 中按字段名取值")

            if api_lines:
                api_info = "\n=== SYSTEM API CALLS ===\n\n" + "\n".join(api_lines) + "\n\n=== END API CALLS ===\n"

        if tool_calls:
            tool_lines = [f"  {name} → _call_tool(\"{name}\", ...)" for name in tool_calls]
            tool_info = "\n=== TOOL CALLS ===\n" + "\n".join(tool_lines) + "\n=== END TOOL CALLS ===\n"

        prompt = f"""You are a Python code generator for SOTABand tools. Generate a COMPLETE, RUNNABLE Python file.

=== TEMPLATE (use as starting point) ===
{TOOL_TEMPLATE}
=== END TEMPLATE ===

=== TOOL SPECIFICATION ===
{spec_md}
=== END SPECIFICATION ===

{api_info}
{tool_info}

CRITICAL RULES:
1. Output the COMPLETE file including template header — all imports and helpers
2. Function: def execute(**kwargs) -> dict[str, Any]
3. Access params: kwargs.get("param_name", default) — NEVER kwargs["param_name"]
4. Return: {{"status":"success"|"failed","output_format":"text"|"image"|"table"|"file","message":"...","data":{{}}}}
5. All errors: try/except. In the top-level except of execute(), the message MUST include the full traceback so that auto-debugging can locate the failure:
       import traceback
       except Exception as e:
           return {{"status":"failed","output_format":"text","message":f"工具执行失败: {{str(e)}}\\n\\nTraceback:\\n{{traceback.format_exc()}}","data":{{}}}}
   NEVER swallow the traceback — a bare str(e) without stack info makes auto-debug unable to find the bug.
6. File paths — PORTABILITY IS MANDATORY. The generated tool must run unchanged no matter where its directory lives (private space, system resources/, another user's home, another machine):
   - NEVER write absolute paths ("/root/...", "/home/...", "C:\\...", a dataset dir copied from the spec, etc.) anywhere in the code — not in constants, not in default parameter values, not in comments used as config.
   - NEVER locate anything with Path(__file__).parents[N] / .parent.parent chains or Path.cwd(). The template already resolves everything for you:
       _PROJECT_ROOT  system root (read-only: built-in resources, core/)
       _USER_HOME     current user's home — ALL outputs go here
       _DATA_DIR      _USER_HOME/data — the base for files this tool produces
       _out_dir(...)  create & return _DATA_DIR/<subdir> (preferred for outputs)
       _tool_dir      this tool's own directory (static assets shipped with it)
   - Writing: use _out_dir("xxx") / _DATA_DIR / ... . NEVER write under _PROJECT_ROOT (it is the git-distributed system area) and never mutate resources/ or resources-private/.
   - Reading a file the user provided: pass the path through _resolve_path(path) (handles relative/absolute/~ cross-platform). Reading a registered dataset/model: use _dataset_dir(id) / _model_data(id) — NEVER reconstruct those paths yourself.
   - Default parameter values must be portable too: default to "" or a relative name and resolve at runtime, not a machine-specific absolute path.
6b. File artifacts & IDs: put every produced file path in a ``*_path`` field of ``data`` (e.g. image_path, file_path, output_path). The platform auto-registers any ``*_path`` pointing inside the user home and injects a ``file_id`` for the UI — NEVER call file APIs or construct download URLs yourself, just return the path fields.
7. API calls: use the EXACT api_id and param names from the SYSTEM API CALLS section above
8. Map tool input parameters (from kwargs) to API parameters with the CORRECT names
9. Tool calls: _call_tool("工具名称", param=value) — 系统自动通过 registry.json 查找工具ID和实现目录
10. NEVER async/await
11. NEVER use pip install, subprocess.run for package installation, or any runtime dependency installation — dependencies are managed by the system automatically
12. LLM calls: ALWAYS use _llm_chat(messages, max_tokens=..., temperature=...) from the template. NEVER hardcode a provider (e.g. DeepSeek / OpenAI / Moonshot / GLM / Qwen / MiMo), NEVER use _call_api("api-deepseek-get-key"), NEVER call any provider endpoint directly (e.g. api.deepseek.com), NEVER hardcode model names like deepseek-chat / gpt-4o — the model and endpoint follow the global LLM_PROVIDER / LLM_API_KEY / LLM_MODEL config automatically
    ALWAYS pass a large max_tokens (100000) to handle potentially large files — NEVER leave a small default like 4096 or 1024, which silently truncates long outputs.
13. NEVER trust external/parsed data types. Data from LLM output, JSON parsing, API responses, or files is NOT guaranteed to be the expected type — an LLM may return 90 as the STRING "90", or a non-numeric word, and then min()/max()/sum()/comparisons crash with "'<' not supported between instances of 'str' and 'int'".
    ALWAYS coerce and validate before aggregating. Filtering by an error flag alone is NOT enough:
       # WRONG — filters out failed items but still mixes types
       success = [r for r in results if not r.get("error")]
       min(r["score"] for r in success)
       # RIGHT — validate the numeric field itself
       scores = [float(r["score"]) for r in results
                 if isinstance(r.get("score"), (int, float))]
       scores = [s for s in scores if s == s and abs(s) != float('inf')]  # drop NaN/inf
       stats = ({{"min": min(scores), "max": max(scores), "avg": sum(scores)/len(scores)}}
                if scores else {{"min": 0, "max": 0, "avg": 0}})
    Also: constructor/accessor calls on parsed data (dict/list index, float(), int(), len()) MUST be guarded by try/except or a type check.
14. When parsing LLM JSON output, be defensive end-to-end: strip markdown fences (```), then json.loads() inside try/except. On failure, record the item as failed WITH the raw response snippet kept for debugging, and CONTINUE processing the remaining items — never let one bad item abort the whole batch.
    For every numeric field the LLM returns, coerce with float()/int() in a try/except; if coercion fails, treat that item as failed rather than storing the raw value, because raw values silently poison later aggregation (see rule 13).
15. Batch processing over many items MUST be per-item fault-tolerant: wrap each item's work in try/except, collect per-item errors, and only return status:"failed" if ALL items failed. If any item succeeds, return status:"success".
16. NEVER hardcode any API key, token, password, or secret in the generated code — not as a literal, not as a default value, not in a comment or docstring. Credentials MUST come from input params (kwargs.get("api_key")) or from @LLM自定义配置对话API. If a credential is needed, accept it as an input parameter named api_key and pass it straight through to the API call; never store it, never log it, never print it.
17. NEVER print, log, or echo credential values (api_key/token/secret) to stdout/stderr or include them in the returned "message"/"data". Tool stdout is recorded into debug logs and may be sent to an LLM for auto-debugging; leaking a key there is irreversible.
18. 资源空间访问（模型空间 / 数据空间 / 工具空间）必须使用模板提供的同步封装，禁止直接调用注册中心：
    - 模型空间：`_model_data(model_id)` 返回 model.json 的 dict，`_model_entry(model_id)` 返回登记项
    - 数据空间：`_dataset_dir(dataset_id)` 返回数据集本地目录
    - 必要时直接调用注册中心，必须先包一层 `_run_async(...)`
    原因：ModelRegistry / DataRegistry / ToolRegistry 的 get / register / list_all 都是 **async** 方法，
    而 execute() 是同步函数（见规则 10）。直接写成 `ModelRegistry().get(x)` 拿到的是 coroutine 对象，
    运行时会报 `'coroutine' object has no attribute 'get'`。
    ❌ `entry = ModelRegistry().get(model_id); entry.get("model_path")`
    ✅ `data = _model_data(model_id); coef = data.get("coefficients", {{}})`
    ❌ `path = DataRegistry().get(dataset_id).get("data_path")`
    ✅ `path = _dataset_dir(dataset_id)`
19. 模型产物（模型空间 model.json）字段契约，读写两端都必须遵守：
    - 读：系数按 `coefficients` → `final_coefficients` → `evaluated_coefficients` 顺序取第一组非空且含数值系数的字典；
      三者都缺失时返回 status:"failed"（说明模型未完成训练），**禁止**用硬编码默认系数继续给出预测。
    - 写：训练型工具必须在 model.json 顶层写入 `coefficients`（预测实际使用的系数），可另存 evaluated_coefficients / final_coefficients 供审计。
    原因：训练工具与预测工具由不同会话生成，字段漂移会让预测工具读不到系数、静默输出常量结果。
20. Output ONLY Python code, no markdown, no explanation"""

        # 多模式规则：仅当 MD 声明了多个模式时注入
        modes = self.parse_modes(spec_md)
        if modes:
            mode_entries = "\n".join(
                f"   - mode id \"{m['id']}\" -> _mode_{m['id']}(kwargs)（{m['name']}）"
                for m in modes
            )
            prompt += f"""

MULTI-MODE RULES (this tool declares multiple modes, MUST follow):
1. execute(**kwargs) is the single entry that dispatches by mode:
   def execute(**kwargs):
       mode = kwargs.get("mode", "{modes[0]['id']}")
       if mode == "{modes[0]['id']}":
           return _mode_{modes[0]['id']}(kwargs)
       elif mode == ...:
           return _mode_xxx(kwargs)
       else:
           return {{"status":"failed","output_format":"text","message":f"未知模式: {{mode}}","data":{{}}}}
   Mode list:
{mode_entries}
2. Each mode is a separate function named _mode_<id>(kwargs), using kwargs.get("param_name", default).
3. Every mode function returns {{"status","output_format","message","data"}}.
4. Modes may share module-level helpers/constants (e.g. model file path under _DATA_DIR).
5. The train mode MUST save the model file to a stable path under _DATA_DIR; other modes load from that same path.
"""
        return prompt

    @staticmethod
    def _clean_code(response: str) -> str:
        """清理 LLM 返回的代码（去除 markdown 代码块标记 + 修复头部转义）"""
        code = response.strip()
        if code.startswith("```python"):
            code = code[len("```python"):]
        elif code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]
        code = code.strip()
        if code.startswith("```") and code.count("```") == 2:
            parts = code.split("```")
            code = parts[1].strip() if len(parts) >= 2 else code
        # 头部 _resolve_path 的反斜杠转义：提示词要求 LLM 完整复现模板头部，
        # 但模型几乎每次都会把 replace("\\", "/") 退化成 replace("\", "/")，
        # 该行是不合法的 Python 字面量，会让每个新生成工具都语法报错。
        # 这里做确定性修复，保证所有生成路径产出的文件都能编译。
        code = code.replace('replace("\\", "/")', 'replace("\\\\", "/")')
        return code

    async def _llm_generate(self, spec_md: str) -> str:
        """LLM 生成完整工具代码（非流式，向后兼容）"""
        prompt = self._build_code_prompt(spec_md)
        response = await self.llm.chat(
            messages=[{"role":"user","content":prompt}],
            temperature=0.3, max_tokens=100000,
        )
        return self._clean_code(response)

    async def _llm_generate_stream(self, spec_md: str):
        """LLM 流式生成工具代码，yield (kind, token)，kind ∈ {reasoning, content}"""
        prompt = self._build_code_prompt(spec_md)
        async for kind, token in self.llm.chat_stream_events(
            messages=[{"role":"user","content":prompt}],
            temperature=0.3, max_tokens=100000,
        ):
            yield kind, token

    # ── 沙箱执行 — 统一走 ToolExecutor，与对话界面完全一致 ──

    @staticmethod
    def _extract_imports(code: str) -> list[str]:
        """从代码中提取第三方依赖包名"""
        try:
            tree = _ast.parse(code)
        except SyntaxError:
            return []
        deps = set()
        # 已知标准库前缀
        stdlib_prefixes = {
            'abc', 'argparse', 'array', 'ast', 'asyncio', 'base64', 'bisect',
            'builtins', 'bz2', 'calendar', 'cgi', 'cmath', 'codecs', 'collections',
            'concurrent', 'configparser', 'contextlib', 'copy', 'csv', 'ctypes',
            'curses', 'dataclasses', 'datetime', 'dbm', 'decimal', 'difflib',
            'dis', 'distutils', 'email', 'encodings', 'enum', 'errno', 'faulthandler',
            'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt', 'gettext',
            'glob', 'graphlib', 'grp', 'gzip', 'hashlib', 'heapq', 'hmac', 'html',
            'http', 'idlelib', 'imaplib', 'importlib', 'inspect', 'io', 'ipaddress',
            'itertools', 'json', 'keyword', 'linecache', 'locale', 'logging', 'lzma',
            'mailbox', 'marshal', 'math', 'mimetypes', 'mmap', 'multiprocessing',
            'netrc', 'numbers', 'operator', 'optparse', 'os', 'pathlib', 'pdb',
            'pickle', 'pipes', 'pkgutil', 'platform', 'plistlib', 'pprint', 'profile',
            'pstats', 'pty', 'pwd', 'py_compile', 'pydoc', 'queue', 'quopri',
            'random', 're', 'readline', 'reprlib', 'resource', 'rlcompleter',
            'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve', 'shlex',
            'shutil', 'signal', 'site', 'socket', 'socketserver', 'sqlite3', 'ssl',
            'stat', 'statistics', 'string', 'struct', 'subprocess', 'sunau', 'sys',
            'sysconfig', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile', 'termios',
            'textwrap', 'threading', 'time', 'timeit', 'tkinter', 'token', 'tokenize',
            'tomllib', 'trace', 'traceback', 'tracemalloc', 'tty', 'turtle',
            'types', 'typing', 'unicodedata', 'unittest', 'urllib', 'uu', 'uuid',
            'venv', 'warnings', 'wave', 'weakref', 'webbrowser', 'xml', 'xmlrpc',
            'zipapp', 'zipfile', 'zipimport', 'zlib', '_thread', '_io',
            '__future__',
        }
        project_prefixes = {'app', 'core', 'resources', 'storage', 'config'}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top not in stdlib_prefixes and top not in project_prefixes:
                        deps.add(top)
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    top = node.module.split('.')[0]
                    if top not in stdlib_prefixes and top not in project_prefixes:
                        deps.add(top)
        return sorted(deps)

    async def _ensure_deps(self, code: str, tool_id: str) -> dict:
        """自动检测并安装代码中的第三方依赖"""
        deps = self._extract_imports(code)
        if not deps:
            return {"installed": [], "failed": [], "message": "无第三方依赖"}
        return await self.install_deps(tool_id, deps)

    async def sandbox_execute(self, code: str, test_input: dict, tool_id: str = None, exec_timeout: float = None) -> dict:
        """沙箱执行：通过 ToolExecutor 统一执行。
        注意：依赖安装由 _auto_debug_loop 统一管理（带日志），此处不再重复安装。
        exec_timeout=None 表示无超时限制（工具可能运行较长时间）。
        """
        import asyncio

        tid = tool_id or "sandbox"

        # 静态安全审查（todo 3.2）：命中阻断项则拒绝执行，stderr 携带
        # 错误清单回流自动调试循环。ToolExecutor 内还有统一 choke point
        # 兜底；这一层前置的意义是让沙箱路径尽早失败、事件结构清晰。
        from core.security.code_scan import scan_code
        scan = scan_code(code)
        if not scan.ok:
            msg = "静态安全审查未通过：\n" + "\n".join(f"- {e}" for e in scan.errors)
            return {
                "exit_code": 1,
                "stdout": json.dumps({"status": "failed", "security_scan": scan.to_dict()}, ensure_ascii=False),
                "stderr": msg,
                "success": False,
            }

        from core.executor.tool_executor import ToolExecutor

        try:
            exec_coro = ToolExecutor.execute(
                tool_id=tid,
                params=test_input,
                code=code,
                timeout=exec_timeout,
            )
            if exec_timeout is None:
                result = await exec_coro
            else:
                result = await asyncio.wait_for(exec_coro, timeout=exec_timeout + 10)
        except asyncio.TimeoutError:
            result = {"status": "failed", "message": f"工具执行超时 ({exec_timeout}秒)", "error": "TimeoutError"}
        except Exception as e:
            # 执行器本身抛异常时，把堆栈保留下来供自动调试定位
            import traceback as _tb
            result = {
                "status": "failed",
                "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{_tb.format_exc()}",
                "error": f"{type(e).__name__}: {str(e)}",
                "stderr": _tb.format_exc(),
            }

        # 失败时若 stderr 为空（工具自身 try/except 吞掉了异常），
        # 把完整结果补进 stderr —— 自动调试依赖 stderr 定位问题，
        # 否则 LLM 只能看到 message 里的一句话，无法定位到具体代码行。
        stderr = result.get("stderr", "")
        if result.get("status") != "success" and not stderr:
            parts = []
            msg = result.get("message", "")
            if msg:
                parts.append(f"[message] {msg}")
            err = result.get("error")
            if err:
                parts.append(f"[error] {err}")
            data = result.get("data")
            if data:
                try:
                    parts.append(f"[data] {json.dumps(data, ensure_ascii=False)[:2000]}")
                except Exception:
                    pass
            if not parts:
                parts.append(f"[result] {json.dumps(result, ensure_ascii=False)[:2000]}")
            stderr = "\n".join(parts)

        return {
            "exit_code": 0 if result.get("status") == "success" else 1,
            "stdout": json.dumps(result, ensure_ascii=False),
            "stderr": stderr,
            "success": result.get("status") == "success",
        }

    # ── 依赖安装 ──

    async def install_deps(self, tool_id: str, dependencies: list[str]) -> dict:
        """安装依赖（非流式，供 sandbox_execute 等调用）"""
        result = {"venv_path": "", "python": "", "results": []}
        async for event in self.install_deps_stream(tool_id, dependencies):
            if event["event"] == "deps_done":
                result = event["data"]
        return result

    def _get_exec_python(self, tool_id: str) -> str:
        """获取工具执行时实际使用的 Python（与 ToolExecutor._get_python_exe 一致）"""
        from core.executor.tool_executor import ToolExecutor
        return ToolExecutor._get_python_exe(tool_id)

    def _is_installed(self, python_exe: str, module_name: str) -> bool:
        """模块存在性探测：find_spec 不执行模块代码，避免大包冷启动超时误判"""
        probe = (
            "import importlib.util, sys; "
            "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
        )
        proc = subprocess.run(
            [python_exe, "-c", probe, module_name],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0

    async def install_deps_stream(self, tool_id: str, dependencies: list[str], stop_event=None):
        """流式安装依赖：实时输出安装日志，失败时用 LLM 分析原因并调整命令重试。

        使用 asyncio.to_thread + subprocess.Popen + Queue 实现：
        - 安装在线程中执行，不阻塞事件循环
        - 安装输出逐行通过 queue 推回异步世界
        - 失败时自动调用 LLM 分析并重试
        - 支持 stop_event 和全局调试标志双重停止

        安装目标永远是工具独立 `.venv`（uv 管理，硬链接缓存去重）：
        缺失时先引导创建并一次性装入工具全部第三方依赖——干净 venv 只装
        缺失项会导致 requests 等公共包反而不可见——完成后 freeze 出
        requirements.txt。uv 缺失或引导失败时明确报错，不静默改装别处。
        """
        import asyncio, threading

        from core.resource import deps as _deps

        tool_dir = _deps.resolve_tool_dir(tool_id)
        env_label = "tool_venv"
        exec_python = str(_deps.venv_python_path(tool_dir))

        bootstrap_error = ""
        try:
            uv = _deps.uv_bin()
            if not _deps.venv_exists(tool_dir):
                tool_py = tool_dir / "tool.py"
                code_text = tool_py.read_text(encoding="utf-8") if tool_py.exists() else ""
                all_mods = self._extract_imports(code_text) if code_text else []
                req_specs = _deps.read_requirements(tool_dir)
                if req_specs:
                    exec_python = _deps.create_tool_venv(tool_dir, requirements=True)
                else:
                    specs = sorted({_deps.pip_name(m) for m in all_mods} | {
                        _deps.pip_name(d.split("==")[0].split(">=")[0].split("<=")[0].strip())
                        for d in dependencies if d.strip()
                    })
                    exec_python = _deps.create_tool_venv(tool_dir, specs=specs) if specs else _deps.create_tool_venv(tool_dir)
                try:
                    _deps.freeze_requirements(tool_dir)
                except Exception:
                    pass
        except Exception as e:
            bootstrap_error = str(e)

        yield {"event": "deps_start", "deps": list(dependencies), "env": env_label}

        if bootstrap_error:
            yield {"event": "pip_output", "dep": ".venv", "line": f"[错误] {bootstrap_error}"}
            results = [
                {"dep": d, "success": False, "env": env_label, "reason": bootstrap_error}
                for d in dependencies if d.strip()
            ]
            for r in results:
                yield {"event": "dep_failed", "dep": r["dep"], "env": env_label, "reason": bootstrap_error}
            yield {"event": "deps_done", "data": {"python": exec_python, "results": results}}
            return

        def _pip_worker(dep: str, output_queue: asyncio.Queue, extra_flags: list[str] | None = None):
            """在线程中执行 uv 安装，逐行推入 queue"""
            parts = _deps.uv_pip_install_argv(uv, exec_python, dep) + (extra_flags or [])
            try:
                proc = subprocess.Popen(
                    parts,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # pip 在中文 Windows 上会输出中文警告，按系统默认 GBK 解码会炸
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        output_queue.put_nowait(("line", line))
                proc.wait()
                output_queue.put_nowait(("done", proc.returncode))
            except Exception as e:
                output_queue.put_nowait(("error", str(e)))

        results = []
        for dep in dependencies:
            dep = dep.strip()
            if not dep: continue

            module_name = dep.split("==")[0].split(">=")[0].split("<=")[0].strip()

            if self._is_installed(exec_python, module_name):
                yield {"event": "dep_already", "dep": dep, "env": env_label}
                results.append({"dep": dep, "success": True, "env": env_label, "already": True})
                continue

            yield {"event": "dep_installing", "dep": dep, "env": env_label}

            # 启动安装线程
            current_dep = dep
            extra_flags: list[str] = []
            binary_fallback_done = False
            max_retries = 2
            for attempt in range(max_retries + 1):
                q = asyncio.Queue()
                all_output = ""
                thread = threading.Thread(target=_pip_worker, args=(current_dep, q, extra_flags), daemon=True)
                thread.start()

                # 从 queue 读取输出，短超时轮询 + 停止检查
                retcode = None
                pip_start = asyncio.get_event_loop().time()
                PIP_TOTAL_TIMEOUT = 300  # pip 总超时 5 分钟
                while True:
                    # 检查停止信号
                    if not is_debug_running(tool_id):
                        all_output += "\n[已停止] 调试已停止\n"
                        retcode = -1
                        break
                    if stop_event and stop_event.is_set():
                        all_output += "\n[已停止] 用户手动停止\n"
                        retcode = -1
                        break
                    # 总超时
                    if asyncio.get_event_loop().time() - pip_start > PIP_TOTAL_TIMEOUT:
                        all_output += f"\n[超时] 安装超过 {PIP_TOTAL_TIMEOUT} 秒\n"
                        retcode = -1
                        break

                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=0.5)
                        if kind == "line":
                            all_output += data + "\n"
                            yield {"event": "pip_output", "dep": current_dep, "line": data}
                        elif kind == "done":
                            retcode = data
                            break
                        elif kind == "error":
                            all_output += f"\n[错误] {data}\n"
                            retcode = -1
                            break
                    except asyncio.TimeoutError:
                        # 0.5 秒超时是正常的，继续循环检查停止信号
                        continue

                thread.join(timeout=5)
                if retcode == 0:
                    yield {"event": "pip_result", "ok": True, "output": all_output[-1000:]}
                    break

                # 确定性回退：源码编译失败（缺编译器/工具链，Windows 最常见）
                # 时先用 --only-binary 重试预编译 wheel，不消耗 LLM 分析轮次
                if not binary_fallback_done and self._looks_like_build_failure(all_output):
                    binary_fallback_done = True
                    extra_flags = ["--only-binary=:all:"]
                    yield {"event": "pip_fallback", "dep": current_dep,
                           "reason": "源码编译失败，改用预编译 wheel 重试"}
                    continue

                if attempt >= max_retries:
                    yield {"event": "pip_result", "ok": False, "output": all_output[-1000:]}
                    break

                # 安装失败，LLM 分析
                yield {"event": "pip_analyzing", "dep": current_dep, "attempt": attempt + 1}
                analysis = await self._llm_analyze_pip_error(current_dep, all_output[-2000:])
                yield {"event": "pip_analysis", "dep": current_dep, "analysis": analysis}
                if analysis.get("adjusted_command"):
                    current_dep = analysis["adjusted_command"]
                    extra_flags = []  # LLM 换版本/参数后重新全量尝试，不沿用 wheel 限定
                else:
                    yield {"event": "pip_result", "ok": False, "output": all_output[-1000:]}
                    break

            # 汇总结果
            if retcode == 0:
                results.append({"dep": dep, "success": True, "env": env_label})
                try:
                    _deps.freeze_requirements(tool_dir)
                except Exception:
                    pass
                yield {"event": "dep_installed", "dep": dep, "env": env_label}
            else:
                results.append({"dep": dep, "success": False, "env": env_label, "reason": all_output[-300:]})
                yield {"event": "dep_failed", "dep": dep, "env": env_label, "reason": all_output[-300:]}

        data = {"python": exec_python, "results": results}
        yield {"event": "deps_done", "data": data}

    @staticmethod
    def _looks_like_build_failure(output: str) -> bool:
        """判断 pip 失败输出是否为源码编译失败（可用预编译 wheel 绕过）。

        只匹配构建阶段特征：网络/索引/权限类失败不该触发 wheel 回退。
        """
        markers = (
            "failed building wheel", "building wheel for",
            "getting requirements to build", "preparing metadata",
            "subprocess-exit-with-status", "microsoft visual c++",
            "cl.exe", "gcc:", "cc1:", "cmake error", "ninja: error",
            "cannot find -l", "fatal error:", "compilation error",
        )
        low = output.lower()
        return any(m in low for m in markers)

    async def _llm_analyze_pip_error(self, dep: str, output: str) -> dict:
        """用 LLM 分析 pip 安装失败原因，返回调整建议"""
        prompt = f"""pip install {dep} 安装失败，以下是完整输出：

{output}

请分析失败原因，给出修复方案。返回 JSON 格式：
{{
  "reason": "失败原因（简短）",
  "suggestion": "修复建议",
  "adjusted_command": "调整后的安装命令（如 pip install xxx --no-deps 或其他参数），如果无需调整则为空字符串"
}}

只返回 JSON，不要其他内容。"""
        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=500, timeout=30,
            )
            # 提取 JSON
            text = response if isinstance(response, str) else response.get("content", "")
            import re as _re
            match = _re.search(r'\{[^{}]*\}', text, _re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
        return {"reason": "未知错误", "suggestion": "", "adjusted_command": ""}

    # ── 调试日志写入 ──

    async def _write_debug_log(self, tool_id: str, timestamp: str, entries: list[dict], success: bool, final_round: int):
        """将调试日志写入 logs/ 目录下的时间戳文件"""
        import asyncio

        # 日志脱敏：避免 API Key 等凭据随工具输出写入日志文件
        from core.security.secrets import scrub_text as _scrub

        def _write():
            try:
                from core.user_paths import user_home
                log_dir = user_home() / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"{tool_id}_{timestamp}.md"

                lines = []
                lines.append(f"# 自动调试日志\n")
                lines.append(f"- **工具**: {tool_id}")
                lines.append(f"- **时间**: {timestamp}")
                lines.append(f"- **结果**: {'成功' if success else '失败'}（共 {final_round} 轮）")
                lines.append(f"- **日志条目**: {len(entries)} 轮\n")
                lines.append("---\n")

                for i, entry in enumerate(entries):
                    r = entry["round"]
                    lines.append(f"## 第 {r} 轮\n")
                    lines.append(f"### 执行结果\n")
                    # 脱敏：工具可能把 API Key 打印到 stdout，
                    # 直接落盘会让密钥长期留存在日志文件中。
                    lines.append(f"```\n{_scrub(entry['exec_result'])}\n```\n")
                    if entry.get("dep_feedback"):
                        lines.append(f"### 依赖反馈\n")
                        lines.append(f"{entry['dep_feedback']}\n")
                    lines.append(f"### 发送给 LLM 的 Prompt\n")
                    lines.append(f"```\n{_scrub(entry['prompt'])}\n```\n")
                    lines.append(f"### LLM 返回\n")
                    lines.append(f"```\n{_scrub(entry['response'])}\n```\n")
                    if i < len(entries) - 1:
                        lines.append("======================\n")

                log_file.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                pass  # 日志写入失败不阻塞调试流程

        await asyncio.to_thread(_write)

    # ── 自动调试 ──

    async def auto_debug_stream(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int = None, stop_event=None):
        """自动调试生成器：执行→LLM分析→改代码→再执行。
        stop_event: asyncio.Event，设置时中断调试。
        CancelledError: producer_task.cancel() 时抛出，立即退出。
        max_rounds 为 None 时读取 .env 的 DEBUG_MAX_ROUNDS（默认 10）。
        """
        import asyncio

        if max_rounds is None:
            max_rounds = _debug_max_rounds()

        current_code = code
        set_debug_running(tool_id)

        try:
            try:
                async for event in self._auto_debug_loop(
                    spec_md, current_code, test_input, tool_id, max_rounds, stop_event
                ):
                    yield event
            except asyncio.CancelledError:
                if stop_event:
                    stop_event.set()
                yield {"event": "stopped", "round": 0, "message": "调试已停止"}
        finally:
            set_debug_stopped(tool_id)

    async def _auto_debug_loop(self, spec_md: str, code: str, test_input: dict, tool_id: str, max_rounds: int, stop_event):
        """自动调试主循环
        
        流程: 执行 → 系统自动处理依赖安装 → LLM 根据结果修复代码
        
        依赖处理由系统自动完成，LLM 不需要关心安装指令：
        - 循环开始前: AST 提取 import → 安装
        - 执行报 ModuleNotFoundError: 自动安装缺失模块
        - LLM 只负责: 根据执行结果 + 安装反馈 → 决定修复代码还是换方案
        """
        import asyncio

        current_code = code
        # 记录安装失败的依赖（用于告诉 LLM 哪些依赖不可用）
        failed_deps: set = set()
        # 调试日志记录
        debug_log_entries: list[dict] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 跨轮修复对话：user=失败上下文, assistant=LLM 修复回复。
        # 只保留首条全量 Prompt + 最近 _DEBUG_HISTORY_KEEP 条，防止逐轮膨胀。
        debug_messages: list[dict] = []
        _DEBUG_HISTORY_KEEP = 12

        # 调试开始：立即通知前端
        yield {"event": "debug_start", "message": "自动调试启动", "max_rounds": max_rounds}

        # ── 静态安全审查（初始代码）──
        # block 级违规：不执行，直接要求 LLM 修复后再跑
        from core.security.review import review_code as _review, block_violations as _blocks

        security_feedback = ""
        skip_exec_for_security = False
        _init_violations = _review(current_code)
        _init_blocks = _blocks(_init_violations)
        if _init_blocks:
            yield {
                "event": "security_violation", "round": 0,
                "violations": [v.to_dict() for v in _init_blocks],
                "message": f"初始代码存在 {len(_init_blocks)} 项安全违规，跳过执行",
            }
            security_feedback = (
                "\n[安全审查] 代码违反安全策略，必须修复以下违规后才能执行"
                "（本地文件操作/系统调用只能通过 core API 完成）:\n"
                + "\n".join(f"- {v.message}（第 {v.line} 行）" for v in _init_blocks)
            )
            skip_exec_for_security = True

        # 初始依赖安装（系统自动，LLM 不参与）
        deps = self._extract_imports(current_code)
        if deps:
            async for dep_event in self.install_deps_stream(tool_id, deps, stop_event=stop_event):
                if stop_event and stop_event.is_set():
                    yield {"event": "stopped", "round": 0, "message": "用户手动停止调试"}
                    return
                yield dep_event

        for round_num in range(1, max_rounds + 1):
            # ── 每轮开始前检查停止标志（全局标志 + stop_event 双重保障）──
            if not is_debug_running(tool_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "round_start", "round": round_num, "max": max_rounds}

            # ── ① 执行（安全审查未通过时跳过，用合成失败结果驱动 LLM 修复）──
            # 事件驱动等待：exec 完成与 stop_event 同时挂起，谁先到听谁的。
            # 旧实现每 0.1s 忙轮询一次，长执行下白耗调度且停止延迟固定。
            if skip_exec_for_security:
                exec_result = {
                    "exit_code": 1,
                    "stdout": json.dumps({
                        "status": "failed",
                        "message": "安全审查未通过，已跳过执行",
                        "error": "SecurityViolation",
                    }, ensure_ascii=False),
                    "stderr": "安全审查未通过，已跳过执行，请修复违规代码",
                    "success": False,
                }
            else:
                exec_task = asyncio.create_task(
                    self.sandbox_execute(current_code, test_input, tool_id, exec_timeout=_debug_exec_timeout())
                )
                stop_waiters = set()
                if stop_event:
                    _sw = asyncio.create_task(stop_event.wait())
                    stop_waiters.add(_sw)
                done, pending = await asyncio.wait(
                    {exec_task} | stop_waiters, timeout=None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if exec_task in done:
                    for w in stop_waiters:
                        w.cancel()
                    exec_result = exec_task.result()
                else:
                    exec_task.cancel()
                    try:
                        await asyncio.wait_for(exec_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试（执行已中断）"}
                    return

            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            # ── ② 解析输出 ──
            success = False
            output_data = None
            stdout = exec_result.get("stdout", "")
            if stdout:
                lines = stdout.strip().split('\n')
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            output_data = json.loads(line)
                            break
                        except:
                            continue
                if output_data is None:
                    try:
                        output_data = json.loads(stdout)
                    except:
                        pass
                if isinstance(output_data, dict) and output_data.get("status") == "success":
                    success = True

            yield {"event": "exec_result", "round": round_num, "stdout": exec_result["stdout"], "stderr": exec_result["stderr"], "success": success}

            # ── ②b 运行时安全事件上报 ──
            if isinstance(output_data, dict) and output_data.get("_security_violations"):
                _rt = output_data["_security_violations"]
                yield {
                    "event": "security_violation", "round": round_num, "runtime": True,
                    "violations": [
                        {"rule": "RUNTIME", "severity": "warn", "message": str(m), "line": 0, "snippet": ""}
                        for m in _rt
                    ],
                    "message": f"运行时记录到 {len(_rt)} 项安全事件",
                }

            if success:
                yield {"event": "done", "round": round_num, "success": True, "code": current_code, "message": f"调试成功 (第{round_num}轮)"}
                await self._write_debug_log(tool_id, timestamp, debug_log_entries, success=True, final_round=round_num)
                return

            # ── ③ 系统自动处理依赖（LLM 不参与）──
            dep_feedback = ""
            if output_data and output_data.get("error") == "ModuleNotFoundError":
                missing = output_data.get("missing_module", "")
                if missing:
                    install_ok = False
                    install_reason = ""
                    async for dep_event in self.install_deps_stream(tool_id, [missing], stop_event=stop_event):
                        if stop_event and stop_event.is_set():
                            yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                            return
                        yield dep_event
                        if dep_event.get("event") == "dep_installed":
                            install_ok = True
                        elif dep_event.get("event") == "dep_failed":
                            install_ok = False
                            install_reason = dep_event.get("reason", "")
                        elif dep_event.get("event") == "pip_analysis":
                            a = dep_event.get("analysis", {})
                            if a:
                                install_reason += f" [分析: {a.get('reason', '')}]"

                    if install_ok:
                        dep_feedback = f"\n[系统已自动安装 {missing}，请保留现有 import，修复其他代码问题]"
                    else:
                        failed_deps.add(missing)
                        dep_feedback = f"\n[依赖 {missing} 安装失败: {install_reason}。该依赖不可用，请换替代方案]"

            # ── ④ LLM 分析并修复代码 ──
            if round_num >= max_rounds:
                break

            if not is_debug_running(tool_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "thinking", "round": round_num, "message": "LLM 分析错误..."}

            # ── 构建本轮执行摘要（用于日志）──
            # 脱敏：以下内容会随 Prompt 发给第三方模型服务商，
            # 一旦 API Key 混入就无法撤回，因此必须在此拦截。
            from core.security.secrets import redact_for_prompt as _redact, scrub_mapping as _scrub_mapping

            _safe_test_input = json.dumps(
                _scrub_mapping(test_input), ensure_ascii=False, indent=2
            )
            _safe_stdout_full = _redact(exec_result['stdout'], max_len=3000)
            _safe_stderr_full = _redact(exec_result['stderr'], max_len=2000)
            _safe_stdout = _redact(exec_result['stdout'], max_len=2000)
            _safe_stderr = _redact(exec_result['stderr'], max_len=1000)

            exec_summary = f"stdout:\n{_safe_stdout_full}\n\nstderr:\n{_safe_stderr_full}"

            # 跨轮对话记忆：首轮发全量上下文（代码+输入+结果），之后每轮只补发
            # 最新执行结果——上一轮代码就在 assistant 历史里，无需每轮重放全文。
            # LLM 因此能看到自己此前试过什么，减少重复试错与逐轮 token 膨胀。
            if debug_messages:
                fix_prompt = f"""上一版代码执行仍失败。请基于你上一轮回复中的代码继续修复（不要推翻重来，除非错误明确指向方案性缺陷）。

=== EXECUTION RESULT ===
stdout: {_safe_stdout}
stderr: {_safe_stderr}
=== END RESULT ===
{dep_feedback}{security_feedback}

仍按 <analysis>/<code> 格式输出。要求：<code> 内是完整 Python 文件（含模板头部），保留已正确的部分；调用大模型时 max_tokens=100000（或更大）。"""
            else:
                fix_prompt = f"""Debug this tool code. It failed execution.

=== CURRENT CODE ===
{current_code}
=== END CODE ===

=== TEST INPUT ===
{_safe_test_input}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {_safe_stdout}
stderr: {_safe_stderr}
=== END RESULT ===
{dep_feedback}{security_feedback}

请分两部分输出，严格使用以下格式：

<analysis>
问题原因：（简要说明代码为什么失败，1-3 句话）
修改计划：（列出打算如何修改，逐条说明）
</analysis>

<code>
（完整修复后的 Python 代码，包括模板头部）
</code>

要求：
1. <analysis> 和 <code> 标签必须完整、成对出现。
2. <analysis> 内用中文简要说明问题原因和修改计划，不要超过 5 行。
3. <code> 内只放完整 Python 代码。
4. 修复规则：
   - 依赖已成功安装：保留 import，修复其他代码逻辑问题。
   - 依赖安装失败：用替代库或标准库方案替换。
   - 代码逻辑 bug（非依赖）：修复 bug。
5. INTERFACE RULES: execute(**kwargs)->dict, kwargs.get, {{status,output_format,message,data}}, try/except。
6. 代码中不要出现 pip install、subprocess 安装指令或 markdown 围栏。
7. 代码中调用大模型（api-llm-chat / api-llm-chat-stream 等）时，必须设置 max_tokens=100000（或更大），不要用 4096、1024 等小值。"""

            # 记录本轮日志条目
            log_entry = {
                "round": round_num,
                "exec_result": exec_summary,
                "dep_feedback": dep_feedback,
                "prompt": fix_prompt,
                "response": "",
            }

            full_response = ""
            llm_task = None
            llm_exception = None
            get_task = None
            LLM_TIMEOUT = int(_os.getenv("LLM_TIMEOUT", "1200"))  # LLM 调用总超时（秒），默认 1200，跟随 .env 可调大
            try:
                # 事件驱动泵：token 经 asyncio.Queue 直达消费侧，与停止信号、
                # 整体超时三方竞态（旧实现 sleep(0.05) 忙轮询 list，空转且延迟固定）。
                _SENTINEL = object()
                token_q: asyncio.Queue = asyncio.Queue()
                loop = asyncio.get_event_loop()

                async def _collect_tokens():
                    nonlocal llm_exception
                    try:
                        async for kind, token in self.llm.chat_stream_events(
                            messages=debug_messages + [{"role": "user", "content": fix_prompt}],
                            temperature=0.2, max_tokens=32000,
                        ):
                            await token_q.put((kind, token))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        llm_exception = exc
                    finally:
                        token_q.put_nowait(_SENTINEL)

                llm_task = asyncio.create_task(_collect_tokens())
                start_time = loop.time()

                timed_out = False
                stopped_msg = None
                # 全程只保持一个挂起的 get_task，消费掉一个 token 后才补挂下一个，
                # 避免两个 getter 竞态导致 token 被孤儿 getter 取走丢失。
                get_task = asyncio.create_task(token_q.get())
                while True:
                    if not is_debug_running(tool_id):
                        stopped_msg = "调试已停止"
                        break
                    if stop_event and stop_event.is_set():
                        stopped_msg = "用户手动停止调试（LLM对话已中断）"
                        break
                    wait_futs = {get_task}
                    if not llm_task.done():
                        wait_futs.add(llm_task)  # done 后摘除，防止 wait 立即返回空转
                    stop_waiter = None
                    if stop_event:
                        stop_waiter = asyncio.create_task(stop_event.wait())
                        wait_futs.add(stop_waiter)
                    remaining = LLM_TIMEOUT - (loop.time() - start_time)
                    done, _pending = await asyncio.wait(
                        wait_futs, timeout=max(remaining, 0.01),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_waiter is not None and not stop_waiter.done():
                        stop_waiter.cancel()
                    if not done:  # 整体超时
                        timed_out = True
                        break
                    if get_task in done:
                        item = get_task.result()
                        if item is _SENTINEL:
                            break  # LLM 流正常结束（finally 保证 sentinel 必然入队）
                        kind, token = item
                        if kind == "content":
                            full_response += token
                        yield {"event": "thinking_stream", "round": round_num, "token": token}
                        get_task = asyncio.create_task(token_q.get())

                if timed_out:
                    llm_task.cancel()
                    try: await llm_task
                    except asyncio.CancelledError: pass
                    get_task.cancel()
                    yield {"event": "llm_error", "round": round_num, "message": f"LLM调用超时（{LLM_TIMEOUT}秒），跳过本轮"}
                    continue
                if stopped_msg is not None:
                    llm_task.cancel()
                    try: await llm_task
                    except asyncio.CancelledError: pass
                    get_task.cancel()
                    yield {"event": "stopped", "round": round_num, "message": stopped_msg}
                    return

                if llm_task.cancelled():
                    continue

                # LLM 流式调用抛出异常（如 API 错误）时，明确反馈
                if llm_exception is not None:
                    yield {"event": "llm_error", "round": round_num, "message": f"LLM调用异常: {str(llm_exception)[:300]}"}
                    if not full_response.strip():
                        continue

            except asyncio.CancelledError:
                # producer_task.cancel() 传播的 CancelledError — 立即退出
                if llm_task and not llm_task.done():
                    llm_task.cancel()
                    try: await llm_task
                    except asyncio.CancelledError: pass
                if get_task is not None and not get_task.done():
                    get_task.cancel()
                raise  # 继续向上传播
            except Exception as e:
                yield {"event": "llm_error", "round": round_num, "message": f"LLM调用异常: {str(e)[:200]}"}
                continue

            # ── ⑤ 解析 LLM 响应：切分「分析」与「代码」──
            log_entry["response"] = full_response
            debug_log_entries.append(log_entry)

            # 切分 <analysis> 与 <code> 两部分
            analysis_text = ""
            code_text = ""
            am = re.search(r"<analysis>(.*?)</analysis>", full_response, re.DOTALL)
            if am:
                analysis_text = am.group(1).strip()
            cm = re.search(r"<code>(.*?)</code>", full_response, re.DOTALL)
            if cm:
                code_text = cm.group(1).strip()
            else:
                # 兜底：无 <code> 标记时，把 <analysis> 之后的全部内容视为代码
                if am:
                    code_text = full_response[am.end():].strip()
                else:
                    code_text = full_response.strip()

            # 清理 markdown 代码块围栏
            if code_text.startswith("```python"):
                code_text = code_text[len("```python"):]
            elif code_text.startswith("```"):
                code_text = code_text[3:]
            if code_text.endswith("```"):
                code_text = code_text[:-3]
            code_text = code_text.strip()
            if code_text.startswith("```") and code_text.count("```") == 2:
                parts = code_text.split("```")
                code_text = parts[1].strip() if len(parts) >= 2 else code_text

            # 先发送分析结论（如果有）
            if analysis_text:
                yield {"event": "analysis_result", "round": round_num, "analysis": analysis_text}

            # 如果代码过短，跳过本轮
            if len(code_text) < 100:
                llm_raw = (analysis_text or full_response.strip())[:2000] or "(空)"
                yield {
                    "event": "thinking",
                    "round": round_num,
                    "message": f"LLM 返回代码过短（{len(code_text)} 字符），跳过本轮。LLM 实际返回: {llm_raw}",
                }
                continue

            # ── ⑤b 静态安全审查（LLM 新代码）──
            # block 级违规：不采纳这段代码（也不写入跨轮对话历史，避免 LLM
            # 误以为它被接受），把违规反馈给 LLM 下一轮重新生成
            _new_violations = _review(code_text)
            _new_blocks = _blocks(_new_violations)
            if _new_blocks:
                yield {
                    "event": "security_violation", "round": round_num,
                    "violations": [v.to_dict() for v in _new_blocks],
                    "message": f"LLM 生成代码存在 {len(_new_blocks)} 项安全违规，已驳回",
                }
                security_feedback = (
                    "\n[安全审查] 上次修改后的代码仍违反安全策略，必须修复以下违规"
                    "（本地文件操作/系统调用只能通过 core API 完成）:\n"
                    + "\n".join(f"- {v.message}（第 {v.line} 行）" for v in _new_blocks)
                )
                continue  # 不更新 current_code，下一轮继续修

            # 通过审查：清除安全反馈，恢复正常执行
            security_feedback = ""
            skip_exec_for_security = False

            current_code = code_text
            debug_messages.append({"role": "user", "content": fix_prompt})
            debug_messages.append({"role": "assistant", "content": full_response})
            if len(debug_messages) > _DEBUG_HISTORY_KEEP + 1:
                debug_messages[:] = [debug_messages[0]] + debug_messages[-_DEBUG_HISTORY_KEEP:]
            yield {"event": "code_updated", "round": round_num, "code": current_code}

            # ── ⑥ 系统自动安装新代码的依赖 ──
            new_deps = self._extract_imports(current_code)
            if new_deps:
                async for dep_event in self.install_deps_stream(tool_id, new_deps, stop_event=stop_event):
                    if stop_event and stop_event.is_set():
                        yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                        return
                    yield dep_event

        await self._write_debug_log(tool_id, timestamp, debug_log_entries, success=False, final_round=max_rounds)
        yield {"event": "done", "round": max_rounds, "success": False, "code": current_code, "message": f"达到最大轮数 {max_rounds}，调试未完成"}
