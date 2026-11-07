"""API 层：定时任务（cron）的 CRUD / 启用切换 / 触发 / 执行记录。

前端「定时任务」视图完全依赖这组接口，此前只有 `/options` 有测试。

副作用：用例会创建真实任务，全部在 finally 里删除；执行历史表保留但按
job_id 关联，删除任务后不会影响其它人（平台库按 uid 隔离）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.api

TOOL_ID = "calc-square"


def _call(base: str, token: str, path: str, method: str = "GET",
          body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _payload(**overrides) -> dict:
    body = {
        "name": f"API 测试任务 {int(time.time())}",
        "kind": "tool",
        "target_id": TOOL_ID,
        "params": {"n": 3},
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "timeout": 120,
        "misfire": "skip",
    }
    body.update(overrides)
    return body


@pytest.fixture
def created_job(backend_url, backend_token):
    """创建一个任务并保证用例结束后删除。"""
    status, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST", _payload())
    assert status == 200, f"创建定时任务失败: {status} {body}"
    job_id = body["job"]["id"]
    yield job_id
    _call(backend_url, backend_token, f"/api/cron/jobs/{job_id}", "DELETE")


# ────────────────────────── 创建 / 读取 ──────────────────────────

def test_create_job_returns_schedule(backend_url, backend_token, created_job):
    """创建后要带回下次执行时间，前端列表直接展示它。"""
    status, body = _call(backend_url, backend_token, f"/api/cron/jobs/{created_job}")
    assert status == 200
    job = body.get("job") or {}
    assert job.get("id") == created_job
    assert job.get("cron") == "0 9 * * *"
    assert job.get("target_id") == TOOL_ID
    # 列表/详情会带本地化时间字段（可能为 None，但键必须存在）
    assert "next_run_local" in job, f"缺下次执行时间字段: {list(job)}"


def test_create_job_rejects_bad_cron(backend_url, backend_token):
    """cron 字段数不对 → 422；时区非法 → 422。"""
    status, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST",
                         _payload(cron="0 9 * *"))
    assert status == 422, f"错误 cron 未被拒绝: {status} {body}"
    assert "cron" in str(body.get("detail", "")), body

    status, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST",
                         _payload(timezone="Mars/Olympus"))
    assert status == 422, f"非法时区未被拒绝: {status} {body}"
    assert "时区" in str(body.get("detail", "")), body


def test_create_job_rejects_unknown_target(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST",
                         _payload(target_id="not-exist-tool-xyz"))
    assert status == 422, f"不存在的目标应被拒绝: {status} {body}"


def test_create_job_rejects_bad_kind(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST",
                         _payload(kind="script"))
    assert status == 422, body


def test_job_list_contains_created(backend_url, backend_token, created_job):
    status, body = _call(backend_url, backend_token, "/api/cron/jobs")
    assert status == 200
    ids = [j.get("id") for j in body.get("jobs", [])]
    assert created_job in ids, f"新建任务不在列表里: {ids}"


def test_get_unknown_job_404(backend_url, backend_token):
    status, _ = _call(backend_url, backend_token, "/api/cron/jobs/not-exist-job-xyz")
    assert status == 404


# ────────────────────────── 更新 / 切换 / 触发 ──────────────────────────

def test_update_job(backend_url, backend_token, created_job):
    status, body = _call(backend_url, backend_token, f"/api/cron/jobs/{created_job}",
                         "PUT", _payload(name="改名后的任务", cron="30 8 * * 1-5"))
    assert status == 200, body
    job = body["job"]
    assert job["name"] == "改名后的任务" and job["cron"] == "30 8 * * 1-5"


def test_toggle_job(backend_url, backend_token, created_job):
    _, before = _call(backend_url, backend_token, f"/api/cron/jobs/{created_job}")
    was_enabled = bool(before["job"].get("enabled"))

    status, body = _call(backend_url, backend_token,
                         f"/api/cron/jobs/{created_job}/toggle", "POST")
    assert status == 200, body
    assert bool(body["job"].get("enabled")) is (not was_enabled), body

    # 再切一次应回到原状态（避免影响后续断言）
    status, body = _call(backend_url, backend_token,
                         f"/api/cron/jobs/{created_job}/toggle", "POST")
    assert status == 200 and bool(body["job"].get("enabled")) is was_enabled


def test_trigger_job(backend_url, backend_token, created_job):
    """手动触发：立即执行并进入执行记录。"""
    status, body = _call(backend_url, backend_token,
                         f"/api/cron/jobs/{created_job}/trigger", "POST")
    assert status in (200, 409), f"触发返回异常: {status} {body}"
    if status == 409:
        pytest.skip(f"任务正在执行中，跳过: {body}")

    assert body.get("status") == "started"
    assert body.get("job_id") == created_job

    # 执行记录会落到 runs 里（可能异步，给一点时间）
    deadline = time.time() + 30
    while time.time() < deadline:
        _, runs = _call(backend_url, backend_token,
                        f"/api/cron/jobs/{created_job}/runs?limit=20")
        if runs.get("runs"):
            break
        time.sleep(1)
    _, runs = _call(backend_url, backend_token,
                    f"/api/cron/jobs/{created_job}/runs?limit=20")
    assert runs.get("runs"), "手动触发后没有执行记录"
    assert all(r.get("job_id") == created_job for r in runs["runs"]), \
        "执行记录混入了别的任务"


def test_trigger_unknown_job_404(backend_url, backend_token):
    status, _ = _call(backend_url, backend_token,
                      "/api/cron/jobs/not-exist-job-xyz/trigger", "POST")
    assert status == 404


def test_recent_runs_shape(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/cron/runs/recent?limit=5")
    assert status == 200
    assert isinstance(body.get("runs"), list)
    for run in body["runs"]:
        for key in ("id", "job_id", "status", "trigger"):
            assert key in run, f"执行记录缺字段 {key}: {list(run)}"


def test_options_shape(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/cron/options")
    assert status == 200
    for key in ("tools", "agents", "timezones"):
        assert key in body, f"选项缺字段 {key}: {list(body)}"
    assert "Asia/Shanghai" in body["timezones"]


# ────────────────────────── 删除 ──────────────────────────

def test_delete_job(backend_url, backend_token):
    _, body = _call(backend_url, backend_token, "/api/cron/jobs", "POST", _payload())
    job_id = body["job"]["id"]
    status, deleted = _call(backend_url, backend_token, f"/api/cron/jobs/{job_id}", "DELETE")
    assert status == 200 and deleted.get("ok") is True, deleted

    status, _ = _call(backend_url, backend_token, f"/api/cron/jobs/{job_id}")
    assert status == 404, "删除后任务仍可读取"


def test_delete_unknown_job_404(backend_url, backend_token):
    status, _ = _call(backend_url, backend_token, "/api/cron/jobs/not-exist-job-xyz",
                      "DELETE")
    assert status == 404
