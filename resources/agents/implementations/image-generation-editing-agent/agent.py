"""Agent: 图片生成与编辑Agent — image-generation-editing-agent"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time as _time
from pathlib import Path
from typing import AsyncGenerator

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext

class ImageGenerationEditingAgent(BaseAgent):
    """图片生成与编辑Agent — image-generation-editing-agent"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        """懒加载 LLM 客户端"""
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import DeepSeekClient
            self._llm = DeepSeekClient(settings.llm)
        return self._llm

    def _log_step(self, step_name: str, action: str, input_data: dict = None, output_data: dict = None, error: str = None):
        """记录执行步骤日志到 resources/agents/logs/{agent_id}-{timestamp}.md"""
        if not hasattr(self, '_log_file'):
            log_dir = PROJECT_ROOT / "resources" / "agents" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            agent_id = self.spec.id if self.spec else "unknown"
            self._log_file = log_dir / f"{agent_id}-{ts}.md"
            # 写入文件头
            self._log_file.write_text(
                f"# Agent 执行日志: {agent_id}\n\n"
                f"**启动时间**: {ts}\n\n"
                f"| 步骤 | 时间 | 操作 | 输入 | 输出 | 状态 |\n"
                f"|------|------|------|------|------|------|\n",
                encoding="utf-8"
            )
        now = datetime.datetime.now().strftime("%H:%M:%S")
        inp_str = json.dumps(input_data, ensure_ascii=False)[:200] if input_data else "-"
        out_str = json.dumps(output_data, ensure_ascii=False)[:200] if output_data else "-"
        status = f"❌ {error}" if error else "✅ 成功"
        row = f"| {step_name} | {now} | {action} | {inp_str} | {out_str} | {status} |\n"
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(row)

    async def _extract_params_with_llm(self, tool_name: str, user_input: str, param_defs: list) -> dict:
        """使用 LLM 从用户输入中智能提取工具/API 所需参数"""
        if not param_defs:
            return {}
        prompt = (
            f"从用户输入中提取工具参数。\n\n"
            f"工具: {tool_name}\n"
            f"参数定义: {json.dumps(param_defs, ensure_ascii=False)}\n"
            f"用户输入: \"{user_input}\"\n\n"
            f"返回 JSON 格式:"
        )
        try:
            response = await self._get_llm().chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300
            )
            text = response.strip()
            if text.startswith("```"): text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
            return json.loads(text)
        except Exception:
            return {}

    def _call_tool_image_synthesis_doubao(self, **params) -> dict:
        """调用工具: 图片合成工具（豆包大模型） (tool_id: image-synthesis-doubao)"""
        impl_path = PROJECT_ROOT / "resources" / "tools" / "implementations" / "image-synthesis-doubao" / "tool.py"
        if not impl_path.exists():
            return {"status": "failed", "message": f"工具不存在: image-synthesis-doubao"}

        code = impl_path.read_text()
        venv_python = PROJECT_ROOT / "resources" / "tools" / "implementations" / "image-synthesis-doubao" / ".venv" / "bin" / "python"
        python_exe = str(venv_python) if venv_python.exists() else sys.executable

        test_script = (
            f"import json, sys\n"
            f"sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
            f"code = {json.dumps(code)}\n"
            f"exec(code)\n"
            f"result = execute(**{json.dumps(params)})\n"
            f"print(json.dumps(result, default=str))\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True,
                env={**os.environ, "TOOL_DIR": str(impl_path.parent), "_PROJECT_ROOT": str(PROJECT_ROOT)},
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {"status": "failed", "message": proc.stderr[:300]}
        finally:
            os.unlink(tmp_path)

    def _call_tool_doubao_image_edit(self, **params) -> dict:
        """调用工具: 图片编辑（Doubao） (tool_id: doubao-image-edit)"""
        impl_path = PROJECT_ROOT / "resources" / "tools" / "implementations" / "doubao-image-edit" / "tool.py"
        if not impl_path.exists():
            return {"status": "failed", "message": f"工具不存在: doubao-image-edit"}

        code = impl_path.read_text()
        venv_python = PROJECT_ROOT / "resources" / "tools" / "implementations" / "doubao-image-edit" / ".venv" / "bin" / "python"
        python_exe = str(venv_python) if venv_python.exists() else sys.executable

        test_script = (
            f"import json, sys\n"
            f"sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
            f"code = {json.dumps(code)}\n"
            f"exec(code)\n"
            f"result = execute(**{json.dumps(params)})\n"
            f"print(json.dumps(result, default=str))\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(test_script)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [python_exe, tmp_path],
                capture_output=True, text=True,
                env={**os.environ, "TOOL_DIR": str(impl_path.parent), "_PROJECT_ROOT": str(PROJECT_ROOT)},
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            else:
                return {"status": "failed", "message": proc.stderr[:300]}
        finally:
            os.unlink(tmp_path)


    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """Agent 主执行逻辑 — 按 MD 执行流程逐步: LLM解析参数→调用工具→LLM合成结果"""
        content = kwargs.get("content", "").strip()
        if not content:
            yield {"event": "error", "data": {"code": "MISSING_INPUT", "message": "输入内容不能为空"}}
            yield {"event": "done", "data": {"messageId": ""}}
            return

        # 步骤1：提取参数
        self._log_step("步骤1", "从用户输入提取参数", {"input": content[:100]})
        extracted = await self._extract_params_with_llm(
            tool_name="提取参数",
            user_input=content,
            param_defs=[
                {"name": "需求", "type": "string", "required": True, "desc": "图片生成的自然语言描述"},
                {"name": "num", "type": "int", "required": True, "desc": "需要合成的图片数量"},
                {"name": "编辑效果", "type": "string", "required": True, "desc": "图片编辑要求描述"},
            ]
        )
        self._log_step("步骤1", "参数提取完成", {"extracted": extracted})

        prompt = extracted.get("需求", "")
        num = extracted.get("num", 1)
        edit_prompt = extracted.get("编辑效果", "")

        if not prompt:
            yield {"event": "error", "data": {"code": "INVALID_PARAMS", "message": "需求描述不能为空"}}
            yield {"event": "done", "data": {"messageId": ""}}
            return

        # 步骤2：图片合成
        self._log_step("步骤2", "调用图片合成工具", {"prompt": prompt, "num_images": num, "dataset_name": prompt})
        synthesis_result = self._call_tool_image_synthesis_doubao(
            prompt=prompt,
            num_images=int(num),
            dataset_name=prompt
        )
        self._log_step("步骤2", "图片合成工具返回", {"status": synthesis_result.get("status")})

        if synthesis_result.get("status") != "success":
            error_msg = synthesis_result.get("message", "图片合成失败")
            yield {"event": "error", "data": {"code": "SYNTHESIS_FAILED", "message": error_msg}}
            yield {"event": "done", "data": {"messageId": ""}}
            return

        # 步骤3：从上一步输出提取图片列表
        self._log_step("步骤3", "从合成结果提取图片列表")
        synthesis_json = json.dumps(synthesis_result, ensure_ascii=False)
        images_extracted = await self._extract_params_with_llm(
            tool_name="图片编辑（Doubao）",
            user_input=synthesis_json,
            param_defs=[
                {"name": "images", "type": "list", "required": True, "desc": "包含所有图片信息的列表，每项包含 id 和 path"}
            ]
        )
        images = images_extracted.get("images", [])
        self._log_step("步骤3", "提取到的图片列表", {"count": len(images)})

        success_count = 0
        fail_count = 0
        all_edit_results = []

        for idx, img in enumerate(images):
            # 提取单张图片路径
            img_json = json.dumps(img, ensure_ascii=False)
            self._log_step(f"步骤3-{idx+1}", f"处理第{idx+1}张图片", {"img": img_json})
            path_extracted = await self._extract_params_with_llm(
                tool_name="图片编辑（Doubao）",
                user_input=img_json,
                param_defs=[
                    {"name": "image_path", "type": "string", "required": True, "desc": "从图片信息中提取图片路径"}
                ]
            )
            image_path = path_extracted.get("image_path", "")
            if not image_path:
                self._log_step(f"步骤3-{idx+1}", "跳过：无有效图片路径")
                fail_count += 1
                continue

            # 调用图片编辑工具
            self._log_step(f"步骤3-{idx+1}", "调用图片编辑工具", {"image_path": image_path, "prompt": edit_prompt})
            edit_result = self._call_tool_doubao_image_edit(
                image_path=image_path,
                prompt=edit_prompt,
                negative_prompt="模糊，水印，文字，畸形",
                strength=0.7,
                size="2048x2048"
            )
            self._log_step(f"步骤3-{idx+1}", "图片编辑工具返回", {"status": edit_result.get("status")})
            all_edit_results.append(edit_result)
            if edit_result.get("status") == "success":
                success_count += 1
            else:
                fail_count += 1

        # 步骤4：结果汇总
        if success_count == 0 and fail_count > 0:
            error_details = "; ".join([r.get("message", "") for r in all_edit_results])
            yield {"event": "error", "data": {"code": "EDIT_ALL_FAILED", "message": f"所有图片编辑失败: {error_details}"}}
        else:
            summary = f"共合成{num}张图片，成功编辑{success_count}张"
            if fail_count > 0:
                summary += f"，{fail_count}张编辑失败"
            llm = self._get_llm()
            response = await llm.chat(messages=[{"role": "user", "content": summary}])
            yield {"event": "content", "data": {"text": response}}

        yield {"event": "done", "data": {"messageId": ""}}
