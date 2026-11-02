"""Run the unmodified official DGI/Cora experiment with random, recorded seeds."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--image", default="sotaband-paper-dgi:1.0")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="sotaband-dgi-results-"))
    os.environ["SOTABAND_ACADEMIC_DATA"] = str(root)
    from resources.tools._academic.common import Store, invoke
    from resources.tools._academic.reproduction import TERMINAL

    def call(params):
        response = invoke("reproduction", params)
        if response["status"] != "success":
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        return response["data"]

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.source, text=True
    ).strip()
    dataset_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (args.source / "data").iterdir()
        if p.is_file()
    }
    # Seeding and reading the upstream result variables do not alter its training protocol.
    launcher = (
        "import os,random,json,runpy,numpy as np,torch; "
        "s=int(os.environ['ACADEMIC_SEED']); random.seed(s); np.random.seed(s); torch.manual_seed(s); "
        "assert torch.cuda.is_available(), 'GPU unavailable'; torch.cuda.manual_seed_all(s); "
        "g=runpy.run_path('execute.py',run_name='__main__'); "
        "m={'accuracy_percent':g['accs'].mean().item(),'accuracy_std_percent':g['accs'].std().item()}; "
        "open('/output/metrics.json','w').write(json.dumps(m)); print('Measured metrics:',m)"
    )
    summary = {
        "repository": "https://github.com/PetarV-/DGI",
        "commit": commit,
        "data_sha256": dataset_hashes,
        "image": args.image,
        "jobs": [],
    }
    print("Evidence:", root, flush=True)
    store = Store()
    for index in range(args.runs):
        seed = secrets.randbelow(2147483647)
        plan = call(
            {
                "mode": "plan",
                "source_path": str(args.source),
                "plan_json": {
                    "image": args.image,
                    "command": ["python", "-c", launcher],
                    "seed": seed,
                    "timeout_seconds": 600,
                    "memory_gb": 4,
                    "cpus": 4,
                    "gpu_count": 1,
                },
            }
        )
        job = call({"mode": "run", "plan_id": plan["id"]})
        print("START", index + 1, "seed", seed, "job", job["id"], flush=True)
        started = time.monotonic()
        try:
            while True:
                job = call({"mode": "status", "job_id": job["id"]})
                if job["state"] in TERMINAL:
                    break
                if time.monotonic() - started > 660:
                    job = call({"mode": "cancel", "job_id": job["id"]})
                    break
                time.sleep(2)
            report = call({"mode": "report", "job_id": job["id"]})
            summary["jobs"].append(
                {
                    "id": job["id"],
                    "seed": seed,
                    "state": job["state"],
                    "seconds": round(time.monotonic() - started, 2),
                    "evaluation": job.get("evaluation"),
                    "report": report["file_path"],
                }
            )
            print(json.dumps(summary["jobs"][-1]), flush=True)
        finally:
            saved = store.get("job", job["id"])
            if saved["state"] not in TERMINAL:
                call({"mode": "cancel", "job_id": job["id"]})
            if saved.get("container_id"):
                subprocess.run(
                    ["docker", "rm", saved["container_name"]],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
            (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if len(summary["jobs"]) >= 2:
        summary["benchmark"] = call(
            {"mode": "benchmark", "job_ids": [j["id"] for j in summary["jobs"]]}
        )
        (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("FINISHED", root / "summary.json", flush=True)
    if any(job["state"] != "succeeded" for job in summary["jobs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
