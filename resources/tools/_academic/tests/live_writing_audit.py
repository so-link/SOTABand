"""Random official-paper -> real LLM -> citation validation -> export audit."""

import argparse
import json
import os
import random
import secrets
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--count", type=int, default=4)
    args = parser.parse_args()
    seed = secrets.randbits(32)
    rng = random.Random(seed)
    root = Path(tempfile.mkdtemp(prefix="sotaband-writing-audit-"))
    os.environ["SOTABAND_ACADEMIC_DATA"] = str(root)
    from resources.tools._academic.common import Store, invoke

    def call(params):
        result = invoke("writing", params)
        if result["status"] != "success":
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result["data"]

    corpus = []
    for venue in ("CVPR", "JMLR"):
        result = json.loads((args.corpus / venue / "retrieval.json").read_text(encoding="utf-8"))
        corpus.extend(result["data"]["items"])
    selected = rng.sample(corpus, args.count)
    summary = {"seed": seed, "cases": []}
    print("Evidence:", root, "seed", seed, flush=True)
    for index, paper in enumerate(selected):
        started = time.monotonic()
        outcome = {"paper": paper["title"], "url": paper["url"]}
        try:
            response = requests.get(paper["url"], timeout=(10, 30))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            abstract = soup.select_one("#abstract" if paper["source"] == "cvf" else ".abstract")
            if abstract is None or len(abstract.get_text(strip=True)) < 50:
                raise ValueError("Official abstract is absent; cannot verify grounded writing")
            paper["abstract"] = abstract.get_text(" ", strip=True)
            Store().put("paper", paper)
            project = call(
                {
                    "mode": "create",
                    "title": paper["title"],
                    "paper_ids": [paper["id"]],
                    "language": "\u4e2d\u6587" if index % 2 == 0 else "English",
                    "research_question": "What problem and method does this paper describe, and what does its abstract leave unproven?",
                }
            )
            result = call(
                {
                    "mode": "literature_review",
                    "project_id": project["id"],
                    "instruction": "Write a concise critical summary, at most 250 words, grounded only in the supplied abstract. Cite the source using its supplied key.",
                }
            )
            validation = call({"mode": "validate", "project_id": project["id"]})
            if not validation["valid_citations"] or not validation["used_citations"]:
                raise ValueError("Missing or invalid citations in real model output")
            text = result["text"]
            if index % 2 == 0 and not any("\u4e00" <= ch <= "\u9fff" for ch in text):
                raise ValueError("Model ignored the project's Chinese language")
            exports = {}
            for fmt in ("markdown", "latex", "docx", "bibtex"):
                path = Path(
                    call({"mode": "export", "project_id": project["id"], "format": fmt})[
                        "file_path"
                    ]
                )
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError("Export is empty: " + fmt)
                if fmt == "docx":
                    with zipfile.ZipFile(path) as archive:
                        if archive.testzip() or not archive.read("word/document.xml"):
                            raise ValueError("Invalid DOCX archive")
                exports[fmt] = str(path)
            outcome.update(
                status="success",
                abstract=paper["abstract"],
                text=text,
                project_id=project["id"],
                validation=validation,
                exports=exports,
            )
        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
            OSError,
            zipfile.BadZipFile,
        ) as exc:
            outcome.update(status="failed", error=str(exc))
        outcome["seconds"] = round(time.monotonic() - started, 2)
        summary["cases"].append(outcome)
        (root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in outcome.items()
                    if k not in ("text", "abstract", "exports", "validation")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    print("FINISHED", root / "summary.json", flush=True)
    if any(case["status"] != "success" for case in summary["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
