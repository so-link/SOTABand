"""密码哈希边界测试 —— bcrypt 的 72 字节上限。

归属需求 8.1；决策见 ``docs/adr-08-user-space-and-network.md`` ADR-08-01a。

## 这个测试为什么存在

``bcrypt`` 的输入上限是 **72 字节**，而官方包的 4.x / 5.x 遇到超长输入会
**直接抛 ``ValueError``**，不是静默截断。这意味着如果注册接口不校验长度，
用户输入一个长密码就会得到一个 500，而不是一个清晰的"密码过长"提示。

这里的断言把**第三方库的真实行为**固定下来：将来 bcrypt 升级若改变行为
（比如改为自动截断），这些测试会失败并提醒我们重新评估 —— 因为"自动截断"
比"报错"更危险：用户设了 80 字节的密码，实际只有前 72 字节生效，而用户
以为自己设的是完整的那串。

## 为什么要按字节而不是字符判断

UTF-8 下 1 个汉字 = 3 字节，所以 **24 个汉字就到达 72 字节上限**，
25 个就超了。校验必须用 ``len(pw.encode("utf-8"))``，用 ``len(pw)`` 会漏掉
中文和 emoji 的情况。
"""

from __future__ import annotations

import bcrypt
import pytest

#: bcrypt 的硬上限（字节）
BCRYPT_MAX_BYTES = 72

# 轮数调到最低，让测试只验证边界行为而不必等慢哈希
_FAST_ROUNDS = 4


def _hash(password: bytes) -> bytes:
    return bcrypt.hashpw(password, bcrypt.gensalt(rounds=_FAST_ROUNDS))


def test_bcrypt_accepts_exactly_72_bytes() -> None:
    assert _hash(b"a" * BCRYPT_MAX_BYTES)


def test_bcrypt_rejects_73_bytes_with_value_error() -> None:
    """73 字节 → ValueError。注册/登录入口必须在此之前拦住。"""
    with pytest.raises(ValueError, match="72 bytes"):
        _hash(b"a" * (BCRYPT_MAX_BYTES + 1))


def test_bcrypt_rejects_far_over_limit() -> None:
    with pytest.raises(ValueError, match="72 bytes"):
        _hash(b"a" * 500)


@pytest.mark.parametrize(
    ("chars", "expected_ok"),
    [
        (23, True),   # 69 bytes
        (24, True),   # 72 bytes —— 恰好到顶
        (25, False),  # 75 bytes —— 超限
    ],
)
def test_chinese_password_byte_boundary(chars: int, expected_ok: bool) -> None:
    """汉字按 3 字节算 —— 这是"用 len(pw) 判断会出错"的实证。"""
    password = ("密" * chars).encode("utf-8")
    assert len(password) == chars * 3

    if expected_ok:
        assert _hash(password)
    else:
        with pytest.raises(ValueError, match="72 bytes"):
            _hash(password)


def test_bcrypt_hashes_are_salted_independently() -> None:
    """同一密码两次哈希结果必须不同 —— 确认每个用户有独立盐。"""
    password = b"correct horse battery staple"
    assert _hash(password) != _hash(password)


def test_bcrypt_verify_round_trip() -> None:
    password = "正确密码-2026".encode()
    hashed = _hash(password)
    assert bcrypt.checkpw(password, hashed)
    assert not bcrypt.checkpw("错误密码".encode(), hashed)


def test_bcrypt_uses_2b_prefix() -> None:
    """统一用 $2b$ 版本。

    $2a$/$2y$/$2b$ 在历史上对 8-bit 字符的处理有差异，生成侧统一 $2b$
    可以避免历史实现的截断缺陷。
    """
    assert _hash(b"prefix-check").startswith(b"$2b$")


def test_bcrypt_exposes_no_about_attribute() -> None:
    """``bcrypt.__about__`` 已不存在 —— 这就是不能用 passlib 的原因。

    passlib 1.7.4 会读取 ``bcrypt.__about__.__version__``，在 bcrypt >= 4.1 上
    直接报错导致服务起不来（社区多次报告）。故本项目直接使用官方 ``bcrypt``
    包，不引 passlib。
    """
    assert not hasattr(bcrypt, "__about__")
