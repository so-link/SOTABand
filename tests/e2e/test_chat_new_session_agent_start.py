"""浏览器自动化回归：新会话 + 自动确认 / 手工确认两条链路。

对应线上问题：新对话输入「广州明天天气怎么样？」→ 匹配到「城市信息查询
Agent」→ 该 Agent 未运行 → 启动后原始提问被丢弃。

## 检查项

1. **必须从新会话开始**：每个用例先在页面上点「新建对话」，断言对话区旧
   消息为 0；跑完再断言本轮消息落在一个**新的独立会话**里，原有会话的
   消息数不变（不会被续写到旧会话上）。
2. **自动确认 / 手工确认分别测试**：
   - 自动确认：Agent 未运行时自动启动，界面上能看到「启动」这一步
     （`start_agent` 步骤），随后自动继续处理原提问，无需用户再说一句话。
   - 手工确认：Agent 未运行时提示用户确认启动（不声称已启动），用户点
     「确认执行」后自动继续处理原提问（补发原话），不需要重新描述需求。

## 运行

需要后端（8201）与前端（5173）都已启动，并提供登录密码（超管密码来自
.env 的 SOTABAND_ROOT_PASSWORD）：

    SOTABAND_ROOT_PASSWORD=... ./venv/bin/python -m pytest \
        tests/e2e/test_chat_new_session_agent_start.py -q

浏览器通过本地 Google Chrome 的 DevTools 协议驱动（可用
SOTABAND_E2E_CHROME 指定可执行文件，默认 /opt/google/chrome/chrome），
只依赖 venv 内的 websocket-client，不引入 Playwright。缺 Chrome、缺
websocket-client、前后端未启动或缺密码时用例自动 skip。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_URL = os.environ.get("SOTABAND_E2E_FRONTEND", "http://127.0.0.1:5173")
BACKEND_URL = os.environ.get("SOTABAND_E2E_BACKEND", "http://127.0.0.1:8201")
AGENT_ID = "city-info-query-agent"
USER_REQUEST = "广州明天天气怎么样？"
# 本轮上限：交互 Agent 转交子 Agent 的 RPC 超时是 300s，这里必须比它长，
# 否则还没等到结果就先断言失败（表现为「看不到启动这一步」的误判）。
TIMEOUT = float(os.environ.get("SOTABAND_E2E_TIMEOUT", "420"))

CHROME = (os.environ.get("SOTABAND_E2E_CHROME")
          or next((p for p in ("/opt/google/chrome/chrome",
                               "/usr/bin/google-chrome", "/usr/bin/chromium")
                   if os.path.exists(p)), ""))


# ────────────────────────── 前置检查 ──────────────────────────

def _http(path: str, method: str = "GET", body: dict | None = None,
          token: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BACKEND_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def _require_services() -> str:
    if not CHROME:
        pytest.skip("未找到 google-chrome/chromium，无法运行浏览器用例")
    try:
        import websocket  # noqa: F401
    except ImportError:
        pytest.skip("缺少 websocket-client，无法驱动浏览器（pip install websocket-client）")
    for url, name in ((FRONTEND_URL, "前端"), (BACKEND_URL, "后端")):
        try:
            urllib.request.urlopen(url, timeout=5)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"{name}未就绪（{url}）: {e}")
    user = os.environ.get("SOTABAND_E2E_USER", "root")
    password = os.environ.get("SOTABAND_E2E_PASSWORD") or os.environ.get(
        "SOTABAND_ROOT_PASSWORD", "")
    if not password:
        pytest.skip("未提供登录密码（SOTABAND_E2E_PASSWORD / SOTABAND_ROOT_PASSWORD）")
    r = _http("/api/auth/login", "POST", {"username": user, "password": password})
    token = r.get("token", "")
    assert token, f"登录失败: {r}"
    return token


def _agent_stop(token: str) -> None:
    """停止目标 Agent：用例需要「Agent 未运行」这个前置条件。"""
    try:
        _http("/api/chat/stop", "POST", {"session_id": "e2e-setup"}, token)
    except Exception:  # noqa: BLE001
        pass
    r = _http("/api/chat/platform-call", "POST",
              {"tool": "stop_agent", "args": {"agent_id": AGENT_ID},
               "session_id": "e2e-setup"}, token)
    if r.get("status") == "pending":
        c = r["confirm"]
        r = _http("/api/chat/platform-call/confirm", "POST",
                  {"opId": c["op_id"], "token": c["token"],
                   "fingerprint": c["fingerprint"]}, token)
    assert r.get("status") in ("ok", "executed"), f"Agent 停止失败: {r}"
    time.sleep(1.5)


def _session_snapshot(token: str) -> dict[str, int]:
    """session_id → 消息数（用于断言旧会话没有被续写）。"""
    out: dict[str, int] = {}
    for s in _http("/api/chat/sessions?limit=50", token=token).get("sessions", []):
        sid = s.get("session_id", "")
        if not sid:
            continue
        msgs = _http(f"/api/chat/sessions/{sid}", token=token).get("messages", [])
        out[sid] = len(msgs)
    return out


# ────────────────────────── 极简 CDP 客户端 ──────────────────────────

class Browser:
    """驱动本地 Chrome 的最小封装（仅本用例所需能力）。"""

    def __init__(self):
        self.proc = None
        self.ws = None
        self._id = 0
        self.profile = tempfile.mkdtemp(prefix="sotaband-e2e-")

    # -- 生命周期 --
    def __enter__(self):
        port = self._free_port()
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", f"--remote-debugging-port={port}",
             f"--user-data-dir={self.profile}", "--window-size=1600,1000",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self._env())
        try:
            ws_url = self._wait_target(port)
        except RuntimeError:
            err = b""
            try:
                self.proc.terminate()
                _, err = self.proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            raise RuntimeError(
                f"Chrome 调试端口未就绪（{CHROME}），返回码 {self.proc.returncode}，"
                f"输出: {(err or b'').decode(errors='replace')[-2000:]}") from None
        import websocket  # 延迟导入：缺依赖时由 _require_services 统一 skip

        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True)
        self.send("Page.enable")
        self.send("Runtime.enable")
        return self

    @staticmethod
    def _env() -> dict:
        """启动 Chrome 的最小环境：系统库路径足够，避免继承 IDE 注入的
        共享库变量（实测会让 headless Chrome 直接起不来）。"""
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", "/root"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "LANG": "C.UTF-8",
        }

    def __exit__(self, *exc):
        try:
            if self.ws:
                self.ws.close()
        finally:
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            shutil.rmtree(self.profile, ignore_errors=True)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _wait_target(self, port: int) -> str:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    targets = json.loads(r.read().decode())
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        raise RuntimeError("Chrome 调试端口未就绪")

    # -- 协议 --
    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise RuntimeError(f"{method} 失败: {msg['error']}")
            return msg.get("result", {})

    def eval(self, expression: str):
        r = self.send("Runtime.evaluate", expression=expression,
                      awaitPromise=True, returnByValue=True, timeout=60000)
        return r.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 30,
                 interval: float = 0.3, label: str = ""):
        if not self.wait_until_js(expression, timeout=timeout, interval=interval):
            raise AssertionError(f"等待超时{('：' + label) if label else ''}\n表达式: {expression}")
        return True

    def wait_until_js(self, expression: str, timeout: float = 30,
                      interval: float = 0.3) -> bool:
        """等待表达式求值为真，超时返回 False（用于业务判定，不抛异常）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.eval(f"(() => {{ try {{ return !!({expression}) }} catch (e) {{ return false }} }})()"):
                return True
            time.sleep(interval)
        return False

    def goto(self, url: str):
        self.send("Page.navigate", url=url)
        self.wait_for("document.readyState === 'complete'", timeout=30, label="页面加载")

    def click(self, selector: str) -> bool:
        return bool(self.eval(
            "(() => { const el = document.querySelector(%s);"
            " if (!el) return false; el.scrollIntoView({block:'center'});"
            " el.click(); return true })()" % json.dumps(selector)))

    def click_by_text(self, text: str, tag: str = "button") -> bool:
        return bool(self.eval(
            "(() => { const els = [...document.querySelectorAll(%s)];"
            " const el = els.find(e => e.textContent.trim().includes(%s));"
            " if (!el) return false; el.scrollIntoView({block:'center'});"
            " el.click(); return true })()" % (json.dumps(tag), json.dumps(text))))

    def type_and_send(self, selector: str, text: str) -> bool:
        """写入并回车（textarea 是受控组件，必须派发 input 事件）。"""
        return bool(self.eval(
            "(() => {"
            " const el = document.querySelector(%s);"
            " if (!el) return false;"
            " el.focus();"
            " const setter = Object.getOwnPropertyDescriptor("
            "   window.HTMLTextAreaElement.prototype, 'value').set;"
            " setter.call(el, %s);"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter',"
            "   code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));"
            " return true })()" % (json.dumps(selector), json.dumps(text))))


# ────────────────────────── 页面操作 ──────────────────────────

def _login(browser: Browser, token: str) -> None:
    """把后端签发的令牌塞进 localStorage，跳过登录弹窗（前端启动时读取）。"""
    browser.goto(FRONTEND_URL)
    browser.eval(f"localStorage.setItem('sotaband-token', {json.dumps(token)})")
    browser.goto(FRONTEND_URL)
    browser.wait_for("document.querySelector('textarea')", timeout=40, label="对话输入框")


def _set_confirm_policy(browser: Browser, policy: str) -> None:
    ok = bool(browser.eval(
        "(() => { const s = document.querySelector('select');"
        " if (!s) return false;"
        " const setter = Object.getOwnPropertyDescriptor("
        "   window.HTMLSelectElement.prototype, 'value').set;"
        " setter.call(s, %s);"
        " s.dispatchEvent(new Event('change', {bubbles: true})); return true })()"
        % json.dumps(policy)))
    assert ok, "找不到确认策略下拉框"
    browser.wait_for(
        f"document.querySelector('select')?.value === {json.dumps(policy)}",
        timeout=5, label="确认策略切换")


def _new_session(browser: Browser) -> None:
    """点「新建对话」，断言对话区旧消息为 0（检查项 1）。"""
    clicked = browser.click_by_text("新建对话") or browser.click_by_text("新建会话")
    if not clicked:
        # 空对话时按钮不渲染（只有有消息才显示），本身就是新会话
        assert browser.eval("document.querySelectorAll('[data-role=\"user\"]').length") == 0, \
            "找不到「新建对话」按钮，且对话区已有消息"
        return
    browser.wait_for("document.querySelectorAll('[data-role=\"user\"]').length === 0",
                     timeout=10, label="新会话为空")
    time.sleep(0.5)
    assert browser.eval("document.querySelectorAll('[data-role=\"user\"]').length") == 0, \
        "新建对话后对话区仍有旧消息：用例不是从新会话开始"


def _agent_text(browser: Browser) -> str:
    """助手气泡的文字（时间线 + 卡片 + 正文）。"""
    return browser.eval(
        "[...document.querySelectorAll('[data-role=\"agent\"]')]"
        ".map(e => e.innerText).join('\\n')") or ""


def _bubble_snapshot(browser: Browser) -> list[tuple[str, str]]:
    """(气泡身份, 内容) 列表：助手气泡会原地流式更新，但身份稳定。

    「本轮是否结束」只能看最后一个气泡：续跑轮会新开一个助手气泡，
    它在流式期间内容不变，用「内容稳定」判结束会提前返回。
    """
    rows = browser.eval(
        "[...document.querySelectorAll('[data-role=\"agent\"]')]"
        ".map(e => [e.getAttribute('data-msg') || '', e.innerText])") or []
    return [(str(k), str(v)) for k, v in rows]


def _send_request(browser: Browser) -> None:
    assert browser.type_and_send("textarea", USER_REQUEST), "输入框写入失败"
    browser.wait_for("document.querySelectorAll('[data-role=\"user\"]').length > 0",
                     timeout=30, label="用户消息已发出")
    browser.wait_for("document.querySelectorAll('[data-role=\"agent\"]').length > 0",
                     timeout=60, label="助手开始输出（匹配 Agent）")


def _wait_result(browser: Browser) -> str:
    """等待本轮结束，返回助手气泡全文。

    结束判定用「最后一个助手气泡」：续跑轮会新开一个气泡，旧气泡不再更新，
    只看旧气泡会在续跑开始前就判结束。新气泡出现结果卡、输入框可用、且
    内容长度在 1.5s 内不再增长，才算本轮结束（气泡里的时间线倒计时每秒
    变化，所以只比较长度，不能比较全文）。
    """
    deadline = time.time() + TIMEOUT
    snap_key = None
    stable_since = time.time()
    while time.time() < deadline:
        rows = _bubble_snapshot(browser)
        idle = browser.eval("document.querySelector('textarea')?.disabled === false")
        if rows:
            key, body = rows[-1]
            if idle and ("Agent 执行" in body or "执行失败" in body):
                marker = (key, len(body))
                if marker != snap_key:
                    snap_key, stable_since = marker, time.time()
                elif time.time() - stable_since > 1.5:
                    return _agent_text(browser)
        time.sleep(0.5)
    raise AssertionError(f"本轮未在 {TIMEOUT}s 内给出 Agent 执行结果："
                         f"{_agent_text(browser)[-800:]}")


# ────────────────────────── 用例 ──────────────────────────

@pytest.mark.parametrize("policy", ["auto", "manual"])
def test_new_session_agent_start(policy: str):
    """检查项：新会话 +（自动确认 | 手工确认）下启动 Agent 并继续原提问。"""
    token = _require_services()
    before = _session_snapshot(token)
    # 前置：Agent 未运行，才能覆盖「启动 Agent」这条路径
    _agent_stop(token)

    with Browser() as b:
        _login(b, token)
        # 自动确认要「不点任何按钮」就走完启动；但平台确认策略的默认值是从
        # localStorage 读的，必须确认它真的生效（切不动就是环境问题，不能
        # 当成自动确认在跑——那会把确认卡误判成 bug）。
        _set_confirm_policy(b, policy)
        assert b.eval("document.querySelector('select')?.value") == policy, \
            f"确认策略未能切换为 {policy}"
        _new_session(b)
        _send_request(b)

        if policy == "manual":
            # 手工确认：先给用户看「需确认后启动」并提示启动，未确认前不能
            # 声称已启动、也不能已经转交给 Agent
            assert b.wait_until_js(
                "!![...document.querySelectorAll('button')].find(x =>"
                " x.textContent.trim() === '确认执行')", timeout=120), \
                "手工确认下没有出现启动确认卡"
            mid = _agent_text(b)
            assert "未运行" in mid and "需确认后启动" in mid, \
                f"没有展示「Agent 未运行，需确认后启动」这一步：{mid[-600:]}"
            assert "已就绪" not in mid, "用户尚未确认就声称已就绪"
            # 未确认前不能有执行痕迹：既不能出现 Agent 结果卡，也不能声称
            # 已把任务交给 Agent（「正在转交给…」只是开场白，不算执行痕迹）
            assert "Agent 执行" not in mid and "平台操作已执行" not in mid, \
                f"用户尚未确认，就已经出现了执行结果：{mid[-600:]}"
            assert "确认后会自动启动该 Agent" in mid, \
                f"没有提示用户「确认执行」即可启动：{mid[-600:]}"
            # 用户点「确认执行」→ 自动继续处理原提问
            assert b.click_by_text("确认执行"), "找不到「确认执行」按钮"
        else:
            # 自动确认：不点任何按钮，网关侧只应该出现「已确认」，不会出现
            # 需要用户点击的卡片（11 秒内确认策略已自动执行）
            assert b.wait_until_js(
                "document.body.innerText.includes('已确认')", timeout=120), \
                "自动确认下确认卡没有被自动执行"
            assert not b.eval(
                "[...document.querySelectorAll('button')]"
                ".filter(x => x.textContent.trim() === '确认执行' && !x.disabled).length"), \
                "自动确认策略下仍有可点击的「确认执行」按钮"

        text = _wait_result(b)

    if policy == "auto":
        # 启动这一步要「用户看得到」：时间线必须有「未运行，需确认后启动」
        # 这一步 + 平台回执「已确认 / 平台操作已执行」。
        assert "未运行" in text and "平台操作已执行" in text, \
            f"自动确认下时间线里没有展示启动这一步：{text[-800:]}"
    else:
        # 手工确认：用户点「确认执行」后，时间线同样要留下启动痕迹
        # （平台回执「平台操作已执行」），证明这次任务是靠启动 Agent 完成的。
        assert "平台操作已执行" in text, \
            f"确认后时间线里看不到启动被执行这一步：{text[-800:]}"

    # 原提问被真正处理：出现了 Agent 执行结果，而不是空结果/未取到
    assert "Agent 执行" in text, f"没有出现 Agent 执行结果卡：{text[-800:]}"
    assert "暂时没有获取到" not in text, f"Agent 未返回天气内容：{text[-800:]}"
    # 不能要求用户重新描述一遍或手动回「继续」
    assert "请重新描述" not in text and "回复我一句（如「继续」）" not in text, \
        f"仍在要求用户手动续跑：{text[-800:]}"

    # 检查项 1：本轮落在新会话里，旧会话没有被续写
    after = _session_snapshot(token)
    new_sessions = [s for s in after if s not in before]
    assert new_sessions, f"没有产生新会话，候选: {sorted(after)}"
    grown = [s for s, n in after.items() if s in before and n != before[s]]
    assert not grown, f"旧会话被续写了: {grown}"
