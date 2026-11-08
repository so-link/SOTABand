"""设备空间 API：REST 快照与 WebSocket 推送（只挂载设备路由，不启动交互 Agent / LLM）"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import device_routes
from core.scheduler.gpu import MockGpuProvider
from core.scheduler.monitor import DeviceMonitor


@pytest.fixture
def client(monkeypatch, tmp_path):
    monitor = DeviceMonitor(MockGpuProvider("2x24", seed=0), project_root=tmp_path, interval=0.05)
    monkeypatch.setattr(device_routes, "get_device_monitor", lambda: monitor)
    app = FastAPI()
    app.include_router(device_routes.router, prefix="/api/devices")
    with TestClient(app) as c:
        yield c, monitor


def test_rest_snapshot(client):
    c, _ = client
    body = c.get("/api/devices").json()
    assert body["gpu_provider"] == "mock"
    assert body["host"]["hostname"]
    latest = body["latest"]
    assert {"cpu", "memory", "disks", "gpus"} <= latest.keys()
    assert [g["index"] for g in latest["gpus"]] == [0, 1]


def test_ws_sends_init_then_snapshots(client):
    c, monitor = client
    with c.websocket_connect("/api/devices/ws") as ws:
        init = ws.receive_json()
        assert init["type"] == "init"
        assert init["gpu_provider"] == "mock"
        assert init["interval"] == monitor.interval

        first, second = ws.receive_json(), ws.receive_json()
        assert first["type"] == second["type"] == "snapshot"
        assert second["data"]["ts"] > first["data"]["ts"]
        assert monitor.subscriber_count == 1


def test_app_ws_requires_session():
    from app.main import app
    from starlette.testclient import WebSocketDenialResponse

    with pytest.raises(WebSocketDenialResponse) as denied:
        with TestClient(app).websocket_connect("/api/devices/ws"):
            pass
    assert denied.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_device_snapshot(authed_client, monkeypatch, tmp_path):
    monitor = DeviceMonitor(MockGpuProvider("2x24", seed=0), project_root=tmp_path)
    monkeypatch.setattr(device_routes, "get_device_monitor", lambda: monitor)
    response = await authed_client.get("/api/devices")
    assert response.status_code == 200
    assert response.json()["gpu_provider"] == "mock"
