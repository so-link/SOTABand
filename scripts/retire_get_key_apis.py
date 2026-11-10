#!/usr/bin/env python3
"""下线明文 get-key API 族（评审 C-3）：清理注册表条目与定义文档

背景：api-doubao-get-key / api-deepseek-get-key 会把平台密钥明文交给任何
注册工具，已于 2026-11 下线。替代通道：
  - 文本对话   → api-llm-chat / api-llm-chat-with-config（provider 解析密钥）
  - 图片生成   → api-llm-images
  - 配置查看   → api-llm-get-config（只返回掩码）

本脚本幂等，可重复执行：
  1. 从 core/api/registry.json 及 SB-USER/*/api/registry.json（如存在）中
     删除已下线条目；若 api-llm-images 缺失则按内置 registry 补登记。
  2. 删除 definitions/ 下的两份 get-key 定义文档。

内置工具调用方已全部迁移完毕，本脚本只负责存量部署的注册表收敛。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETIRED_IDS = ["api-doubao-get-key", "api-deepseek-get-key"]

REPLACEMENT = {
    "id": "api-llm-images",
    "name": "LLM图片生成API",
    "version": "1.0.0",
    "category": "llm",
    "status": "active",
    "spec_path": "definitions/api-llm-images.md",
    "impl_module": "core.api.implementations.api_llm",
    "impl_class": "ApiLlmImages",
    "impl_method": "call",
    "is_async": True,
    "input_schema": {
        "prompt": "string",
        "model": "string",
        "provider": "string",
        "image": "string",
        "size": "string",
        "watermark": "bool",
        "n": "int",
    },
    "output_schema": {
        "images": "list",
        "model": "string",
        "error": "string",
    },
    "tags": ["LLM", "图片生成", "图片编辑", "AI"],
    "created_at": "2026-11-10T00:00:00Z",
}


def migrate_registry(path: Path) -> None:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"跳过（读取失败）: {path} — {exc}")
        return
    kept = [e for e in entries if e.get("id") not in RETIRED_IDS]
    removed = len(entries) - len(kept)
    added = 0
    if not any(e.get("id") == REPLACEMENT["id"] for e in kept):
        kept.append(dict(REPLACEMENT))
        added = 1
    if removed or added:
        from core.resource.json_store import write_json_atomic
        write_json_atomic(path, kept)
        print(f"更新: {path}（删除 {removed} 条，新增 {added} 条）")
    else:
        print(f"无需变更: {path}")


def main() -> int:
    sys.path.insert(0, str(ROOT))

    registries = [ROOT / "core" / "api" / "registry.json"]
    sb_user = ROOT.parent / "SB-USER"
    if sb_user.is_dir():
        registries.extend(sorted(sb_user.glob("*/api/registry.json")))

    for reg in registries:
        if reg.exists():
            migrate_registry(reg)

    defs_dir = ROOT / "core" / "api" / "definitions"
    for api_id in RETIRED_IDS:
        doc = defs_dir / f"{api_id}.md"
        if doc.exists():
            doc.unlink()
            print(f"删除定义文档: {doc}")
        else:
            print(f"定义文档已不存在: {doc}")

    print("get-key 族下线迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
