"""本地身份层：用户存储 + 密码哈希 + 签名令牌（todo 8.1 第一步）

设计约束：
- 零第三方依赖：PBKDF2-HMAC-SHA256（hashlib.pbkdf2_hmac）做密码哈希，
  HMAC-SHA256 签名令牌（格式 ``<payload_b64url>.<sig_b64url>``），
  避免在现有环境引入 passlib/pyjwt 等新包。
- 用户数据写入**私有资源根**（``<private>/users/users.json``），与
  「写只进私有根」的全局约定一致；内置根保持只读。
- 令牌密钥 ``<private>/users/secret.key`` 首次使用自动生成（0600）。
  可通过环境变量 ``SOTABAND_AUTH_SECRET`` 显式指定（多实例共享密钥时使用）。
- 本模块只做身份原语，不做路由挂载，也不改变
  ``core.user_context.get_current_user_id()`` 的既有回退行为——
  路由层在解析到有效令牌时以本模块返回的用户为准，
  无令牌时仍回退实例身份，保证单用户实例不被断裂。
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

from core.user_paths import auth_dir
from core.resource.json_store import write_json_atomic

# 系统级例外：登录用户表与令牌密钥是**平台级实体**（验证任何用户的令牌
# 都需要同一把密钥），放 SYSTEM_ROOT/storage/auth/，不随用户目录走。
USERS_DIR = auth_dir()
USERS_FILE = USERS_DIR / "users.json"
SECRET_FILE = USERS_DIR / "secret.key"

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


def get_user(username: str) -> dict | None:
    for u in _load_users():
        if u.get("username") == username:
            return u
    return None


def get_user_by_id(user_id: str) -> dict | None:
    for u in _load_users():
        if u.get("id") == user_id:
            return u
    return None


def list_users() -> list[dict]:
    """返回不含密码散列的用户列表（供 /users 等只读展示）。"""
    return [{k: v for k, v in u.items() if k != "password"} for u in _load_users()]


# ── 密码 ──

def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str, int]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_HASH, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex(), _PBKDF2_ITERATIONS


def verify_password(password: str, entry: dict) -> bool:
    salt_hex = entry.get("password", {}).get("salt", "") if isinstance(entry.get("password"), dict) else ""
    stored = entry.get("password", {}).get("hash", "") if isinstance(entry.get("password"), dict) else ""
    iters = entry.get("password", {}).get("iterations", _PBKDF2_ITERATIONS)
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


def register_user(username: str, password: str, display_name: str = "") -> dict:
    err = validate_username(username)
    if err:
        raise AuthError(err)
    err = validate_password(password)
    if err:
        raise AuthError(err)
    users = _load_users()
    if any(u.get("username") == username for u in users):
        raise AuthError(f"用户名 {username} 已存在", 409)
    salt_hex, digest, iters = _hash_password(password)
    entry = {
        "id": f"user-{secrets.token_hex(6)}",
        "username": username,
        "display_name": display_name or username,
        "password": {"algo": _PBKDF2_HASH, "salt": salt_hex, "hash": digest, "iterations": iters},
        "role": "admin" if not users else "user",  # 首个用户即管理员
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    users.append(entry)
    _save_users(users)
    return {k: v for k, v in entry.items() if k != "password"}


def authenticate(username: str, password: str) -> dict | None:
    entry = get_user(username)
    if entry is None:
        # 用户不存在时也执行一次哈希，拉平响应时间防用户名枚举
        _hash_password(password)
        return None
    if not verify_password(password, entry):
        return None
    return {k: v for k, v in entry.items() if k != "password"}


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
