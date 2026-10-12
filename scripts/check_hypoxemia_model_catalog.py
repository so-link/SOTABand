"""Smoke-test the five-channel model catalog in isolated CPU subprocesses.

Run with .venv-hypoxemia/Scripts/python.exe. This uses synthetic tensors only;
it does not read datasets, model weights, patient files, or the LLM config.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "resources/tools/implementations/timeseries-classification-runner/tool.py"
COMPONENT = ROOT / "resources/workflows/hypoxemia/runtime"


def tool_functions() -> dict:
    return runpy.run_path(str(TOOL_PATH))


def check_model(model_name: str) -> dict:
    import torch

    torch.set_num_threads(1)
    torch.manual_seed(42)
    functions = tool_functions()
    card = functions["_model_parameter_schema"]({"model_id": model_name})["data"]["architecture"]
    _, resolved_name, configuration = functions["_resolve_training_configuration"]({"model_id": model_name})
    architecture = configuration["architecture"]
    if resolved_name != model_name or card != architecture:
        raise AssertionError("model card and training configuration differ")

    sys.path.insert(0, str(COMPONENT))
    from scripts.tslib_classification_common import (
        DEFAULT_TSLIB_ROOT, build_tslib_config, instantiate_tslib_model,
    )
    from scripts.run_tslib_classification_trial import (
        ModelEma, evaluate_with_optional_ema, forward_model,
    )

    config = build_tslib_config(
        model_name, seq_len=256, enc_in=5, num_class=2,
        device=torch.device("cpu"), overrides=architecture,
    )
    model = instantiate_tslib_model(model_name, config, tslib_root=DEFAULT_TSLIB_ROOT)
    features = torch.randn(2, 256, 5)
    mask = torch.ones(2, 256)
    labels = torch.tensor([0, 1])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.00025)
    ema = ModelEma(model, momentum=0.99)
    model.train()
    logits = forward_model(model, features, mask)
    if logits.shape != (2, 2) or not torch.isfinite(logits).all():
        raise AssertionError(f"training logits must be finite [2, 2], got {tuple(logits.shape)}")
    torch.nn.functional.cross_entropy(logits, labels).backward()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    ema.update(model)
    model.eval()
    with torch.no_grad():
        logits = forward_model(model, features, mask)
    if logits.shape != (2, 2) or not torch.isfinite(logits).all():
        raise AssertionError(f"evaluation logits must be finite [2, 2], got {tuple(logits.shape)}")
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(features, labels, mask), batch_size=2,
    )
    metrics = evaluate_with_optional_ema(
        model, loader, torch.nn.CrossEntropyLoss(reduction="none"), torch.device("cpu"), ema,
    )
    if not torch.isfinite(torch.tensor(metrics["loss"])):
        raise AssertionError("EMA evaluation loss must be finite")
    return {"model": model_name, "status": "passed", "down_sampling_layers": architecture["down_sampling_layers"],
            "down_sampling_window": architecture["down_sampling_window"]}


def check_catalog(timeout_seconds: int) -> dict:
    functions = tool_functions()
    catalog = functions["_model_catalog"]()
    results = []
    for index, entry in enumerate(catalog, 1):
        name = entry["model_name"]
        if not entry["available"]:
            result = {"model": name, "status": "skipped", "reason": entry["reason"]}
        else:
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--model", name],
                    cwd=str(ROOT), capture_output=True, text=True,
                    timeout=timeout_seconds, env={**os.environ, "SOTABAND_LOAD_DOTENV": "0"},
                    encoding="utf-8", errors="replace",
                )
                result = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
                if completed.returncode or result.get("status") != "passed":
                    result = {"model": name, "status": "failed",
                              "error": (completed.stderr or completed.stdout)[-2400:]}
            except subprocess.TimeoutExpired:
                result = {"model": name, "status": "failed", "error": f"exceeded {timeout_seconds}s"}
            except (ValueError, IndexError, json.JSONDecodeError) as exc:
                result = {"model": name, "status": "failed", "error": str(exc)}
            result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
        print(f"[{index}/{len(catalog)}] {name}: {result['status']}", flush=True)
    registered = []
    for entry in functions["_read_json_list"](functions["model_registry_path"]()):
        if entry.get("associated_tool_id") != "timeseries-classification-runner":
            continue
        model_id = str(entry.get("id", ""))
        record = {"id": model_id, "model": entry.get("model_name", ""),
                  "runtime_status": entry.get("runtime_status", "")}
        try:
            functions["_resolve_training_configuration"]({"model_id": model_id})
            record["configuration"] = "valid"
        except (TypeError, ValueError) as exc:
            record["configuration"] = "invalid"
            record["reason"] = str(exc)
        registered.append(record)
    return {"python": sys.version.split()[0], "torch": __import__("torch").__version__,
            "total": len(catalog), "passed": sum(r["status"] == "passed" for r in results),
            "skipped": sum(r["status"] == "skipped" for r in results),
            "failed": sum(r["status"] == "failed" for r in results), "results": results,
            "registered_configurations": registered}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help=argparse.SUPPRESS)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.model:
        try:
            print(json.dumps(check_model(args.model)))
        except Exception:
            traceback.print_exc()
            raise SystemExit(1)
        return

    output = args.output or (ROOT / ".runtime/model-catalog-checks"
                             / f"catalog-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json")
    if output.exists():
        parser.error(f"Output already exists: {output}")
    result = check_catalog(args.timeout_seconds)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"passed={result['passed']} skipped={result['skipped']} failed={result['failed']} output={output}")
    invalid = [item for item in result["registered_configurations"] if item["configuration"] == "invalid"]
    print(f"registered={len(result['registered_configurations'])} invalid_configurations={len(invalid)}")
    for item in invalid:
        print(f"  {item['id']} ({item['model']}): {item['reason']}")
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
