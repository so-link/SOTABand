"""资源需求 — 从 spec front-matter 读取，未声明时静态推断

spec（工具 / Agent 的 MD 规范）front-matter 中可声明：

    resources:
      gpu: none | optional | required   # 不用 GPU / 有就用 / 必须
      min_vram_gb: 4                     # gpu 非 none 时有意义
      min_ram_gb: 2
      cpu_cores: 2

未声明时对代码做静态分析推断（结果按代码哈希缓存，不回写 spec）：
引用了深度学习 / GPU 计算库的代码至少判为 gpu: optional —— 宁可让它有机会用上 GPU，
也不要因误判为 none 而被隐藏所有显卡（调度时 none 会设置 CUDA_VISIBLE_DEVICES=""）。
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = PROJECT_ROOT / "resources" / "tools"

GPU_MODES = ("none", "optional", "required")

# 引用即视为「可能使用 GPU」的顶层模块
GPU_LIBRARIES = {
    "torch", "torchvision", "torchaudio", "tensorflow", "keras", "jax", "jaxlib",
    "cupy", "pycuda", "tensorrt", "onnxruntime", "paddle", "mxnet",
    "ultralytics", "transformers", "diffusers", "accelerate", "vllm", "mmdet", "mmcv",
    "detectron2", "timm", "lightning", "pytorch_lightning", "xformers",
}


class RequirementsError(ValueError):
    """spec 中声明的 resources 字段不合法"""


@dataclass(frozen=True)
class ResourceRequirements:
    gpu: str = "none"
    min_vram_gb: float = 0.0
    min_ram_gb: float = 0.0
    cpu_cores: int = 0
    source: str = "default"  # declared / inferred / default
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "gpu": self.gpu,
            "min_vram_gb": self.min_vram_gb,
            "min_ram_gb": self.min_ram_gb,
            "cpu_cores": self.cpu_cores,
            "source": self.source,
            "reason": self.reason,
        }


def parse_requirements(raw: object) -> ResourceRequirements:
    """校验 front-matter 中的 resources 字段"""
    if not isinstance(raw, dict):
        raise RequirementsError(f"resources 应为键值对，实际为 {type(raw).__name__}")
    unknown = set(raw) - {"gpu", "min_vram_gb", "min_ram_gb", "cpu_cores"}
    if unknown:
        raise RequirementsError(f"resources 中有未知字段: {', '.join(sorted(unknown))}")

    gpu = raw.get("gpu", "none")
    if isinstance(gpu, bool):  # YAML 里写 gpu: true / false
        gpu = "required" if gpu else "none"
    if gpu not in GPU_MODES:
        raise RequirementsError(f"resources.gpu 应为 {' / '.join(GPU_MODES)}，实际为 {gpu!r}")

    return ResourceRequirements(
        gpu=gpu,
        min_vram_gb=_non_negative(raw, "min_vram_gb", float),
        min_ram_gb=_non_negative(raw, "min_ram_gb", float),
        cpu_cores=_non_negative(raw, "cpu_cores", int),
        source="declared",
        reason="spec 中声明",
    )


def read_declared(spec_md: str) -> ResourceRequirements | None:
    """从 MD 的 YAML front-matter 读取 resources；没有 front-matter 或未声明时返回 None"""
    meta = _front_matter(spec_md)
    if meta is None or meta.get("resources") is None:
        return None
    return parse_requirements(meta["resources"])


_inferred_cache: dict[str, ResourceRequirements] = {}


def infer_from_code(code: str) -> ResourceRequirements:
    digest = hashlib.sha256(code.encode("utf-8", errors="replace")).hexdigest()
    cached = _inferred_cache.get(digest)
    if cached is not None:
        return cached

    libs = sorted(_imported_modules(code) & GPU_LIBRARIES)
    if libs:
        req = ResourceRequirements(gpu="optional", source="inferred", reason=f"引用了 {', '.join(libs)}")
    elif re.search(r"\bcuda\b", code):
        req = ResourceRequirements(gpu="optional", source="inferred", reason="代码中出现 cuda")
    else:
        req = ResourceRequirements(source="inferred", reason="未发现 GPU 相关代码")
    _inferred_cache[digest] = req
    return req


def resolve_tool_requirements(tool_id: str, code: str | None = None) -> ResourceRequirements:
    """工具的资源需求：spec 声明优先，否则推断 code（未传入时读取已注册的实现）"""
    spec = TOOLS_DIR / "definitions" / f"{tool_id}.md"
    if spec.is_file():
        declared = read_declared(spec.read_text(encoding="utf-8"))
        if declared is not None:
            return declared
    if code is None:
        impl = TOOLS_DIR / "implementations" / tool_id / "tool.py"
        if not impl.is_file():
            return ResourceRequirements(reason="未找到工具代码")
        code = impl.read_text(encoding="utf-8")
    return infer_from_code(code)


def combine(reqs: list[ResourceRequirements], reason: str) -> ResourceRequirements:
    """取多个需求的上界（Agent 继承其调用工具的需求时使用）"""
    if not reqs:
        return ResourceRequirements(source="inferred", reason=reason)
    return replace(
        ResourceRequirements(),
        gpu=max((r.gpu for r in reqs), key=GPU_MODES.index),
        min_vram_gb=max(r.min_vram_gb for r in reqs),
        min_ram_gb=max(r.min_ram_gb for r in reqs),
        cpu_cores=max(r.cpu_cores for r in reqs),
        source="inferred",
        reason=reason,
    )


def _front_matter(md: str) -> dict | None:
    if not md.startswith("---"):
        return None
    end = md.find("\n---", 3)
    if end == -1:
        return None
    try:
        meta = yaml.safe_load(md[3:end])
    except yaml.YAMLError as e:
        raise RequirementsError(f"spec front-matter 不是合法的 YAML: {e}") from e
    return meta if isinstance(meta, dict) else None


def _imported_modules(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 代码有语法错误时退化为正则（自动调试中的半成品代码）
        return set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", code, re.MULTILINE))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


def _non_negative(raw: dict, key: str, typ: type):
    value = raw.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequirementsError(f"resources.{key} 应为数字，实际为 {value!r}")
    if value < 0:
        raise RequirementsError(f"resources.{key} 不能为负数")
    if typ is int and value != int(value):
        raise RequirementsError(f"resources.{key} 应为整数，实际为 {value!r}")
    return typ(value)
