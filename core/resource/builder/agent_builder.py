"""Agent 代码生成器 — 从 MD 规范文档生成 Agent Python 代码

架构：模板组装 + LLM 填充执行逻辑 + 后处理修复。
工具/API 辅助方法由模板机械生成（保证正确），execute() 体由 LLM 生成。
"""

import ast
import json
import re
from pathlib import Path

from core.resource.builder.builder_base import BaseBuilder
from core.llm.client import create_llm_client
from core.agent.base import _extract_fenced_json

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

# 项目根目录（位置无关：优先从 sys.path 反查）
def _detect_project_root() -> Path:
    """定位项目根。

    Agent 子进程由 core/agent/entrypoint.py 启动，后者已把真实项目根插入
    sys.path，因此优先从 sys.path 反查 —— 不依赖资源根位于项目内还是项目同级
    （私有资源根默认位于项目根的同级目录，用固定层数推导会偏一级，导致
    本文件下方按 PROJECT_ROOT 扫描「内置 + 私有」双根时找错目录）。

    sys.path 未命中时，再从文件位置向上查找：含 core/ 与 app/ 的目录即为项目根。
    """
    for _p in list(sys.path):
        try:
            _c = Path(_p).resolve()
        except Exception:
            continue
        if (_c / "core").is_dir() and (_c / "app").is_dir():
            return _c
    _start = Path(__file__).resolve().parent
    for _c in [_start, *_start.parents]:
        if (_c / "core").is_dir() and (_c / "app").is_dir():
            return _c
    return _start.parent.parent.parent.parent  # 兜底：内置布局


PROJECT_ROOT = _detect_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext
'''

# ═══════════════════════════════════════════════════════════════════
# 工具辅助方法模板 — 机械生成，不依赖 LLM
# ═══════════════════════════════════════════════════════════════════

TOOL_HELPER_TEMPLATE = '''
    def _call_tool_{tool_id_safe}(self, **params) -> dict:
        """调用工具: {tool_name} (tool_id: {tool_id})"""
        # 在「内置 + 私有」资源根中定位工具实现
        _tool_base = None
        for _root in (PROJECT_ROOT / "resources", PROJECT_ROOT.parent / "resources-private"):
            _cand = _root / "tools" / "implementations" / "{tool_id}"
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
            f"result = execute(**{{_safe_dumps(params)}})\\n"
            f"print(json.dumps(result, default=str))\\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True,
                env={{**os.environ, "TOOL_DIR": str(impl_path.parent), "_PROJECT_ROOT": str(PROJECT_ROOT)}},
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {{"status": "failed", "message": proc.stderr[:300]}}
        finally:
            os.unlink(tmp_path)
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
        """记录执行步骤日志到 resources/agents/logs/{{agent_id}}-{{timestamp}}.md"""
        if not hasattr(self, '_log_file'):
            log_dir = PROJECT_ROOT / "resources" / "agents" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
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
        "出口：`content`（文本）/ `card`（结构化卡片）/ `done` / `error` 事件。"
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
        self.llm = llm_client or create_llm_client()

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

    @staticmethod
    def _lookup_tool(name: str) -> dict:
        """按「工具显示名」或「工具 id」查工具条目。

        使用平台权威的 ToolRegistry（内置 + 私有两个资源根），而不是自己
        去读内置 registry.json —— 历史上这里只读内置根，导致 UI 新建的
        私有工具查不到，进而把【【显示名】】当成 id 写进 MD 与生成代码。
        """
        from core.resource.registry.tool_registry import find_tool_entry, resolve_tool_id

        entry = find_tool_entry(name)
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

    async def build(self, spec: dict) -> str:
        """组装完整 Agent 代码：模板头部 + 机械生成的辅助方法 + execute 体。

        入口参数与出口事件的形态属于「框架契约」，必须由框架保证，不能交给
        LLM 自由发挥（历史 bug：web-service 生成的代码读 kwargs["content"]，
        而 HTTP 宿主只传 api_name/params，导致所有请求都报「缺少必填参数」）。
        因此按角色分流：

        - web-service：确定性骨架（api_name/params → flow → result 事件）
        - task 且有 flow：确定性骨架（启动参数 → flow → done 事件）
        - 其余（interactive / task 无 flow）：LLM 生成业务逻辑，
          入口参数归一化由框架注入，不依赖 LLM 遵守提示词。
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
        elif role == "task" and isinstance(flow, list) and flow:
            execute_body = self._build_task_body(inputs)
        else:
            execute_body = await self._generate_execute_body(
                raw_md=raw_md,
                agent_name=agent_name,
                role=role,
                tool_refs=tool_refs,
                api_refs=api_refs,
                inputs=inputs,
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
            self._log_step(f"步骤{{idx}}", f"调用工具 {{tool_id}}", {{"params": params}})
            try:
                result = await self.call_tool(tool_id, params)
            except Exception as e:
                self._log_step(f"步骤{{idx}}", "工具调用异常", {{"error": str(e)}})
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 调用工具失败: {{e}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            status = result.get("status") if isinstance(result, dict) else None
            if status == "failed":
                msg = result.get("message", "未知错误") if isinstance(result, dict) else ""
                self._log_step(f"步骤{{idx}}", "工具执行失败", {{"message": msg}})
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 执行失败: {{msg}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            bind = step.get("output_bind") or f"step{{idx}}"
            scope[bind] = result if isinstance(result, dict) else {{"data": result}}
            scope[f"step{{idx}}"] = scope[bind]
            self._log_step(f"步骤{{idx}}", "完成", {{"status": status}})
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
            self._log_step(f"步骤{{idx}}", f"调用工具 {{tool_id}}", {{"params": params}})
            try:
                result = await self.call_tool(tool_id, params)
            except Exception as e:
                self._log_step(f"步骤{{idx}}", "工具调用异常", {{"error": str(e)}})
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 调用工具失败: {{e}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            status = result.get("status") if isinstance(result, dict) else None
            if status == "failed":
                msg = result.get("message", "未知错误") if isinstance(result, dict) else ""
                self._log_step(f"步骤{{idx}}", "工具执行失败", {{"message": msg}})
                yield {{"event": "error", "data": {{"message": f"步骤 {{idx}} 执行失败: {{msg}}"}}}}
                yield {{"event": "done", "data": {{"messageId": ctx.session_id}}}}
                return

            scope[f"step{{idx}}"] = result if isinstance(result, dict) else {{"data": result}}
            self._log_step(f"步骤{{idx}}", "完成", {{"status": status}})

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
        """
        if role == "interactive":
            return body

        names = [i["name"] for i in inputs]
        if not names:
            return body

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
    ) -> str:
        """让 LLM 生成 execute() 方法体（仅函数体内部代码）。

        入口参数与出口事件的形态不由 LLM 决定：这里只把「角色契约」写进
        提示词让业务逻辑对齐，实际参数归一化由 `_apply_input_contract`
        在生成后注入，避免提示词不被遵守时再次出现契约错配。
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

# Step 2: 用提取的变量直接构造工具参数（不要重新从 content 提取！）
prompt = extracted.get("需求")  # ← 直接用变量
num = extracted.get("数量")
self._log_step("步骤2", "调用工具", {{"prompt": prompt, "num": num}})
result = self._call_tool_xxx(prompt=prompt, num_images=int(num))
self._log_step("步骤2", "工具返回", {{"status": result.get("status")}})
```

### 模式 B: 上一步输出 → LLM 提取数据 → 调用下一个工具
```python
# Step N: 用 LLM 从上一步输出中提取下一工具所需参数（仅此场景用 LLM 提取）
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
```
【重要】绝对不要用硬编码字段！禁止 `prev_result["data"]["field"]`！
【重要】_extract_params_with_llm 只在两种场景调用: (1)首次从 content 提取 (2)从上一步工具输出提取。其他场景都用已提取的变量。

### 模式 C: 用 LLM 生成最终回复（最后一步）
```python
llm = self._get_llm()
prompt_text = f"根据结果生成自然语言回复: {{json.dumps(result, ensure_ascii=False)}}"
response = await llm.chat(messages=[{{"role": "user", "content": prompt_text}}])
yield {{"event": "content", "data": {{"text": response}}}}
```

## 代码要求

1. 入口参数严格按上文「入口契约」获取；**不要**自己写 `content = kwargs.get("content", ...)`（框架会注入归一化逻辑）
2. 按 MD 规范的"执行流程"逐步执行，每步对应一个代码段落
3. **每步执行前/后记录日志**: `self._log_step("步骤名", "操作描述", input_data={{...}}, output_data={{...}})`
4. 第一个工具：用 `await self._extract_params_with_llm()` 从 `content` 中提取参数，保存到局部变量（content 已由框架准备好）
5. 后续工具：【重要】用已提取的变量直接构造参数，绝对不要再次对 content 调用 _extract_params_with_llm
6. 仅当需要从上一步工具输出中提取数据时，才再次调用 _extract_params_with_llm，输入为 json.dumps(prev_result)
6. 【禁止】绝对不要硬编码字段: 禁止 result["data"]["xxx"] 或 result.get("data",{{}}).get("xxx") — 始终用 LLM 提取
7. 调用 `self._call_tool_xxx(...)` 或 `self._call_api_xxx(...)`
8. 最后一步用 LLM 将结果转化为自然语言回复
9. 错误处理: yield error 后 yield done

## 生成规则

- 代码缩进为 8 个空格
- 输入为空时提示用户
- 工具返回 status=="failed" 时返回友好错误
- 【重要】工具和 API 辅助方法是同步的，直接调用即可: `result = self._call_tool_xxx(...)`，不要加 await
- 不要使用 ctx.session、ctx.metadata_id
- 【禁止】绝对不要使用 pip install、subprocess.run 安装依赖包 — 依赖由系统自动管理
- 直接返回 Python 代码，不要 markdown 代码块"""

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=100000,
        )
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

    async def dry_run(self, code: str, role: str | None = None) -> dict:
        """沙箱预跑：语法检查 + 接口校验 + 角色契约校验 + 导入检查。

        Args:
            code: 待校验的 Agent 代码
            role: Agent 角色；给定时额外校验入口/出口契约是否与角色一致
        """
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

        # 8. 角色契约校验 — 防止再次生成「读 content 的 web-service」
        role_norm = self._normalize_role(role) if role else ""
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
