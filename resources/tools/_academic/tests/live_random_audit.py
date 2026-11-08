"""Opt-in real-source audit. No fixtures, mocked transports or substituted results.

Run with the project Python: -m resources.tools._academic.tests.live_random_audit
Each source has its own process deadline; JSON evidence stays outside the repository.
"""

import argparse
import json
import os
import random
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def audit_source(case, root, seed, samples):
    os.environ["SOTABAND_ACADEMIC_DATA"] = str(root / "state")
    from resources.tools._academic.common import invoke

    rng = random.Random(seed)
    params = {
        "mode": "search",
        "venues": [case[0]],
        "source": case[1],
        "since": f"{case[2]}-01-01",
        "until": f"{case[2]}-12-31",
        "max_pages": 2,
    }
    response = invoke("papers", params)
    write(root / "retrieval.json", response)
    data = response.get("data", {})
    items = data.get("items", [])
    summary = {
        "case": case,
        "seed": seed,
        "status": response["status"],
        "message": response.get("message"),
        "papers": len(items),
        "partial": data.get("partial"),
        "sources": data.get("sources"),
        "checks": [],
    }
    selected = rng.sample(items, min(samples, len(items)))
    for paper in selected:
        # Query actual titles drawn from the newly downloaded official corpus.
        result = invoke("papers", {**params, "query": paper["title"]})
        returned = result.get("data", {}).get("items", [])
        found = next((p for p in returned if p["id"] == paper["id"]), None)
        summary["checks"].append(
            {
                "id": paper["id"],
                "title": paper["title"],
                "url": paper.get("url"),
                "passed": result["status"] == "success" and found is not None,
                "message": result.get("message"),
                "matches": len(returned),
            }
        )
    summary["passed"] = sum(c["passed"] for c in summary["checks"])
    summary["failed"] = len(summary["checks"]) - summary["passed"]
    write(root / "result.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--deadline", type=int, default=150)
    args = parser.parse_args()
    cases = [
        ("ACL", "anthology", 2024),
        ("EMNLP", "anthology", 2024),
        ("NAACL", "anthology", 2024),
        ("CVPR", "cvf", 2024),
        ("ICCV", "cvf", 2023),
        ("JMLR", "jmlr", 2024),
        ("AAAI", "dblp", 2024),
        ("ICLR", "auto", 2024),
        ("ECCV", "cvf", 2024),
    ]
    if args.worker is not None:
        audit_source(cases[args.worker], args.root, args.seed, args.samples)
        return
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    root = args.root or Path(tempfile.mkdtemp(prefix="sotaband-real-audit-"))
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("Use an empty evidence directory; previous results must not be reused")
    print(json.dumps({"seed": seed, "root": str(root)}, ensure_ascii=False), flush=True)
    order = list(range(len(cases)))
    random.Random(seed).shuffle(order)
    summaries = []
    for index in order:
        folder = root / cases[index][0]
        folder.mkdir(exist_ok=True)
        started = time.monotonic()
        print("START", cases[index], flush=True)
        command = [
            sys.executable,
            "-u",
            "-m",
            __spec__.name,
            "--worker",
            str(index),
            "--root",
            str(folder),
            "--seed",
            str(seed + index),
            "--samples",
            str(args.samples),
        ]
        with (folder / "process.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                proc.wait(timeout=args.deadline)
                result_path = folder / "result.json"
                summary = (
                    json.loads(result_path.read_text(encoding="utf-8"))
                    if result_path.exists()
                    else {
                        "case": cases[index],
                        "status": "process_failed",
                        "exit_code": proc.returncode,
                    }
                )
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                else:
                    proc.kill()
                proc.wait(timeout=15)
                summary = {
                    "case": cases[index],
                    "status": "deadline_exceeded",
                    "deadline": args.deadline,
                }
        summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
        summaries.append(summary)
        write(root / "summary.json", {"seed": seed, "cases": summaries})
        print(
            json.dumps(
                {k: v for k, v in summary.items() if k not in ("checks", "sources")},
                ensure_ascii=False,
            ),
            flush=True,
        )
    print("Evidence:", root / "summary.json", flush=True)
    if any(
        item.get("status") != "success" or item.get("failed") or item.get("partial")
        for item in summaries
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
