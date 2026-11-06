"""Agent 代码生成器 — 从 MD 规范文档生成 Agent Python 代码

架构：模板组装 + LLM 填充执行逻辑 + 后处理修复。
工具/API 辅助方法由模板机械生成（保证正确），execute() 体由 LLM 生成。
"""

import ast
import asyncio
import json
import os as _os
import re
import shutil
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.resource import paths
from config.settings import get_debug_exec_timeout, get_debug_max_rounds
from core.llm.client import create_gen_llm_client
from core.agent.base import _extract_fenced_json

# ── 全局调试状态：线程安全的停止标志（与 tool_builder 同构，key=agent_id）──
_debug_states: dict[str, bool] = {}
_debug_lock = threading.Lock()


def set_debug_running(agent_id: str):
    with _debug_lock:
        _debug_states[agent_id] = True


def set_debug_stopped(agent_id: str):
    with _debug_lock:
        _debug_states.pop(agent_id, None)


def is_debug_running(agent_id: str) -> bool:
    with _debug_lock:
        return _debug_states.get(agent_id, False)


def stop_debug(agent_id: str):
    with _debug_lock:
        if agent_id in _debug_states:
            _debug_states[agent_id] = False

# ═══════════════════════════════════════════════════════════════════
# 头文件模板 — 始终正确，非 LLM 生成
# ═══════════════════════════════════════════════════════════════════

HEADER_TEMPLATE = '''"""Agent: {agent_name} — {agent_id}"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time as _time
from pathlib import Path
from typing import AsyncGenerator

# 项目根目录：只信平台注入的 env，不写死目录层级。
# Agent 实现可能落在内置 resources/agents/implementations/ 或私有
# resources-private/agents/implementations/，两者到项目根的层级不同，
# 写死 parents[N] 会把上级目录当成项目根（工具查找、日志目录全部失效）。
def _detect_project_root() -> Path:
    env_root = os.environ.get("SOTABAND_ROOT", "").strip()
    if env_root and (Path(env_root) / "core" / "agent" / "base.py").exists():
        return Path(env_root)
    for parent in Path(__file__).resolve().parents:
        if (parent / "core" / "agent" / "base.py").exists():
            return parent
    return Path(__file__).resolve().parent.parent.parent.parent.parent

PROJECT_ROOT = _detect_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 当前用户目录：Agent 的一切产物（日志、导出文件）写这里，绝不写系统根。
def _detect_user_home() -> Path:
    uh = os.environ.get("SOTABAND_USER_HOME", "").strip()
    if uh:
        return Path(uh)
    base = os.environ.get("SOTABAND_HOME", "").strip() or str(PROJECT_ROOT.parent / "SB-USER")
    return Path(base) / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")

USER_HOME = _detect_user_home()

def out_dir(*parts) -> Path:
    """产物目录：USER_HOME/<子目录>/…，自动创建。日志、导出文件都走这里。"""
    d = USER_HOME.joinpath(*parts) if parts else USER_HOME
    d.mkdir(parents=True, exist_ok=True)
    return d

from core.agent.base import BaseAgent, AgentContext
'''

# ═══════════════════════════════════════════════════════════════════
# 工具辅助方法模板 — 机械生成，不依赖 LLM
# ═══════════════════════════════════════════════════════════════════

TOOL_HELPER_TEMPLATE = '''
    def _call_tool_{tool_id_safe}(self, **params) -> dict:
        """调用工具: {tool_name} (tool_id: {tool_id})"""
        # 在「内置 + 私有」资源根中定位工具实现。
        # 私有根在 USER_HOME/resources-private，与项目根层级无关，
        # 运行时优先向 core.resource.paths 询问，失败再按 env 还原。
        try:
            from core.resource import paths as _paths
            _spaces = _paths.space_dirs("tools")
        except Exception:
            _spaces = None
        if not _spaces:
            _spaces = [PROJECT_ROOT / "resources" / "tools",
                       USER_HOME / "resources-private" / "tools"]
        _tool_base = None
        for _space in _spaces:
            _cand = _space / "implementations" / "{tool_id}"
            if (_cand / "tool.py").exists():
                _tool_base = _cand
                break
        if _tool_base is None:
            return {{"status": "failed", "message": f"工具不存在: {tool_id}"}}
        impl_path = _tool_base / "tool.py"

        code = impl_path.read_text(encoding='utf-8')
        venv_python = _tool_base / ".venv" / "bin" / "python"
        python_exe = str(venv_python) if venv_python.exists() else sys.executable

        def _safe_dumps(obj):
            return json.dumps(obj, ensure_ascii=False).encode('utf-8', errors='replace').decode('utf-8')

        test_script = (
            f"import json, sys\\n"
            f"sys.path.insert(0, {{_safe_dumps(str(PROJECT_ROOT))}})\\n"
            f"code = {{_safe_dumps(code)}}\\n"
            f"exec(code)\\n"
            f"result = execute(**{{repr(params)}})\\n"
            f"print(json.dumps(result, default=str))\\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True,
                # 用户目录一并注入：工具靠它定位产物落点，缺失会写错地方
                env={{**os.environ, "TOOL_DIR": str(impl_path.parent),
                      "_PROJECT_ROOT": str(PROJECT_ROOT), "SOTABAND_ROOT": str(PROJECT_ROOT),
                      "SOTABAND_USER_HOME": str(USER_HOME),
                      "SOTABAND_USER_ID": os.environ.get("SOTABAND_USER_ID", "0")}},
                cwd=str(USER_HOME),
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {{"status": "failed", "message": proc.stderr[:300]}}
        finally:
            # 用 Path.unlink 而非 os.unlink：静态安全扫描把 os.unlink 列为阻断项，
            # 否则每个引用工具的 Agent 都会在调试首轮被拦下。
            Path(tmp_path).unlink(missing_ok=True)
'''

# ═══════════════════════════════════════════════════════════════════
# API 辅助方法模板 — 机械生成
# ═══════════════════════════════════════════════════════════════════

API_HELPER_TEMPLATE = '''
    def _call_api_{api_id_safe}(self, **params) -> dict:
        """调用 API: {api_name} (api_id: {api_id})"""
        from core.api import get_api
        try:
            api = get_api("{api_id}")
            return api.call(**params)
        except Exception as e:
            return {{"status": "failed", "message": str(e)}}
'''

# ═══════════════════════════════════════════════════════════════════
# 类骨架模板 — 含 LLM 参数提取辅助方法 + tool/API 调用
# ═══════════════════════════════════════════════════════════════════

CLASS_SKELETON = '''
class {cls_name}(BaseAgent):
    """{description}"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        """懒加载 LLM 客户端"""
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import create_llm_client
            self._llm = create_llm_client(settings.llm)
        return self._llm

    def _log_step(self, step_name: str, action: str, input_data: dict = None, output_data: dict = None, error: str = None):
        """记录执行步骤日志到 USER_HOME/agent_logs/{{agent_id}}-{{timestamp}}.md"""
        if not hasattr(self, '_log_file'):
            # 日志属用户数据，落 USER_HOME/agent_logs（out_dir 已按 env 解析）
            log_dir = out_dir("agent_logs")
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            agent_id = self.spec.id if self.spec else "unknown"
            self._log_file = log_dir / f"{{agent_id}}-{{ts}}.md"
            # 写入文件头
            self._log_file.write_text(
                f"# Agent 执行日志: {{agent_id}}\\n\\n"
                f"**启动时间**: {{ts}}\\n\\n"
                f"| 步骤 | 时间 | 操作 | 输入 | 输出 | 状态 |\\n"
                f"|------|------|------|------|------|------|\\n",
                encoding="utf-8"
            )
        now = datetime.datetime.now().strftime("%H:%M:%S")
        inp_str = json.dumps(input_data, ensure_ascii=False)[:200] if input_data else "-"
        out_str = json.dumps(output_data, ensure_ascii=False)[:200] if output_data else "-"
        status = f"❌ {{error}}" if error else "✅ 成功"
        row = f"| {{step_name}} | {{now}} | {{action}} | {{inp_str}} | {{out_str}} | {{status}} |\\n"
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(row)

    def _step(self, step_id: str, text: str, status: str = "running"):
        """构造前端「工作流程」步骤事件（SSE step）。

        用法: yield self._step("tool", "调用工具", "running")
              yield self._step("tool", "工具执行完成", "done")
        status: running / done / error。前端按 step_id 合并状态并自动记录每步耗时。
        """
        return {{"event": "step", "data": {{"id": step_id, "text": text, "status": status}}}}

    async def _extract_params_with_llm(self, tool_name: str, user_input: str, param_defs: list) -> dict:
        """使用 LLM 从用户输入中智能提取工具/API 所需参数"""
        if not param_defs:
            return {{}}
        prompt = (
            f"从用户输入中提取工具参数。\\n\\n"
            f"工具: {{tool_name}}\\n"
            f"参数定义: {{json.dumps(param_defs, ensure_ascii=False)}}\\n"
            f"用户输入: \\"{{user_input}}\\"\\n\\n"
            f"返回 JSON 格式:"
        )
        try:
            response = await self._get_llm().chat(
                messages=[{{"role": "user", "content": prompt}}],
                temperature=0.0, max_tokens=300
            )
            text = response.strip()
            if text.startswith("```"): text = text.split("\\n", 1)[1].rsplit("\\n", 1)[0]
            return json.loads(text)
        except Exception:
            return {{}}
{helper_methods}

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """Agent 主执行逻辑 — 按 MD 执行流程逐步: LLM解析参数→调用工具→LLM合成结果"""
{execute_body}
'''


# ═══════════════════════════════════════════════════════════════════
# 角色契约 — 入口参数 / 出口事件形态（框架级约定，非 LLM 自由决定）
# ═══════════════════════════════════════════════════════════════════

ROLE_CONTRACTS = {
    "interactive": (
        "入口：`content = kwargs.get(\"content\", \"\").strip()` —— 用户消息。\n"
        "出口：`delta`（最终回复的逐 token 增量，正文以此流式下发）/ "
        "`content`（简短固定提示语）/ `card`（结构化卡片）/ `done` / `error` 事件。"
    ),
    "task": (
        "入口：启动参数由调用方以「参数名」直接传入 kwargs（启动面板按 MD 的"
        "「## 输入」逐项渲染），必须按声明的名字取值，例如 `name = kwargs.get(\"name\", \"\")`。\n"
        "只有当 MD 的输入里确实声明了 `content` 时才使用 content。\n"
        "出口：`content`（进度/结果文本）与 `done`，失败时 `error` + `done`。\n"
        "【禁止】不要假设调用方会传 content，也不要用 content 代替启动参数。"
    ),
    "web-service": (
        "入口：`api_name = kwargs.get(\"api_name\", \"\")`、"
        "`req = kwargs.get(\"params\", {}) or {}`；请求参数一律从 req 取。\n"
        "【禁止】禁止读取 `kwargs[\"content\"]` —— HTTP 宿主不会传，"
        "读它会导致所有请求都被判为「缺少必填参数」。\n"
        "出口：必须 `yield {\"event\": \"result\", \"data\": {\"response\": {...}}}`，"
        "否则 HTTP 宿主拿不到响应体（只会返回 {\"status\": \"ok\"}）。"
    ),
}


class AgentCodeBuilder(BaseBuilder):
    """从 Agent MD 规范文档生成 Agent Python 代码"""

    def __init__(self, llm_client=None):
        # 代码生成走「生成模型」（LLM_GEN_*，未配置时回退全局）；
        # 客户端每次调用时才解析配置，设置页改动即时生效
        self.llm = llm_client or create_gen_llm_client()

    # ══════════════════════════════════════════════════════════
    # MD 解析 — 提取工具/API 引用
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _extract_tool_refs(spec_md: str) -> list[dict]:
        """从 MD 中提取【【工具名称】】引用"""
        names = re.findall(r'【【(.+?)】】', spec_md)
        refs = []
        seen = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            info = AgentCodeBuilder._lookup_tool(name)
            refs.append({"name": name, "id": info["id"], "params": info["params"]})
        return refs

    @staticmethod
    def _extract_api_refs(spec_md: str) -> list[dict]:
        """从 MD 中提取【API名称】引用（排除工具引用【【...】】）"""
        tool_spans = [(m.start(), m.end()) for m in re.finditer(r'【【.+?】】', spec_md)]
        refs = []
        seen = set()
        for m in re.finditer(r'【(.+?)】', spec_md):
            if any(m.start() >= s and m.end() <= e for s, e in tool_spans):
                continue
            name = m.group(1)
            if name in seen:
                continue
            seen.add(name)
            info = AgentCodeBuilder._lookup_api(name)
            refs.append({"name": name, "id": info["id"], "params": info.get("params", {})})
        return refs

    @staticmethod
    def _extract_role(spec_md: str) -> str:
        """从 MD frontmatter 中提取 role。

        注意：不能用 `\\w+` —— 它不匹配连字符，会把 `role: web-service`
        截成 `web`，导致 web-service 类型的 Agent 被当成 task 生成代码
        （表现为生成代码读 kwargs["content"]，而 HTTP 宿主从不传 content）。
        """
        m = re.search(r'^\s*role\s*:\s*([A-Za-z0-9_\-]+)', spec_md, re.MULTILINE)
        # 未声明时返回空串，由调用方回退到显式传入的 role（不能在这里直接默认 task）
        return AgentCodeBuilder._normalize_role(m.group(1)) if m else ""

    @staticmethod
    def _normalize_role(role: str | None) -> str:
        """把各种写法归一为三类角色之一：interactive / task / web-service。"""
        v = (role or "").strip().lower().replace("_", "-")
        if v in ("web-service", "webservice", "observer"):
            return "web-service"
        if v in ("interactive", "chat"):
            return "interactive"
        # orchestrator 及其他历史取值一律按 task 处理（与 AgentRole._missing_ 一致）
        return "task"

    @staticmethod
    def _parse_spec_inputs(spec_md: str) -> list[dict]:
        """解析 MD 中「## 输入」/「## 输入规范」段，返回声明的启动参数。

        支持两种写法（生成 MD 时会用到其一）：
          1. 列表：``- name: 说明（string，必填）``
          2. 表格：``| name | string | 是 | 说明 |``

        返回：[{"name","type","required","desc"}]；解析不到返回空列表。
        """
        if not spec_md:
            return []

        lines = spec_md.split("\n")
        body: list[str] = []
        in_section = False
        for line in lines:
            # 兼容 "## 输入" 与 "## 3. 输入规范" 等带编号的标题
            m = re.match(r"^(#{2,3})\s*(?:[\d一二三四五六七八九十]+[.、)．]?\s*)?"
                         r"(输入|输入规范|参数|启动参数)\s*$", line.strip())
            if m:
                in_section = True
                continue
            if in_section:
                # 遇到同级或更高级标题即结束该段
                if re.match(r"^#{1,3}\s+\S", line.strip()):
                    break
                body.append(line)

        inputs: list[dict] = []
        seen: set[str] = set()
        _types = ("string", "str", "int", "integer", "float", "number",
                  "bool", "boolean", "list", "array", "dict", "object")

        def _add(name: str, type_hint: str = "", required_hint: str = "", desc: str = ""):
            name = (name or "").strip().strip("`")
            if not re.match(r"^[A-Za-z_][\w-]*$", name) or name in seen:
                return
            seen.add(name)
            t = ""
            low = (type_hint or "").strip().lower()
            for cand in _types:
                if cand in low:
                    t = "integer" if cand in ("int", "integer") else \
                        "boolean" if cand in ("bool", "boolean") else \
                        "array" if cand in ("list", "array") else \
                        "object" if cand in ("dict", "object") else \
                        "number" if cand == "number" else "string"
                    break
            req = True
            if "可选" in (required_hint or "") or "非必填" in (required_hint or ""):
                req = False
            elif re.search(r"(^|\|)\s*(否|no|false)\s*(\||$)", (required_hint or ""), re.I):
                req = False
            inputs.append({"name": name, "type": t or "string",
                           "required": req, "desc": (desc or "").strip()[:120]})

        for i, raw in enumerate(body):
            line = raw.strip()
            if not line:
                continue
            # 表格行
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 2:
                    continue
                if set(cells[0]) <= set("-: "):      # 分隔行
                    continue
                # 表头行判据：下一行是分隔行（不能按首列文字判断——
                #  参数本身可能就叫 name，按文字过滤会把它误删）
                nxt = next((b.strip() for b in body[i + 1:] if b.strip()), "")
                if re.match(r"^\|[\s:|\-]+\|?$", nxt):
                    continue
                head = cells[0].strip("`")
                if not re.match(r"^[A-Za-z_][\w-]*$", head):
                    continue
                type_hint = cells[1] if len(cells) > 1 else ""
                req_hint = cells[2] if len(cells) > 2 else ""
                desc = cells[3] if len(cells) > 3 else ""
                _add(head, type_hint, req_hint, desc)
                continue
            # 列表行
            m = re.match(r"^[-*]\s*`?([A-Za-z_][\w-]*)`?\s*[:：]\s*(.+)$", line)
            if m:
                name, rest = m.group(1), m.group(2)
                type_hint, req_hint = "", ""
                pm = re.search(r"[（(]([^）)]*)[）)]", rest)
                if pm:
                    bracket = pm.group(1)
                    for part in re.split(r"[,，/、]", bracket):
                        part = part.strip()
                        if any(t == part.lower() or t in part.lower() for t in _types):
                            type_hint += part
                        if "必填" in part or "可选" in part:
                            req_hint += part
                _add(name, type_hint, req_hint, rest)
        return inputs

    @staticmethod
    def _extract_frontmatter_field(spec_md: str, field: str) -> str:
        """从 MD frontmatter 中提取指定字段"""
        m = re.search(rf'^{field}:\s*(.+)', spec_md, re.MULTILINE)
        return m.group(1).strip() if m else ""

    # 引用的宽松归一：去掉「工具」后缀、空白、大小写差异后再比对
    @staticmethod
    def _loose_tool_key(name: str) -> str:
        s = re.sub(r"\s+", "", name or "").lower()
        if s.endswith("工具"):
            s = s[: -len("工具")]
        return s

    @staticmethod
    def _lookup_tool(name: str) -> dict:
        """按「工具显示名」或「工具 id」查工具条目。

        使用平台权威的 ToolRegistry 解析（内置 + 私有两个资源根，私有优先），
        而不是自己按 name 精确匹配 registry.json —— 历史上这里查不到时
        直接把【【显示名】】当成 id 写进 MD 与生成代码。

        解析不到时再做一次宽松匹配：LLM 写规范时常把工具名改写成
        「<名称>工具」并顺手去掉空格（如真实名 `ETF 目标价预测` 被写成
        `ETF目标价预测工具`），精确匹配落空后会把这段假 id 一路带进
        生成代码与运行时目录拼接，最终报「工具不存在」。
        """
        from core.resource.registry.tool_registry import (
            find_tool_entry, iter_tool_entries, resolve_tool_id,
        )

        entry = find_tool_entry(name)
        if entry is None:
            key = AgentCodeBuilder._loose_tool_key(name)
            if key:
                for e in iter_tool_entries():
                    if AgentCodeBuilder._loose_tool_key(
                            str(e.get("name") or "")) == key:
                        entry = e
                        break
        if entry is not None:
            return {"id": str(entry.get("id", "")).strip() or resolve_tool_id(name),
                    "params": entry.get("param_meta", []) or []}
        return {"id": resolve_tool_id(name), "params": []}

    # 只匹配代码块里的 "tool": "xxx" 字段（flow / apis 声明）
    _TOOL_FIELD_RE = re.compile(r'("tool"\s*:\s*")([^"\\]+)(")')

    @staticmethod
    def normalize_tool_refs(spec_md: str) -> str:
        """把 MD 里 `"tool": "<显示名>"` 就地改写为真实工具 id。

        在「MD 写入/产出」边界调用（generate-spec、register），让落库的规范
        文档从一开始就是 id，下游（代码生成、flow 执行、常驻预热）无需再猜。
        """
        if not spec_md or '"tool"' not in spec_md:
            return spec_md
        from core.resource.registry.tool_registry import resolve_tool_id

        def _sub(m):
            resolved = resolve_tool_id(m.group(2))
            return f'{m.group(1)}{resolved}{m.group(3)}'

        return AgentCodeBuilder._TOOL_FIELD_RE.sub(_sub, spec_md)

    @staticmethod
    def _lookup_api(name: str) -> dict:
        api_dir = Path(__file__).resolve().parent.parent.parent / "api"
        registry_file = api_dir / "registry.json"
        try:
            if registry_file.exists():
                for a in json.loads(registry_file.read_text(encoding='utf-8')):
                    if a.get("name") == name:
                        return {"id": a["id"], "params": a.get("input_schema", {})}
        except Exception:
            pass
        return {"id": name, "params": {}}

    @staticmethod
    def _to_class_name(agent_id: str) -> str:
        parts = agent_id.replace("-", " ").replace("_", " ").split()
        return "".join(p.capitalize() for p in parts)

    @staticmethod
    def _safe_ident(s: str) -> str:
        return s.replace("-", "_").replace(".", "_")

    # ══════════════════════════════════════════════════════════
    # 校验
    # ══════════════════════════════════════════════════════════

    async def validate_spec(self, spec: dict) -> bool:
        """校验 MD 规范文档是否包含必需的段落（兼容新旧模板）"""
        md = spec.get("raw_md", "")
        required = ["功能概述", "角色定位", "输入规范", "输出规范"]
        flow_ok = "执行流程" in md or "运行机制" in md
        version_ok = "版本历史" in md
        return all(s in md for s in required) and flow_ok and version_ok

    # ══════════════════════════════════════════════════════════
    # 代码组装（核心）
    # ══════════════════════════════════════════════════════════

    async def build(
        self,
        spec: dict,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> str:
        """组装完整 Agent 代码：模板头部 + 机械生成的辅助方法 + execute 体。

        入口参数与出口事件的形态属于「框架契约」，必须由框架保证，不能交给
        LLM 自由发挥（历史 bug：web-service 生成的代码读 kwargs["content"]，
        而 HTTP 宿主只传 api_name/params，导致所有请求都报「缺少必填参数」）。
        因此按角色分流：

        - web-service：确定性骨架（api_name/params → flow → result 事件）
        - task 且有 flow：确定性骨架（启动参数 → flow → done 事件）
        - 其余（interactive / task 无 flow）：LLM 生成业务逻辑，
          入口参数归一化由框架注入，不依赖 LLM 遵守提示词。

        on_token: 可选回调，每收到一个 LLM 正文 token（execute 体）时调用。
        on_reasoning: 可选回调，每收到一个思考过程 token 时调用。
                  两者均用于前端流式展示；最终完整代码仍由返回值给出。
                  确定性骨架不经 LLM，无 token 可流。
        """
        raw_md = spec.get("raw_md", "")
        agent_id = spec.get("id", self._extract_frontmatter_field(raw_md, "id") or "custom-agent")
        agent_name = spec.get("name", self._extract_frontmatter_field(raw_md, "name") or "Custom Agent")

        # role 以 MD frontmatter 为准：运行时 entrypoint 也是按 MD 解析角色的，
        # 二者必须一致，否则生成的代码与实际运行模式错配。
        role = self._normalize_role(self._extract_role(raw_md) or spec.get("role"))

        tool_refs = self._extract_tool_refs(raw_md)
        api_refs = self._extract_api_refs(raw_md)
        inputs = self._parse_spec_inputs(raw_md)
        flow = _extract_fenced_json(raw_md, "flow") or []
        apis = _extract_fenced_json(raw_md, "apis") or []
        cls_name = self._to_class_name(agent_id)

        # 1. 头部 — 始终正确
        header = HEADER_TEMPLATE.format(agent_name=agent_name, agent_id=agent_id)

        # 2. 工具/API 辅助方法 — 机械生成，保证正确
        helpers = ""
        for tr in tool_refs:
            helpers += TOOL_HELPER_TEMPLATE.format(
                tool_id_safe=self._safe_ident(tr["id"]),
                tool_name=tr["name"],
                tool_id=tr["id"],
            )
        for ar in api_refs:
            helpers += API_HELPER_TEMPLATE.format(
                api_id_safe=self._safe_ident(ar["id"]),
                api_name=ar["name"],
                api_id=ar["id"],
            )

        # 3. execute() 体 — 契约由框架保证
        if role == "web-service":
            execute_body = self._build_web_service_body(apis)
            if on_token is not None and execute_body:
                on_token(execute_body)
        elif role == "task" and isinstance(flow, list) and flow:
            execute_body = self._build_task_body(inputs)
            if on_token is not None and execute_body:
                on_token(execute_body)
        else:
            execute_body = await self._generate_execute_body(
                raw_md=raw_md,
                agent_name=agent_name,
                role=role,
                tool_refs=tool_refs,
                api_refs=api_refs,
                inputs=inputs,
                on_token=on_token,
                on_reasoning=on_reasoning,
            )
            execute_body = self._apply_input_contract(execute_body, role, inputs)

        # 4. 组装
        code = header + CLASS_SKELETON.format(
            cls_name=cls_name,
            description=f"{agent_name} — {agent_id}",
            helper_methods=helpers,
            execute_body=execute_body,
        )

        # 5. 后处理修复
        code = self._fix_common_errors(code)

        return code

    # ══════════════════════════════════════════════════════════
    # 确定性 execute() 体 — 契约由框架保证，不经 LLM
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _required_names(inputs: list[dict]) -> list[str]:
        return [i["name"] for i in (inputs or []) if i.get("required")]

    def _build_task_body(self, inputs: list[dict]) -> str:
        """任务式 execute 体：启动参数 → 按 spec.flow 逐步执行 → done。

        与 MD 声明的 ` {{input.xxx}} ` / ` {{stepN.data.xxx}} ` 引用语法配合，
        启动参数一律按名字从 kwargs 取（不做 content 猜测）。
        每步同时产出 `step` 事件（前端「工作流程」时间线）与 `_log_step` 日志。
        """
        required = json.dumps(self._required_names(inputs), ensure_ascii=False)
        declared = json.dumps([i["name"] for i in inputs], ensure_ascii=False)
        return f'''
        # ── 框架契约：启动参数按 MD「## 输入」声明的名字从 kwargs 获取 ──
        _declared = {declared}
        _missing = [n for n in {required} if not str(kwargs.get(n) or "").strip()]
        if _missing:
            self._log_step("步骤0", "缺少必填参数", {{"missing": _missing}})
            yield {{"event": "error", "data": {{"message": "缺少必填参数: " + ", ".join(_missing)}}}}
            yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
            return

        # 执行作用域：启动参数（input.*）+ 各步骤输出（stepN.*）
        scope: dict = {{"input": dict(kwargs), **kwargs}}
        flow = self.spec.flow or []
        if not flow:
            yield {{"event": "error", "data": {{"message": "该任务式 Agent 未声明执行流程（flow）"}}}}
            yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
            return

        for idx, step in enumerate(flow, start=1):
            tool_id = step.get("tool", "")
            params = self.resolve_refs(step.get("params") or {{}}, scope)
            if isinstance(params, dict):
                # 未取到值的可选参数不下传（避免把 "" 传给 int/float 形参）
                params = {{k: v for k, v in params.items() if v not in (None, "")}}
            yield self._step(f"step{{idx}}", f"调用工具 {{tool_id}}", "running")
            self._log_step(f"步骤{{idx}}", f"调用工具 {{tool_id}}", {{"params": params}})
            try:
                result = await self.call_tool(tool_id, params)
            except Exception as e:
                self._log_step(f"步骤{{idx}}", "工具调用异常", {{"error": str(e)}})
                yield self._step(f"step{{idx}}", "工具调用异常", "error")
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 调用工具失败: {{e}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            status = result.get("status") if isinstance(result, dict) else None
            if status == "failed":
                msg = result.get("message", "未知错误") if isinstance(result, dict) else ""
                self._log_step(f"步骤{{idx}}", "工具执行失败", {{"message": msg}})
                yield self._step(f"step{{idx}}", "工具执行失败", "error")
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 执行失败: {{msg}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            bind = step.get("output_bind") or f"step{{idx}}"
            scope[bind] = result if isinstance(result, dict) else {{"data": result}}
            scope[f"step{{idx}}"] = scope[bind]
            self._log_step(f"步骤{{idx}}", "完成", {{"status": status}})
            yield self._step(f"step{{idx}}", f"{{tool_id}} 完成", "done")
            yield {{"event": "content", "data": {{"text": f"[步骤 {{idx}}/{{len(flow)}}] {{tool_id}} 完成"}}}}

        # 收尾：把末步的可读结果回显（旧实现由 LLM 汇总，这里直接取工具输出，
        # 避免确定性流程丢失最终结论；没有可读文本时不额外输出）
        _last = scope.get(f"step{{len(flow)}}")
        _text = ""
        if isinstance(_last, dict):
            _data = _last.get("data")
            if isinstance(_data, dict):
                _text = str(_data.get("text") or _data.get("message") or "")
            _text = _text or str(_last.get("message") or "")
        if _text:
            yield {{"event": "content", "data": {{"text": _text}}}}

        yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}'''

    def _build_web_service_body(self, apis: list[dict]) -> str:
        """Web 服务式 execute 体：api_name/params → flow → result 事件。

        契约要点（历史 bug 的三处根因都在这）：
          1. 入口只认 api_name / params，绝不读 content；
          2. 必填参数按 MD 的 apis[].params 校验，缺参时给明确提示；
          3. 出口必须是 result 事件，否则 HTTP 宿主拿不到响应体。
        """
        api_names = json.dumps([a.get("name", "") for a in (apis or [])], ensure_ascii=False)
        return f'''
        # ── 框架契约：HTTP 宿主传入 api_name / params，不传 content ──
        api_name = kwargs.get("api_name", "")
        req = kwargs.get("params", {{}}) or {{}}
        if not isinstance(req, dict):
            req = {{}}

        apis = self.spec.apis or []
        api = next((a for a in apis if a.get("name") == api_name), None)
        if api is None and len(apis) == 1 and not api_name:
            api = apis[0]
        if api is None:
            known = {api_names} or [a.get("name", "") for a in apis]
            yield {{"event": "error", "data": {{"message": f"未定义的 API: {{api_name or '(空)'}}；可用: {{known}}"}}}}
            yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
            return

        # 必填参数校验（依据 MD 中该 API 的 params 声明）
        missing = [p.get("name") for p in (api.get("params") or [])
                   if p.get("required") and not str(req.get(p.get("name")) or "").strip()]
        if missing:
            self._log_step("步骤0", "缺少必填参数", {{"api": api_name, "missing": missing}})
            yield {{"event": "error", "data": {{"message": "缺少必填参数: " + ", ".join(missing)}}}}
            yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
            return

        # 执行作用域：请求参数（param.*）+ 各步骤输出（stepN.*）
        scope = {{"param": dict(req), "request": dict(req), **req}}
        flow = api.get("flow") or []
        for idx, step in enumerate(flow, start=1):
            tool_id = step.get("tool", "")
            params = self.resolve_refs(step.get("params") or {{}}, scope)
            if isinstance(params, dict):
                # 未取到值的可选参数不下传（避免把 "" 传给 int/float 形参）
                params = {{k: v for k, v in params.items() if v not in (None, "")}}
            yield self._step(f"step{{idx}}", f"调用工具 {{tool_id}}", "running")
            self._log_step(f"步骤{{idx}}", f"调用工具 {{tool_id}}", {{"params": params}})
            try:
                result = await self.call_tool(tool_id, params)
            except Exception as e:
                self._log_step(f"步骤{{idx}}", "工具调用异常", {{"error": str(e)}})
                yield self._step(f"step{{idx}}", "工具调用异常", "error")
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 调用工具失败: {{e}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            status = result.get("status") if isinstance(result, dict) else None
            if status == "failed":
                msg = result.get("message", "未知错误") if isinstance(result, dict) else ""
                self._log_step(f"步骤{{idx}}", "工具执行失败", {{"message": msg}})
                yield self._step(f"step{{idx}}", "工具执行失败", "error")
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 执行失败: {{msg}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            scope[f"step{{idx}}"] = result if isinstance(result, dict) else {{"data": result}}
            self._log_step(f"步骤{{idx}}", "完成", {{"status": status}})
            yield self._step(f"step{{idx}}", f"{{tool_id}} 完成", "done")

        # 响应体：按 MD 的 apis[].response 声明组装；未声明则回退到末步 data
        response = {{}}
        for item in api.get("response") or []:
            response[item.get("name", "")] = self.resolve_refs(item.get("from", ""), scope)
        if not response:
            last = scope.get(f"step{{max(len(flow), 1)}}")
            if isinstance(last, dict):
                response = last.get("data") if isinstance(last.get("data"), dict) else last

        yield {{"event": "result", "data": {{"response": response}}}}
        yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}'''

    def _apply_input_contract(self, body: str, role: str, inputs: list[dict]) -> str:
        """给「LLM 生成的 execute 体」补上框架级入口参数归一化。

        LLM 只负责业务步骤；参数从哪来由框架注入，避免它擅自读 content。
        同时删除 LLM 自己写的 `content = kwargs.get("content", "").strip()`，
        防止覆盖框架算好的 content。

        interactive 同样必须注入：提示词禁止 LLM 自取 content，若此处直接放行，
        生成的代码一引用 content 就抛 `name 'content' is not defined`（此前
        ETF 目标价预测 Agent 首次生成即稳定复现，只能靠自动调试补回）。
        """
        names = [i["name"] for i in inputs]

        # 删除 LLM 自行获取 content 的赋值语句（含括号 / or "" / .strip() 等写法），
        # 否则它会把框架算好的 content 覆盖成空串
        body = re.sub(
            r"^[ \t]*content\s*=\s*\(?\s*kwargs\.get\(\s*['\"]content['\"]\s*"
            r"(?:,\s*['\"]['\"]?)?\s*\)\s*(?:or\s*['\"]['\"]?)?\s*\)?\s*"
            r"(?:\.\s*strip\(\))?\s*$\n?",
            "",
            body,
            flags=re.MULTILINE,
        )

        if not names:
            # MD 未声明输入：仍要保证 content 有定义（提示词已禁止 LLM 自取）。
            # _declared 标记保留：dry_run 的角色契约校验据此识别「框架注入」。
            return (
                "        # ── 框架注入：入口参数归一化 ──\n"
                "        _declared = []\n"
                "        content = str(kwargs.get(\"content\") or \"\").strip()\n"
            ) + body

        if role == "interactive":
            # 交互式：只保证 content 有定义并做归一化，空输入交给 LLM 生成的
            # 业务分支处理（它会给出面向用户的友好提示，如「请输入 ETF 代码」）。
            # 不能在这里 error+return，否则交互式 Agent 的空输入引导永远走不到。
            return (
                "        # ── 框架注入：用户消息（框架保证 content 必有定义）──\n"
                "        content = str(kwargs.get(\"content\") or \"\").strip()\n"
            ) + body

        prologue = (
            "        # ── 框架注入：入口参数归一化（MD「## 输入」声明）──\n"
            f"        _declared = {json.dumps(names, ensure_ascii=False)}\n"
            "        content = str(kwargs.get(\"content\") or \"\").strip()\n"
            "        if not content:\n"
            "            # 调用方按声明直接传参数（如 name=...）时，拼成上下文交给后续逻辑\n"
            "            content = \"; \".join(\n"
            "                f\"{_n}={kwargs.get(_n)}\" for _n in _declared\n"
            "                if str(kwargs.get(_n) or \"\").strip()\n"
            "            )\n"
            "        if not content:\n"
            "            yield {\"event\": \"error\", \"data\": {\"message\": "
            "\"缺少必填参数: \" + \", \".join(_declared)}}\n"
            "            yield {\"event\": \"done\", \"data\": {\"messageId\": ctx.session_id}}\n"
            "            return\n"
        )
        return prologue + body

    # ══════════════════════════════════════════════════════════
    # LLM 生成 execute() 体
    # ══════════════════════════════════════════════════════════

    async def _generate_execute_body(
        self,
        raw_md: str,
        agent_name: str,
        role: str,
        tool_refs: list[dict],
        api_refs: list[dict],
        inputs: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> str:
        """让 LLM 生成 execute() 方法体（仅函数体内部代码）

        入口参数与出口事件的形态不由 LLM 决定：这里只把「角色契约」写进
        提示词让业务逻辑对齐，实际参数归一化由 `_apply_input_contract`
        在生成后注入，避免提示词不被遵守时再次出现契约错配。

        on_token: 可选回调，每收到一个正文 token 时调用（流式展示用）。
        on_reasoning: 可选回调，每收到一个思考过程 token 时调用。
        """
        inputs = inputs or []

        # 构建可用方法清单
        tool_calls_desc = ""
        for tr in tool_refs:
            safe_id = self._safe_ident(tr["id"])
            tool_calls_desc += f"""
- **{tr['name']}** (`【【{tr['name']}】】`): 调用 `self._call_tool_{safe_id}(**params)` → 返回 `{{"status":"success"|"failed", "message":"...", "data":{{...}}}}`
  参数定义: {json.dumps(tr['params'], ensure_ascii=False)}
"""

        api_calls_desc = ""
        for ar in api_refs:
            safe_id = self._safe_ident(ar["id"])
            api_calls_desc += f"""
- **{ar['name']}** (`【{ar['name']}】`): 调用 `self._call_api_{safe_id}(**params)` → 返回 dict
"""

        inputs_desc = "（MD 未声明输入参数）"
        if inputs:
            inputs_desc = "\n".join(
                f"- `{i['name']}`（{i.get('type', 'string')}，"
                f"{'必填' if i.get('required') else '可选'}）: {i.get('desc', '')}"
                for i in inputs
            )

        prompt = f"""你是一个 Python 代码生成器。生成 Agent 类的 `execute()` 方法体（仅方法内部的代码，不包含方法签名和类定义）。

## Agent 信息
- 名称: {agent_name}
- 角色: {role}

## 入口契约（由框架保证，必须严格遵守）
{ROLE_CONTRACTS.get(self._normalize_role(role), ROLE_CONTRACTS['task'])}

## 本 Agent 声明的输入参数（来自 MD 的「## 输入」段）
{inputs_desc}

## 完整的 MD 规范文档
{raw_md}

## 可用的工具调用（已实现的辅助方法，直接调用即可）
{tool_calls_desc if tool_calls_desc else "（无）"}

## 可用的 API 调用（已实现的辅助方法）
{api_calls_desc if api_calls_desc else "（无）"}

## 代码生成模式（必须遵循）

对于每个工具/API 调用步骤，按以下模式生成代码：

### 模式 A: 首次从用户输入提取参数 → 保存变量 → 调用工具
```python
# Step 1: 从用户输入中一次提取所有变量（只做一次！）
yield self._step("parse", "解析用户输入", "running")
self._log_step("步骤1", "从用户输入提取参数", {{"input": content[:100]}})
extracted = await self._extract_params_with_llm(
    tool_name="提取参数",
    user_input=content,
    param_defs=[
        {{"name": "需求", "type": "string", "required": true, "desc": "图片描述"}},
        {{"name": "数量", "type": "int", "required": true, "desc": "生成数量"}},
        {{"name": "效果", "type": "string", "required": true, "desc": "编辑效果"}},
    ]
)
self._log_step("步骤1", "参数提取完成", {{"extracted": extracted}})
yield self._step("parse", "已解析输入参数", "done")

# Step 2: 用提取的变量直接构造工具参数（不要重新从 content 提取！）
prompt = extracted.get("需求")  # ← 直接用变量
num = extracted.get("数量")
yield self._step("tool", "调用工具处理", "running")
self._log_step("步骤2", "调用工具", {{"prompt": prompt, "num": num}})
result = self._call_tool_xxx(prompt=prompt, num_images=int(num))
self._log_step("步骤2", "工具返回", {{"status": result.get("status")}})
if result.get("status") != "success":
    yield self._step("tool", "工具执行失败", "error")
else:
    yield self._step("tool", "工具执行完成", "done")
```

### 模式 B: 上一步输出 → LLM 提取数据 → 调用下一个工具
```python
# Step N: 用 LLM 从上一步输出中提取下一工具所需参数（仅此场景用 LLM 提取）
yield self._step("step_n", "从上一步结果提取参数", "running")
prev_output = json.dumps(prev_result, ensure_ascii=False)
self._log_step("步骤N", "从上步输出提取参数", {{"output": prev_output[:200]}})
next_params = await self._extract_params_with_llm(
    tool_name="下一个工具名",
    user_input=prev_output,
    param_defs=[{{"name": "image_path", "type": "string", "required": true, "desc": "从上一步输出中提取图片路径"}}]
)
self._log_step("步骤N", "调用工具", {{"params": next_params}})
next_result = self._call_tool_next_tool(**next_params)
self._log_step("步骤N", "工具返回", {{"status": next_result.get("status")}})
yield self._step("step_n", "该步骤完成", "done")
```
【重要】绝对不要用硬编码字段！禁止 `prev_result["data"]["field"]`！
【重要】_extract_params_with_llm 只在两种场景调用: (1)首次从 content 提取 (2)从上一步工具输出提取。其他场景都用已提取的变量。

### 模式 C: 用 LLM 流式生成最终回复（最后一步，必须逐段输出）
```python
yield self._step("reply", "生成回复", "running")
llm = self._get_llm()
prompt_text = f"根据结果生成自然语言回复: {{json.dumps(result, ensure_ascii=False)}}"
full = ""
try:
    async for token in llm.chat_stream(messages=[{{"role": "user", "content": prompt_text}}], max_tokens=100000):
        full += token
        yield {{"event": "delta", "data": {{"text": token}}}}
except Exception:
    full = json.dumps(result, ensure_ascii=False)
    yield {{"event": "delta", "data": {{"text": full}}}}
yield self._step("reply", "回复完成", "done")
```
【强制】最终回复必须用 `llm.chat_stream(...)` 逐 token 下发 `delta` 事件，让前端实时看到生成过程；
【禁止】用 `await llm.chat(...)` 阻塞等全文再一次性 yield content——用户会长时间盯着空白等待。
（`content` 事件仅用于简短的固定提示语，如输入校验提示。）

## 代码要求

1. 入口参数严格按上文「入口契约」获取；**不要**自己写 `content = kwargs.get("content", ...)`（框架会注入归一化逻辑）
2. 按 MD 规范的"执行流程"逐步执行，每步对应一个代码段落
3. **每步执行前/后记录日志**: `self._log_step("步骤名", "操作描述", input_data={{...}}, output_data={{...}})`
4. **每步开始/结束向前端发工作流程事件**: 步骤开始 `yield self._step("步骤id", "人类可读的步骤描述", "running")`，成功 `yield self._step("步骤id", "完成描述", "done")`，失败 `yield self._step("步骤id", "失败描述", "error")`。步骤 id 在同一步骤的 running/done/error 之间保持一致，不同步骤用不同 id（如 parse/tool/reply），描述用中文、面向用户
5. 第一个工具：用 `await self._extract_params_with_llm()` 从用户输入 content 中提取参数，保存到局部变量
6. 后续工具：【重要】用已提取的变量直接构造参数，绝对不要再次对 content 调用 _extract_params_with_llm
7. 仅当需要从上一步工具输出中提取数据时，才再次调用 _extract_params_with_llm，输入为 json.dumps(prev_result)
8. 【禁止】绝对不要硬编码字段: 禁止 result["data"]["xxx"] 或 result.get("data",{{}}).get("xxx") — 始终用 LLM 提取
9. 调用 `self._call_tool_xxx(...)` 或 `self._call_api_xxx(...)`
10. 最后一步用 LLM 将结果转化为自然语言回复：必须按模式 C 用 `llm.chat_stream` 流式 yield `delta`，禁止 `await llm.chat` 一次性输出
11. 错误处理: yield error 事件前先 yield 对应的 _step(..., "error")，再 yield done
12. **必须是合法 Python 字面量**: 布尔值与空值一律写 `True` / `False` / `None`，禁止写 JSON/JS 风格的 `true` / `false` / `null` / `undefined`。这类写法在运行时直接报 `name 'true' is not defined`，且静态语法检查不会报错，只能靠试运行才发现

## 生成规则

- 代码缩进为 8 个空格
- 输入为空时提示用户
- 工具返回 status=="failed" 时返回友好错误
- 【重要】工具和 API 辅助方法是同步的，直接调用即可: `result = self._call_tool_xxx(...)`，不要加 await
- 不要使用 ctx.session、ctx.metadata_id
- 【禁止】绝对不要使用 pip install、subprocess.run 安装依赖包 — 依赖由系统自动管理
- 【路径可移植性·强制】生成的 Agent 必须能在任意目录下不改代码运行（私有区、系统 resources/、其他用户目录、其他机器）：
  - 【禁止】任何绝对路径（/root/…、/home/…、C:… 等），包括写成常量、参数默认值、或从规范文档里抄来的数据集目录
  - 【禁止】用 Path(__file__).parents[N] / .parent.parent 链或 Path.cwd() 猜目录——模板已备好：PROJECT_ROOT（系统根，只读）、USER_HOME（当前用户目录，产物写这里）、out_dir(...) （自动创建并返回 USER_HOME/子目录）
  - 需要落盘（导出文件、中间结果）一律 out_dir("子目录")；执行日志已由 _log_step 统一落 USER_HOME/agent_logs，不要自己拼日志路径
  - 【禁止】往 PROJECT_ROOT、resources/、resources-private/ 里写任何文件——那是 git 管理的系统资产区
  - 工具产物的路径从工具返回结果里读，不要自己按目录规则拼出来
- 直接返回 Python 代码，不要 markdown 代码块"""

        response = ""
        async for kind, token in self.llm.chat_stream_events(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=100000,
        ):
            if kind == "reasoning":
                if on_reasoning is not None:
                    on_reasoning(token)
                continue
            response += token
            if on_token is not None:
                on_token(token)
        body = response.strip()

        # 去除可能的 markdown 代码块
        if "```python" in body:
            body = body.split("```python")[1].split("```")[0]
        elif "```" in body:
            body = body.split("```")[1].split("```")[0]

        # 确保有缩进（8 空格）
        lines = body.split("\n")
        indented = []
        for line in lines:
            stripped = line.rstrip()
            if stripped == "":
                indented.append("")
            elif stripped.startswith("        "):
                indented.append(stripped)
            else:
                indented.append("        " + stripped.lstrip())
        return "\n".join(indented)

    # ══════════════════════════════════════════════════════════
    # 后处理 — 修复常见 LLM 错误
    # ══════════════════════════════════════════════════════════

    def _fix_common_errors(self, code: str) -> str:
        """修复 LLM 生成代码中的常见错误"""
        fixes = [
            # 1. 错误的 import
            (r'^from base_agent import', 'from core.agent.base import'),
            (r'^from agent_base import', 'from core.agent.base import'),
            (r'^import base_agent', 'from core.agent.base import BaseAgent, AgentContext'),
            # 2. __import__ hack
            (r"__import__\('sys'\)\.executable", "sys.executable"),
            (r'__import__\("sys"\)\.executable', "sys.executable"),
            # 3. ctx.get_input 不存在
            (r'ctx\.get_input\(', 'kwargs.get('),
            # 3b. ctx.session 不存在
            (r'ctx\.session\[', 'self._state['),
            (r'ctx\.session\.', 'self._state.'),
            # 3c. ctx.metadata_id 不存在 — 应为 ctx.session_id
            (r'ctx\.metadata_id', 'ctx.session_id'),
            # 3d. await self._call_tool_ — 工具/API 辅助方法是同步的
            (r'await self\._call_tool_', 'self._call_tool_'),
            (r'await self\._call_api_', 'self._call_api_'),
            # 3e. kwargs.get("messageId" — 应为 ctx.session_id
            (r'kwargs\.get\("messageId",?\s*""?\)?', 'ctx.session_id'),
            # 3f. 确保 llm.chat(messages 前有 await (异步方法); 修复重复 await await
            (r'(?<!\bawait )llm\.chat\(messages', 'await llm.chat(messages'),
            (r'await +await +llm\.chat\(', 'await llm.chat('),
            # 4. 删除 LLM 在方法体内重复的 import（仅匹配缩进的，保护头部导入）
            (r'\n        import json\n', '\n'),
            (r'\n        import sys\n', '\n'),
            (r'\n        from pathlib import Path\n', '\n'),
            (r'\n        from typing import AsyncGenerator\n', '\n'),
            (r'\n        from core.agent.base import.*\n', '\n'),
        ]

        for pattern, replacement in fixes:
            code = re.sub(pattern, replacement, code)

        return code

    # ══════════════════════════════════════════════════════════
    # 沙箱测试
    # ══════════════════════════════════════════════════════════

    async def dry_run(self, code: str, role: str = "task") -> dict:
        """沙箱预跑：语法检查 + 接口校验 + 导入检查"""
        results = {"passed": [], "failed": [], "errors": []}

        # 1. 语法检查
        try:
            ast.parse(code)
            results["passed"].append("语法检查通过")
        except SyntaxError as e:
            results["failed"].append(f"语法错误: {e}")
            return results

        # 2. 检查正确导入
        bad_imports = []
        for line in code.split("\n"):
            if "from base_agent" in line or "import base_agent" in line:
                bad_imports.append(line.strip())
        if bad_imports:
            results["failed"].append(f"错误的导入: {bad_imports}")
        else:
            results["passed"].append("导入路径正确")

        # 3. 检查错误方法调用
        if "ctx.get_input" in code:
            results["failed"].append('使用了不存在的 ctx.get_input()，应为 kwargs.get()')
        else:
            results["passed"].append("无错误方法调用")

        # 4. 检查 BaseAgent 继承
        if "BaseAgent" in code:
            results["passed"].append("继承 BaseAgent")
        else:
            results["failed"].append("未继承 BaseAgent")

        # 5. 检查 execute 方法
        if "async def execute" in code:
            results["passed"].append("实现 execute() 方法")
        else:
            results["failed"].append("未实现 execute() 方法")

        # 6. 检查 yield done 是否存在
        if 'yield {"event": "done"' in code or "yield {'event': 'done'" in code:
            results["passed"].append("包含 done 事件")
        else:
            results["failed"].append("缺少 done 事件")

        # 7. 检查 __import__ hack
        if "__import__" in code:
            results["failed"].append("包含 __import__ hack，应使用 import sys; sys.executable")
        else:
            results["passed"].append("无 __import__ hack")

        # 8. 检查工作流程步骤事件注入（对话中展示每步过程与耗时的前提）。
        #    task/interactive 在对话里运行，必须有 _step；web-service 走 HTTP，不强制。
        role_norm = self._normalize_role(role)
        if role_norm != "web-service":
            if "self._step(" in code:
                results["passed"].append("包含工作流程步骤事件 (_step)")
            else:
                results["failed"].append(
                    '缺少工作流程步骤事件：每个步骤必须 yield self._step("步骤id", "描述", "running"/"done"/"error")，'
                    '并用 self._log_step(...) 记录执行日志'
                )

        # 9. 角色契约校验 — 防止再次生成「读 content 的 web-service」
        if role_norm == "web-service":
            if "api_name" in code and "params" in code:
                results["passed"].append("入口契约正确（api_name / params）")
            else:
                results["failed"].append(
                    "入口契约错误：web-service Agent 必须从 kwargs 读取 api_name / params"
                )
            if '"event": "result"' in code or "'event': 'result'" in code:
                results["passed"].append("出口契约正确（result 事件）")
            else:
                results["failed"].append(
                    "出口契约错误：web-service Agent 必须 yield result 事件，否则 HTTP 宿主拿不到响应"
                )
            if 'kwargs.get("content"' in code or "kwargs.get('content'" in code:
                results["failed"].append(
                    "入口契约错误：web-service Agent 不应读取 kwargs['content']（宿主不会传）"
                )
        elif role_norm == "task":
            results["passed"].append(
                "入口参数以 kwargs 为准（声明输入直取）" if ("_declared" in code or "spec.flow" in code)
                else "入口参数以 kwargs 为准"
            )
            if 'kwargs.get("content"' in code and "_declared" not in code:
                results["failed"].append(
                    "入口契约风险：task Agent 未声明输入时仍读 content，"
                    "启动面板只会传声明参数"
                )

        return results

    # ══════════════════════════════════════════════════════════
    # 沙箱试运行（自动调试每轮的真实执行）
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _neutralize_port(spec_md: str) -> str:
        """调试用 spec：把 server 块声明的固定端口强制改为自动分配。

        调试目录每轮重建、进程随起随停，绝不允许占用固定端口
        （尤其是 8200/8201 等本机在运行服务）。
        """
        # 与 base._extract_fenced_json 的解析模式保持一致（大小写不敏感、
        # 闭合围栏允许同行），否则 LLM 生成 ```Server 或同行闭合时本函数
        # 漏改、entrypoint 却照常解析出固定端口——形成绕过。
        def _repl(m: "re.Match[str]") -> str:
            try:
                cfg = json.loads(m.group(2))
            except Exception:
                return m.group(0)
            if not isinstance(cfg, dict):
                return m.group(0)
            try:
                port = float(str(cfg.get("port")).strip())
            except (TypeError, ValueError):
                port = None
            if cfg.get("port") in (None, "") or port == 0:
                return m.group(0)
            cfg["port"] = 0
            return "```server\n" + json.dumps(cfg, ensure_ascii=False) + "\n```"

        return re.sub(r"```(server)\s*\n(.*?)```", _repl, spec_md, flags=re.DOTALL | re.IGNORECASE)

    @staticmethod
    def _http_call(method: str, url: str, payload: dict | None, timeout: float = 60) -> tuple[int, str]:
        """回环 HTTP 调用（stdlib urllib，不依赖 httpx/httpx2）。返回 (状态码, 响应体文本)。"""
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return e.code, body

    @staticmethod
    def _api_test_params(api: dict, test_input: dict) -> dict:
        """从 test_input 中解出某个 API 的请求参数。

        支持三种形态（按优先级）：
        - {"<api名>": {"params": {...}}}  /  {"<api名>": {...}}  —— 按 API 分组
        - {"params": {...}}                        —— 统一参数
        - 扁平 dict（第一个 API 直接使用整个 test_input）
        """
        name = api.get("name") or ""
        if name and name in test_input:
            v = test_input[name]
            if isinstance(v, dict):
                return v.get("params") if isinstance(v.get("params"), dict) else v
        if isinstance(test_input.get("params"), dict):
            return test_input["params"]
        declared = [p.get("name") for p in (api.get("params") or []) if isinstance(p, dict) and p.get("name")]
        if declared and any(k in test_input for k in declared):
            return {k: v for k, v in test_input.items() if k in declared}
        return dict(test_input)

    @staticmethod
    def _debug_round_timeout() -> float:
        """自动调试单轮试运行超时（秒），可通过 .env 的 DEBUG_EXEC_TIMEOUT 调整。

        取值统一走 config.settings，与工具调试、modify-and-debug 共用同一份
        配置与默认值，避免「系统设置」里改大了超时却被本处小值掐断。
        """
        return get_debug_exec_timeout()

    async def _run_agent_once(
        self,
        code: str,
        spec_md: str,
        role: str,
        test_input: dict,
        agent_id: str,
        round_timeout: float = 180,
    ) -> dict:
        """单次沙箱试运行：写私有调试目录 → 起 entrypoint 子进程 → 按角色语义判定成败。

        不复用 agent_factory：factory.start 会对代码做 _auto_fix 改写并写盘，
        且 _processes 会混入 _debug-* 条目。调试循环每轮都要用新代码重启进程，
        factory 的常驻生命周期在此场景没有价值。

        角色语义：
        - interactive / task：stdin 发一条 execute 任务，流式收 stdout 事件直到
          done；出现 error 事件 / 提前退出 / 超时 → 失败。
        - web-service：等 service_ready 拿端口，用回环 HTTP 逐个调用 spec.apis
          声明的 API（端口强制 0 自动分配）；全部 2xx 且无 error 事件 → 成功。
        """
        # 调试目录带随机后缀：同一 Agent 的多个调试会话（多标签页/重复点击）
        # 不再共享目录，避免相互覆盖或 finally 删掉对方正在用的文件。
        debug_id = f"_debug-{agent_id}-{uuid.uuid4().hex[:8]}"
        # 静态安全审查（todo 3.2）：每轮试运行前先审，命中阻断项直接失败，
        # 错误清单经 stderr 回流给 LLM 修复（与 tool_builder.sandbox_execute 同构）。
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

        impl_dir = paths.private_space_dir("agents") / "implementations" / debug_id
        try:
            impl_dir.mkdir(parents=True, exist_ok=True)
            (impl_dir / "agent.py").write_text(code, encoding="utf-8")
            (impl_dir / "spec.md").write_text(self._neutralize_port(spec_md), encoding="utf-8")
        except OSError as e:
            # 建目录/写文件失败是环境问题（权限、磁盘满、只读挂载），
            # 必须单独标记，避免被当成「Agent 代码错误」反复喂给 LLM。
            shutil.rmtree(impl_dir, ignore_errors=True)
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"[debug][env] 调试目录写入失败（环境问题，非代码问题）：{type(e).__name__}: {e}",
                "success": False,
                "infra_error": True,
                # 稳定的错误签名（不含随机调试目录路径），用于判定「连续同错」
                "infra_kind": f"debug_dir_write:{type(e).__name__}",
            }

        events: list[dict] = []
        stderr_chunks: list[str] = []
        proc = None

        async def _collect_stderr() -> None:
            while True:
                chunk = await proc.stderr.read(2048)
                if not chunk:
                    break
                stderr_chunks.append(chunk.decode("utf-8", "replace"))

        def _fail(reason: str, extra_stdout: str = "") -> dict:
            stdout = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
            if extra_stdout:
                stdout = stdout + "\n" + extra_stdout if stdout else extra_stdout
            stderr = "".join(stderr_chunks)
            if reason:
                stderr = (stderr + "\n" if stderr else "") + f"[debug] {reason}"
            if not stderr.strip() and not events and not extra_stdout:
                stderr = "[debug] Agent 子进程无任何输出即退出"
            return {"exit_code": 1, "stdout": stdout, "stderr": stderr, "success": False}

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(paths.PROJECT_ROOT / "core" / "agent" / "entrypoint.py"),
                "--agent-id", debug_id,
                "--impl-path", str(impl_dir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(paths.PROJECT_ROOT),
            )
            stderr_task = asyncio.create_task(_collect_stderr())

            async def _next_event(wait: float) -> dict | None:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=wait)
                if not line:
                    return None
                line = line.strip()
                if not line:
                    return {}
                try:
                    return json.loads(line.decode("utf-8", "replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {}

            async def _read_until_done(first: float, total_deadline: float) -> bool:
                """持续读事件直到 done 或 error；EOF 返回 False。

                error 视为终止事件：entrypoint 对 execute 抛出的异常只发
                error 不发 done，若只等 done 会白等满超时。
                """
                nonlocal events
                wait = first
                while True:
                    remaining = total_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    ev = await _next_event(min(wait, remaining))
                    wait = round_timeout
                    if ev is None:
                        return False
                    if ev:
                        events.append(ev)
                        if ev.get("event") in ("done", "error"):
                            return True

            total_deadline = asyncio.get_running_loop().time() + round_timeout

            if role == "web-service":
                # 等 service_ready（加载失败时 entrypoint 会先发 error+done）
                service_info = None
                while True:
                    remaining = total_deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    ev = await _next_event(min(30, remaining))
                    if ev is None:
                        break
                    if not ev:
                        continue
                    events.append(ev)
                    if ev.get("event") == "service_ready":
                        service_info = ev.get("data") or {}
                        break
                    if ev.get("event") == "error":
                        break
                if service_info is None:
                    return _fail("Web 服务未就绪（未收到 service_ready）")

                port = service_info.get("port")
                from core.agent.base import AgentSpec
                _spec = AgentSpec(id=debug_id, name=debug_id)
                _spec.raw_md = self._neutralize_port(spec_md)
                try:
                    _spec.parse_structured_fields()
                except Exception:
                    pass
                api_specs = _spec.apis or []
                if not api_specs:
                    return _fail("spec 未声明任何 apis，无法进行 HTTP 验证")
                bodies = []
                for api in api_specs:
                    method = (api.get("method") or "POST").upper()
                    path = api.get("path") or "/"
                    params = self._api_test_params(api, test_input)
                    url = f"http://127.0.0.1:{port}{path}"
                    try:
                        status, body = await asyncio.to_thread(
                            self._http_call, method, url,
                            params if method != "GET" else None, 30)
                    except Exception as e:
                        return _fail(f"API {api.get('name')} 调用异常: {type(e).__name__}: {e}")
                    bodies.append(f"### API {api.get('name')} {method} {path} → {status}\n{body[:2000]}")
                    if status >= 300:
                        events.append({"event": "error",
                                       "data": {"message": f"API {api.get('name')} 返回 {status}"}})
                        events.append({"event": "done", "data": {}})
                        # 已成功的 API 结果 + 本次 5xx 响应体一并回流，供 LLM 定位
                        return _fail("", "\n".join(bodies))
                # 全部 API 通过 → 通知子进程优雅退出并执行 on_stop 清理
                try:
                    proc.stdin.write((json.dumps({"action": "stop"}) + "\n").encode())
                    await proc.stdin.drain()
                    proc.stdin.close()
                except Exception:
                    pass
                stdout = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
                stdout += "\n" + "\n".join(bodies)
                return {"exit_code": 0, "stdout": stdout,
                        "stderr": "".join(stderr_chunks), "success": True}

            # interactive / task：发送一条 execute 任务并读到 done
            task = {
                "action": "execute",
                "context": {"session_id": "debug", "user_id": "debug"},
                "params": dict(test_input or {"content": ""}),
            }
            proc.stdin.write((json.dumps(task, ensure_ascii=False) + "\n").encode())
            await proc.stdin.drain()

            got_done = await _read_until_done(60, total_deadline)
            err_msg = next(
                ((e.get("data") or {}).get("message", "") for e in events if e.get("event") == "error"),
                None,
            )
            if err_msg is not None:
                return _fail(f"Agent 执行抛出 error 事件: {err_msg}")
            if not got_done:
                return _fail("未收到 done 事件（进程提前退出或输出中断）")
            return {"exit_code": 0,
                    "stdout": "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
                    "stderr": "".join(stderr_chunks), "success": True}

        except asyncio.TimeoutError:
            return _fail(f"Agent 试运行超时（{int(round_timeout)}秒）")
        except Exception as e:
            return _fail(f"沙箱执行异常: {type(e).__name__}: {e}")
        finally:
            if proc is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                if proc.returncode is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                try:
                    await asyncio.wait_for(stderr_task, timeout=2)
                except Exception:
                    stderr_task.cancel()
            shutil.rmtree(impl_dir, ignore_errors=True)

    # ══════════════════════════════════════════════════════════
    # 自动调试（todo 4.3）— 试运行 → LLM 分析 → 改代码 → 再试运行
    # 循环骨架移植自 tool_builder.auto_debug_stream；差异：
    # - 无依赖安装环节（Agent 运行于项目 venv，缺模块时反馈引导 LLM 换方案）
    # - 每轮执行是完整 entrypoint 子进程（代码每轮都在变，无常驻生命周期可复用）
    # - 静态安全审查 + 辅助方法防丢失检查在每轮执行前生效
    # ══════════════════════════════════════════════════════════

    def _restore_helpers(self, code: str, spec_md: str) -> tuple[str, list[str]]:
        """LLM 整文件重写时可能丢掉模板机械生成的工具/API 辅助方法。

        辅助方法本就从 spec 确定性生成，缺失时按模板重新注入类体，
        返回 (修复后的代码, 注入说明列表)。
        """
        restored: list[str] = []
        missing = []
        for tr in self._extract_tool_refs(spec_md):
            if f"def _call_tool_{self._safe_ident(tr['id'])}(" not in code:
                missing.append(("tool", tr))
        for ar in self._extract_api_refs(spec_md):
            if f"def _call_api_{self._safe_ident(ar['id'])}(" not in code:
                missing.append(("api", ar))
        if not missing:
            return code, restored

        helpers = ""
        for kind, ref in missing:
            if kind == "tool":
                helpers += TOOL_HELPER_TEMPLATE.format(
                    tool_id_safe=self._safe_ident(ref["id"]),
                    tool_name=ref["name"], tool_id=ref["id"])
            else:
                helpers += API_HELPER_TEMPLATE.format(
                    api_id_safe=self._safe_ident(ref["id"]),
                    api_name=ref["name"], api_id=ref["id"])
            restored.append(ref["name"])

        # 注入到类体：定位第一个 "    async def execute" 之前
        m = re.search(r"^    async def execute", code, re.MULTILINE)
        if m:
            code = code[:m.start()] + helpers + "\n" + code[m.start():]
        return code, restored

    async def _write_debug_log(self, agent_id: str, timestamp: str, entries: list[dict],
                               success: bool, final_round: int):
        """将调试日志写入 logs/ 目录（脱敏后落盘，与 tool_builder 同构）"""
        from core.security.secrets import scrub_text as _scrub

        def _write():
            try:
                from core.user_paths import user_home
                log_dir = user_home() / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"agent_{agent_id}_{timestamp}.md"

                lines = ["# Agent 自动调试日志\n",
                         f"- **Agent**: {agent_id}",
                         f"- **时间**: {timestamp}",
                         f"- **结果**: {'成功' if success else '失败'}（共 {final_round} 轮）",
                         f"- **日志条目**: {len(entries)} 轮\n", "---\n"]
                for i, entry in enumerate(entries):
                    lines.append(f"## 第 {entry['round']} 轮\n")
                    lines.append("### 试运行结果\n")
                    lines.append(f"```\n{_scrub(entry['exec_result'])}\n```\n")
                    if entry.get("gate_feedback"):
                        lines.append(f"### 附加反馈\n{entry['gate_feedback']}\n")
                    lines.append("### 发送给 LLM 的 Prompt\n")
                    lines.append(f"```\n{_scrub(entry['prompt'])}\n```\n")
                    lines.append("### LLM 返回\n")
                    lines.append(f"```\n{_scrub(entry['response'])}\n```\n")
                    if i < len(entries) - 1:
                        lines.append("======================\n")
                log_file.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                pass

        await asyncio.to_thread(_write)

    async def auto_debug_stream(self, spec_md: str, code: str, test_input: dict,
                                agent_id: str, role: str = "task",
                                max_rounds: int = None, stop_event=None):
        """自动调试生成器：试运行→LLM分析→改代码→再试运行。

        事件名与工具自动调试一致（debug_start/round_start/exec_result/
        thinking/thinking_stream/analysis_result/llm_error/code_updated/
        stopped/done），前端无需新事件类型。无 deps_* 事件。
        max_rounds 为 None 时读取 .env 的 DEBUG_MAX_ROUNDS（默认 10）。
        """
        if max_rounds is None:
            max_rounds = get_debug_max_rounds()
        set_debug_running(agent_id)
        try:
            try:
                async for event in self._auto_debug_loop(
                    spec_md, code, test_input, agent_id, role, max_rounds, stop_event
                ):
                    yield event
            except asyncio.CancelledError:
                if stop_event:
                    stop_event.set()
                yield {"event": "stopped", "round": 0, "message": "调试已停止"}
        finally:
            set_debug_stopped(agent_id)

    async def _auto_debug_loop(self, spec_md: str, code: str, test_input: dict,
                               agent_id: str, role: str, max_rounds: int, stop_event):
        current_code = code
        debug_log_entries: list[dict] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 跨轮修复对话：user=失败上下文, assistant=LLM 修复回复。
        # 只保留首条全量 Prompt + 最近 _DEBUG_HISTORY_KEEP 条，防止逐轮膨胀。
        debug_messages: list[dict] = []
        _DEBUG_HISTORY_KEEP = 12

        yield {"event": "debug_start", "message": "Agent 自动调试启动",
               "max_rounds": max_rounds, "role": role}

        _last_infra_kind = ""
        infra_repeat = 0

        for round_num in range(1, max_rounds + 1):
            if not is_debug_running(agent_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "round_start", "round": round_num, "max": max_rounds}

            # ── ① 试运行（事件驱动等待：执行完成与停止信号同时挂起）──
            exec_task = asyncio.create_task(
                self._run_agent_once(
                    current_code, spec_md, role, test_input, agent_id,
                    round_timeout=self._debug_round_timeout(),
                )
            )
            stop_waiters = set()
            if stop_event:
                _sw = asyncio.create_task(stop_event.wait())
                stop_waiters.add(_sw)
            done, _pending = await asyncio.wait(
                {exec_task} | stop_waiters, timeout=None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if exec_task in done:
                for w in stop_waiters:
                    w.cancel()
                try:
                    exec_result = exec_task.result()
                except Exception as e:
                    exec_result = {"exit_code": 1, "stdout": "",
                                   "stderr": f"试运行内部异常: {type(e).__name__}: {e}",
                                   "success": False}
            else:
                exec_task.cancel()
                try:
                    await asyncio.wait_for(exec_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                yield {"event": "stopped", "round": round_num,
                       "message": "用户手动停止调试（试运行已中断）"}
                return

            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            success = bool(exec_result.get("success"))
            yield {"event": "exec_result", "round": round_num,
                   "stdout": exec_result.get("stdout", ""),
                   "stderr": exec_result.get("stderr", ""), "success": success}

            # ── 环境错误不喂给 LLM：PermissionError/磁盘满等是运行环境问题，
            # 代码本身没有错；连续两轮同样错误即短路，避免 LLM 误删正常代码。──
            if exec_result.get("infra_error"):
                infra_msg = (exec_result.get("stderr") or "").strip()
                infra_kind = exec_result.get("infra_kind") or infra_msg
                if infra_kind == _last_infra_kind:
                    infra_repeat += 1
                else:
                    _last_infra_kind, infra_repeat = infra_kind, 1
                yield {"event": "llm_error", "round": round_num,
                       "message": f"调试环境异常（非代码问题，跳过 LLM 改码）：{infra_msg}"}
                if infra_repeat >= 2:
                    await self._write_debug_log(agent_id, timestamp, debug_log_entries,
                                                success=False, final_round=round_num)
                    yield {"event": "stopped", "round": round_num,
                           "message": "调试环境异常连续出现，已停止自动调试"}
                    return
                continue

            if success:
                yield {"event": "done", "round": round_num, "success": True,
                       "code": current_code, "message": f"调试成功 (第{round_num}轮)"}
                await self._write_debug_log(agent_id, timestamp, debug_log_entries,
                                            success=True, final_round=round_num)
                return

            if round_num >= max_rounds:
                break

            if not is_debug_running(agent_id):
                yield {"event": "stopped", "round": round_num, "message": "调试已停止"}
                return
            if stop_event and stop_event.is_set():
                yield {"event": "stopped", "round": round_num, "message": "用户手动停止调试"}
                return

            yield {"event": "thinking", "round": round_num, "message": "LLM 分析错误..."}

            # ── ② LLM 分析并修复代码 ──
            # 脱敏：以下内容会随 Prompt 发给第三方模型服务商，密钥混入即泄露。
            from core.security.secrets import redact_for_prompt as _redact, scrub_mapping as _scrub_mapping

            _safe_test_input = json.dumps(_scrub_mapping(test_input), ensure_ascii=False, indent=2)
            _safe_stdout_full = _redact(exec_result.get("stdout", ""), max_len=3000)
            _safe_stderr_full = _redact(exec_result.get("stderr", ""), max_len=2000)
            _safe_stdout = _redact(exec_result.get("stdout", ""), max_len=2000)
            _safe_stderr = _redact(exec_result.get("stderr", ""), max_len=1000)
            exec_summary = f"stdout:\n{_safe_stdout_full}\n\nstderr:\n{_safe_stderr_full}"

            role_iface = (
                "execute(self, ctx: AgentContext, api_name: str = None, params: dict = None) 为异步生成器，"
                "按 api_name 分派，结果用 yield {\"event\": \"result\", \"data\": {\"response\": {...}}} 返回"
                if role == "web-service" else
                "execute(self, ctx: AgentContext, **kwargs) 为异步生成器，"
                "content = kwargs.get(\"content\", \"\") 取输入，简短提示用 yield {\"event\": \"content\", \"data\": {\"text\": ...}}，"
                "最终自然语言回复用 async for token in self._get_llm().chat_stream(...) 逐段 yield {\"event\": \"delta\", \"data\": {\"text\": token}}"
            )

            if debug_messages:
                fix_prompt = f"""上一版 Agent 代码试运行仍失败。请基于你上一轮回复中的代码继续修复（不要推翻重来，除非错误明确指向方案性缺陷）。

=== 试运行结果 ===
stdout: {_safe_stdout}
stderr: {_safe_stderr}
===  END 结果 ===

仍按 <analysis>/<code> 格式输出。要求：<code> 内是完整 Python 文件（含模板头部与全部辅助方法）。
【重要】必须完整保留原有的 self._step(...) 工作流程步骤事件与 self._log_step(...) 执行日志调用（每步 running/done/error 成对），修复不得删减它们。"""
            else:
                fix_prompt = f"""Debug this Agent code. It failed the sandbox test run.

=== CURRENT CODE ===
{current_code}
=== END CODE ===

=== AGENT 类型 ===
role: {role}

=== MD 规范文档 ===
{spec_md}
=== END 规范 ===

=== TEST INPUT ===
{_safe_test_input}
=== END INPUT ===

=== 试运行结果 ===
stdout: {_safe_stdout}
stderr: {_safe_stderr}
=== END 结果 ===

请分两部分输出，严格使用以下格式：

<analysis>
问题原因：（简要说明代码为什么失败，1-3 句话）
修改计划：（列出打算如何修改，逐条说明）
</analysis>

<code>
（完整修复后的 Python 代码，含模板头部与全部辅助方法）
</code>

要求：
1. <analysis> 和 <code> 标签必须完整、成对出现；<analysis> 中文、不超过 5 行。
2. <code> 内只放完整 Python 代码，保留头部 import（import json / import sys / from pathlib import Path）与全部 _call_tool_* / _call_api_* 辅助方法。
3. INTERFACE RULES: 类必须继承 BaseAgent；{role_iface}；结束时 yield {{"event": "done", "data": {{}}}}。
4. _call_tool_* / _call_api_* 是同步方法，直接调用，不要 await；llm.chat 是异步方法，必须 await。
5. 不要使用 ctx.get_input()、ctx.session、ctx.metadata_id（不存在）；多轮状态存 self._state。
6. 依赖由项目环境管理：ModuleNotFoundError 时改用标准库或已安装模块，禁止 pip install / subprocess 安装依赖。
7. 禁止 shell=True、eval、删除类原语（静态安全审查会阻断执行）。
8. 调用大模型（self._get_llm().chat / _extract_params_with_llm）时 max_tokens 用大值（100000），不要 4096/1024。
9. 【重要】必须完整保留 self._step(...) 工作流程步骤事件与 self._log_step(...) 执行日志调用（每步 running/done/error 成对），修复不得删减它们。
10. 【重要】最终自然语言回复必须保持流式：`async for token in self._get_llm().chat_stream(...)` 内逐 token `yield {{"event": "delta", "data": {{"text": token}}}}`，禁止改回 `await llm.chat(...)` + 一次性 yield content。"""

            log_entry = {"round": round_num, "exec_result": exec_summary,
                         "gate_feedback": "", "prompt": fix_prompt, "response": ""}

            full_response = ""
            llm_task = None
            llm_exception = None
            get_task = None
            LLM_TIMEOUT = int(_os.getenv("LLM_TIMEOUT", "1200"))
            try:
                # 事件驱动泵：token 经 asyncio.Queue 直达消费侧，与停止信号、
                # 整体超时三方竞态（与 tool_builder 调试循环同构）。
                _SENTINEL = object()
                token_q: asyncio.Queue = asyncio.Queue()
                loop = asyncio.get_running_loop()

                async def _collect_tokens():
                    nonlocal llm_exception
                    try:
                        # reasoning 和 content 都推到队列：reasoning 仅用于前端展示
                        # 「分析中…」，full_response 只累计 content 供后续解析。
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
                get_task = asyncio.create_task(token_q.get())
                while True:
                    if not is_debug_running(agent_id):
                        stopped_msg = "调试已停止"
                        break
                    if stop_event and stop_event.is_set():
                        stopped_msg = "用户手动停止调试（LLM对话已中断）"
                        break
                    wait_futs = {get_task}
                    if not llm_task.done():
                        wait_futs.add(llm_task)
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
                    if not done:
                        timed_out = True
                        break
                    if get_task in done:
                        item = get_task.result()
                        if item is _SENTINEL:
                            break
                        kind, token = item
                        if kind == "content":
                            full_response += token
                        # reasoning 与 content 都推到前端分析区，思考阶段即可见
                        yield {"event": "thinking_stream", "round": round_num, "token": token}
                        get_task = asyncio.create_task(token_q.get())

                if timed_out:
                    llm_task.cancel()
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass
                    get_task.cancel()
                    yield {"event": "llm_error", "round": round_num,
                           "message": f"LLM调用超时（{LLM_TIMEOUT}秒），跳过本轮"}
                    continue
                if stopped_msg is not None:
                    llm_task.cancel()
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass
                    get_task.cancel()
                    yield {"event": "stopped", "round": round_num, "message": stopped_msg}
                    return

                if llm_task.cancelled():
                    continue
                if llm_exception is not None:
                    yield {"event": "llm_error", "round": round_num,
                           "message": f"LLM调用异常: {str(llm_exception)[:300]}"}
                    if not full_response.strip():
                        continue

            except asyncio.CancelledError:
                if llm_task and not llm_task.done():
                    llm_task.cancel()
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass
                if get_task is not None and not get_task.done():
                    get_task.cancel()
                raise
            except Exception as e:
                yield {"event": "llm_error", "round": round_num,
                       "message": f"LLM调用异常: {str(e)[:200]}"}
                continue

            # ── ③ 解析 LLM 响应：切分「分析」与「代码」──
            log_entry["response"] = full_response
            debug_log_entries.append(log_entry)

            analysis_text = ""
            code_text = ""
            am = re.search(r"<analysis>(.*?)</analysis>", full_response, re.DOTALL)
            if am:
                analysis_text = am.group(1).strip()
            cm = re.search(r"<code>(.*?)</code>", full_response, re.DOTALL)
            if cm:
                code_text = cm.group(1).strip()
            elif am:
                code_text = full_response[am.end():].strip()
            else:
                code_text = full_response.strip()

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

            if analysis_text:
                yield {"event": "analysis_result", "round": round_num, "analysis": analysis_text}

            if len(code_text) < 100:
                llm_raw = (analysis_text or full_response.strip())[:2000] or "(空)"
                yield {"event": "thinking", "round": round_num,
                       "message": f"LLM 返回代码过短（{len(code_text)} 字符），跳过本轮。LLM 实际返回: {llm_raw}"}
                continue

            # 辅助方法防丢失：LLM 整文件重写可能删掉模板生成的 _call_tool_*，
            # 这些方法本就从 spec 确定性生成，缺失时按模板重新注入。
            code_text, restored = self._restore_helpers(code_text, spec_md)
            if restored:
                note = f"[系统提示] 上一轮回复丢失辅助方法已自动补回: {', '.join(restored)}"
                log_entry["gate_feedback"] = note
                yield {"event": "thinking", "round": round_num, "message": note}

            current_code = code_text
            debug_messages.append({"role": "user", "content": fix_prompt})
            debug_messages.append({"role": "assistant", "content": full_response})
            if len(debug_messages) > _DEBUG_HISTORY_KEEP + 1:
                debug_messages[:] = [debug_messages[0]] + debug_messages[-_DEBUG_HISTORY_KEEP:]
            yield {"event": "code_updated", "round": round_num, "code": current_code}

        yield {"event": "done", "round": max_rounds, "success": False,
               "code": current_code, "message": f"达到最大调试轮数（{max_rounds}），仍未通过"}
        await self._write_debug_log(agent_id, timestamp, debug_log_entries,
                                    success=False, final_round=max_rounds)
