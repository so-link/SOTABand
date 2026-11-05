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


# ── 超级用户（用户名/显示名在用户表，密码只在 .env） ──

def superuser_password_configured() -> bool:
    return bool(os.getenv("SOTABAND_ROOT_PASSWORD", "").strip())


def _seed_superuser_row(users: list[dict]) -> dict:
    """首次使用时把超管行种进用户表（id=0, builtin=True）。

    用户名初值取 ``SOTABAND_ROOT_USERNAME``（留空=root）；此后 .env 只决定
    **密码**，用户名/显示名以表里这一行为准，界面上与普通用户一样可改。
    行内**不存密码散列**：超管密码只在 .env，验证走 ``_verify_superuser``
    独立通道，表里即使被手工塞入散列也永不被使用（见 authenticate）。
    """
    username = os.getenv("SOTABAND_ROOT_USERNAME", "").strip() or "root"
    row = {
        "id": SUPERUSER_UID,
        "username": username,
        "display_name": username,
        "role": "admin",
        "builtin": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    users.insert(0, row)
    _save_users(users)
    return row


def _downgrade_legacy_admins(users: list[dict]) -> None:
    """表内 role=admin 的普通行降级为 user——原地修改，仅变化时落盘。

    唯一 admin 是 .env 超管（id=0）：老库首用户 role=admin 的历史遗留若
    不清除，升级后仍会保留全部超管权限，与「仅 .env 超管可管理用户/内置
    资源」的设计冲突。显示字段（username/display_name）不动。
    """
    changed = False
    for u in users:
        if isinstance(u, dict) and str(u.get("id")) != SUPERUSER_UID and u.get("role") == "admin":
            u["role"] = "user"
            changed = True
    if changed:
        _save_users(users)


def is_superuser_uid(uid: str) -> bool:
    """uid 是否属于 .env 超管——全系统唯一的 admin 判定口径（角色收口用）。"""
    return str(uid) == SUPERUSER_UID


def known_uids() -> set[str]:
    """用户表中存在的 uid 集合。

    令牌回查与 cron 调度都以用户表为准判定「这个 uid 还是不是用户」——
    超管行恒在表中（``_load_users`` 保证），因此单用户模式下 ``{"0"}``
    始终有效，匿名超管的 cron 不会被误停。
    """
    return {str(u.get("id")) for u in _load_users() if isinstance(u, dict)}


def superuser() -> dict:
    """超级用户行（普通用户形状的公开视图，缺行时先种入）。

    与"能否用密码登录"是两件事：单用户模式（未配置密码）下匿名请求本身就是
    超级用户，用户管理页必须能看到这一行并把它标成内置——若此时返回
    None，列表里就没有超管行，看起来像"超级用户不存在"。
    是否可密码登录由 ``_verify_superuser`` 把门。
    """
    row = next((u for u in _load_users() if str(u.get("id")) == SUPERUSER_UID), {})
    return _public(row)


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

def _raw_load_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _load_users() -> list[dict]:
    """读用户表：清除遗留 admin 角色，并保证超管行（id=0）存在（首次自动种入）。

    超管行是表里的普通一行（无密码散列），因此所有读写路径天然覆盖它；
    种入动作放在这里而不是各调用点，避免遗漏。
    """
    users = _raw_load_users()
    _downgrade_legacy_admins(users)  # 唯一 admin = .env 超管；原地改，仅变化时落盘
    if not any(str(u.get("id")) == SUPERUSER_UID for u in users if isinstance(u, dict)):
        _seed_superuser_row(users)  # 原地 insert(0) 并落盘
    return users


def _save_users(users: list[dict]) -> None:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(USERS_FILE, users)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "password"}


def _raw_by_name(username: str) -> dict | None:
    """含密码散列的原始条目——**仅供本模块校验密码使用**。

    超管行已在表里（无密码散列），无需特判。
    """
    for u in _load_users():
        if u.get("username") == username:
            return u
    return None


def _raw_by_id(user_id: str) -> dict | None:
    user_id = str(user_id)
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
    users = list(_load_users())
    # 稳定排序：超管行置顶，其余保持表内顺序（种入在首、创建追加在尾）
    users.sort(key=lambda u: str(u.get("id")) != SUPERUSER_UID)
    return [_public(u) for u in users]


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
    # 超管行已在表里（users 来自 _load_users），循环覆盖全部账号
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
    """改用户名。**只改名字不改 uid**——uid 是用户目录名，搬家不在本次范围。

    超管与普通用户同路径：都在用户表这一行上改名（超管行的密码不在表里，
    登录名随本行 username 走）。
    """
    err = validate_username(new_username)
    if err:
        raise AuthError(err)
    users = _load_users()
    if next((u for u in users if str(u.get("id")) == str(uid)), None) is None:
        # 超管行可能尚未种入（首次访问前就改名）：先种，保证 id=0 可改
        _seed_superuser_row(users)
    target = next((u for u in users if str(u.get("id")) == str(uid)), None)
    if target is None:
        raise AuthError(f"用户不存在: {uid}", 404)
    _assert_username_free(users, new_username, exclude_uid=str(uid))
    target["username"] = new_username
    _save_users(users)
    return _public(target)


def set_display_name(uid: str, display_name: str) -> dict:
    users = _load_users()
    if next((u for u in users if str(u.get("id")) == str(uid)), None) is None:
        _seed_superuser_row(users)
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
    # 凭据代次 +1：改密/重置后，旧代次签发的令牌在 verify_token 回查时全部失效
    target["pwd_version"] = int(target.get("pwd_version", 0)) + 1
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
    # 顺手清空该用户的 cron 任务记录（只删任务定义，不碰用户目录）：
    # 否则调度器重启后仍会从他的 platform.db 里把任务捞起来永动执行。
    from core.cron import store as cron_store

    cron_store.delete_all(uid)
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
        # 超管只能经 .env 密码通道命中（上面 _verify_superuser）；
        # 即使有人手工往超管行塞了散列，也不从普通散列通道通过（纵深防御）
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
    # 凭据代次 pwd：签发时取表内 pwd_version（改密/重置时 +1）。超管行恒无
    # pwd_version（散列不在表里、set_password 拒绝写超管行）→ 恒为 0，
    # .env 超管通道的令牌天然不受回查影响（改 .env 密码须重启，语义不变）。
    pwd = int((_raw_by_id(str(user["id"])) or {}).get("pwd_version", 0))
    payload = {
        "uid": user["id"],
        "username": user["username"],
        "role": user.get("role", "user"),
        "pwd": pwd,
        "exp": int(time.time()) + (ttl if ttl is not None else TOKEN_TTL_SECONDS),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_get_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def verify_token(token: str) -> dict | None:
    """校验令牌，有效返回 payload dict，无效/过期返回 None。

    HMAC + exp 之外还要回查用户表（users.json 单文件原子读，足够轻量）：
    - 查无此人（删号）→ 拒绝；
    - payload 的 pwd 与表内 pwd_version 不一致（改密/重置）→ 拒绝。
    堵住「删号/改密后旧令牌最长 7 天仍有效」的窗口。超管行恒在表里且
    pwd_version 恒缺省 0，等效于按现有逻辑单独放行，不受影响。
    """
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
    row = _raw_by_id(str(payload["uid"]))
    if row is None or int(row.get("pwd_version", 0)) != int(payload.get("pwd", 0)):
        return None
    # 角色以 uid 为准重写：唯一 admin = .env 超管，旧令牌里烙下的 role=admin
    # （老库首用户）与表内遗留 role 都不再是权限依据
    payload["role"] = "admin" if str(payload["uid"]) == SUPERUSER_UID else "user"
    return payload
