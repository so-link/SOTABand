"""文件登记表（core.user_files）与 /api/file 接口测试

覆盖：登记/幂等、file_id 解析、目录外路径拒绝、跨用户隔离、
工具结果自动登记（*_id 注入）、参数 file_id 解析、接口冒烟。

全部隔离到 tmp_path：用户目录根指向临时位置，绝不碰真实 /root/SB-USER。
"""

import pytest

from pathlib import Path

from core import user_paths
from core import user_files


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """用户目录根与登记表连接池全部指向临时位置；默认单用户（uid=0）。"""
    root = tmp_path / "SB-USER"
    for uid in ("0", "1000"):
        (root / uid).mkdir(parents=True)
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    user_files._pool.reset()
    yield
    user_files._pool.reset()


def _make_file(name: str = "a.txt", content: bytes = b"hello", rel_dir: str = "uploads") -> Path:
    home = user_paths.user_home()
    d = home / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


# ── 登记与解析 ──

def test_register_and_resolve():
    p = _make_file()
    rec = user_files.register(p, kind="upload")
    assert rec["file_id"].startswith("f_")
    assert rec["rel_path"] == "uploads/a.txt"
    assert user_files.resolve(rec["file_id"]) == p.resolve()
    assert user_files.get(rec["file_id"])["name"] == "a.txt"


def test_register_is_idempotent():
    p = _make_file()
    r1 = user_files.register(p)
    r2 = user_files.register(p)
    assert r1["file_id"] == r2["file_id"]
    # 同名覆盖上传后元数据刷新、id 不变（历史卡片不失效）
    p.write_bytes(b"x" * 99)
    r3 = user_files.register(p)
    assert r3["file_id"] == r1["file_id"]
    assert r3["size"] == 99


def test_register_rejects_outside_user_home():
    with pytest.raises(ValueError):
        user_files.register("/etc/hostname")
    # 用户目录外的兄弟目录同样拒绝（resolve 后仍在 USER_HOME 外）
    home = user_paths.user_home()
    escape = home.parent / "escape.txt"
    escape.write_text("x")
    with pytest.raises(ValueError):
        user_files.register(escape)


def test_resolve_unknown_id_raises():
    with pytest.raises(FileNotFoundError):
        user_files.resolve("f_deadbeef00")


def test_resolve_rejects_tainted_record(tmp_path):
    """登记记录被污染成目录外路径时，解析必须拒绝而不是跟读。"""
    p = _make_file()
    rec = user_files.register(p)
    db = user_files._db()
    db.execute("UPDATE user_files SET rel_path=? WHERE file_id=?",
               ("../../etc/hostname", rec["file_id"]))
    db.commit()
    with pytest.raises(FileNotFoundError):
        user_files.resolve(rec["file_id"])


def test_delete_purges_disk_file():
    p = _make_file()
    rec = user_files.register(p)
    assert user_files.delete(rec["file_id"], purge=True) is True
    assert not p.exists()
    assert user_files.get(rec["file_id"]) is None


# ── 用户隔离 ──

def test_files_isolated_between_users():
    p0 = _make_file("shared_name.txt", b"owner-uid-0")
    rec0 = user_files.register(p0)

    # 切到 1000 号用户：同 id 查不到、同路径各登记各的
    import os
    os.environ["SOTABAND_USER_ID"] = "1000"
    try:
        assert user_files.get(rec0["file_id"]) is None
        with pytest.raises(FileNotFoundError):
            user_files.resolve(rec0["file_id"])
        p1000 = _make_file("shared_name.txt", b"owner-uid-1000")
        rec1000 = user_files.register(p1000)
        assert rec1000["file_id"] != rec0["file_id"]
        assert len(user_files.list_files()) == 1
    finally:
        del os.environ["SOTABAND_USER_ID"]
    assert len(user_files.list_files()) == 1


# ── 工具出口登记 / 入口解析 ──

def test_register_result_paths_injects_ids():
    p = _make_file("fig.png", b"\x89PNG")
    obj = {
        "status": "success",
        "data": {"image_path": str(p), "note": "ok"},
        "nested": [{"file_path": str(p)}],
    }
    recs = user_files.register_result_paths(obj)
    assert len(recs) == 2  # 同一路径幂等复用同一 id
    assert obj["data"]["image_path_id"] == recs[0]["file_id"]
    assert obj["data"]["imageId"] == recs[0]["file_id"]
    assert obj["nested"][0]["file_path_id"] == recs[0]["file_id"]


def test_register_result_paths_skips_foreign_paths():
    obj = {"data": {"image_path": "/etc/hostname", "file_path": "/nope/x.png"}}
    assert user_files.register_result_paths(obj) == []
    assert "image_path_id" not in obj["data"]


def test_register_result_paths_plot_kind():
    p = _make_file("plot1.png", b"\x89PNG", rel_dir="plots")
    recs = user_files.register_result_paths({"image_path": str(p)})
    assert recs[0]["kind"] == "plot"


def test_resolve_params_replaces_file_ids():
    p = _make_file("in.edf", b"0")
    rec = user_files.register(p)
    params = {"input_file": rec["file_id"], "window": 10, "label": "f_notregistered0"}
    user_files.resolve_params(params)
    assert params["input_file"] == str(p.resolve())
    assert params["window"] == 10
    # 未注册的 id 原样传递（失败面与无效路径一致，不额外造错）
    assert params["label"] == "f_notregistered0"


def test_resolve_params_replaces_file_name():
    """对话附加文件只显示名字时，参数里的文件名也要能解析成路径。"""
    p = _make_file("report.png", b"\x89PNG")
    user_files.register(p)
    params = {"input": "report.png", "window": 10, "note": "hello"}
    user_files.resolve_params(params)
    assert params["input"] == str(p.resolve())
    assert params["window"] == 10
    # 无扩展名的普通字符串不拖进文件解析，避免误伤
    assert params["note"] == "hello"


def test_resolve_params_name_ambiguous_not_resolved():
    """同名多个文件时不解析（避免猜错），原样传递。"""
    p1 = _make_file("dup.txt", b"a")
    d2 = user_paths.user_home() / "plots"
    d2.mkdir(parents=True, exist_ok=True)
    (d2 / "dup.txt").write_bytes(b"b")
    user_files.register(p1)
    user_files.register(d2 / "dup.txt")
    params = {"input": "dup.txt"}
    user_files.resolve_params(params)
    assert params["input"] == "dup.txt"


# ── /api/file 接口冒烟（只挂 file 路由，身份走 SOTABAND_USER_ID） ──

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import file_routes

    app = FastAPI()
    app.include_router(file_routes.router, prefix="/api/file")
    return TestClient(app)


def test_api_upload_serve_delete(client):
    res = client.post(
        "/api/file/upload",
        files={"file": ("note.md", b"# hi", "text/markdown")},
    )
    assert res.status_code == 200
    body = res.json()
    fid = body["fileId"]
    assert fid.startswith("f_")
    assert "filePath" not in body and "path" not in body

    got = client.get(f"/api/file/{fid}")
    assert got.status_code == 200
    assert got.content == b"# hi"

    listed = client.get("/api/file").json()["files"]
    assert [f["file_id"] for f in listed] == [fid]

    assert client.delete(f"/api/file/{fid}").status_code == 200
    assert client.get(f"/api/file/{fid}").status_code == 404
    # 磁盘文件同步删除（purge）
    assert not list((user_paths.user_home() / "uploads").rglob("note.md"))


def test_api_unknown_and_path_traversal_ids_404(client):
    assert client.get("/api/file/f_000000000000").status_code == 404
    assert client.get("/api/file/..%2F..%2Fetc").status_code == 404


def test_api_old_path_based_endpoints_gone(client):
    """旧 ?path= 接口必须 404——防止路径式访问死灰复燃。"""
    assert client.get("/api/file/download", params={"path": "/etc/hostname"}).status_code == 404
    assert client.get("/api/file/image", params={"path": "/etc/hostname"}).status_code == 404
    # POST /delete 落到 /{file_id} 的路径上但没有 POST 方法 → 405（旧接口已不存在）
    assert client.post("/api/file/delete", json={"path": "/etc/hostname"}).status_code in (404, 405)
    assert client.get("/api/file/scan-directory", params={"path": "/etc"}).status_code == 404
    assert client.get("/api/file/browse-directory", params={"path": "/etc"}).status_code == 404


def test_api_workspace_tree_roundtrip(client):
    tree = {"id": "root", "name": "工作区间", "type": "directory",
            "category": "folder", "children": []}
    assert client.put("/api/file/workspace/tree", json={"root": tree}).status_code == 200
    assert client.get("/api/file/workspace/tree").json()["root"] == tree


def test_build_user_message_includes_file_content():
    """文本附件：LLM 消息里应包含内容预览 + file_id（供工具参数使用）。

    绕过 InteractiveAgent 的 __init__（避免拉起 LLM/worker），只测消息构建。
    """
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    p = _make_file("note.md", "# 标题\n正文内容".encode())
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    msg, image_urls = agent._build_user_message(
        "分析这个文件",
        [{"fileName": "note.md", "fileId": rec["file_id"],
          "format": "md", "fileSize": p.stat().st_size}],
    )
    assert "# 标题" in msg and "正文内容" in msg
    assert rec["file_id"] in msg
    assert image_urls == []


def test_build_user_message_binary_only_metadata():
    """二进制附件：只给元数据 + 提示用工具，不读内容。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    p = _make_file("img.png", b"\x89PNG\r\n\x1a\n")
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    msg, image_urls = agent._build_user_message(
        "看看这个图",
        [{"fileName": "img.png", "fileId": rec["file_id"],
          "format": "png", "fileSize": p.stat().st_size}],
    )
    assert "img.png" in msg
    assert rec["file_id"] in msg
    # 8 字节小图：作为多模态内容发送
    assert len(image_urls) == 1


def test_build_user_message_uses_server_metadata():
    """前端传的 fileSize 恒为 0 时，服务器应以登记表为准（否则 LLM 误判空文件）。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    p = _make_file("favicon.ico", b"\x00\x00\x01\x00" + b"x" * 252)
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    msg, _ = agent._build_user_message(
        "这是什么文件",
        [{"fileName": "favicon.ico", "fileId": rec["file_id"],
          "format": "", "fileSize": 0}],  # 前端不传真实大小
    )
    assert "0B" not in msg  # 不再显示 0 字节
    assert "256B" in msg    # 真实大小来自登记表
    assert "ICO" in msg     # 格式由服务器从文件名解析


def test_build_user_message_small_image_inlined():
    """小图片（≤2MB）作为多模态内容随消息发送，不写进文本。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    p = _make_file("small.png", b"\x89PNG\r\n\x1a\n" + b"x" * 1024)
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    text, image_urls = agent._build_user_message(
        "看看这张图",
        [{"fileName": "small.png", "fileId": rec["file_id"],
          "format": "png", "fileSize": p.stat().st_size}],
    )
    assert len(image_urls) == 1
    assert image_urls[0].startswith("data:image/png;base64,")
    # 图片字节只在多模态 URL 里，不进文本
    assert "x" * 100 not in text
    assert "已随消息发送" in text


def test_build_user_message_large_image_metadata_only():
    """大图片（>2MB）只给元数据 + file_id，不进消息。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    p = _make_file("big.png", b"\x89PNG\r\n\x1a\n" + b"x" * (3 * 1024 * 1024))
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    text, image_urls = agent._build_user_message(
        "这张大图",
        [{"fileName": "big.png", "fileId": rec["file_id"],
          "format": "png", "fileSize": p.stat().st_size}],
    )
    assert image_urls == []
    assert "图片较大" in text
    assert rec["file_id"] in text


def test_build_user_message_large_file_head():
    """大文件（>1MB）文本：只读头部，提示用工具处理完整内容。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    content = b"col1,col2,col3\n" + b"1,2,3\n" * 300000  # ~1.2MB
    p = _make_file("big.csv", content)
    rec = user_files.register(p)

    agent = object.__new__(InteractiveAgent)
    msg, image_urls = agent._build_user_message(
        "分析这个大文件",
        [{"fileName": "big.csv", "fileId": rec["file_id"],
          "format": "csv", "fileSize": len(content)}],
    )
    assert "col1,col2,col3" in msg
    assert "大文件" in msg and "工具处理" in msg
    assert image_urls == []


def test_workspace_tool_schemas():
    """工作空间工具应转成 function calling schema（name=工具 id，含参数）。"""
    agent_mod = pytest.importorskip(
        "resources.agents.implementations.interactive_agent.agent")
    InteractiveAgent = agent_mod.InteractiveAgent
    agent = object.__new__(InteractiveAgent)
    schemas = agent._workspace_tool_schemas(None)
    assert schemas, "工具注册表应能转出 function calling schema"
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"]
    assert "parameters" in s["function"]
    assert s["function"]["parameters"]["type"] == "object"
