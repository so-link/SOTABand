"""Exercise actual tool failures/storage/retries; no mocked sources or model results."""

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from resources.tools._academic.common import ROOT, Store, ToolError, invoke


def empty_config(tmp_path, service):
    path = tmp_path / f"{service}.json"
    path.write_text(json.dumps({service: {}}), encoding="utf-8")
    return str(path)


def test_openreview_missing_credentials_and_same_subscription_retry(tmp_path, store):
    params = {
        "mode": "track", "request": "持续跟踪 ICLR 2025 年以来的图神经网络论文，每周更新",
        "config_file": empty_config(tmp_path, "openreview"),
    }
    response = invoke("papers", params)
    assert response["data"]["retrieval_status"] == "failed"
    assert not response["data"]["paper_count_known"]
    ident = response["data"]["recovery"]["id"]
    subscription = response["data"]["subscription"]["id"]
    assert "username" in response["message"] and "password" in response["message"]
    assert "--service openreview" in response["message"]
    retried = invoke("papers", {"mode": "recover", "request": "重试 " + ident})
    assert retried["data"]["recovery"]["id"] == ident
    assert retried["data"]["recovery"]["attempts"] == 1
    assert len(store.find("subscription")) == 1
    assert store.find("subscription")[0]["id"] == subscription
    assert store.get("recovery", ident)["params"]["mode"] == "sync"
    assert not store.find("paper")


def test_llm_missing_config_preserves_actual_project(tmp_path, store):
    response = invoke("writing", {
        "mode": "compose", "request": "生成《错误恢复机制验收》的提纲，不编造实验结果",
        "config_file": empty_config(tmp_path, "llm"),
    })
    assert response["status"] == "failed"
    assert response["data"]["error_code"] == "CONFIG_REQUIRED"
    ident = response["data"]["recovery"]["id"]
    entry = store.get("recovery", ident)
    assert entry["can_retry"]
    project_id = entry["params"]["project_id"]
    assert not store.get("writing", project_id)["sections"]
    retried = invoke("writing", {"request": "我已配置好，请重试 " + ident})
    assert retried["status"] == "failed"  # Still absent: never claim configuration was fixed.
    assert len(store.find("writing")) == 1
    assert store.get("recovery", ident)["params"]["project_id"] == project_id
    assert not store.get("writing", project_id)["sections"]


def test_github_credentials_visible_without_network(tmp_path):
    response = invoke("github", {
        "mode": "search_code", "query": "GraphConvolution",
        "config_file": empty_config(tmp_path, "github"),
    })
    assert response["status"] == "failed"
    assert response["data"]["error_code"] == "AUTH_REQUIRED"
    assert "--service github" in response["message"]
    assert response["data"]["recovery"]["can_retry"]


def test_pdf_missing_engine_keeps_real_source(tmp_path, store):
    if shutil.which("xelatex"):
        pytest.skip("This machine has XeLaTeX; missing-engine case does not apply")
    created = invoke("writing", {"mode": "create", "title": "PDF 编译依赖验收"})
    project_id = created["data"]["id"]
    response = invoke("writing", {"mode": "export", "project_id": project_id, "format": "pdf"})
    assert response["status"] == "failed"
    assert response["data"]["error_code"] == "DEPENDENCY_MISSING"
    assert "Noto Serif CJK SC" in response["message"]
    entry = store.get("recovery", response["data"]["recovery"]["id"])
    assert entry["params"]["format"] == "pdf"
    folder = store.folder("writing", project_id)
    assert list(folder.rglob("manuscript.md"))
    assert not list(folder.rglob("*.pdf"))


@pytest.mark.parametrize("iteration", range(12))
def test_random_missing_file_fixed_and_retry_in_new_process(tmp_path, store, iteration):
    """Real missing config file, actual repair and export; state survives process exit."""
    project = invoke("writing", {"mode": "create", "title": "恢复状态持久化验收"})["data"]
    config = tmp_path / (secrets.token_hex(8) + ".json")
    response = invoke("writing", {
        "mode": "export", "project_id": project["id"], "format": "markdown", "config_file": str(config),
    })
    assert response["status"] == "failed"
    ident = response["data"]["recovery"]["id"]
    assert store.get("recovery", ident)["can_retry"]
    config.write_text("{}", encoding="utf-8")  # No credentials required for Markdown export.
    proc = subprocess.run(
        [sys.executable, "-m", "resources.tools._academic.recovery", "--tool", "writing", "--request", "重试 " + ident],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    retried = json.loads(proc.stdout)
    assert retried["status"] == "success", retried
    assert Path(retried["data"]["file_path"]).read_text(encoding="utf-8").startswith("# " + project["title"])
    assert store.get("recovery", ident)["state"] == "resolved"
    repeated = invoke("writing", {"mode": "recover", "request": "重试 " + ident})
    assert repeated["data"]["error_code"] == "RECOVERY_NOT_RETRYABLE"
    assert len(list(store.folder("writing", project["id"]).rglob("manuscript.md"))) == 1


def test_cancel_wrong_tool_and_user_isolation(tmp_path, store):
    response = invoke("github", {"mode": "search_code", "query": "torch", "config_file": empty_config(tmp_path, "github")})
    ident = response["data"]["recovery"]["id"]
    wrong = invoke("writing", {"mode": "recover", "request": "查看 " + ident})
    assert wrong["data"]["error_code"] == "RECOVERY_TOOL_MISMATCH"
    with pytest.raises(ToolError, match="not found"):
        Store(store.root, "different-user").get("recovery", ident)
    cancelled = invoke("github", {"mode": "recover", "request": "取消恢复 " + ident})
    assert cancelled["data"]["state"] == "cancelled"
    assert invoke("github", {"mode": "recover", "request": "重试 " + ident})["status"] == "failed"


def test_training_submission_not_replayed(store):
    response = invoke("reproduction", {"mode": "run", "plan_id": "absent-plan"})
    assert response["status"] == "failed"
    recovery = response["data"]["recovery"]
    assert not recovery["can_retry"]
    retried = invoke("reproduction", {"mode": "recover", "request": "重试 " + recovery["id"]})
    assert retried["data"]["error_code"] == "RECOVERY_NOT_RETRYABLE"
    assert not store.find("job")


def test_concurrent_retry_uses_real_lock(tmp_path, store):
    response = invoke("github", {"mode": "search_code", "query": "torch", "config_file": empty_config(tmp_path, "github")})
    ident = response["data"]["recovery"]["id"]
    with store.lock("recovery:" + ident):
        blocked = invoke("github", {"mode": "recover", "request": "重试 " + ident})
        assert blocked["status"] == "failed"
        assert store.get("recovery", ident)["attempts"] == 0


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_HTTP") != "1", reason="real network opt-in")
def test_live_platform_github_retry_with_real_results(tmp_path):
    from core.executor.tool_executor import ToolExecutor

    repositories = ["lightgbm-org/LightGBM", "facebookresearch/fastText", "networkx/networkx", "scikit-learn/scikit-learn"]
    selected = secrets.SystemRandom().sample(repositories, 3)
    for repository in selected:
        path = tmp_path / (secrets.token_hex(8) + ".json")
        response = ToolExecutor.execute_sync("github-code-search", {
            "mode": "search", "query": "repo:" + repository, "limit": 1, "config_file": str(path),
        }, timeout=180)
        assert response["status"] == "failed"
        ident = response["data"]["recovery"]["id"]
        path.write_text("{}", encoding="utf-8")
        retried = ToolExecutor.execute_sync("github-code-search", {
            "mode": "recover", "request": "我已配置好，请重试 " + ident,
        }, timeout=180)
        assert retried["status"] == "success", retried
        assert retried["data"]["items"][0]["repository"].lower() == repository.lower()
        assert retried["data"]["recovery"]["state"] == "resolved"
        print(json.dumps({"repository": repository, "recovery_id": ident, "actual_rows": len(retried["data"]["items"])}))


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_LLM") != "1", reason="real paid LLM opt-in")
def test_live_llm_recovery_keeps_one_project(tmp_path, store):
    response = invoke("writing", {
        "mode": "compose", "request": "生成《学术工具错误恢复流程》的提纲，只写章节标题，不生成研究结论或实验数据",
        "config_file": empty_config(tmp_path, "llm"),
    })
    assert response["data"]["error_code"] == "CONFIG_REQUIRED"
    ident = response["data"]["recovery"]["id"]
    project_id = store.get("recovery", ident)["params"]["project_id"]
    configured = tmp_path / "platform.json"
    configured.write_text("{}", encoding="utf-8")
    retried = invoke("writing", {"mode": "recover", "request": f'重试 {ident}，配置文件："{configured}"'})
    assert retried["status"] == "success", retried
    assert retried["data"]["project_id"] == project_id
    assert retried["data"]["text"].strip()
    assert len(store.find("writing")) == 1
    assert store.get("recovery", ident)["state"] == "resolved"


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_HTTP") != "1", reason="real network opt-in")
def test_live_dblp_challenge_or_actual_result():
    response = invoke("papers", {"mode": "diagnose_sources", "sources": ["dblp"]})
    probe = response["data"]["probes"][0]
    print(json.dumps({"source": "dblp", "state": probe["state"], "error_code": probe.get("error_code"), "evidence": probe.get("evidence")}, ensure_ascii=True))
    if probe["state"] == "reachable":
        assert response["data"]["all_reachable"]
        assert "recovery" not in response["data"]
    else:
        assert probe["error_code"] in response["message"]
        if probe["error_code"] == "CHALLENGE_REQUIRED":
            assert "站点验证" in response["message"]
        assert response["data"]["recovery"]["can_retry"]


@pytest.mark.skipif(os.getenv("ACADEMIC_LIVE_HTTP") != "1", reason="real network opt-in")
def test_live_github_rejected_query_is_explained():
    response = invoke("github", {"mode": "search", "query": "repo:/", "limit": 1})
    assert response["status"] == "failed"
    assert response["data"]["error_code"] == "SOURCE_QUERY_REJECTED"
    assert response["data"]["details"]["validation_errors"]
    assert "GitHub 拒绝了检索条件" in response["message"]
