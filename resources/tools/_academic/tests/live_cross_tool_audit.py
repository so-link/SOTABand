"""Fresh official corpora, author repositories, GPU execution and concurrent recovery.

All external responses and measurements are real. Evidence is written outside the repo.
"""

import argparse
import json
import os
import random
import secrets
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=("papers", "github", "gpu", "writing"), required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--image", default="sotaband-paper-dgi:1.0")
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    os.environ["SOTABAND_ACADEMIC_DATA"] = str(root / "state")
    from resources.tools._academic.common import Store, invoke

    def save(name, data):
        (root / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(module, params):
        response = invoke(module, params)
        if response["status"] != "success":
            save("failure-" + secrets.token_hex(8), {"module": module, "params": params, "response": response})
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        return response

    seed = secrets.randbits(31)
    rng = random.Random(seed)
    report = {"seed": seed, "phase": args.phase, "cases": []}
    store = Store()
    print("START", args.phase, "seed", seed, flush=True)
    try:
        if args.phase == "papers":
            cases = [("EMNLP", "anthology", 2023), ("NAACL", "anthology", 2022), ("JMLR", "jmlr", 2022)]
            rng.shuffle(cases)
            for venue, source, year in cases:
                params = {"mode": "search", "venues": [venue], "source": source,
                          "since": f"{year}-01-01", "until": f"{year}-12-31"}
                response = call("papers", params)
                save("corpus-" + venue, response)
                items = response["data"]["items"]
                assert items and not response["data"]["partial"], response["message"]
                checks = []
                for paper in rng.sample(items, min(args.samples, len(items))):
                    found = call("papers", {**params, "query": paper["title"]})["data"]["items"]
                    assert any(p["id"] == paper["id"] for p in found), paper["title"]
                    checks.append({"id": paper["id"], "title": paper["title"], "matches": len(found)})
                report["cases"].append({"venue": venue, "year": year, "papers": len(items), "checks": checks})
                save("audit-papers", report)
                print("CORPUS", venue, len(items), "title_checks", len(checks), flush=True)
            config = root / "missing-openreview.json"
            config.write_text('{"openreview":{}}', encoding="utf-8")
            mixed = call("papers", {"mode": "search", "venues": ["EMNLP", "ICLR"],
                                    "since": "2023-01-01", "until": "2023-12-31", "query": "graph",
                                    "config_file": str(config)})
            save("mixed-source", mixed)
            assert mixed["data"]["items"] and mixed["data"]["partial"]
            assert any(s["state"] == "failed" and s["source"] == "openreview" for s in mixed["data"]["sources"])
            assert "openreview" in mixed["message"] and "重试" in mixed["message"]
            report["mixed"] = {"papers": len(mixed["data"]["items"]), "sources": mixed["data"]["sources"]}
        elif args.phase == "github":
            repositories = [("tkipf/pygcn", "Semi-Supervised Classification with Graph Convolutional Networks"),
                            ("facebookresearch/mae", "Masked Autoencoders Are Scalable Vision Learners"),
                            ("microsoft/LoRA", "LoRA: Low-Rank Adaptation of Large Language Models")]
            rng.shuffle(repositories)
            for repository, title in repositories:
                response = call("github", {"mode": "inspect", "repository": repository, "paper_title": title})
                save("snapshot-" + repository.replace("/", "-"), response)
                if repository == "tkipf/pygcn":
                    save("pygcn-snapshot", response)
                    assert response["data"]["upstream_caveats"]
                data = response["data"]
                assert len(data["commit"]) == 40 and data["readme"] and data["files"]
                assert data["evidence"], data
                report["cases"].append({"repository": repository, "commit": data["commit"],
                                        "files": len(data["files"]), "relationship": data["relationship"]})
                print("SNAPSHOT", report["cases"][-1], flush=True)
        elif args.phase == "gpu":
            from resources.tools._academic.reproduction import TERMINAL

            snapshot = json.loads((root / "pygcn-snapshot.json").read_text(encoding="utf-8"))["data"]
            report["repository"] = snapshot["url"]
            report["commit"] = snapshot["commit"]
            report["limitation"] = "Upstream README explicitly says this PyTorch reimplementation is not intended to reproduce paper results. These are repository execution measurements only."
            for _ in range(3):
                run_seed = rng.randrange(2147483647)
                launcher = (
                    "import os,sys,runpy,torch; assert torch.cuda.is_available(), 'GPU unavailable'; "
                    "sys.path.insert(0,os.getcwd()); os.chdir('pygcn'); "
                    f"sys.argv=['train.py','--seed','{run_seed}']; "
                    "g=runpy.run_path('train.py',run_name='__main__'); "
                    "assert next(g['model'].parameters()).is_cuda, 'Model did not use GPU'"
                )
                plan = call("reproduction", {"mode": "plan", "snapshot_id": snapshot["id"],
                    "plan_json": {"image": args.image, "command": ["python", "-c", launcher],
                                  "seed": run_seed, "timeout_seconds": 180, "memory_gb": 4,
                                  "cpus": 4, "gpu_count": 1, "log_metrics": ["accuracy", "loss"]}})["data"]
                job = call("reproduction", {"mode": "run", "plan_id": plan["id"]})["data"]
                started = time.monotonic()
                try:
                    while True:
                        response = call("reproduction", {"mode": "status", "job_id": job["id"]})
                        job = response["data"]
                        if job["state"] in TERMINAL:
                            break
                        if time.monotonic() - started > 240:
                            raise TimeoutError("GPU execution audit exceeded deadline")
                        time.sleep(1)
                    save("gpu-" + job["id"], response)
                    logs = call("reproduction", {"mode": "logs", "job_id": job["id"]})
                    save("logs-" + job["id"], logs)
                    assert job["state"] == "succeeded", job
                    assert 0 <= job["evaluation"]["metrics"]["accuracy"] <= 1
                    assert job["evaluation"]["conclusion"] == "not_comparable"
                    report["cases"].append({"id": job["id"], "seed": run_seed,
                                            "state": job["state"], "evaluation": job["evaluation"]})
                    print("GPU", report["cases"][-1], flush=True)
                    save("audit-gpu", report)
                finally:
                    current = store.get("job", job["id"])
                    if current["state"] not in TERMINAL:
                        invoke("reproduction", {"mode": "cancel", "job_id": job["id"]})
        else:
            import requests
            from bs4 import BeautifulSoup

            for venue, selector in (("EMNLP", ".acl-abstract"), ("JMLR", ".abstract")):
                items = json.loads((root / ("corpus-" + venue + ".json")).read_text(encoding="utf-8"))["data"]["items"]
                paper = rng.choice(items)
                page = requests.get(paper["url"], timeout=(10, 45))
                page.raise_for_status()
                abstract = BeautifulSoup(page.text, "lxml").select_one(selector)
                assert abstract is not None, paper["url"]
                paper["abstract"] = abstract.get_text(" ", strip=True)
                assert len(paper["abstract"]) > 50
                store.put("paper", paper)
                project = call("writing", {"mode": "create", "title": paper["title"],
                    "paper_ids": [paper["id"]], "language": "中文"})["data"]
                config = root / ("empty-llm-" + venue + ".json")
                config.write_text('{"llm":{}}', encoding="utf-8")
                failure = invoke("writing", {"mode": "literature_review", "project_id": project["id"],
                    "instruction": "只依据摘要写一段中文评述，最多250字，使用给定引用。不要编造实验数据或摘要外结论。",
                    "config_file": str(config)})
                assert failure["data"]["error_code"] == "CONFIG_REQUIRED"
                ident = failure["data"]["recovery"]["id"]
                config.write_text("{}", encoding="utf-8")
                with (root / ("model-" + venue + ".json")).open("w", encoding="utf-8") as output:
                    proc = subprocess.Popen([sys.executable, "-m", "resources.tools._academic.recovery",
                        "--tool", "writing", "--request", "重试 " + ident], stdout=output,
                        stderr=subprocess.PIPE, env={**os.environ, "PYTHONUTF8": "1"})
                    deadline = time.monotonic() + 120
                    observed = False
                    try:
                        while proc.poll() is None and time.monotonic() < deadline:
                            if store.get("recovery", ident)["state"] == "retrying":
                                viewed = invoke("writing", {"mode": "recover", "request": "查看 " + ident})
                                assert viewed["status"] == "success", viewed
                                observed = True
                                break
                            time.sleep(0.05)
                        _, stderr = proc.communicate(timeout=120)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                            proc.communicate()
                    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")
                    assert observed, "Could not observe the real in-flight retry"
                response = json.loads((root / ("model-" + venue + ".json")).read_text(encoding="utf-8"))
                assert response["status"] == "success", response
                validation = call("writing", {"mode": "validate", "project_id": project["id"]})["data"]
                assert validation["valid_citations"] and validation["used_citations"]
                exports = {}
                for fmt in ("markdown", "latex", "docx", "bibtex"):
                    exported = call("writing", {"mode": "export", "project_id": project["id"], "format": fmt})
                    path = Path(exported["data"]["file_path"])
                    assert path.is_file() and path.stat().st_size > 0
                    exports[fmt] = str(path)
                report["cases"].append({"title": paper["title"], "url": paper["url"], "abstract": paper["abstract"],
                    "project_id": project["id"], "concurrent_view": observed, "validation": validation, "exports": exports})
                save("audit-writing", report)
                print("WRITING", paper["title"], "concurrent view verified", flush=True)
    finally:
        save("audit-" + args.phase + "-" + str(seed), report)
        save("audit-" + args.phase, report)
    print("FINISHED", root / ("audit-" + args.phase + ".json"), flush=True)


if __name__ == "__main__":
    main()
