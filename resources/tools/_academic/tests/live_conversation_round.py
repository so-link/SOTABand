"""One fresh real dialogue round; any unexpected error fails the round."""

import argparse
import json
import os
import random
import secrets
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, required=True, choices=tuple(range(1, 9)))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix=f"sotaband-dialogue-round{args.round}-"))
    os.environ["SOTABAND_ACADEMIC_DATA"] = str(root / "state")
    from core.executor.tool_executor import ToolExecutor
    from resources.tools._academic.common import Store

    seed = secrets.randbits(31)
    rng = random.Random(seed)
    cases = {
        1: ("ACL", 2021, "facebookresearch/deit", "main.py", ".acl-abstract"),
        2: ("CVPR", 2022, "facebookresearch/detr", "main.py", "#abstract"),
        3: ("JMLR", 2020, "google-research/vision_transformer", "vit_jax/main.py", ".abstract"),
        4: ("NAACL", 2021, "facebookresearch/ijepa", "src/train.py", ".acl-abstract"),
        5: ("ACL", 2020, "facebookresearch/dinov2", "dinov2/train/train.py", ".acl-abstract"),
        6: ("JMLR", 2019, "microsoft/Swin-Transformer", "main.py", ".abstract"),
        7: ("EMNLP", 2022, "facebookresearch/moco", "main_moco.py", ".acl-abstract"),
        8: ("ACL", 2022, "facebookresearch/dino", "main_dino.py", ".acl-abstract"),
    }
    venue, year, repository, entrypoint, selector = cases[args.round]
    summary = {"round": args.round, "seed": seed, "root": str(root), "checks": [], "passed": False}
    print(json.dumps({"root": str(root), "seed": seed, "round": args.round}), flush=True)

    def save(name, value):
        (root / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(tool, params, name, expected="success"):
        response = ToolExecutor.execute_sync(tool, params, timeout=240)
        save(name, response)
        assert response["status"] == expected, response
        summary["checks"].append(name)
        print("CHECK", name, response["status"], flush=True)
        return response["data"]

    try:
        github = call("github-code-search", {"request": f"帮我检查 https://github.com/{repository} 的代码，固定当前版本"}, "github-natural")
        assert len(github["commit"]) == 40 and entrypoint in github["files"]
        papers = call("paper-tracker", {"request": f"查找 {venue} {year} 年的图神经网络论文"}, "papers-natural")
        assert papers["items"] and not papers["partial"]
        assert papers["interpreted_request"]["since"] == f"{year}-01-01"
        assert all(item["year"] == year for item in papers["items"])
        assert not Store().find("subscription"), "A one-off search must not create a subscription"
        selected = rng.sample(papers["items"], min(12, len(papers["items"])))
        for index, paper in enumerate(selected):
            detail = call("paper-tracker", {"mode": "paper_detail", "paper_id": paper["id"]}, f"paper-detail-{index}")
            assert detail["title"] == paper["title"]
        paper = rng.choice(selected)
        page = requests.get(paper["url"], timeout=(10, 45))
        page.raise_for_status()
        abstract = BeautifulSoup(page.text, "lxml").select_one(selector)
        assert abstract is not None, paper["url"]
        paper["abstract"] = abstract.get_text(" ", strip=True)
        assert len(paper["abstract"]) > 50
        Store().put("paper", paper)
        config = root / "学术配置.json"
        config.write_text('{"llm":{}}', encoding="utf-8")
        failed = call("paper-writing", {
            "request": f"为《{paper['title']}》生成中文提纲，只依据所给摘要，不编造实验结论或引用。",
            "paper_ids": [paper["id"]], "config_file": str(config),
        }, "writing-missing-config", expected="failed")
        assert failed["error_code"] == "CONFIG_REQUIRED"
        config.write_text("{}", encoding="utf-8")
        replies = {1: "我已经配置好了，再试一次", 2: "修复完成，请继续", 3: "请继续刚才的任务",
                   4: "帮我再试一次吧", 5: "处理好了，请继续", 6: "继续刚才的任务", 7: "我已修复好了，继续",
                   8: "麻烦你重试一下"}
        generated = call("paper-writing", {"request": replies[args.round]}, "writing-natural-retry")
        assert generated["text"].strip() and len(Store().find("writing")) == 1
        assert "outline" in Store().get("writing", generated["project_id"])["sections"]
        assert not generated["validation"]["unknown_citations"]
        question = call("paper-writing", {"request": "导出这篇文稿"}, "writing-format-question")
        assert question["needs_input"]
        output = call("paper-writing", {"request": "Word 就行"}, "writing-format-answer")
        assert Path(output["file_path"]).is_file() and Path(output["file_path"]).suffix == ".docx"
        hardware = call("code-reproduction", {"request": "查看 GPU 和 Docker 环境"}, "gpu-natural-inspect")
        assert hardware["rows"] and hardware["docker_version"]
        plan = call("code-reproduction", {"mode": "plan", "snapshot_id": github["id"],
            "request": f"创建复现计划，用一张GPU，运行命令为 python {entrypoint}，最多跑两分钟，内存4GB"}, "gpu-natural-plan")
        assert plan["plan"]["gpu_count"] == 1 and plan["plan"]["timeout_seconds"] == 120
        assert plan["plan"]["command"] == ["python", entrypoint]
        assert not Store().find("job"), "Creating a plan must not start training"
        summary.update(passed=True, papers=len(papers["items"]), selected_paper=paper["title"],
                       repository=repository, commit=github["commit"])
    finally:
        save("summary", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
