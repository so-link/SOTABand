"""本地身份层：超级用户 + 普通用户表 + 密码哈希 + 签名令牌

## 两类账号（2026-11 多用户设计）

**超级用户**（类比 Linux root）：凭据只来自 .env，不入库、不在界面上改。
- 用户名 ``SOTABAND_ROOT_USERNAME``（默认 ``root``）
- 密码   ``SOTABAND_ROOT_PASSWORD``（**未设置则超级用户不可登录**，
  启动日志会给出提示——宁可拒绝登录，也不让一个空密码的 root 暴露出去）
- uid 固定为 ``DEFAULT_UID``（"0"），沿用历史数据已落地的 ``SB-USER/0/``
- 唯一能管理用户（增删改、重置密码）的角色

**普通用户**：存 ``users.json``，用户名由创建者指定（可改），
uid 由系统从 **1000** 起顺序分配且**不可修改**——uid 就是用户目录名
（``SB-USER/1000/``），改 uid 等于搬家，因此界面上只读。
普通用户只能登录、改自己的密码、保存自己的偏好（数据都在自己目录下）。

## 设计约束

- 零第三方依赖：PBKDF2-HMAC-SHA256（hashlib.pbkdf2_hmac）做密码哈希，
  HMAC-SHA256 签名令牌（格式 ``<payload_b64url>.<sig_b64url>``），
  避免在现有环境引入 passlib/pyjwt 等新包。
- 用户数据是**平台级实体**（验证任何用户的令牌都需要同一把密钥），
  写 ``SYSTEM_ROOT/storage/auth/users.json``，不进任何用户的目录。
- 令牌密钥 ``secret.key`` 首次使用自动生成（0600）。
  可通过环境变量 ``SOTABAND_AUTH_SECRET`` 显式指定（多实例共享密钥时使用）。
- 本模块只做身份原语，不做路由挂载。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from core import user_paths
from core.user_paths import DEFAULT_UID, auth_dir, user_home
from core.resource.json_store import write_json_atomic

# 系统级例外：登录用户表与令牌密钥是**平台级实体**（验证任何用户的令牌
# 都需要同一把密钥），放 SYSTEM_ROOT/storage/auth/，不随用户目录走。
USERS_DIR = auth_dir()
USERS_FILE = USERS_DIR / "users.json"
SECRET_FILE = USERS_DIR / "secret.key"

# 普通用户 uid 起始值（类比 Linux 普通账号从 1000 开始）
FIRST_USER_UID = 1000

# 超级用户 uid（历史数据已经落在 SB-USER/0/，直接复用）
SUPERUSER_UID = DEFAULT_UID

# 令牌默认有效期（秒）
TOKEN_TTL_SECONDS = int(os.getenv("SOTABAND_AUTH_TOKEN_TTL", str(7 * 24 * 3600)))

# PBKDF2 参数：24 万次迭代对交互登录约 0.1~0.3s，足够慢以防离线爆破
_PBKDF2_ITERATIONS = 240_000
_PBKDF2_HASH = "sha256"

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_一-鿿][a-zA-Z0-9_.\-一-鿿]{1,63}$")


class AuthError(Exception):
    """认证相关的业务错误（携带 HTTP 语义由路由层翻译）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── 超级用户（.env 驱动，不入库） ──

def superuser_password_configured() -> bool:
    return bool(os.getenv("SOTABAND_ROOT_PASSWORD", "").strip())


def superuser() -> dict:
    """超级用户身份（始终可得，用于展示与占用用户名）。

    与"能否用密码登录"是两件事：单用户模式（未配置密码）下匿名请求本身就是
    超级用户，用户管理页必须能看到这一行并把它标成内置只读——若此时返回
    None，列表里就没有超管行，反而能看见 alice 这类普通用户，看起来像
    "超级用户不存在"。是否可密码登录由 ``_verify_superuser`` 把门。
    """
    username = os.getenv("SOTABAND_ROOT_USERNAME", "").strip() or "root"
    return {
        "id": SUPERUSER_UID,
        "username": username,
        "display_name": username,
        "role": "admin",
        "builtin": True,
    }


def _verify_superuser(password: str) -> dict | None:
    """密码校验。未配置密码（单用户模式）一律拒绝——该模式下无需登录。"""
    expected = os.getenv("SOTABAND_ROOT_PASSWORD", "").strip()
    if not expected:
        return None
    # 定时攻击无意义（比对对象是 .env 明文），但仍走常数时间比较保持一致
    if not hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
        return None
    return superuser()


# ── 存储 ──

def _load_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_users(users: list[dict]) -> None:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(USERS_FILE, users)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "password"}


def _raw_by_name(username: str) -> dict | None:
    """含密码散列的原始条目——**仅供本模块校验密码使用**。"""
    su = superuser()
    if su and su["username"] == username:
        return su
    for u in _load_users():
        if u.get("username") == username:
            return u
    return None


def _raw_by_id(user_id: str) -> dict | None:
    user_id = str(user_id)
    su = superuser()
    if su and su["id"] == user_id:
        return su
    for u in _load_users():
        if str(u.get("id")) == user_id:
            return u
    return None


def get_user(username: str) -> dict | None:
    """公开查询：永不携带密码散列（路由层直接返回给前端）。"""
    entry = _raw_by_name(username)
    return _public(entry) if entry else None


def get_user_by_id(user_id: str) -> dict | None:
    entry = _raw_by_id(user_id)
    return _public(entry) if entry else None


def list_users() -> list[dict]:
    """全部用户（超级用户在前），不含密码散列。"""
    return [superuser()] + [_public(u) for u in _load_users()]


def _next_uid(users: list[dict]) -> str:
    """分配下一个普通用户 uid：从 1000 起，只增不减、永不回填。

    uid 一旦分配就是用户目录名（SB-USER/<uid>/），因此**号位永不复用**：
    删掉 1000 之后下一个仍是当前最大值 +1，而不是 1000。回填会让新用户直接
    继承上一个用户遗留的对话、私有资源和偏好——这是数据串号，比留下空洞严重。

    已删除的用户不在 users.json 里了，所以还要看 USER_HOME_ROOT 下残留的目录名
    （删号默认保留目录），否则照样会命中一个有数据的旧目录。
    """
    taken = {int(i) for i in (str(u.get("id")) for u in users) if i.isdigit()}
    try:
        taken |= {
            int(p.name) for p in user_paths.USER_HOME_ROOT.iterdir()
            if p.is_dir() and p.name.isdigit()
        }
    except OSError:
        pass
    return str(max([FIRST_USER_UID - 1, *taken]) + 1)


# ── 密码 ──

def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str, int]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_HASH, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex(), _PBKDF2_ITERATIONS


def verify_password(password: str, entry: dict) -> bool:
    pw = entry.get("password") if isinstance(entry.get("password"), dict) else {}
    salt_hex = pw.get("salt", "")
    stored = pw.get("hash", "")
    if not salt_hex or not stored:
        return False
    try:
        _, digest, _ = _hash_password(password, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(digest, stored)


# ── 注册 / 校验 ──

def validate_username(username: str) -> str | None:
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        return "用户名需为 2-64 位字母、数字、下划线、点、连字符或中文，且不以符号开头"
    return None


def validate_password(password: str) -> str | None:
    if not isinstance(password, str) or len(password) < 8:
        return "密码至少 8 位"
    return None


def _assert_username_free(users: list[dict], username: str, exclude_uid: str = "") -> None:
    su = superuser()
    if su and su["username"] == username and su["id"] != exclude_uid:
        raise AuthError(f"用户名 {username} 已被占用", 409)
    for u in users:
        if u.get("username") == username and str(u.get("id")) != exclude_uid:
            raise AuthError(f"用户名 {username} 已存在", 409)


def create_user(username: str, password: str, display_name: str = "") -> dict:
    """创建普通用户（uid 由系统分配，从 1000 起）。"""
    err = validate_username(username)
    if err:
        raise AuthError(err)
    err = validate_password(password)
    if err:
        raise AuthError(err)
    users = _load_users()
    _assert_username_free(users, username)
    uid = _next_uid(users)
    salt_hex, digest, iters = _hash_password(password)
    entry = {
        "id": uid,
        "username": username,
        "display_name": (display_name or username).strip()[:64],
        "password": {"algo": _PBKDF2_HASH, "salt": salt_hex, "hash": digest, "iterations": iters},
        "role": "user",
        "builtin": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    users.append(entry)
    _save_users(users)
    # 目录在创建时就建好：首登前用户目录不存在会让「偏好/工作区」读接口
    # 面对不存在的父目录，早建早简单（写入本身各处仍各自 mkdir）。
    user_home(uid).mkdir(parents=True, exist_ok=True)
    return _public(entry)


def rename_user(uid: str, new_username: str) -> dict:
    """改用户名。**只改名字不改 uid**——uid 是用户目录名，搬家不在本次范围。"""
    err = validate_username(new_username)
    if err:
        raise AuthError(err)
    if str(uid) == SUPERUSER_UID:
        raise AuthError("超级用户的用户名在 .env 中配置，不能在此修改", 400)
    users = _load_users()
    target = next((u for u in users if str(u.get("id")) == str(uid)), None)
    if target is None:
        raise AuthError(f"用户不存在: {uid}", 404)
    _assert_username_free(users, new_username, exclude_uid=str(uid))
    target["username"] = new_username
    _save_users(users)
    return _public(target)


def set_display_name(uid: str, display_name: str) -> dict:
    if str(uid) == SUPERUSER_UID:
        raise AuthError("超级用户的展示名跟随 .env 中的用户名", 400)
    users = _load_users()
    target = next((u for u in users if str(u.get("id")) == str(uid)), None)
    if target is None:
        raise AuthError(f"用户不存在: {uid}", 404)
    target["display_name"] = (display_name or target.get("username") or "").strip()[:64]
    _save_users(users)
    return _public(target)


def set_password(uid: str, new_password: str, *, allow_superuser: bool = False) -> None:
    """重置指定用户的密码。

    超级用户的密码只在 .env 里，默认拒绝（``allow_superuser`` 仅供未来
    导入工具等内部场景显式开启，且不回写 .env）。
    """
    err = validate_password(new_password)
    if err:
        raise AuthError(err)
    if str(uid) == SUPERUSER_UID:
        if not allow_superuser:
            raise AuthError("超级用户的密码在 .env 的 SOTABAND_ROOT_PASSWORD 中配置", 400)
        return
    users = _load_users()
    target = next((u for u in users if str(u.get("id")) == str(uid)), None)
    if target is None:
        raise AuthError(f"用户不存在: {uid}", 404)
    salt_hex, digest, iters = _hash_password(new_password)
    target["password"] = {"algo": _PBKDF2_HASH, "salt": salt_hex, "hash": digest, "iterations": iters}
    target["password_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_users(users)


def change_own_password(uid: str, old_password: str, new_password: str) -> None:
    """用户改自己的密码——必须先验证旧密码。"""
    if str(uid) == SUPERUSER_UID:
        raise AuthError("超级用户的密码在 .env 的 SOTABAND_ROOT_PASSWORD 中配置", 400)
    entry = _raw_by_id(uid)
    if entry is None:
        raise AuthError("用户不存在", 404)
    if not verify_password(old_password, entry):
        raise AuthError("原密码不正确", 403)
    set_password(uid, new_password)


def delete_user(uid: str) -> dict:
    """删除账号。**只删凭据，不删用户目录**（数据删除不可逆，留给手工处理）。"""
    if str(uid) == SUPERUSER_UID:
        raise AuthError("不能删除超级用户", 400)
    users = _load_users()
    remain = [u for u in users if str(u.get("id")) != str(uid)]
    if len(remain) == len(users):
        raise AuthError(f"用户不存在: {uid}", 404)
    _save_users(remain)
    return {"id": str(uid), "home_kept": str(user_home(str(uid)))}


def authenticate(username: str, password: str) -> dict | None:
    su = _verify_superuser(password)
    if su and su["username"] == username:
        return su
    entry = _raw_by_name(username)
    if entry is None:
        # 用户不存在时也执行一次哈希，拉平响应时间防用户名枚举
        _hash_password(password)
        return None
    if entry.get("id") == SUPERUSER_UID:
        # 超级用户只应通过 .env 命中；走到这里说明 .env 已改但库里留了旧号
        return None
    if not verify_password(password, entry):
        return None
    return _public(entry)


# ── 令牌 ──

def _get_secret() -> bytes:
    env = os.getenv("SOTABAND_AUTH_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secret)
    return secret


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user: dict, ttl: int | None = None) -> str:
    payload = {
        "uid": user["id"],
        "username": user["username"],
        "role": user.get("role", "user"),
        "exp": int(time.time()) + (ttl if ttl is not None else TOKEN_TTL_SECONDS),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_get_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def verify_token(token: str) -> dict | None:
    """校验令牌，有效返回 payload dict，无效/过期返回 None。"""
    if not isinstance(token, str) or "." not in token:
        return None
    body, _, sig = token.partition(".")
    try:
        sig_raw = _b64d(sig)
    except Exception:
        return None
    expect = hmac.new(_get_secret(), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig_raw, expect):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("uid"):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload
