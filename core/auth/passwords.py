"""密码哈希与策略 —— bcrypt + 组合规则。

决策见 ``docs/adr-08-user-space-and-network.md`` ADR-08-01a：
用官方 ``bcrypt`` 包，**不用 ``passlib``**（1.7.4 与 ``bcrypt>=4.1`` 不兼容）。

## 密码策略（用户 2026-10-27 指定）

- **长度 8–20 位**（按**字符数**，不是字节数）；
- **必须同时包含字母和数字**。

为什么长度改用字符数：旧规则是"8–72 字节，中文按 3 字节计"，给用户的提示是
"至少 8 个字节" —— 普通用户无法理解，也没法自己数。改成"8-20 位"后提示可以直接
写成「8-20 位，需同时包含字母和数字」。

bcrypt 的 **72 字节硬上限仍然守着**：20 位里如果塞满多字节字符仍可能超限
（例如 2 个 ASCII + 18 个 4 字节字符 = 74 字节）。这种情况单独给一条提示。

## 三个必须守住的东西

1. **72 字节上限**：bcrypt 对超长输入**直接抛 ``ValueError``**（不是静默截断）。
   绝不能静默截断 —— 那会让"同一密码不同后缀"都能登录，违背用户的安全预期。
2. **恒定耗时**：用户不存在时也必须跑一次 bcrypt，否则响应时间本身就是用户
   枚举的信道（威胁模型 T3）。
3. **不泄露用户是否存在**：所有失败对外都是同一个响应，差异只进审计日志。
"""

from __future__ import annotations

import re

import bcrypt

from config.settings import settings

#: bcrypt 的硬上限，不可配置、不可绕过
BCRYPT_HARD_LIMIT = 72

#: 至少一个字母（不区分大小写，不限 ASCII —— 允许 Unicode 字母更友好）
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
#: 至少一个数字（``\d`` 在 Unicode 模式下也匹配全角数字等）
_HAS_DIGIT = re.compile(r"\d", re.UNICODE)


class PasswordValidationError(ValueError):
    """密码不符合策略。

    消息是**可以直接返回给用户**的（不含敏感信息），由路由层转成 400。
    """


def password_byte_length(password: str) -> int:
    """密码的 UTF-8 字节长度（仅供 bcrypt 上限检查与诊断，不再用于长度策略）。"""
    return len(password.encode("utf-8"))


def validate_password(password: str) -> None:
    """按策略校验密码。不合规抛 :class:`PasswordValidationError`。

    校验顺序刻意如此：**先长度、再组合、最后字节上限**。
    这样用户每次只会看到**一条**最该先修的问题，而不是一次收到三条错误。
    """
    if not isinstance(password, str) or not password:
        raise PasswordValidationError("密码不能为空")

    min_len = settings.auth.password_min_length
    max_len = settings.auth.password_max_length
    length = len(password)

    if length < min_len or length > max_len:
        raise PasswordValidationError(
            f"密码长度需为 {min_len}-{max_len} 位，当前 {length} 位"
        )

    if not _HAS_LETTER.search(password):
        raise PasswordValidationError("密码需包含至少一个字母")
    if not _HAS_DIGIT.search(password):
        raise PasswordValidationError("密码需包含至少一个数字")

    # 长度合规（≤20 位）不代表字节数合规：多字节字符仍可能撞上 bcrypt 的硬上限。
    nbytes = password_byte_length(password)
    if nbytes > BCRYPT_HARD_LIMIT:
        raise PasswordValidationError(
            f"密码包含过多多字节字符（{nbytes} 字节，bcrypt 上限 {BCRYPT_HARD_LIMIT}），"
            "请改用字母、数字与常见符号"
        )


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希（``$2b$`` + 配置的 work factor）。

    调用方**必须**先 :func:`validate_password`：超长密码会让 ``bcrypt`` 抛
    ``ValueError``。
    """
    rounds = settings.auth.bcrypt_rounds
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)
    ).decode("ascii")


_dummy_hash: bytes | None = None


def _get_dummy_hash() -> bytes:
    """懒生成的对照哈希，用于"用户不存在"路径的恒定耗时。

    不在模块导入时生成 —— 那会让每次启动多花约 250ms（work factor 12）。
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(
            b"dummy-password-for-constant-time-comparison",
            bcrypt.gensalt(rounds=settings.auth.bcrypt_rounds),
        )
    return _dummy_hash


def verify_password(password: str, hashed: str | None) -> bool:
    """校验密码。

    ``hashed is None`` 表示"用户不存在"，此时**仍然跑一次 bcrypt** 再返回
    ``False`` —— 抹平耗时差异（威胁模型 T3）。

    超长密码同样走假校验路径：注册时已拒绝 >72 字节，所以库里不可能存在这样的
    密码；这里既不能截断（会制造误判），也不能直接返回（会暴露长度信息）。
    """
    raw = password.encode("utf-8")
    try:
        if not hashed or len(raw) > BCRYPT_HARD_LIMIT:
            bcrypt.checkpw(raw[:BCRYPT_HARD_LIMIT], _get_dummy_hash())
            return False
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        # 库里的哈希格式坏了（被篡改/迁移出错）：按失败处理，不抛给调用方
        return False


def needs_rehash(hashed: str) -> bool:
    """判断已有哈希是否需要按当前配置升级。

    场景：将来提高 work factor 或换算法（如 Argon2id）。登录成功后检测一次，
    需要则用明文重新哈希并更新 —— 用户无感，配置可以渐进升级。
    """
    try:
        prefix, params = hashed.split("$")[1], hashed.split("$")[2]
        if prefix != "2b":
            return True
        return int(params) < settings.auth.bcrypt_rounds
    except (IndexError, ValueError):
        return True
