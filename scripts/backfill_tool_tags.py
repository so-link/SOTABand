"""为所有现有工具补齐标签（通过 LLM 自动生成）"""

import json
import sys
import asyncio
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.llm.client import create_llm_client


async def generate_tags(llm, tool: dict) -> list[str]:
    """为单个工具生成标签"""
    name = tool.get("name", tool.get("id", ""))
    spec_path = PROJECT_ROOT / "resources" / "tools" / tool.get("spec_path", "")
    spec_md = spec_path.read_text()[:500] if spec_path.exists() else name

    prompt = f"""根据以下工具信息，生成3-5个简短的中文标签（每个2-4字），用于工具分类和检索。

工具名称: {name}
工具描述: {spec_md}

请严格只返回一个JSON数组，格式如: ["标签1","标签2","标签3"]，不要有任何其他文字。"""
    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=150, timeout=30,
        )
        import re
        # 提取方括号内容
        match = re.search(r'\[[\s\S]*?\]', response)
        if match:
            raw = match.group()
            # 清理可能的控制字符
            raw = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', raw)
            tags = json.loads(raw)
            if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
                return tags
        print(f"  ⚠️ {name} LLM返回无法解析: {response[:100]}")
    except Exception as e:
        print(f"  ⚠️ {name} 标签生成失败: {e}")
    return []


async def main():
    registry_path = PROJECT_ROOT / "resources" / "tools" / "registry.json"
    with open(registry_path) as f:
        tools = json.load(f)

    print(f"共 {len(tools)} 个工具")
    llm = create_llm_client()
    updated = 0

    for tool in tools:
        tool_id = tool.get("id", "")
        name = tool.get("name", tool_id)
        existing_tags = tool.get("tags") or []

        if len(existing_tags) >= 3:
            print(f"  ✓ {name}: 已有 {len(existing_tags)} 个标签，跳过")
            continue

        print(f"  🔄 {name}: 生成标签中...")
        tags = await generate_tags(llm, tool)
        if tags:
            tool["tags"] = tags
            updated += 1
            print(f"    → {tags}")

    with open(registry_path, "w") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)

    print(f"\n完成！更新了 {updated} 个工具的标签")


if __name__ == "__main__":
    asyncio.run(main())
