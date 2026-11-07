"""API 层：工具（tool）模块关键链路。

覆盖用户实际会踩到的写入/执行路径：

1. 执行工具：内置样本 calc-square 真实跑通，返回结果；不存在 → 404
2. 工具详情：返回 spec/code/来源字段；不存在 → 404
3. 工作空间：增（幂等）/删，改动落到个人工作空间
4. 保存代码/规范/需求：空内容 400；内置资源非超管 403；私有工具可写
5. 覆盖层（fork）：内置工具 fork → fork-status 标记 → 丢弃后回落内置
6. 标签 / 搜索 / 仓库统计：写入后能被检索到
7. 导出 / 导入：导出 .sotatool.zip → 导入私有根 → 同名冲突 409 → overwrite 覆盖

副作用约束：fork / 保存 / 导入都会写用户私有资源根，用例结束必须清理，
避免污染开发环境（每个写入用例用 finally 复原）。
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.api

BUILTIN_TOOL = "calc-square"          # 纯算术：无网络、无 LLM、执行快
PRIVATE_PREFIX = "api-test-"          # 本套用例创建的私有工具统一前缀，便于清理


# ────────────────────────── HTTP 小工具 ──────────────────────────

def _call(base: str, token: str, path: str, method: str = "GET",
          body: dict | None = None, raw: bytes | None = None,
          content_type: str = "application/json", timeout: float = 120):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": content_type,
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(payload.decode()), resp.headers
            return resp.status, payload, resp.headers
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw_body), e.headers
        except json.JSONDecodeError:
            return e.code, {"raw": raw_body}, e.headers


def _multipart(field: str, filename: str, payload: bytes,
               extra: dict[str, str] | None = None) -> tuple[bytes, str]:
    boundary = f"----sotaband{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for k, v in (extra or {}).items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
            .encode())
    chunks.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
         f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n").encode())
    chunks.append(payload)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# ───────────────────────���── 执行 / 详情 ──────────────────────────

def test_execute_builtin_tool(backend_url, backend_token):
    """内置工具真实执行：参数进、结果出（不是只回 200）。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/tool/{BUILTIN_TOOL}/execute", "POST",
                            {"params": {"n": 5}}, timeout=180)
    assert status == 200, body
    assert body.get("status") == "success", f"工具执行失败: {body}"
    data = (body.get("result") or {}).get("data") or {}
    assert data.get("square") == 25, f"执行结果不正确: {body}"


def test_execute_unknown_tool_404(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            "/api/tool/not-exist-tool-xyz/execute", "POST", {"params": {}})
    assert status == 404, body


def test_tool_detail_returns_code_and_spec(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token, f"/api/tool/{BUILTIN_TOOL}")
    assert status == 200
    assert body.get("id") == BUILTIN_TOOL
    # 详情页依赖这些字段渲染，缺一个就是前端白屏
    for key in ("spec_md", "code", "source"):
        assert key in body, f"详情缺少字段 {key}: {list(body)}"
    assert body["code"].strip(), "详情没有返回实现代码"


def test_tool_detail_404(backend_url, backend_token):
    status, _, _ = _call(backend_url, backend_token, "/api/tool/not-exist-tool-xyz")
    assert status == 404


# ────────────────────────── 工作空间 ──────────────────────────

def test_workspace_add_and_remove(backend_url, backend_token):
    """加到工作空间 → 出现在列表 → 移除后消失（幂等，不重复）。"""
    _, before, _ = _call(backend_url, backend_token, "/api/tool/workspace")
    try:
        status, body, _ = _call(backend_url, backend_token, "/api/tool/workspace", "POST",
                                {"id": BUILTIN_TOOL, "name": BUILTIN_TOOL, "tags": []})
        assert status == 200 and body.get("ok") is True
        ids = [t.get("id") for t in body.get("tools", [])]
        assert ids.count(BUILTIN_TOOL) == 1, f"重复加入产生了重复条目: {ids}"

        # 重复加入保持幂等
        _, again, _ = _call(backend_url, backend_token, "/api/tool/workspace", "POST",
                            {"id": BUILTIN_TOOL, "name": BUILTIN_TOOL, "tags": []})
        assert [t.get("id") for t in again.get("tools", [])].count(BUILTIN_TOOL) == 1
    finally:
        _, after, _ = _call(backend_url, backend_token,
                            f"/api/tool/workspace/{BUILTIN_TOOL}", "DELETE")
        assert BUILTIN_TOOL not in [t.get("id") for t in after.get("tools", [])]
        # 恢复原状：用例前已在工作空间则加回
        if BUILTIN_TOOL in [t.get("id") for t in before.get("tools", [])]:
            _call(backend_url, backend_token, "/api/tool/workspace", "POST",
                  {"id": BUILTIN_TOOL, "name": BUILTIN_TOOL, "tags": []})


# ────────────────────── 保存代码 / 规范 / 需求 ──────────────────────

def test_save_code_rejects_empty(backend_url, backend_token):
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/tool/{BUILTIN_TOOL}/save-code", "POST", {"code": "   "})
    assert status in (400, 403), f"空代码没有被拒绝: {status} {body}"


def test_save_code_on_builtin_requires_superuser(backend_url, backend_token):
    """内置工具不能被普通用户直接改（避免污染全平台共享实现）。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/tool/{BUILTIN_TOOL}/save-code", "POST",
                            {"code": "print('x')\n"})
    assert status == 403, f"普通用户改内置工具应 403: {status} {body}"


def test_save_code_private_fork_then_reject_bad_code(backend_url, backend_token):
    """私有副本可保存代码；空代码 / 静态安全审查不通过 → 400。"""
    tool_id = _fork_builtin(backend_url, backend_token)
    try:
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-code", "POST", {"code": "  "})
        assert status == 400, f"空代码应拒绝: {status} {body}"

        bad = "import os\nos.system('rm -rf /tmp/x')\n"
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-code", "POST", {"code": bad})
        assert status == 400 and "安全审查" in str(body.get("detail", "")), \
            f"危险代码未被静态审查拦下: {status} {body}"

        good = "def main(params):\n    return {'status': 'success', 'data': params}\n"
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-code", "POST", {"code": good})
        assert status == 200 and body.get("saved") == tool_id, body
    finally:
        _discard_fork(backend_url, backend_token, tool_id)


def test_save_spec_on_private_fork(backend_url, backend_token):
    """私有副本可保存规范，且回显保存内容（写入路径真的生效）。"""
    tool_id = _fork_builtin(backend_url, backend_token)
    try:
        new_spec = "# API 测试规范\n\n- 参数 n: 数字\n"
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-spec", "POST",
                                {"spec_md": new_spec, "resync_params": False})
        assert status == 200, body
        assert body.get("saved") == tool_id
        _, detail, _ = _call(backend_url, backend_token, f"/api/tool/{tool_id}")
        assert "API 测试规范" in str(detail.get("spec_md", "")), "规范没有落盘"
    finally:
        _discard_fork(backend_url, backend_token, tool_id)


def test_save_step1_requires_one_field(backend_url, backend_token):
    tool_id = _fork_builtin(backend_url, backend_token)
    try:
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-step1", "POST", {})
        assert status == 400, f"空请求应拒绝: {status} {body}"
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/save-step1", "POST",
                                {"demand_md": "统计立方体体积"})
        assert status == 200 and "demand" in body.get("written", []), body
    finally:
        _discard_fork(backend_url, backend_token, tool_id)


# ────────────────────────── fork 覆盖层 ──────────────────────────

def _fork_builtin(base: str, token: str) -> str:
    status, body, _ = _call(base, token, f"/api/tool/{BUILTIN_TOOL}/fork", "POST")
    assert status == 200, body
    assert body.get("forked") == BUILTIN_TOOL
    return BUILTIN_TOOL


def _discard_fork(base: str, token: str, tool_id: str) -> None:
    _call(base, token, f"/api/tool/{tool_id}/fork", "DELETE")


def test_fork_status_and_discard(backend_url, backend_token):
    """fork 前无副本；fork 后有副本且标记为内置来源；丢弃后回落内置。"""
    _discard_fork(backend_url, backend_token, BUILTIN_TOOL)   # 归一为「无副本」
    _, status_body, _ = _call(backend_url, backend_token,
                              f"/api/tool/{BUILTIN_TOOL}/fork-status")
    assert status_body.get("is_builtin") is True
    assert status_body.get("has_copy") is False

    status, _, _ = _call(backend_url, backend_token,
                         f"/api/tool/{BUILTIN_TOOL}/fork", "POST")
    assert status == 200
    try:
        _, forked, _ = _call(backend_url, backend_token,
                             f"/api/tool/{BUILTIN_TOOL}/fork-status")
        assert forked.get("has_copy") is True and forked.get("forked") is True
        assert (forked.get("forked_from") or {}).get("builtin_hash"), forked
        assert (forked.get("forked_from") or {}).get("forked_at"), forked
    finally:
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{BUILTIN_TOOL}/fork", "DELETE")
        assert status == 200, body
        _, restored, _ = _call(backend_url, backend_token,
                               f"/api/tool/{BUILTIN_TOOL}/fork-status")
        assert restored.get("has_copy") is False, f"丢弃副本后仍有私有副本: {restored}"


def test_fork_twice_then_discard_once_is_400(backend_url, backend_token):
    """没有副本时再丢弃 → 400（不能把内置工具删掉）。"""
    _discard_fork(backend_url, backend_token, BUILTIN_TOOL)
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/tool/{BUILTIN_TOOL}/fork", "DELETE")
    assert status == 400, f"无副本时丢弃应拒绝: {status} {body}"


# ────────────────────── 标签 / 搜索 / 仓库 ──────────────────────

def test_tags_then_search(backend_url, backend_token):
    """打标签后能被搜索命中（搜索是前端「工具仓库」的主入口）。

    内置工具的标签属平台元数据，普通用户改会 403；这里在私有副本上验证
    写标签 → 搜索 → 仓库统计的完整链路。
    """
    tool_id = _fork_builtin(backend_url, backend_token)
    tag = f"{PRIVATE_PREFIX}{int(time.time()) % 100000}"
    _, before, _ = _call(backend_url, backend_token, f"/api/tool/{tool_id}")
    old_tags = list(before.get("tags") or [])
    try:
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/tags", "POST",
                                {"tags": old_tags + [tag]})
        assert status == 200, body
        _, found, _ = _call(backend_url, backend_token,
                            f"/api/tool/search/find?q=&tags={tag}")
        assert tool_id in [t.get("id") for t in found.get("tools", [])], \
            f"按标签搜索不到: {found}"
        _, repo, _ = _call(backend_url, backend_token, "/api/tool/repository")
        assert repo.get("total") == len(repo.get("tools", []))
        assert tag in (repo.get("tag_stats") or {}), "仓库标签统计没有计入新标签"
    finally:
        _discard_fork(backend_url, backend_token, tool_id)


def test_tags_on_builtin_requires_superuser(backend_url, backend_token):
    """内置工具标签不能由普通用户改。"""
    status, body, _ = _call(backend_url, backend_token,
                            f"/api/tool/{BUILTIN_TOOL}/tags", "POST",
                            {"tags": ["should-not-apply"]})
    assert status == 403, f"普通用户改内置工具标签应 403: {status} {body}"


def test_tags_rejects_non_list(backend_url, backend_token):
    tool_id = _fork_builtin(backend_url, backend_token)
    try:
        status, body, _ = _call(backend_url, backend_token,
                                f"/api/tool/{tool_id}/tags", "POST",
                                {"tags": "不是列表"})
        assert status == 400, f"非法 tags 类型应拒绝: {status} {body}"
    finally:
        _discard_fork(backend_url, backend_token, tool_id)


def test_search_by_keyword(backend_url, backend_token):
    _, body, _ = _call(backend_url, backend_token, "/api/tool/search/find?q=calc")
    ids = [t.get("id") for t in body.get("tools", [])]
    assert BUILTIN_TOOL in ids, f"关键字搜索不到 {BUILTIN_TOOL}: {ids}"


# ────────────────────────── 导出 / 导入 ──────────────────────────

def test_export_returns_bundle_zip(backend_url, backend_token):
    status, payload, headers = _call(backend_url, backend_token,
                                     f"/api/tool/{BUILTIN_TOOL}/export")
    assert status == 200
    assert "application/zip" in headers.get("content-type", "")
    disposition = headers.get("content-disposition", "")
    assert ".sotatool.zip" in disposition, f"导出文件名不是迁移包: {disposition}"
    assert isinstance(payload, bytes) and payload[:2] == b"PK", "导出的不是 zip 数据"


def test_export_unknown_tool_400(backend_url, backend_token):
    status, _, _ = _call(backend_url, backend_token, "/api/tool/not-exist-xyz/export")
    assert status == 400


def test_import_rejects_non_zip(backend_url, backend_token):
    payload, ctype = _multipart("file", "bundle.txt", b"hello")
    status, body, _ = _call(backend_url, backend_token, "/api/tool/import", "POST",
                            raw=payload, content_type=ctype)
    assert status == 400, f"非 zip 文件没有被拒绝: {status} {body}"


def test_import_bundle_over_builtin_creates_private_copy(backend_url, backend_token):
    """导入与内置同名的包 → 生成私有副本遮蔽内置（不该被 409 挡住）。

    覆盖层语义：内置同 id 不是冲突，导入后在私有层生成可编辑副本。
    """
    _discard_fork(backend_url, backend_token, BUILTIN_TOOL)
    _, bundle, _ = _call(backend_url, backend_token, f"/api/tool/{BUILTIN_TOOL}/export")
    payload, ctype = _multipart("file", f"{BUILTIN_TOOL}.sotatool.zip", bundle)
    try:
        status, body, _ = _call(backend_url, backend_token, "/api/tool/import", "POST",
                                raw=payload, content_type=ctype, timeout=180)
        assert status == 200, f"内置同名包导入被拒: {status} {body}"
        assert body.get("imported") == BUILTIN_TOOL
        assert body.get("overrode_builtin") is True, body
        _, detail, _ = _call(backend_url, backend_token, f"/api/tool/{BUILTIN_TOOL}")
        assert detail.get("source") == "private", "导入没有生成私有副本"
    finally:
        _call(backend_url, backend_token, f"/api/tool/{BUILTIN_TOOL}", "DELETE")


def test_export_then_discard_then_import_roundtrip(backend_url, backend_token):
    """私有工具导出 → 删条目 → 再导入：同名冲突 409，带 overwrite 才覆盖。"""
    _discard_fork(backend_url, backend_token, BUILTIN_TOOL)
    _fork_builtin(backend_url, backend_token)
    status, bundle, headers = _call(backend_url, backend_token,
                                    f"/api/tool/{BUILTIN_TOOL}/export")
    assert status == 200 and isinstance(bundle, bytes)
    assert ".sotatool.zip" in headers.get("content-disposition", "")

    payload, ctype = _multipart("file", f"{BUILTIN_TOOL}.sotatool.zip", bundle)
    try:
        # 先导入一次，确保私有层有同名（不依赖前面的 fork 状态）
        _call(backend_url, backend_token, "/api/tool/import", "POST",
              raw=payload, content_type=ctype, timeout=180)

        status2, body2, _ = _call(backend_url, backend_token, "/api/tool/import", "POST",
                                  raw=payload, content_type=ctype, timeout=180)
        assert status2 == 409, f"同名导入未要求 overwrite: {status2} {body2}"

        payload_ow, ctype_ow = _multipart("file", f"{BUILTIN_TOOL}.sotatool.zip", bundle,
                                          {"overwrite": "true"})
        status3, body3, _ = _call(backend_url, backend_token, "/api/tool/import", "POST",
                                  raw=payload_ow, content_type=ctype_ow, timeout=180)
        assert status3 == 200 and body3.get("overwrote") is True, body3
    finally:
        _call(backend_url, backend_token, f"/api/tool/{BUILTIN_TOOL}", "DELETE")
