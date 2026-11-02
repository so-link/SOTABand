import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bibtexparser
import pytest

from resources.tools._academic import reproduction as rep
from resources.tools._academic import writing
from resources.tools._academic.common import Store, ToolError, invoke


def plan(**updates):
    return rep.validate_plan(
        {"image": "sotaband-academic-gpu:1.0", "command": ["python", "train.py"], **updates}
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"command": "python train.py"},
        {"gpu_count": 0},
        {"network": "host"},
        {"metric_file": "../escape"},
        {"targets": []},
        {"image": "-v evil"},
        {"log_metrics": ["mse", "bad metric"]},
    ],
)
def test_plan_validation(updates):
    with pytest.raises(ToolError):
        plan(**updates)


def test_plain_reproduction_request_parsing():
    parsed = rep.parse_reproduction_request(
        r'复现 E:\projects\paper-code 中的代码，使用2张GPU运行 python train.py --seed 7 --name "baseline run"，'
        "超时3分钟，内存32GB，镜像 research/pytorch:2.5"
    )
    assert parsed["source_path"] == r"E:\projects\paper-code"
    assert parsed["plan"]["command"] == [
        "python",
        "train.py",
        "--seed",
        "7",
        "--name",
        "baseline run",
    ]
    assert parsed["plan"]["gpu_count"] == 2
    assert parsed["plan"]["timeout_seconds"] == 180
    assert parsed["plan"]["memory_gb"] == 32
    assert parsed["plan"]["image"] == "research/pytorch:2.5"


def test_plain_reproduction_blocks_unprepared_source(tmp_path, store):
    source = tmp_path / "paper code"
    source.mkdir()
    (source / "train.py").write_text("print('ok')", encoding="utf-8")

    with pytest.raises(ToolError, match="preflight failed"):
        rep.execute(
            {
                "mode": "reproduce",
                "request": f'复现 "{source}" 中的代码，使用1张GPU运行 python train.py，超时120秒。',
            }
        )
    assert store.find("job") == []


def test_plain_reproduction_consumes_github_snapshot(store, monkeypatch):
    store.put(
        "repository",
        {
            "id": "repo-fixed",
            "repository": "owner/paper-code",
            "url": "https://github.com/owner/paper-code",
            "commit": "a" * 40,
            "files": ["run.py", "requirements.txt"],
            "readme": "```bash\npython run.py --seed 7\n```",
            "entrypoints": ["run.py"],
            "dependency_files": ["requirements.txt"],
            "reproducibility": {"candidate_commands": ["python run.py --seed 7"]},
        },
    )

    def start(active_store, ident):
        job = active_store.get("job", ident)
        job["state"] = "running"
        return active_store.put("job", job)

    monkeypatch.setattr(rep, "advance", start)
    response = rep.execute(
        {
            "mode": "reproduce",
            "snapshot_id": "repo-fixed",
            "request": "使用1张GPU复现这个固定仓库，超时2分钟",
        }
    )
    data = response["data"]
    assert data["plan"]["command"] == ["python", "run.py", "--seed", "7"]
    assert data["interpreted_request"]["command_source"] == "repository_readme"
    assert data["interpreted_request"]["repository"] == "owner/paper-code"
    assert data["preflight"]["ready"]
    assert data["writing_handoff"]["params"]["job_ids"] == [data["id"]]


@pytest.mark.parametrize(
    "request_text,error",
    [
        ("运行 python train.py，超时120秒", "源码目录"),
        (r"复现 E:\projects\paper-code 中的代码", "启动命令"),
        (r"复现 E:\projects\paper-code，运行 python train.py && echo done", "shell"),
    ],
)
def test_plain_reproduction_request_errors(request_text, error):
    with pytest.raises(ToolError, match=error):
        rep.parse_reproduction_request(request_text)


def test_reproduction_preflight_detects_entrypoint_and_dependencies(store, tmp_path):
    source = tmp_path / "paper"
    source.mkdir()
    (source / "train.py").write_text("print('ok')", encoding="utf-8")
    (source / "requirements.txt").write_text("torch", encoding="utf-8")
    (source / "README.md").write_text("Download dataset before training.", encoding="utf-8")
    check = rep.preflight(
        store,
        {"source_path": str(source), "command": "python -u train.py", "check_runtime": False},
    )
    assert check["ready"]
    assert check["entrypoints"] == ["train.py"]
    assert check["dependency_files"] == ["requirements.txt"]
    assert check["warnings"]


def test_reproduction_benchmark_aggregates_real_job_metrics(store):
    for ident, value in (("job-a", 0.8), ("job-b", 1.0)):
        store.put(
            "job",
            {
                "id": ident,
                "state": "succeeded",
                "snapshot": {"commit": "same"},
                "plan": {"image": "image", "command": ["python", "train.py"]},
                "evaluation": {"conclusion": "metric_matched", "metrics": {"score": value}},
            },
        )
    report = rep.benchmark(store, ["job-a", "job-b"])
    assert report["protocol_consistent"]
    assert report["metrics"][0]["mean"] == pytest.approx(0.9)
    assert report["metrics"][0]["std"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    "direction,value,matched",
    [("higher", 0.91, True), ("higher", 0.8, False), ("lower", 0.8, True), ("match", 0.91, True)],
)
def test_metric_comparison(direction, value, matched):
    target = {
        "score": {
            "value": 0.9,
            "direction": direction,
            "tolerance": 0.02,
            "source": "paper table 1",
            "protocol": "test split",
        }
    }
    report = rep.compare({"score": value}, target)
    assert report["comparisons"][0]["matched"] is matched
    assert rep.compare({}, target)["conclusion"] == "not_comparable"
    assert rep.compare({"score": float("nan")}, target)["conclusion"] == "not_comparable"


def test_log_metric_extraction_uses_last_finite_value():
    logs = "epoch=1 loss: 0.9\nmse:0.44, mae:0.42\nmse:3.75e-1, mae=0.399\n"
    assert rep.metrics_from_log(logs, ["mse", "mae", "missing"]) == {
        "mse": pytest.approx(0.375),
        "mae": pytest.approx(0.399),
    }


def test_gpu_atomic_allocation_and_restart_reuse(store):
    available = [{"uuid": "GPU-1", "free_mib": 8000}, {"uuid": "GPU-2", "free_mib": 8000}]
    jobs = [{"id": name, "plan": plan(gpu_count=2)} for name in ("one", "two")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        allocations = list(pool.map(lambda job: rep.reserve(store, job, available), jobs))
    assert sorted(map(len, allocations)) == [0, 2]
    winner = jobs[0] if allocations[0] else jobs[1]
    assert len(rep.reserve(store, winner, available)) == 2
    rep.release(store, winner["id"])
    assert len(rep.reserve(store, jobs[1], available)) == 2


def test_idempotency_and_cross_owner_container_names(store):
    record = {"id": "plan-test", "plan": plan(), "snapshot": {"commit": "abc"}}
    first = rep.submit(store, record, "same")
    assert rep.submit(store, record, "same")["id"] == first["id"]
    other = rep.submit(Store(store.root, "other"), record, "same")
    assert other["container_name"] != first["container_name"]


def test_local_source_cannot_change_after_plan(store, tmp_path):
    folder = tmp_path / "source"
    folder.mkdir()
    file = folder / "train.py"
    file.write_text("print(1)", encoding="utf-8")
    snapshot = rep.local_snapshot(folder)
    file.write_text("print(2)", encoding="utf-8")
    with pytest.raises(ToolError, match="changed"):
        rep.prepare_source(store, {"id": "job-test", "snapshot": snapshot})


def test_setup_failure_persists_and_releases(store, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    job = rep.submit(
        store, {"id": "p", "plan": plan(), "snapshot": rep.local_snapshot(source)}, "one"
    )
    monkeypatch.setattr(rep, "container_state", lambda _: None)
    monkeypatch.setattr(rep, "devices", lambda: [{"uuid": "GPU-1", "free_mib": 8000}])

    def fail(*args, **kwargs):
        raise ToolError("Image missing")

    monkeypatch.setattr(rep, "command", fail)
    with pytest.raises(ToolError, match="Image missing"):
        rep.advance(store, job["id"])
    assert store.get("job", job["id"])["state"] == "failed"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM gpu_leases").fetchone()[0] == 0


def test_cancel_stops_real_container_interface(store, monkeypatch):
    job = rep.submit(store, {"id": "p", "plan": plan(), "snapshot": {}}, "one")
    monkeypatch.setattr(rep, "container_state", lambda _: {"State": {"Running": True}})
    calls = []
    monkeypatch.setattr(rep, "command", lambda args, **kw: calls.append(args))
    response = rep.execute({"mode": "cancel", "job_id": job["id"]})
    assert response["data"]["state"] == "cancelled"
    assert calls[0][:2] == ["docker", "stop"]


def test_writing_revisions_citations_and_exports(tmp_path):
    bib = tmp_path / "refs.bib"
    bib.write_text(
        "@article{paper.one,title={Verified Title},author={A. Researcher},year={2025},journal={A Journal}}",
        encoding="utf-8",
    )
    project = writing.execute({"mode": "create", "title": "Study", "bibliography_path": str(bib)})[
        "data"
    ]
    ident = project["id"]
    writing.execute(
        {
            "mode": "save_section",
            "project_id": ident,
            "base_revision": 1,
            "section": "Introduction",
            "content": "Evidence [@paper.one].",
        }
    )
    with pytest.raises(ToolError, match="changed"):
        writing.execute(
            {
                "mode": "save_section",
                "project_id": ident,
                "base_revision": 1,
                "section": "Intro",
                "content": "Conflict",
            }
        )
    check = writing.execute({"mode": "validate", "project_id": ident})["data"]
    assert check["valid_citations"]
    output = writing.execute({"mode": "export", "project_id": ident, "format": "bibtex"})
    parsed = bibtexparser.parse_string(
        Path(output["data"]["file_path"]).read_text(encoding="utf-8")
    )
    assert not parsed.failed_blocks and parsed.entries[0].key == "paper.one"
    restored = writing.execute(
        {"mode": "restore", "project_id": ident, "version_id": ident + "-v1", "base_revision": 2}
    )
    assert restored["data"]["revision"] == 3
    assert writing.execute({"mode": "read", "project_id": ident})["data"]["sections"] == {}


def test_writing_imports_tracker_digest_and_builds_evidence_matrix(store, monkeypatch):
    paper = store.put(
        "paper",
        {
            "id": "paper-tracked",
            "title": "Temporal Evidence",
            "authors": ["A Researcher"],
            "year": 2026,
            "venue": "ICML",
            "abstract": "A time series forecasting method.",
            "url": "https://example.org/paper",
            "relevance_score": 8,
            "matched_terms": ["time series", "forecasting"],
        },
    )
    digest_record = store.put(
        "digest",
        {"id": "digest-tracked", "paper_ids": [paper["id"]], "paper_snapshots": [paper]},
    )
    project = writing.execute(
        {
            "mode": "create",
            "title": "Temporal Study",
            "research_question": "How should temporal models be evaluated?",
            "report_ids": [digest_record["id"]],
        }
    )["data"]
    matrix = writing.execute({"mode": "evidence_matrix", "project_id": project["id"]})
    assert matrix["data"]["items"][0]["evidence_level"] == "abstract"
    assert matrix["data"]["items"][0]["relevance_score"] == 8

    monkeypatch.setattr(
        writing,
        "llm",
        lambda messages: (
            "Prior work studies forecasting [@ref_" + writing.digest(paper["id"])[:12] + "]."
        ),
    )
    review = writing.execute({"mode": "literature_review", "project_id": project["id"]})
    assert review["data"]["evidence_count"] == 1
    checked = writing.execute({"mode": "claim_check", "project_id": project["id"]})
    assert checked["data"]["items"][0]["status"] == "cited"


def test_plain_writing_request_creates_project_and_review(store, monkeypatch):
    paper = store.put(
        "paper",
        {
            "id": "paper-compose",
            "title": "Temporal Models",
            "authors": ["A Researcher"],
            "year": 2026,
            "venue": "ICML",
            "abstract": "A time series forecasting study.",
            "url": "https://example.org/compose",
        },
    )
    monkeypatch.setattr(writing, "llm", lambda messages: "已有研究关注预测 [@ref_" + writing.digest(paper["id"])[:12] + "].")
    response = writing.execute(
        {
            "mode": "compose",
            "request": "用中文撰写关于时间序列预测的文献综述",
            "paper_ids": [paper["id"]],
        }
    )
    assert response["data"]["workflow"]["project_created"]
    assert response["data"]["workflow"]["interpreted_request"]["action"] == "literature_review"
    assert response["data"]["section"] == "Related Work"


def test_unknown_reference_blocks_export():
    project = writing.execute({"mode": "create", "title": "Study"})["data"]
    writing.execute(
        {
            "mode": "save_section",
            "project_id": project["id"],
            "base_revision": 1,
            "section": "Intro",
            "content": "Unsupported [@fake].",
        }
    )
    response = invoke("writing", {"mode": "export", "project_id": project["id"]})
    assert response["data"]["error_code"] == "INVALID_CITATIONS"


def test_llm_uses_supplied_material_and_citation_validation(tmp_path, monkeypatch):
    source = tmp_path / "notes.txt"
    source.write_text("Controlled experiment has no measured result yet.", encoding="utf-8")
    project = writing.execute(
        {"mode": "create", "title": "Study", "material_paths": [str(source)]}
    )["data"]
    seen = []

    def llm(messages):
        seen.extend(messages)
        return "[NEEDS_EVIDENCE]"

    monkeypatch.setattr(writing, "llm", llm)
    response = writing.execute({"mode": "draft", "project_id": project["id"]})
    assert "Controlled experiment" in seen[1]["content"]
    assert "Never invent" in seen[0]["content"]
    assert response["data"]["validation"]["placeholders"] == 1


def test_real_document_conversion():
    pytest.importorskip("pypandoc")
    project = writing.execute({"mode": "create", "title": "Evidence Study"})["data"]
    writing.execute(
        {
            "mode": "save_section",
            "project_id": project["id"],
            "base_revision": 1,
            "section": "Methods",
            "content": "No experimental results are claimed.",
        }
    )
    for fmt, suffix in [("docx", ".docx"), ("latex", ".tex"), ("bundle", ".zip")]:
        response = writing.execute({"mode": "export", "project_id": project["id"], "format": fmt})
        path = Path(response["data"]["file_path"])
        assert path.suffix == suffix and path.stat().st_size > 50
        assert (
            json.loads((path.parent / "provenance.json").read_text(encoding="utf-8"))["revision"]
            == 2
            if fmt != "bundle"
            else True
        )


def test_missing_pdf_engine_is_explicit(monkeypatch):
    monkeypatch.setattr(writing.shutil, "which", lambda name: None)
    project = writing.execute({"mode": "create", "title": "Study"})["data"]
    response = invoke("writing", {"mode": "export", "project_id": project["id"], "format": "pdf"})
    assert response["status"] == "failed"
    assert response["data"]["error_code"] == "DEPENDENCY_MISSING"


def test_missing_llm_key_is_explicit(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings.llm, "api_key", "")
    project = writing.execute({"mode": "create", "title": "Study"})["data"]
    response = invoke("writing", {"mode": "outline", "project_id": project["id"]})
    assert response["data"]["error_code"] == "CONFIG_REQUIRED"
